"""Pins on the guided website setup (``/webadmin setup``).

``mutate_for`` is the only function that changes ``config/services/webservice.yaml`` from Discord, so
it is pinned without a bot, a network or a running service: a plan goes in, the patched document comes
out, and every gate that must not drift is asserted on the serialised file — the operator's own keys,
comments and listen address survive, the session key is generated only when it is absent, the Discord
block names a secret-store KEY, and neither the client secret nor the plaintext local password ever
reaches the YAML.

Each login mode collects its answers in ONE ``utils.ConfigModal`` built from a per-mode config dict,
which is pinned field by field. The last block drives the shipped callback against a temporary
configuration directory: the refusal on a non-master node, the modal it opens on the master, and the
answers a pressed modal hands back. Command REGISTRATION on a live bot is not covered here — that
needs a bot restart and a Discord Admin pressing the button.
"""
from __future__ import annotations

import asyncio
import gettext
import types

from pathlib import Path

import discord
import pytest

from core import utils
from plugins.admin import webadmin
from core.utils import ConfigModal, configwrite
from core.utils.yaml_helper import new_yaml
from plugins.admin.webadmin import (
    CLIENT_SECRET_KEY, CONFIG_FILE, CONFIG_SCHEMA, DEFAULT_LISTEN, DEFAULT_PORT, MAX_PASSWORD,
    MIN_PASSWORD, MIN_PORT, MAX_PORT, MODAL_LIMIT, MODE_FIELDS, SetupPlan, SetupView,
    SetupWizard, config_path, default_public_url, listen_address, master_name, mode_config,
    mode_fields, mode_modal, mode_title, mutate_for, outcome_embed, parse_port, secret_looks_valid,
    store_secret, taken_username, webadmin_group)
from services.webservice.auth.local import verify_password
from services.webservice.setup.console import MAX_PASSWORD_LENGTH, hash_password

REPO = Path(__file__).resolve().parents[1]

_URL = "https://dcs.example.com"
_SECRET = "s3cr3t-client-value-" + "x" * 22
#: the password the operator types into the wizard's form
_PASSWORD = "correct horse battery"

_EXISTING = """\
# operator note: dashboard.live is set on purpose
DEFAULT:
  dashboard:
    live: true
  listen: 127.0.0.1
  port: 8000
  debug: false
  access:
    music.write:
      add: [Media Manager]
"""

