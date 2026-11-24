"""Pins on the local-account management of ``/webadmin user`` and the shared web-console restart.

The account mutations are pure functions applied by ``configwrite.patch_config_file``, so they are
pinned without a bot, a network or Discord: each write is applied to a real file in ``tmp_path`` and
the raw text is read back to prove the comments and every key the write does not own survive.

The restart helper is pinned against a faked ``ServiceRegistry`` and a faked bot: the order stop →
start, the REST API reload only when that plugin is loaded, the refusal when ``session.secret`` is
unset (nothing is stopped), and a start that leaves the service down reported as a failure.
"""
from __future__ import annotations

import types

from pathlib import Path

import pytest

from core import DEFAULT_TAG
from core.utils import configwrite
from plugins.admin import webadmin as W

_SECRET = "s" * 40
_URL = "https://dcs.example.com"

#: a full document: an owned key, two comments, two accounts and a key the write does not own
_TWO_USERS = """\
DEFAULT:
  frontend: true
  port: 8443
  # keep this comment exactly as it is
  auth:
    public_base_url: https://dcs.example.com
    local:
      # the local accounts
      enabled: true
      users:
        - username: admin
          password_hash: pbkdf2_sha256$ADMIN
          roles: [Admin]
        - username: operator
          password_hash: pbkdf2_sha256$OPERATOR
          roles: [DCS Admin]
          scope:
            - Server 1
"""

#: the same document without ``auth.local.enabled``
_NO_ENABLED = _TWO_USERS.replace("      enabled: true\n", "")

#: a document with no ``auth.local`` block at all — the add has to create it enabled
_BARE_DOCUMENT = """\
DEFAULT:
  frontend: true
  port: 8443
"""

#: one Admin only — the account the last-Admin rule protects
_ONE_ADMIN = """\
DEFAULT:
  auth:
    local:
      enabled: true
      users:
        - username: admin
          password_hash: pbkdf2_sha256$ADMIN
          roles: [Admin]
"""

_KNOWN = frozenset({"Admin", "DCS Admin", "DCS", "GameMaster", "Alert"})


@pytest.fixture(autouse=True)
def _bot_argv(monkeypatch):
    """The schema validator's ``unique_port`` reads ``COMMAND_LINE_ARGS.node``."""
    import core.commandline as commandline
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS",
                        types.SimpleNamespace(node="master", config="config"))


