"""Pins on the nine review findings on ``/webadmin`` (the doctor and the guided setup).

Each finding gets its own pin: the blocking I/O the two commands did on the event loop, the wizard's
prefill of a configured address and port, the credentials an abandoned run held, the doctor's verdict
on a public plain-http address, ``mutate_for`` with a Discord plan that has no client id, the text a
refused write puts in a reply, a local account whose name already exists, the outcome's silence about the stored
secret, and the manual route's one-liner.

The loop pins patch the blocking function with a probe and read the thread it was called on. A caller
that keeps the call on the loop fails them: the probe records the main thread.
"""
from __future__ import annotations

import asyncio
import os
import threading
import types

from pathlib import Path

import pytest

from core import DEFAULT_TAG
from core.utils import configwrite
from core.utils.yaml_helper import new_yaml
from plugins.admin import webadmin
from plugins.admin.webadmin import (CONFIG_FILE, CONFIG_SCHEMA, DEFAULT_PORT, FAIL, OK, WARN, Facts,
                                    SetupPlan, SetupView, SetupWizard, config_path, mode_modal,
                                    mutate_for, outcome_embed, status_rows, webadmin_group)
from services.webservice.setup.console import hash_password

_URL = "https://dcs.example.com"
_SECRET = "s3cr3t-client-value-" + "x" * 22
_SENTINEL = "SESSION-SECRET-SENTINEL"
#: the password the operator types into the wizard's form
_PASSWORD = "correct horse battery"

#: the row the public address occupies in a full document
ADDRESS = 4

_CONFIGURED = """\
DEFAULT:
  port: 8443
  listen: 0.0.0.0
  auth:
    public_base_url: https://dcs.example.com
"""

_TWO_USERS = """\
DEFAULT:
  auth:
    local:
      enabled: true
      users:
        - username: admin
          password_hash: pbkdf2_sha256$OLD
          roles: [Admin]
        - username: operator
          password_hash: pbkdf2_sha256$OPERATOR
          roles: [Admin]
"""


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``."""
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


# ------------------------------------------------------------------------------ the helpers

def _node(config_dir, *, master=True, public_ip=None):
    return types.SimpleNamespace(config_dir=str(config_dir), master=master, public_ip=public_ip,
                                 all_nodes={'master': types.SimpleNamespace(master=True,
                                                                            name='DCSSB-01')})


def _bot(node=None):
    return types.SimpleNamespace(node=node, application_id=123456789012345678, user=None,
                                 guilds=[])


def _write_config(tmp_path, text: str) -> Path:
    path = Path(config_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def _block(path) -> dict:
    with open(path, encoding='utf-8') as handle:
        return new_yaml().load(handle)[DEFAULT_TAG]


def _plan(**overrides) -> SetupPlan:
    values = dict(port=9876, public_base_url=_URL, listen='0.0.0.0')
    values.update(overrides)
    return SetupPlan(**values)


def _patch(tmp_path, plan, text=None):
    path = Path(config_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        path.write_text(text, encoding='utf-8')
    return path, configwrite.patch_config_file(str(path), mutate_for(plan),
                                               schemas=[CONFIG_SCHEMA])


def _address_row(block) -> object:
    document = {DEFAULT_TAG: {'frontend': True, 'port': 9876,
                              'auth': dict(block),
                              'session': {'secret': 's' * 40}}}
    facts = Facts(file_present=True, service_present=True, service_running=True, is_master=True,
                  port=9876, url=_URL)
    return status_rows(document, facts)[ADDRESS]


def _text(embed) -> str:
    parts = [embed.title or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


class _Response:
    """An interaction response: records and answers, never reaches Discord."""

    def __init__(self):
        self.sent = []
        self.modals = []
        self.deferred = False

    async def send_message(self, *args, **kwargs):
        sent = dict(kwargs)
        if args:
            sent['content'] = args[0]
        self.sent.append(sent)

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def edit_message(self, **kwargs):
        self.edited_message = kwargs

    async def defer(self):
        self.deferred = True


class _Interaction:
    def __init__(self, node):
        self.client = _bot(node=node)
        self.response = _Response()
        self.edited = []

    async def edit_original_response(self, **kwargs):
        self.edited.append(kwargs)


def _run(name, interaction):
    asyncio.run(webadmin_group.get_command(name).callback(None, interaction))


# ------------------------------------------------------- 1. the blocking calls left the loop

def test_the_wizards_write_runs_on_a_worker_thread(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('manual')                      # complete without a secret or an account
    seen = []

    def probe():
        seen.append(threading.current_thread())
        return None, None

    wizard.write = probe
    interaction = _Interaction(_node(tmp_path))
    asyncio.run(SetupView(wizard)._write(interaction))
    assert seen, "the view never called the write"
    assert seen[0] is not threading.main_thread()
    assert interaction.response.deferred
    assert len(interaction.edited) == 1


def test_the_wizards_config_read_runs_on_a_worker_thread(tmp_path, monkeypatch):
    seen = []

    def probe(config_dir):
        seen.append(threading.current_thread())
        return None

    monkeypatch.setattr(webadmin, 'read_config', probe)
    interaction = _Interaction(_node(tmp_path))
    _run('setup', interaction)
    assert seen, "the command never read the configuration"
    assert seen[0] is not threading.main_thread()
    # the reply is the wizard message itself: no first form asks for the port and the address again
    assert not interaction.response.modals
    assert 'embed' in interaction.response.sent[0]


def test_the_doctors_config_read_runs_on_a_worker_thread(tmp_path, monkeypatch):
    seen = []

    def probe(config_dir):
        seen.append(threading.current_thread())
        return None

    monkeypatch.setattr(webadmin, 'read_config', probe)
    interaction = _Interaction(_node(tmp_path))
    _run('status', interaction)
    assert seen, "the command never read the configuration"
    assert seen[0] is not threading.main_thread()


# ------------------------------------------- 2. a configured address and port are prefilled

def test_a_configured_port_and_address_are_prefilled(tmp_path):
    _write_config(tmp_path, _CONFIGURED)
    wizard = SetupWizard(_node(tmp_path, public_ip='203.0.113.9'), _bot())
    assert wizard.port == 8443
    assert wizard.public_base_url == 'https://dcs.example.com'


def _form_defaults(mode, wizard):
    """The prefilled answers of *mode*'s one form, keyed by field name."""
    modal = mode_modal(mode, wizard.port, wizard.public_base_url)
    return {label.component.custom_id: label.component.default for label in modal.children}