_EXISTING_WITH_SECRET = """\
DEFAULT:
  session:
    secret: 0123456789abcdef0123456789abcdef
  auth:
    local:
      enabled: true
      users:
        - username: operator
          password_hash: pbkdf2_sha256$OPERATOR
          roles: [Admin]
"""


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``.

    A test process is not one of the bot's own entry points, so the parser's ``-n/--node`` attribute
    never exists. pykwalify loads ``core/utils/validators.py`` as a fresh extension module, so only
    the patch on ``core.commandline`` reaches it.
    """
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def _plan(**overrides) -> SetupPlan:
    values = dict(port=9876, public_base_url=_URL, listen='0.0.0.0')
    values.update(overrides)
    return SetupPlan(**values)


def _patch(tmp_path, plan, text=None, schemas=(CONFIG_SCHEMA,)):
    """Write *text* (when given) below a temp config dir and patch it with *plan*."""
    path = Path(config_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding='utf-8')
    result = configwrite.patch_config_file(str(path), mutate_for(plan), schemas=list(schemas))
    return path, result


def _document(path) -> dict:
    with open(path, encoding='utf-8') as handle:
        return new_yaml().load(handle)['DEFAULT']


# ------------------------------------------------------------------ the operator's file survives

def test_an_existing_file_keeps_its_other_keys_comment_and_listen(tmp_path):
    listen = listen_address(new_yaml().load(_EXISTING))
    path, result = _patch(tmp_path, _plan(listen=listen), text=_EXISTING)
    block = _document(path)
    text = path.read_text(encoding='utf-8')
    assert result.changed
    assert block['dashboard']['live'] is True
    assert block['access'] == {'music.write': {'add': ['Media Manager']}}
    assert block['debug'] is False
    assert block['listen'] == '127.0.0.1'
    assert '# operator note: dashboard.live is set on purpose' in text


def test_an_absent_file_is_created_with_the_answers(tmp_path):
    path, result = _patch(tmp_path, _plan(port=9000, public_base_url='http://dcs.lan:9000'))
    block = _document(path)
    assert result.changed and result.backup is None
    assert block['frontend'] is True
    assert block['port'] == 9000
    assert block['listen'] == '0.0.0.0'
    assert block['auth']['public_base_url'] == 'http://dcs.lan:9000'


def test_frontend_port_and_address_are_the_answers_on_an_existing_file(tmp_path):
    path, _ = _patch(tmp_path, _plan(port=8123, public_base_url='https://other.example.org'),
                     text=_EXISTING)
    block = _document(path)
    assert block['frontend'] is True
    assert block['port'] == 8123
    assert block['auth']['public_base_url'] == 'https://other.example.org'


# ------------------------------------------------------------------------- the session key rule
def test_a_missing_session_secret_is_generated_at_least_32_characters(tmp_path):
    path, _ = _patch(tmp_path, _plan(), text=_EXISTING)
    secret = _document(path)['session']['secret']
    assert isinstance(secret, str)
    assert len(secret) >= 32


def test_an_existing_session_secret_is_left_byte_identical(tmp_path):
    path, _ = _patch(tmp_path, _plan(), text=_EXISTING_WITH_SECRET)
    assert _document(path)['session']['secret'] == '0123456789abcdef0123456789abcdef'


def test_an_empty_session_secret_is_replaced(tmp_path):
    path, _ = _patch(tmp_path, _plan(), text="DEFAULT:\n  session:\n    secret: ''\n")
    assert len(_document(path)['session']['secret']) >= 32


# ---------------------------------------------------------------------- the Discord login block

def test_the_discord_block_names_the_store_key_and_nothing_inline(tmp_path):
    path, _ = _patch(tmp_path, _plan(discord=True, client_id=123456789012345678), text=_EXISTING)
    discord = _document(path)['auth']['discord']
    assert set(discord) == {'enabled', 'client_id', 'client_secret_key'}
    assert discord['enabled'] is True
    assert discord['client_id'] == 123456789012345678
    assert discord['client_secret_key'] == CLIENT_SECRET_KEY
    assert 'client_secret' not in discord


def test_the_store_key_is_the_one_the_oauth_backend_reads():
    assert CLIENT_SECRET_KEY == 'discord_client_secret'


def test_no_login_block_is_written_when_no_login_was_chosen(tmp_path):
    path, _ = _patch(tmp_path, _plan(), text=_EXISTING)
    auth = _document(path)['auth']
    assert 'discord' not in auth and 'local' not in auth


# --------------------------------------------------------------------------- the local account

def test_the_local_password_hash_verifies_and_the_plaintext_is_nowhere(tmp_path):
    path, _ = _patch(tmp_path, _plan(local=True, username='operator',
                                     password_hash=hash_password(_PASSWORD)),
                     text=_EXISTING)
    block = _document(path)
    account = block['auth']['local']['users'][-1]
    assert account['username'] == 'operator'
    assert account['roles'] == ['Admin']
    assert verify_password(_PASSWORD, account['password_hash'])
    assert _PASSWORD not in path.read_text(encoding='utf-8')


def test_a_second_run_appends_to_the_existing_users_list(tmp_path):
    path, _ = _patch(tmp_path, _plan(local=True, username='backup',
                                     password_hash=hash_password(_PASSWORD)),
                     text=_EXISTING_WITH_SECRET)
    users = _document(path)['auth']['local']['users']
    assert [user['username'] for user in users] == ['operator', 'backup']
    assert users[0]['password_hash'] == 'pbkdf2_sha256$OPERATOR'
    assert _document(path)['auth']['local']['enabled'] is True


def test_an_existing_account_is_never_replaced(tmp_path):
    with pytest.raises(ValueError):
        _patch(tmp_path, _plan(local=True, username='operator',
                               password_hash=hash_password(_PASSWORD)),
               text=_EXISTING_WITH_SECRET)
    assert _document(config_path(tmp_path))['auth']['local']['users'][0]['password_hash'] == \
        'pbkdf2_sha256$OPERATOR'


# -------------------------------------------------------------------------------- secret hygiene

def test_the_client_secret_is_stored_under_the_backend_key_and_never_in_the_yaml(tmp_path):
    store_secret(tmp_path, _SECRET)
    assert (tmp_path / '.secret' / f'{CLIENT_SECRET_KEY}.pkl').is_file()
    path, _ = _patch(tmp_path, _plan(discord=True, client_id=1), text=_EXISTING)
    text = path.read_text(encoding='utf-8')
    assert _SECRET not in text
    assert CLIENT_SECRET_KEY in text


def test_storing_an_empty_secret_is_refused(tmp_path):
    with pytest.raises(ValueError):
        store_secret(tmp_path, '')


def test_the_wizard_drops_the_secret_once_it_is_stored(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('discord')
    wizard.bind_secret(_SECRET)
    result, error = wizard.write()
    assert error is None and result is not None
    assert wizard.secret is None
    assert _SECRET not in Path(config_path(tmp_path)).read_text(encoding='utf-8')


def test_a_failed_write_reports_without_the_payload(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('discord')
    wizard.bind_secret(_SECRET)
    wizard.port = 1                       # outside the schema's range: the write is refused
    result, error = wizard.write()
    assert result is None and error
    assert _SECRET not in error


# ------------------------------------------------------------------------------ the real schema

def test_the_mutated_document_passes_the_real_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(REPO)
    path, _ = _patch(tmp_path, _plan(discord=True, client_id=123456789012345678, local=True,
                                     username='admin', password_hash=hash_password('x' * 12)),
                     text=_EXISTING)
    with open(path, encoding='utf-8') as handle:
        document = new_yaml().load(handle)
    configwrite.validate_against_schema(configwrite.dump_config(document), [CONFIG_SCHEMA])


# ------------------------------------------------------------------------------- the plain helpers

@pytest.mark.parametrize("value,expected", [
    ("9876", 9876), (1024, 1024), (65535, 65535), ("65536", None), ("80", None),
    ("abc", None), (None, None), ("", None)])
def test_parse_port_accepts_only_a_port_in_range(value, expected):
    assert parse_port(value) == expected


def test_the_default_address_uses_the_public_ip_then_localhost():
    assert default_public_url('203.0.113.7', DEFAULT_PORT) == f'http://203.0.113.7:{DEFAULT_PORT}'
    assert default_public_url(None, 9876) == 'http://localhost:9876'


def test_the_listen_address_is_kept_from_the_file():
    assert listen_address({'DEFAULT': {'listen': '10.0.0.5'}}) == '10.0.0.5'
    assert listen_address(None) == DEFAULT_LISTEN
    assert listen_address({'DEFAULT': {}}) == DEFAULT_LISTEN


def test_the_master_node_is_named_from_the_cluster():
    node = types.SimpleNamespace(all_nodes={
        'DCSSB-02': types.SimpleNamespace(master=False, name='DCSSB-02'),
        'DCSSB-01': types.SimpleNamespace(master=True, name='DCSSB-01')})
    assert master_name(node) == 'DCSSB-01'
    assert master_name(types.SimpleNamespace(all_nodes={})) is None


@pytest.mark.parametrize("value,expected", [
    ("abc123", True), ("", False), (None, False), ("two words", False)])
def test_a_client_secret_is_a_non_empty_value_without_whitespace(value, expected):
    assert secret_looks_valid(value) is expected


# ------------------------------------------------------------------------------ the closing reply

def _text(embed) -> str:
    parts = [embed.title or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


#: the questions every mode's form asks, in the order the modal shows them
_FORMS = {
    'discord': ['port', 'public_base_url', 'client_secret'],
    'local': ['port', 'public_base_url', 'username', 'password'],
    'both': ['port', 'public_base_url', 'client_secret', 'username', 'password'],
}


@pytest.mark.parametrize('mode', sorted(_FORMS))
def test_each_mode_asks_exactly_its_own_fields(mode):
    assert list(mode_fields(mode)) == _FORMS[mode]
    assert list(mode_config(mode, DEFAULT_PORT, _URL)) == _FORMS[mode]


def test_the_both_mode_asks_for_five_fields_and_never_more():
    assert len(mode_fields('both')) == 5
    assert MODE_FIELDS['both'] == ('port', 'public_base_url', 'client_secret', 'username',
                                   'password')
    assert len(mode_fields('both')) <= MODAL_LIMIT


@pytest.mark.parametrize('mode', sorted(_FORMS))
def test_no_mode_needs_more_components_than_one_modal_carries(mode):
    assert len(mode_fields(mode)) <= MODAL_LIMIT


def test_the_password_field_carries_the_logins_bounds():
    password = mode_fields('both')['password']
    assert password['min_length'] == 12
    assert password['max_length'] == 1024
    assert password['required'] is True
    assert (MIN_PASSWORD, MAX_PASSWORD) == (12, MAX_PASSWORD_LENGTH)


def test_the_username_field_stays_within_its_own_bound():
    username = mode_fields('local')['username']
    assert username['max_length'] == 64
    assert username['required'] is True


@pytest.mark.parametrize('mode', sorted(_FORMS))
def test_every_mode_opens_a_utils_config_modal(mode):
    modal = mode_modal(mode, DEFAULT_PORT, _URL)
    assert isinstance(modal, ConfigModal)


@pytest.mark.parametrize('mode', sorted(_FORMS))
def test_the_modal_is_prefilled_with_this_runs_address_and_port(mode):
    modal = mode_modal(mode, 8443, _URL)
    assert modal.value['port'] == 8443
    assert modal.value['public_base_url'] == _URL


def test_the_client_id_is_never_a_field():
    for fields in _FORMS.values():
        assert 'client_id' not in fields


# ------------------------------------------------------------------------------ the answers

def test_the_answers_come_from_the_modals_values(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('both')
    refusal = wizard.bind_answers('both', {'port': 8443, 'public_base_url': 'https://dcs.lan:8443',
                                           'client_secret': _SECRET, 'username': 'admin',
                                           'password': _PASSWORD})
    assert refusal is None
    assert (wizard.port, wizard.public_base_url) == (8443, 'https://dcs.lan:8443')
    assert wizard.secret == _SECRET
    assert (wizard.username, wizard.password_hash) != ('', '')
    assert verify_password(_PASSWORD, wizard.password_hash)


def test_the_plaintext_password_is_never_kept_on_the_wizard(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    assert wizard.bind_answers('local', {'port': 9876, 'public_base_url': _URL,
                                         'username': 'admin', 'password': _PASSWORD}) is None
    assert not hasattr(wizard, 'password')
    assert _PASSWORD not in repr(vars(wizard))


@pytest.mark.parametrize('answers,expected', [
    ({'port': '1', 'public_base_url': _URL, 'client_secret': _SECRET}, 'port'),
    ({'port': '9876', 'public_base_url': 'dcs.example.com', 'client_secret': _SECRET}, 'address'),
    ({'port': '9876', 'public_base_url': _URL, 'client_secret': ''}, 'client secret'),
])
def test_a_discord_form_is_refused_until_every_field_holds(tmp_path, answers, expected):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('discord')
    refusal = wizard.bind_answers('discord', answers)
    assert refusal and expected in refusal
    assert wizard.complete is False


def test_a_local_form_without_a_long_enough_password_is_refused(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    refusal = wizard.bind_answers('local', {'port': 9876, 'public_base_url': _URL,
                                            'username': 'admin', 'password': 'short'})
    assert refusal and '12' in refusal
    assert wizard.password_hash == ''


def test_an_existing_username_is_refused_by_name_and_writes_nothing(tmp_path):
    (tmp_path / 'services').mkdir()
    Path(config_path(tmp_path)).write_text(_EXISTING_WITH_SECRET, encoding='utf-8')
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    refusal = wizard.bind_answers('local', {'port': 9876, 'public_base_url': _URL,
                                            'username': 'operator', 'password': _PASSWORD})
    assert refusal and 'operator' in refusal
    assert wizard.password_hash == ''
    assert taken_username(wizard.config, 'operator') == 'operator'
    assert _document(config_path(tmp_path))['auth']['local']['users'][0]['password_hash'] == \
        'pbkdf2_sha256$OPERATOR'


def test_the_write_refuses_an_account_the_file_gained_meanwhile(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())      # reads a directory without a file
    wizard.bind_mode('both')
    wizard.bind_answers('both', {'port': 9876, 'public_base_url': _URL, 'client_secret': _SECRET,
                                 'username': 'operator', 'password': _PASSWORD})
    Path(config_path(tmp_path)).write_text(_EXISTING_WITH_SECRET, encoding='utf-8')
    result, error = wizard.write()
    assert result is None and 'operator' in error
    assert wizard.secret_stored is False
    assert _document(config_path(tmp_path))['auth']['local']['users'][0]['password_hash'] == \
        'pbkdf2_sha256$OPERATOR'


# ------------------------------------------------------------------------------ the closing reply

def test_the_outcome_names_the_file_the_backup_and_the_account(tmp_path):
    path, result = _patch(tmp_path, _plan(), text=_EXISTING)
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    wizard.username = 'operator'
    embed = outcome_embed(wizard, result, None, '/node restart')
    text = _text(embed)
    assert CONFIG_FILE in text
    assert result.backup is not None and result.backup.name in text
    assert 'operator' in text
    assert '/node restart' in text
    assert wizard.public_base_url in text


def test_the_outcome_never_carries_a_password(tmp_path):
    path, result = _patch(tmp_path, _plan(), text=_EXISTING)
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    assert wizard.bind_answers('local', {'port': 9876, 'public_base_url': _URL,
                                         'username': 'operator', 'password': _PASSWORD}) is None
    text = _text(outcome_embed(wizard, result, None, '/node restart'))
    assert _PASSWORD not in text
    assert 'only copy' not in text and 'one-time' not in text


def test_the_outcome_says_nothing_changed_and_lists_no_password(tmp_path):
    path, result = _patch(tmp_path, _plan())
    wizard = SetupWizard(_node(tmp_path), _bot())
    first = outcome_embed(wizard, result, None, '/node restart')
    assert 'one-time' not in _text(first)
    rewrite = configwrite.patch_config_file(str(path), mutate_for(_plan()), schemas=[CONFIG_SCHEMA])
    second = outcome_embed(wizard, rewrite, None, '/node restart')
    assert rewrite.changed is False
    assert 'already matches' in _text(second)


def test_an_error_outcome_carries_no_password_and_no_restart_hint(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    wizard.username = 'operator'
    embed = outcome_embed(wizard, None, 'the file could not be written', '/node restart')
    text = _text(embed)
    assert 'the file could not be written' in text
    assert _PASSWORD not in text


# ------------------------------------------------------------------------------ the command itself

class _ModalResponse:
    """The modal-submit interaction's response: only its deferral matters here."""

    def __init__(self):
        self.deferred = False

    async def defer(self, *args, **kwargs):
        self.deferred = True