def _write(tmp_path, text: str) -> Path:
    path = Path(W.config_path(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(path: Path) -> dict:
    return W.read_config(str(path.parent.parent))[DEFAULT_TAG]


def _users(path: Path) -> list:
    return _block(path)["auth"]["local"]["users"]


# ----------------------------------------------------------------------------------- the additions

def test_add_appends_one_entry_and_keeps_the_rest_verbatim(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    W.add_local_user(tmp_path, "newcomer", "correct horse battery", ["Admin"], known=_KNOWN)
    users = _users(path)
    assert [user["username"] for user in users] == ["admin", "operator", "newcomer"]
    assert users[2]["roles"] == ["Admin"]
    from services.webservice.auth.local import verify_password
    assert verify_password("correct horse battery", users[2]["password_hash"])
    raw = _raw(path)
    assert "# keep this comment exactly as it is" in raw
    assert "# the local accounts" in raw
    assert "port: 8443" in raw
    assert "pbkdf2_sha256$ADMIN" in raw and "pbkdf2_sha256$OPERATOR" in raw
    # the operator's scope list is untouched, in place and in order
    assert users[1]["scope"] == ["Server 1"]


def test_add_enables_the_local_backend_when_the_key_is_absent(tmp_path):
    path = _write(tmp_path, _NO_ENABLED)
    W.add_local_user(tmp_path, "newcomer", "correct horse battery", ["Admin"], known=_KNOWN)
    local = _block(path)["auth"]["local"]
    assert local["enabled"] is True
    assert [user["username"] for user in local["users"]] == ["admin", "operator", "newcomer"]


def test_a_duplicate_username_is_refused_and_writes_nothing(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.add_local_user(tmp_path, "admin", "correct horse battery", ["Admin"], known=_KNOWN)
    assert "already exists" in str(refusal.value)
    assert _raw(path) == _TWO_USERS


def test_an_unknown_role_is_refused_and_writes_nothing(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.add_local_user(tmp_path, "newcomer", "correct horse battery", ["Wizard"], known=_KNOWN)
    assert "Wizard" in str(refusal.value)
    assert "Known roles" in str(refusal.value)
    assert _raw(path) == _TWO_USERS


def test_a_short_password_is_refused_and_writes_nothing(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.add_local_user(tmp_path, "newcomer", "too-short", ["Admin"], known=_KNOWN)
    assert "at least 12" in str(refusal.value)
    assert _raw(path) == _TWO_USERS


def test_the_password_bounds_are_the_logins_own():
    assert "at least 12" in W.password_refusal("x" * 11)
    assert "at most 1024" in W.password_refusal("x" * 1025)
    assert W.password_refusal("x" * 12) is None
    assert W.password_refusal("x" * 1024) is None


def test_the_username_bounds_are_the_entrys_own():
    assert "required" in W.username_refusal("")
    assert "at most 64" in W.username_refusal("x" * 65)
    assert W.username_refusal("x" * 64) is None


# ------------------------------------------------------------------------------------ the removals

def test_delete_removes_exactly_the_named_entry(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    W.delete_local_user(tmp_path, "operator")
    users = _users(path)
    assert [user["username"] for user in users] == ["admin"]
    raw = _raw(path)
    assert "operator" not in raw
    assert "# keep this comment exactly as it is" in raw
    assert "pbkdf2_sha256$ADMIN" in raw


def test_deleting_the_last_admin_is_refused(tmp_path):
    path = _write(tmp_path, _ONE_ADMIN)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.delete_local_user(tmp_path, "admin")
    assert "last one holding Admin" in str(refusal.value)
    assert _raw(path) == _ONE_ADMIN


def test_deleting_an_unknown_name_is_refused(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.delete_local_user(tmp_path, "ghost")
    assert "reload and pick again" in str(refusal.value)
    assert _raw(path) == _TWO_USERS


def test_a_second_admin_may_be_removed(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    W.add_local_user(tmp_path, "second", "correct horse battery", ["Admin"], known=_KNOWN)
    W.delete_local_user(tmp_path, "admin")
    assert [user["username"] for user in _users(path)] == ["operator", "second"]


# ------------------------------------------------------------------------------------ the passwords

def test_password_replaces_that_entries_hash_only(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    W.set_local_password(tmp_path, "operator", "a brand new secret")
    users = _users(path)
    from services.webservice.auth.local import verify_password
    assert verify_password("a brand new secret", users[1]["password_hash"])
    # roles and scope are untouched, in place
    assert users[1]["roles"] == ["DCS Admin"]
    assert users[1]["scope"] == ["Server 1"]
    # the other account is untouched
    assert users[0]["password_hash"] == "pbkdf2_sha256$ADMIN"
    raw = _raw(path)
    assert "# keep this comment exactly as it is" in raw
    assert "# the local accounts" in raw


def test_a_short_new_password_is_refused(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused):
        W.set_local_password(tmp_path, "operator", "too-short")
    assert _raw(path) == _TWO_USERS


def test_a_password_for_a_vanished_name_is_refused(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    with pytest.raises(W.UserWriteRefused) as refusal:
        W.set_local_password(tmp_path, "ghost", "a brand new secret")
    assert "reload and pick again" in str(refusal.value)
    assert _raw(path) == _TWO_USERS


# ------------------------------------------------------------------------------------ the schema

@pytest.mark.parametrize("document, action", [
    pytest.param(_TWO_USERS, "add", id="full-add"),
    pytest.param(_TWO_USERS, "delete", id="full-delete"),
    pytest.param(_TWO_USERS, "password", id="full-password"),
    pytest.param(_BARE_DOCUMENT, "add", id="bare-add"),
])
def test_every_resulting_document_passes_the_real_schema(tmp_path, document, action):
    path = _write(tmp_path, document)
    if action == "add":
        W.add_local_user(tmp_path, "newcomer", "correct horse battery", ["Admin"], known=_KNOWN)
    elif action == "delete":
        W.delete_local_user(tmp_path, "operator")
    else:
        W.set_local_password(tmp_path, "operator", "a brand new secret")
    if document is _BARE_DOCUMENT:
        local = _block(path)["auth"]["local"]
        assert local["enabled"] is True, "a bare document's new auth.local must be enabled"
        assert [user["username"] for user in local["users"]] == ["newcomer"]
    configwrite.validate_against_schema(_raw(path), [W.CONFIG_SCHEMA], path=path)


def test_local_account_names_lists_the_file_order(tmp_path):
    _write(tmp_path, _TWO_USERS)
    assert W.local_account_names(tmp_path) == ["admin", "operator"]


# ------------------------------------------------------------------------------------ the restart

class _FakeRegistry:
    """Records the order of the cycle and answers ``get`` with a fake service."""

    def __init__(self, *, running_after_start=True):
        self.calls: list[str] = []
        self.running = False
        self.running_after_start = running_after_start

    async def stop_service(self, service):
        self.calls.append("stop")
        self.running = False

    async def start_service(self, service):
        self.calls.append("start")
        self.running = self.running_after_start

    def get(self, service):
        return types.SimpleNamespace(
            is_running=lambda: self.running,
            get_ports=lambda: {"WebService": types.SimpleNamespace(port=8443)},
        )


class _FakeBot:
    def __init__(self, plugins, *, reload_result=True, reload_error=None):
        self.plugins = plugins
        self.reloaded: list[str] = []
        self.reload_result = reload_result
        self.reload_error = reload_error

    async def reload(self, plugin):
        self.reloaded.append(plugin)
        if self.reload_error is not None:
            raise self.reload_error
        return self.reload_result


def _node(tmp_path, *, master=True):
    return types.SimpleNamespace(config_dir=str(tmp_path), master=master, all_nodes={})


def _config_with_secret(secret=_SECRET):
    return _TWO_USERS.replace("  # keep this comment exactly as it is\n",
                              "  session:\n    secret: " + secret + "\n"
                              "  # keep this comment exactly as it is\n")


@pytest.mark.asyncio
async def test_restart_stops_before_it_starts(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot(["restapi"]), _node(tmp_path))
    assert registry.calls == ["stop", "start"], "stop must happen before start"
    assert outcome.ok is True
    assert "8443" in outcome.message
    assert outcome.url == _URL


@pytest.mark.asyncio
async def test_restart_reloads_restapi_after_the_start_when_loaded(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    bot = _FakeBot(["restapi"])
    outcome = await W.restart_web_console(bot, _node(tmp_path))
    assert registry.calls == ["stop", "start"]
    assert bot.reloaded == ["restapi"], "the cached app's routes are re-attached after the start"
    assert "reloaded" in outcome.message


@pytest.mark.asyncio
async def test_restart_leaves_restapi_alone_when_not_loaded(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    bot = _FakeBot([])
    outcome = await W.restart_web_console(bot, _node(tmp_path))
    assert registry.calls == ["stop", "start"]
    assert bot.reloaded == []
    assert ("The REST API plugin is not loaded on this node, so no routes had to be re-attached."
            in outcome.message)


@pytest.mark.asyncio
async def test_a_reload_that_returns_false_is_reported_as_a_failed_reload(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    bot = _FakeBot(["restapi"], reload_result=False)
    outcome = await W.restart_web_console(bot, _node(tmp_path))
    assert registry.calls == ["stop", "start"]
    assert bot.reloaded == ["restapi"]
    assert "not loaded" not in outcome.message, "the plugin IS loaded, so that sentence is false"
    assert "routes are attached to the new" not in outcome.message, "the routes did not move"
    assert "reload it by hand" in outcome.message


@pytest.mark.asyncio
async def test_a_reload_that_raises_never_leaks_its_message(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    secret = "the reload's private payload"
    bot = _FakeBot(["restapi"], reload_error=RuntimeError(secret))
    outcome = await W.restart_web_console(bot, _node(tmp_path))
    assert registry.calls == ["stop", "start"]
    assert "not loaded" not in outcome.message, "the plugin IS loaded, so that sentence is false"
    assert "routes are attached to the new" not in outcome.message, "the routes did not move"
    assert "reload it by hand" in outcome.message
    assert secret not in outcome.message, "the exception's message must never reach the reply"


@pytest.mark.asyncio
async def test_a_service_cycle_failure_with_restapi_loaded_is_not_reported_as_not_loaded(
        tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())

    class _Boom(_FakeRegistry):
        async def stop_service(self, service):
            raise RuntimeError("boom")

    registry = _Boom()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot(["restapi"]), _node(tmp_path))
    assert outcome.ok is False
    assert "RuntimeError" in outcome.message
    assert "not loaded" not in outcome.message, "the plugin IS loaded; the service cycle failed"
    assert "reload it by hand" in outcome.message


@pytest.mark.asyncio
async def test_restart_without_a_session_secret_stops_nothing(tmp_path, monkeypatch):
    _write(tmp_path, _TWO_USERS)             # no session.secret
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot([]), _node(tmp_path))
    assert registry.calls == [], "a refusal must not touch the service"
    assert outcome.ok is False
    assert outcome.needs_confirm is True
    assert "session.secret" in outcome.message
    assert "signs everyone out" in outcome.message


@pytest.mark.asyncio
async def test_restart_with_an_explicit_confirm_proceeds(tmp_path, monkeypatch):
    _write(tmp_path, _TWO_USERS)
    registry = _FakeRegistry()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot([]), _node(tmp_path), confirm=True)
    assert registry.calls == ["stop", "start"]
    assert outcome.ok is True


@pytest.mark.asyncio
async def test_a_start_that_leaves_the_service_down_is_a_failure(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())
    registry = _FakeRegistry(running_after_start=False)
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot([]), _node(tmp_path))
    assert registry.calls == ["stop", "start"]
    assert outcome.ok is False
    assert "did not come back up" in outcome.message
    assert "down until the bot is restarted" in outcome.message


@pytest.mark.asyncio
async def test_a_service_that_raises_is_reported_as_a_failure(tmp_path, monkeypatch):
    _write(tmp_path, _config_with_secret())

    class _Boom(_FakeRegistry):
        async def stop_service(self, service):
            raise RuntimeError("boom")

    registry = _Boom()
    monkeypatch.setattr(W, "ServiceRegistry", registry)
    outcome = await W.restart_web_console(_FakeBot([]), _node(tmp_path))
    assert outcome.ok is False
    assert "RuntimeError" in outcome.message


# ------------------------------------------------------------------------------------- the forms

def test_the_add_form_carries_three_components_and_a_multi_role_select():
    config = W.add_user_config(["Admin", "DCS Admin"])
    assert list(config) == ["username", "password", "roles"]
    assert config["username"]["max_length"] == W.MAX_USERNAME
    assert config["password"]["min_length"] == W.MIN_PASSWORD
    assert config["password"]["max_length"] == W.MAX_PASSWORD
    assert config["roles"]["type"] is list
    assert config["roles"]["min_values"] == 1
    assert config["roles"]["max_values"] == 2
    assert config["roles"]["options"] == ["Admin", "DCS Admin"]


def test_the_password_form_asks_for_the_password_only():
    assert list(W.password_config()) == ["password"]


def test_the_modal_is_a_utils_config_modal():
    utils = W.utils
    modal = W.AddUserModal(object(), types.SimpleNamespace(config_dir="config"), ["Admin"])
    assert isinstance(modal, utils.ConfigModal)
    assert len(modal.children) == 3
    password_modal = W.SetPasswordModal(object(), types.SimpleNamespace(config_dir="config"), "admin")
    assert isinstance(password_modal, utils.ConfigModal)
    assert len(password_modal.children) == 1


# ------------------------------------------------------------------------------------- the group

def test_the_user_group_is_nested_under_webadmin():
    assert "user" in [command.name for command in W.webadmin_group.commands]
    group = W.webadmin_group.get_command("user")
    assert group is W.user_group
    assert [command.name for command in group.commands] == ["add", "delete", "password"]
    for command in group.commands:
        assert command.parent is group
        assert command.guild_only is True
        assert len(command.checks) == 2


def test_the_account_commands_take_an_autocompleted_username_argument():
    """The account is named in an autocompleted argument, not chosen from a select."""
    for name in ("delete", "password"):
        command = W.user_group.get_command(name)
        assert [parameter.name for parameter in command.parameters] == ["username"]
        parameter = command.parameters[0]
        assert parameter.required is True
        assert parameter.autocomplete is True
        assert command.callback.__discord_app_commands_param_autocomplete__ == {
            "username": W.local_account_autocomplete}
    assert "Account to remove" in str(
        W.user_group.get_command("delete").parameters[0].description)
    assert "Account whose password to change" in str(
        W.user_group.get_command("password").parameters[0].description)
    assert not hasattr(W, "UserPicker"), "the 25-option account select is gone"


def test_the_non_master_refusal_names_the_master():
    node = types.SimpleNamespace(master=False, all_nodes={
        "master": types.SimpleNamespace(master=True, name="DCSSB-01")})
    text = W.not_master_refusal(node, "/webadmin user add")
    assert "not the master" in text and "DCSSB-01" in text and "/webadmin user add" in text


def test_every_write_offers_the_shared_restart_button():
    view = W.RestartConsoleView(object(), object())
    assert [item.label for item in view.children] == ["Restart the web console"]


# -------------------------------------------------------------------------------------- the picker
# ``delete`` and ``password`` used to offer the accounts in a 25-option select, so an installation
# with more than 25 could never reach the rest. Both name the account in an autocompleted argument
# now: the list is filtered by what the operator types and is not bounded by 25.

_ACCOUNT_ADMIN_ROLES = {"Admin": ["Admin"]}


class _AccountResponse:
    """An interaction response for the account commands: records, answers nothing."""

    def __init__(self):
        self.sent: list[dict] = []
        self.modals: list = []
        self.deferred = False

    async def send_message(self, *args, **kwargs):
        sent = dict(kwargs)
        if args:
            sent["content"] = args[0]
        self.sent.append(sent)

    async def send(self, *args, **kwargs):
        await self.send_message(*args, **kwargs)

    async def send_modal(self, modal):
        self.modals.append(modal)

    async def defer(self, **kwargs):
        self.deferred = True


class _AccountInteraction:
    """An interaction for the account commands: a member holding *role_names* and the bot's node."""

    def __init__(self, tmp_path, *, role_names=("Admin",), master=True):
        self.user = types.SimpleNamespace(
            roles=[types.SimpleNamespace(id=index + 1, name=name)
                   for index, name in enumerate(role_names)])
        self.client = types.SimpleNamespace(node=_node(tmp_path, master=master),
                                            roles=_ACCOUNT_ADMIN_ROLES)
        self.response = _AccountResponse()
        self.followup = _AccountResponse()
        self.edited: list[dict] = []

    async def edit_original_response(self, **kwargs):
        self.edited.append(kwargs)


def _answer(monkeypatch, value):
    """Replace the shared yes/no question with a recorded one that answers *value*."""
    asked: list[dict] = []

    async def question(interaction, text, **kwargs):
        asked.append({"text": text, **kwargs})
        return value

    monkeypatch.setattr(W.utils, "yn_question", question)
    return asked


async def _choices(interaction, current=""):
    return await W.local_account_autocomplete(interaction, current)


def _names(choices) -> list[str]:
    return [choice.name for choice in choices]


def _thirty_users() -> str:
    entries = "".join(f"        - username: user{index:02d}\n"
                      f"          password_hash: pbkdf2_sha256$U{index:02d}\n"
                      f"          roles: [Admin]\n" for index in range(1, 31))
    return "DEFAULT:\n  auth:\n    local:\n      enabled: true\n      users:\n" + entries


@pytest.mark.asyncio
async def test_an_admin_on_the_master_gets_the_matching_names_sorted(tmp_path):
    _write(tmp_path, _TWO_USERS)
    interaction = _AccountInteraction(tmp_path)
    assert _names(await _choices(interaction)) == ["admin", "operator"]
    assert _names(await _choices(interaction, "ra")) == ["operator"]
    assert _names(await _choices(interaction, "ADM")) == ["admin"]


@pytest.mark.asyncio
async def test_a_caller_without_the_admin_role_learns_no_account_names(tmp_path):
    _write(tmp_path, _TWO_USERS)
    interaction = _AccountInteraction(tmp_path, role_names=("DCS",))
    assert await _choices(interaction) == []
    assert await _choices(interaction, "adm") == []


@pytest.mark.asyncio
async def test_a_node_that_is_not_the_master_offers_no_account_names(tmp_path):
    _write(tmp_path, _TWO_USERS)
    interaction = _AccountInteraction(tmp_path, master=False)
    assert await _choices(interaction) == []
    assert await _choices(interaction, "adm") == []


@pytest.mark.asyncio
async def test_a_matching_query_returns_25_and_a_later_account_is_still_offered(tmp_path):
    """The defect the change removes: the 30th account was unreachable behind the select."""
    _write(tmp_path, _thirty_users())
    interaction = _AccountInteraction(tmp_path)
    everything = await _choices(interaction)
    assert W.SELECT_LIMIT == 25
    assert len(everything) == W.SELECT_LIMIT
    assert "user30" not in _names(everything)
    named = await _choices(interaction, "user30")
    assert _names(named) == ["user30"]
    assert named[0].value == "user30"


@pytest.mark.asyncio
async def test_delete_refuses_an_unknown_name_and_writes_nothing(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    before = _raw(path)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("delete").callback(None, interaction, "ghost")
    assert len(interaction.response.sent) == 1
    assert "The local account 'ghost' is not in the configuration any more" in \
        interaction.response.sent[0]["content"]
    assert "view" not in interaction.response.sent[0]
    assert _raw(path) == before


@pytest.mark.asyncio
async def test_delete_with_a_known_name_asks_the_shared_yes_no_question(tmp_path, monkeypatch):
    path = _write(tmp_path, _TWO_USERS)
    before = _raw(path)
    asked = _answer(monkeypatch, False)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("delete").callback(None, interaction, "operator")
    assert asked == [{"text": "Delete the local account 'operator'? This cannot be undone.",
                      "ephemeral": True}]
    assert interaction.response.sent == [], "the question is the whole reply"
    assert interaction.followup.sent[0]["content"] == "Nothing was deleted."
    assert _raw(path) == before


@pytest.mark.asyncio
async def test_delete_removes_the_named_entry_once_the_question_is_answered(tmp_path, monkeypatch):
    path = _write(tmp_path, _TWO_USERS)
    _answer(monkeypatch, True)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("delete").callback(None, interaction, "operator")
    assert [user["username"] for user in _users(path)] == ["admin"]
    sent = interaction.followup.sent[0]
    assert sent["embed"].title == "Local account removed"
    assert isinstance(sent["view"], W.RestartConsoleView)
    assert sent["ephemeral"] is True


@pytest.mark.asyncio
async def test_a_timed_out_question_writes_nothing(tmp_path, monkeypatch):
    path = _write(tmp_path, _TWO_USERS)
    before = _raw(path)
    _answer(monkeypatch, None)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("delete").callback(None, interaction, "operator")
    assert interaction.followup.sent[0]["content"] == "Nothing was deleted."
    assert _raw(path) == before


@pytest.mark.asyncio
async def test_password_with_a_known_name_opens_the_password_form(tmp_path):
    _write(tmp_path, _TWO_USERS)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("password").callback(None, interaction, "operator")
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, W.SetPasswordModal)
    assert modal.username == "operator"


@pytest.mark.asyncio
async def test_password_refuses_an_unknown_name_and_writes_nothing(tmp_path):
    path = _write(tmp_path, _TWO_USERS)
    before = _raw(path)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("password").callback(None, interaction, "ghost")
    assert "The local account 'ghost' is not in the configuration any more" in \
        interaction.response.sent[0]["content"]
    assert interaction.response.modals == []
    assert _raw(path) == before


@pytest.mark.asyncio
async def test_the_last_admin_refusal_holds_through_the_new_signature(tmp_path, monkeypatch):
    path = _write(tmp_path, _ONE_ADMIN)
    before = _raw(path)
    _answer(monkeypatch, True)
    interaction = _AccountInteraction(tmp_path)
    await W.user_group.get_command("delete").callback(None, interaction, "admin")
    assert interaction.followup.sent[0]["content"].startswith(
        "The local account 'admin' is the last one holding Admin")
    assert _raw(path) == before