def test_the_mode_form_shows_the_configured_values(tmp_path):
    _write_config(tmp_path, _CONFIGURED)
    wizard = SetupWizard(_node(tmp_path, public_ip='203.0.113.9'), _bot())
    defaults = _form_defaults('both', wizard)
    assert defaults['port'] == '8443'
    assert defaults['public_base_url'] == 'https://dcs.example.com'


def test_without_a_file_the_defaults_stand(tmp_path):
    wizard = SetupWizard(_node(tmp_path, public_ip='203.0.113.9'), _bot())
    assert wizard.port == DEFAULT_PORT
    assert wizard.public_base_url == f'http://203.0.113.9:{DEFAULT_PORT}'
    defaults = _form_defaults('local', wizard)
    assert defaults['port'] == str(DEFAULT_PORT)
    assert defaults['public_base_url'] == f'http://203.0.113.9:{DEFAULT_PORT}'


# ---------------------------------------------- 3. an abandoned run drops its credentials

def test_an_abandoned_run_drops_the_secret_and_holds_no_password(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    wizard.bind_local('admin', _PASSWORD)
    wizard.secret = _SECRET
    view = SetupView(wizard)
    asyncio.run(view.on_timeout())
    assert wizard.secret is None
    assert not hasattr(wizard, 'password')
    assert _PASSWORD not in repr(vars(wizard))


# ------------------------------------------------- 4. a public plain-http address is a WARN

def test_a_public_address_over_plain_http_warns_about_cleartext():
    row = _address_row({'public_base_url': 'http://203.0.113.9:9876',
                        'local': {'enabled': True}})
    assert row.state == WARN
    assert 'cleartext' in row.detail


def test_a_private_address_over_plain_http_is_fine():
    row = _address_row({'public_base_url': 'http://192.168.1.10:9876',
                        'local': {'enabled': True}})
    assert row.state == OK


def test_loopback_over_plain_http_keeps_its_own_warning():
    row = _address_row({'public_base_url': 'http://127.0.0.1:9876', 'local': {'enabled': True}})
    assert row.state == WARN
    assert 'this machine only' in row.detail


def test_any_loopback_address_is_the_local_machine():
    row = _address_row({'public_base_url': 'http://127.0.0.2:9876', 'local': {'enabled': True}})
    assert row.state == WARN
    assert 'this machine only' in row.detail


def test_https_on_a_public_name_is_fine():
    row = _address_row({'public_base_url': _URL, 'local': {'enabled': True}})
    assert row.state == OK


# --------------------------------------------------- 5. a Discord plan needs its client id

def test_a_discord_plan_without_a_client_id_is_refused():
    with pytest.raises(ValueError):
        mutate_for(SetupPlan(discord=True, client_id=None))
    with pytest.raises(ValueError):
        mutate_for(SetupPlan(discord=True, client_id=0))


def test_a_discord_plan_with_a_client_id_still_writes_the_three_keys(tmp_path):
    path, _ = _patch(tmp_path, _plan(discord=True, client_id=123456789012345678))
    discord = _block(path)['auth']['discord']
    assert set(discord) == {'enabled', 'client_id', 'client_secret_key'}
    assert discord['client_id'] == 123456789012345678


def test_a_refused_discord_plan_never_reaches_a_file(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('discord')
    wizard.bind_secret(_SECRET)
    wizard.client_id = 0
    result, error = wizard.write()
    assert result is None and error
    assert not Path(config_path(tmp_path)).exists()


# ---------------------------------------------- 6. a write error never carries its message

def test_a_write_error_names_the_type_and_never_the_message(tmp_path, monkeypatch):
    wizard = SetupWizard(_node(tmp_path), _bot())

    def boom(*args, **kwargs):
        raise configwrite.ConfigWriteError(f"Configuration could not be serialised: {_SENTINEL!r}",
                                           path=config_path(tmp_path))

    monkeypatch.setattr(configwrite, 'patch_config_file', boom)
    result, error = wizard.write()
    assert result is None
    assert _SENTINEL not in error
    assert 'ConfigWriteError' in error
    assert 'webservice.yaml' in error


# ---------------------------------- 7. an existing account is refused, never overwritten

def test_an_existing_username_is_refused_rather_than_overwritten(tmp_path):
    with pytest.raises(ValueError):
        _patch(tmp_path, _plan(local=True, username='admin',
                               password_hash=hash_password(_PASSWORD)),
               text=_TWO_USERS)
    users = _block(config_path(tmp_path))['auth']['local']['users']
    assert [user['username'] for user in users] == ['admin', 'operator']
    assert users[0]['password_hash'] == 'pbkdf2_sha256$OLD'
    assert users[1]['password_hash'] == 'pbkdf2_sha256$OPERATOR'


# ------------------------------------------ 8. the outcome says the secret was stored

def test_an_unchanged_file_still_reports_the_stored_secret(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('manual')
    wizard.bind_secret(_SECRET)
    result, error = wizard.write()
    assert error is None and result.changed
    wizard.bind_secret(_SECRET + '-second')          # same plan, a different secret
    result, error = wizard.write()
    assert error is None and result.changed is False
    text = _text(outcome_embed(wizard, result, None, '/node restart'))
    assert 'already matches' in text
    assert 'auth.discord.client_secret_key' in text
    assert _SECRET + '-second' not in text


def test_no_stored_secret_means_no_secret_row(tmp_path):
    (tmp_path / 'services').mkdir()
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('local')
    wizard.bind_local('admin', _PASSWORD)
    result, error = wizard.write()
    assert error is None
    embed = outcome_embed(wizard, result, None, '/node restart')
    assert 'Client secret' not in [field.name for field in embed.fields]
    assert 'client_secret_key' not in _text(embed)


# -------------------------------------- 9. the manual one-liner names the absolute config dir

def test_the_manual_command_carries_the_absolute_config_dir(tmp_path):
    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('manual')
    wizard.bind_basics(9876, _URL)
    text = wizard.manual_steps()
    config_dir = os.path.abspath(str(tmp_path))
    assert f"set_password('discord_client_secret', '<secret>', '{config_dir}')" in text


def test_a_relative_config_dir_is_rendered_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    node = types.SimpleNamespace(config_dir='config', master=True, public_ip=None, all_nodes={})
    wizard = SetupWizard(node, _bot())
    wizard.bind_mode('manual')
    text = wizard.manual_steps()
    assert f"'{os.path.join(str(tmp_path), 'config')}'" in text


# ------------------------------------------------------------------ the doctor still answers

def test_the_status_reply_still_carries_every_gate(tmp_path):
    _write_config(tmp_path, _CONFIGURED.replace('DEFAULT:\n',
                                                'DEFAULT:\n  frontend: true\n  session:\n'
                                                '    secret: ' + 's' * 40 + '\n'))
    interaction = _Interaction(_node(tmp_path))
    _run('status', interaction)
    assert len(interaction.response.sent[0]['embed'].fields) == 8
    assert CONFIG_FILE in _text(interaction.response.sent[0]['embed'])


# --------------------------------------------- 10. the wizard has a way out that writes nothing

def test_cancel_drops_the_secret_stops_the_view_and_leaves_one_sentence(tmp_path):
    from discord.ui import Button, Select

    wizard = SetupWizard(_node(tmp_path), _bot())
    wizard.bind_mode('discord')
    wizard.bind_secret(_SECRET)
    interaction = _Interaction(_node(tmp_path))

    async def press_cancel():
        """Build the view where Discord builds it — on the loop, so ``stop()`` can land."""
        view = SetupView(wizard)
        # the controls it offers first, so the absence afterwards is not an absence of both states
        assert any(isinstance(child, Select) for child in view.children)
        buttons = [child for child in view.children if isinstance(child, Button)]
        assert [button.label for button in buttons] == ['Write the configuration', 'Cancel']
        # the control the operator presses is the one whose callback ends the run
        assert buttons[1].callback == view._cancel
        await buttons[1].callback(interaction)
        return view

    view = asyncio.run(press_cancel())

    assert wizard.secret is None
    assert view.is_finished()
    edited = interaction.response.edited_message
    assert edited == {'content': 'Setup cancelled — nothing was written.',
                      'embed': None, 'view': None}
    # no control survives: no select, no write button, no cancel button
    assert edited['view'] is None
    assert edited['embed'] is None
    assert [key for key in edited if key not in ('content', 'embed', 'view')] == []