class _RecordingResponse:
    """An interaction response that records and answers, never reaching Discord.

    With ``answers`` set, a ``utils.ConfigModal`` this response receives is PRESSED: its fields are
    filled from the answers, ``on_submit`` runs the way Discord runs it, and the caller's
    ``await modal.wait()`` resumes on the next loop tick.
    """

    def __init__(self, answers=None):
        self.sent = []
        self.modals = []
        self.answers = answers

    async def send_message(self, *args, **kwargs):
        sent = dict(kwargs)
        if args:
            sent['content'] = args[0]
        self.sent.append(sent)

    async def send_modal(self, modal):
        self.modals.append(modal)
        if self.answers is None or not isinstance(modal, ConfigModal):
            return
        for label in modal.children:
            key = label.component.custom_id
            if key in self.answers:
                label.component._value = str(self.answers[key])
        await modal.on_submit(types.SimpleNamespace(response=_ModalResponse()))
        asyncio.get_running_loop().call_soon(modal.stop)


class _Interaction:
    """A component interaction over the wizard message."""

    def __init__(self, node, answers=None):
        self.client = _bot(node=node)
        self.response = _RecordingResponse(answers)
        self.edited = []
        self.followups = []

    async def edit_original_response(self, **kwargs):
        self.edited.append(kwargs)

    class _Followup:
        def __init__(self, parent):
            self.parent = parent

        async def send(self, *args, **kwargs):
            sent = dict(kwargs)
            if args:
                sent['content'] = args[0]
            self.parent.followups.append(sent)

    @property
    def followup(self):
        return self._Followup(self)


def _bot(node=None):
    return types.SimpleNamespace(node=node, application_id=123456789012345678, user=None,
                                 guilds=[])


def _node(config_dir, master=True):
    return types.SimpleNamespace(config_dir=str(config_dir), master=master, public_ip=None,
                                 all_nodes={'master': types.SimpleNamespace(master=True,
                                                                            name='DCSSB-01')})


def _answers(**overrides) -> dict:
    """The answers an operator types into a mode's modal."""
    values = {'port': 8443, 'public_base_url': _URL, 'client_secret': _SECRET,
              'username': 'operator', 'password': _PASSWORD}
    values.update(overrides)
    return values


def _choose(interaction, mode):
    """Press one sign-in mode on a fresh wizard message."""
    wizard = SetupWizard(interaction.client.node, interaction.client)
    view = SetupView(wizard)
    view.mode_select._values = [mode]
    asyncio.run(view._choose_mode(interaction))
    return wizard


def _run_setup(interaction):
    asyncio.run(webadmin_group.get_command('setup').callback(None, interaction))


def test_setup_is_a_command_of_the_webadmin_group():
    names = {'setup', 'status'}
    assert webadmin_group.get_command('setup') is not None
    assert names.issubset({command.name for command in webadmin_group.commands})


def test_setup_carries_the_guild_and_role_checks_and_takes_no_parameters():
    command = webadmin_group.get_command('setup')
    checks = {check.__qualname__ for check in command.checks}
    assert "restricted_check" in checks
    assert any("app_has_role" in name for name in checks), checks
    assert not command._params


def test_a_non_master_node_is_refused_and_nothing_is_written(tmp_path):
    interaction = _Interaction(_node(tmp_path, master=False))
    _run_setup(interaction)
    assert not interaction.response.modals
    assert len(interaction.response.sent) == 1
    text = interaction.response.sent[0]['content']
    assert 'DCSSB-01' in text
    assert '/webadmin setup' in text
    assert list(tmp_path.iterdir()) == []


def test_the_master_node_replies_with_the_wizard_and_opens_no_form(tmp_path):
    interaction = _Interaction(_node(tmp_path))
    _run_setup(interaction)
    assert not interaction.response.modals
    assert len(interaction.response.sent) == 1
    sent = interaction.response.sent[0]
    assert 'embed' in sent and 'view' in sent
    assert sent.get('ephemeral') is True
    # the message already carries both values: they are typed once, in the chosen mode's form
    values = {field.name: field.value for field in sent['embed'].fields}
    assert values['Public address'] == f'http://localhost:{DEFAULT_PORT}'
    assert values['Port'] == str(DEFAULT_PORT)


# ------------------------------------------------------------------------- one modal per mode

@pytest.mark.parametrize('mode,fields', [
    ('discord', ['port', 'public_base_url', 'client_secret']),
    ('local', ['port', 'public_base_url', 'username', 'password']),
    ('both', ['port', 'public_base_url', 'client_secret', 'username', 'password']),
    ('manual', ['port', 'public_base_url']),
])
def test_each_mode_opens_one_utils_config_modal_and_nothing_hand_built(tmp_path, mode, fields):
    interaction = _Interaction(_node(tmp_path), answers=_answers())
    wizard = _choose(interaction, mode)
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, ConfigModal)
    assert type(modal) is ConfigModal
    assert [label.component.custom_id for label in modal.children] == fields
    assert wizard.mode == mode


#: the answers each mode's one form asks for, and how many that is
_FIELD_COUNTS = {'discord': 3, 'local': 4, 'both': 5, 'manual': 2}


@pytest.mark.parametrize('mode,count', sorted(_FIELD_COUNTS.items()))
def test_each_mode_asks_every_answer_once_and_never_more_than_one_modal_carries(mode, count):
    fields = list(mode_fields(mode))
    assert len(fields) == count
    assert len(set(fields)) == count, fields          # no answer is asked twice
    assert fields[:2] == ['port', 'public_base_url'], fields
    assert len(fields) <= MODAL_LIMIT


@pytest.mark.parametrize('mode', sorted(_FIELD_COUNTS))
def test_no_mode_titles_its_form_as_a_step(mode):
    title = mode_title(mode)
    assert 'Step' not in title
    assert title == mode_modal(mode, DEFAULT_PORT, _URL).title


def test_the_manual_form_asks_for_the_address_and_the_port_and_no_login(tmp_path):
    interaction = _Interaction(_node(tmp_path), answers=_answers())
    wizard = _choose(interaction, 'manual')
    assert len(interaction.response.modals) == 1
    fields = [label.component.custom_id for label in interaction.response.modals[0].children]
    assert fields == ['port', 'public_base_url']
    assert wizard.mode == 'manual'


def test_a_pressed_manual_form_binds_the_basics_and_asks_for_no_secret_or_account(tmp_path):
    (tmp_path / 'services').mkdir()
    url = 'https://dcs.lan:8443'
    interaction = _Interaction(_node(tmp_path), answers=_answers(public_base_url=url))
    wizard = _choose(interaction, 'manual')
    assert not interaction.followups
    assert (wizard.port, wizard.public_base_url) == (8443, url)
    assert wizard.secret is None
    assert (wizard.username, wizard.password_hash) == ('', '')
    assert wizard.complete is True
    result, error = wizard.write()
    assert error is None and result.changed
    block = _document(config_path(tmp_path))
    assert block['port'] == 8443
    assert block['auth']['public_base_url'] == url
    assert block['auth']['discord']['client_secret_key'] == CLIENT_SECRET_KEY
    assert 'local' not in block['auth']


def test_the_both_mode_collects_five_answers_in_one_modal_without_a_second_form(tmp_path):
    (tmp_path / 'services').mkdir()
    interaction = _Interaction(_node(tmp_path), answers=_answers())
    wizard = _choose(interaction, 'both')
    assert not interaction.followups
    assert len(interaction.edited) == 1
    assert wizard.complete is True


def test_the_answers_reach_the_file_and_the_store_from_the_modal(tmp_path):
    (tmp_path / 'services').mkdir()
    url = 'https://dcs.lan:8443'
    interaction = _Interaction(_node(tmp_path), answers=_answers(public_base_url=url))
    wizard = _choose(interaction, 'both')
    result, error = wizard.write()
    assert error is None and result.changed
    path = Path(config_path(tmp_path))
    text = path.read_text(encoding='utf-8')
    block = _document(path)
    assert block['port'] == 8443
    assert block['auth']['public_base_url'] == url
    assert verify_password(_PASSWORD, block['auth']['local']['users'][0]['password_hash'])
    assert _PASSWORD not in text
    assert _SECRET not in text
    assert utils.get_password(CLIENT_SECRET_KEY, str(tmp_path)) == _SECRET


def test_a_pressed_local_modal_sets_the_account_up(tmp_path):
    (tmp_path / 'services').mkdir()
    interaction = _Interaction(_node(tmp_path), answers=_answers(username='admin'))
    wizard = _choose(interaction, 'local')
    result, error = wizard.write()
    assert error is None and result.changed
    block = _document(config_path(tmp_path))
    assert block['auth']['local']['enabled'] is True
    assert block['auth']['local']['users'][0]['username'] == 'admin'
    assert verify_password(_PASSWORD, block['auth']['local']['users'][0]['password_hash'])
    assert 'discord' not in block['auth']


def test_a_taken_username_is_refused_on_the_spot(tmp_path):
    (tmp_path / 'services').mkdir()
    Path(config_path(tmp_path)).write_text(_EXISTING_WITH_SECRET, encoding='utf-8')
    interaction = _Interaction(_node(tmp_path), answers=_answers(username='operator'))
    wizard = _choose(interaction, 'local')
    assert len(interaction.followups) == 1
    assert 'operator' in interaction.followups[0]['content']
    assert wizard.complete is False
    assert _document(config_path(tmp_path))['auth']['local']['users'][0]['password_hash'] == \
        'pbkdf2_sha256$OPERATOR'


# --------------------------------------------------------------------------- the manual route

def test_the_manual_mode_names_the_store_key_without_asking_for_the_secret(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('manual')
    assert wizard.complete is True          # nothing is asked: the operator stores the secret
    plan = wizard.plan()
    assert plan.discord is True and plan.local is False
    path, _ = _patch(tmp_path, plan, text=_EXISTING)
    discord = _document(path)['auth']['discord']
    assert discord['client_secret_key'] == CLIENT_SECRET_KEY
    assert 'client_secret' not in discord


def test_the_manual_reply_carries_the_redirect_and_the_storing_command(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('manual')
    wizard.bind_basics(9876, 'https://dcs.example.com')
    text = wizard.manual_steps()
    assert 'https://dcs.example.com/auth/discord/callback' in text
    assert 'from core.utils.os import set_password' in text
    assert 'discord_client_secret' in text


def test_the_client_id_is_the_bots_application_id(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    assert wizard.client_id == 123456789012345678
    user_only = types.SimpleNamespace(config_dir=str(tmp_path), master=True, public_ip=None,
                                      all_nodes={})
    bot = types.SimpleNamespace(node=user_only, application_id=None,
                                user=types.SimpleNamespace(id=987654321098765432))
    assert SetupWizard(user_only, bot).client_id == 987654321098765432


def test_the_wizard_keeps_the_listen_address_the_file_already_has(tmp_path):
    (tmp_path / 'services').mkdir()
    Path(config_path(tmp_path)).write_text(_EXISTING, encoding='utf-8')
    wizard = SetupWizard(_node(tmp_path), _bot())
    assert wizard.listen == listen_address(new_yaml().load(_EXISTING)) == '127.0.0.1'


def test_the_default_address_comes_from_the_nodes_public_ip(tmp_path):
    node = types.SimpleNamespace(config_dir=str(tmp_path), master=True, public_ip='203.0.113.9',
                                 all_nodes={})
    wizard = SetupWizard(node, _bot())
    assert wizard.public_base_url == 'http://203.0.113.9:9876'


def test_a_bot_without_an_application_id_cannot_configure_the_discord_login(tmp_path):
    node = _node(tmp_path)
    bot = types.SimpleNamespace(node=node, application_id=None, user=None, guilds=[])
    wizard = SetupWizard(node, bot)
    assert wizard.client_id == 0
    view = SetupView(wizard)
    view.mode_select._values = ['discord']
    interaction = _Interaction(node)
    asyncio.run(view._choose_mode(interaction))
    assert not interaction.response.modals
    assert len(interaction.response.sent) == 1
    assert 'application id' in interaction.response.sent[0]['content']
    assert wizard.mode is None


def test_the_local_mode_never_needs_the_application_id(tmp_path):
    node = _node(tmp_path)
    bot = types.SimpleNamespace(node=node, application_id=None, user=None, guilds=[])
    wizard = SetupWizard(node, bot)
    view = SetupView(wizard)
    view.mode_select._values = ['local']
    interaction = _Interaction(node, answers=_answers())
    asyncio.run(view._choose_mode(interaction))
    assert len(interaction.response.modals) == 1
    assert wizard.mode == 'local'


# ------------------------------------------------------------------- the way out of the wizard

def _buttons(view) -> list:
    """The view's buttons, in the order they were added."""
    return [child for child in view.children if isinstance(child, discord.ui.Button)]


def test_the_wizard_view_carries_a_secondary_cancel_button_beside_the_write(tmp_path):
    view = SetupView(SetupWizard(_node(tmp_path), _bot()))
    assert isinstance(view.mode_select, discord.ui.Select)
    buttons = _buttons(view)
    assert [button.label for button in buttons] == ['Write the configuration', 'Cancel']
    cancel = buttons[1]
    assert cancel.style is discord.ButtonStyle.secondary
    assert cancel.row == 1
    assert buttons[0].row == 1


def test_the_cancel_button_is_labelled_through_the_catalog(tmp_path, monkeypatch):
    catalog = gettext.translation('admin', localedir=str(REPO / 'locale'), languages=['de'])
    monkeypatch.setattr(webadmin, '_', catalog.gettext)
    view = SetupView(SetupWizard(_node(tmp_path), _bot()))
    cancel = _buttons(view)[1]
    assert cancel.label == catalog.gettext('Cancel') == 'Abbrechen'
