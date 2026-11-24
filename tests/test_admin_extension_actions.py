"""The extension pair (``/extension enable`` / ``/extension disable``) as ACTIONS.

``enable_extension`` / ``disable_extension`` in ``plugins/admin/actions.py`` are the ONE call site of
``Server.enable_extension`` / ``Server.disable_extension``: the Discord ``ext`` group reaches them
through ``call_action`` and passes the config pair its modal / ``ConfigView`` collected, and the
console reaches the same functions. The trail is written by the action, once, for every outcome —
including each refusal.

Invariants: the CONFIG PAIR the caller passes is what the server receives (``None`` keeps the
in-memory values); a ``False`` return and an ``InstallException`` are TYPED REFUSALS, never a crash
and never a success; a ``disable`` the extension itself declines is reported as the extension's own
decision; an unknown server is refused like every sibling action's.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

import core.actions as actions_module
from core.actions import ActionContext, call_action, discover_actions, reset_in_flight
from core.extension import InstallException

REPO = Path(__file__).resolve().parents[1]
SERVER = "SRS-1"
EXTENSION = "SRS"

#: The ``{"Node": {...}, "Instance": {...}}`` pair ``Server.config_extension`` reads and writes.
PAIR = {"Node": {"key": "node-value"}, "Instance": {"enabled": True, "port": 5002}}


# ------------------------------------------------------------------------------- test doubles

class ExtensionServer:
    """A server double shaped where the two actions read it.

    ``enable_extension`` / ``disable_extension`` answer ``result`` and record the arguments they were
    handed, so a test can pin WHAT the action passed through; ``raises`` makes them raise instead.
    """

    def __init__(self, name: str = SERVER, *, result: bool = True, raises: Exception | None = None):
        self.name = name
        self.display_name = name
        self.result = result
        self.raises = raises
        self.calls: list[tuple] = []
        self.initialised = 0

    async def init_extensions(self) -> list[str]:
        """``/extension disable`` initialises the registry before it looks a name up."""
        self.initialised += 1
        return []

    async def enable_extension(self, name: str, config: dict | None = None) -> bool:
        self.calls.append(("enable", name, config))
        if self.raises is not None:
            raise self.raises
        return self.result

    async def disable_extension(self, name: str) -> bool:
        self.calls.append(("disable", name, None))
        if self.raises is not None:
            raise self.raises
        return self.result


class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry (not the no-op)."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def audit(self, event, server=None, **kwargs):
        self.calls.append((event, getattr(server, "name", None)))


def ctx_for(servers, *, actor: str = "probe") -> ActionContext:
    """The seam's context over a stub cluster — the shape the console builds (duck-typed)."""
    bus = types.SimpleNamespace(servers={server.name: server for server in servers})
    identity = types.SimpleNamespace(roles=(), scope=None, shown_name=actor, backend="local",
                                     subject=actor)
    return ActionContext.from_web(identity, None, bus)


@pytest.fixture(autouse=True)
def clean_action_state():
    """The registry and the in-flight guard are module state: reset them around every test."""
    reset_in_flight()
    sys.modules.pop("plugins.admin.actions", None)
    discover_actions(["admin"])
    try:
        yield
    finally:
        sys.modules.pop("plugins.admin.actions", None)
        reset_in_flight()


@pytest.fixture
def audit_bot(monkeypatch):
    """Make the seam's audit resolvable, so the entries are RECORDED and can be asserted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


def messages(bot: RecordingBot) -> list[str]:
    return [event for event, _server in bot.calls]


# ------------------------------------------------------------------------- 1. enable: the happy path

@pytest.mark.asyncio
async def test_enable_passes_the_config_pair_and_reports_success(audit_bot):
    """The pair the caller built is what the server receives, and the result names the extension."""
    server = ExtensionServer()
    result = await call_action("enable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION, config=PAIR)

    assert result.success is True
    assert server.calls == [("enable", EXTENSION, PAIR)], \
        "the config pair decides WHICH values the extension starts with"
    assert result.message == f"Extension '{EXTENSION}' enabled on server '{SERVER}'."
    assert getattr(result, "server_name", "") == SERVER
    assert messages(audit_bot) == [result.message], "one attempt, one trail entry"
    assert audit_bot.calls[0][1] == SERVER, "the trail entry carries the server it is about"


@pytest.mark.asyncio
async def test_enable_without_a_config_pair_hands_the_server_none(audit_bot):
    """``config=None`` is today's behaviour: the server's own in-memory values."""
    server = ExtensionServer()
    result = await call_action("enable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION)

    assert result.success is True
    assert server.calls == [("enable", EXTENSION, None)]


@pytest.mark.asyncio
async def test_enable_is_resolved_case_insensitively_like_every_sibling(audit_bot):
    """``resolve_server`` folds case, so the action reaches the server it was named with."""
    server = ExtensionServer()
    result = await call_action("enable_extension", ctx_for([server]), server_name="srs-1",
                               extension=EXTENSION, config=PAIR)

    assert result.success is True
    assert result.message == f"Extension '{EXTENSION}' enabled on server '{SERVER}'."


# ------------------------------------------------------------------- 2. enable: the two refusals

@pytest.mark.asyncio
async def test_enable_returning_false_is_a_refusal_naming_both_causes(audit_bot):
    """``False`` is a refusal, not a crash — and the text names what it can mean."""
    server = ExtensionServer(result=False)
    result = await call_action("enable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION, config=PAIR)

    assert result.success is False
    assert server.calls == [("enable", EXTENSION, PAIR)], "the call was made and it declined"
    assert f"Extension '{EXTENSION}'" in result.message and SERVER in result.message
    assert "was not found" in result.message, "the class-not-found cause is named"
    assert "install/prepare" in result.message, "the failed-install cause is named"
    assert messages(audit_bot) == [result.message], "a refusal is an attempt in the trail"


@pytest.mark.asyncio
async def test_enable_raising_install_exception_is_a_refusal_carrying_its_message(audit_bot):
    """``InstallException`` is mapped to a refusal — it never escapes the action."""
    server = ExtensionServer(raises=InstallException(f"{EXTENSION} is not installed."))
    result = await call_action("enable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION, config=PAIR)

    assert result.success is False
    assert f"{EXTENSION} is not installed." in result.message, "the exception's own text travels"
    assert f"Extension '{EXTENSION}'" in result.message and SERVER in result.message
    assert messages(audit_bot) == [result.message]


# ------------------------------------------------------------------------- 3. disable: both outcomes

@pytest.mark.asyncio
async def test_disable_reports_success(audit_bot):
    """The happy path: the server is told the name, and the message names it back."""
    server = ExtensionServer()
    result = await call_action("disable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION)

    assert result.success is True
    assert server.calls == [("disable", EXTENSION, None)]
    assert result.message == f"Extension '{EXTENSION}' disabled on server '{SERVER}'."
    assert messages(audit_bot) == [result.message]


@pytest.mark.asyncio
async def test_disable_of_a_name_that_is_not_loaded_is_a_refusal(audit_bot):
    """``Server.disable_extension`` raises for a name outside the loaded extensions."""
    server = ExtensionServer(raises=InstallException(f"Extension '{EXTENSION}' not found"))
    result = await call_action("disable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION)

    assert result.success is False
    assert f"Extension '{EXTENSION}' not found" in result.message
    assert messages(audit_bot) == [result.message]


@pytest.mark.asyncio
async def test_disable_declined_by_the_extension_does_not_blame_the_bot(audit_bot):
    """``False`` is the extension's OWN ``disable()`` declining (DKS while its server runs)."""
    server = ExtensionServer(result=False)
    result = await call_action("disable_extension", ctx_for([server]), server_name=SERVER,
                               extension=EXTENSION)

    assert result.success is False
    assert "the extension's own disable()" in result.message
    assert "still enabled" in result.message
    assert "bot" not in result.message.lower(), "the decline is never attributed to the bot"
    assert messages(audit_bot) == [result.message]


# ------------------------------------------------------------------------- 4. the unknown server

@pytest.mark.asyncio
async def test_both_actions_refuse_an_unknown_server_and_audit_it(audit_bot):
    """The sibling actions' one wording, and a refusal is still one attempt in the trail."""
    ctx = ctx_for([])
    for qualname in ("enable_extension", "disable_extension"):
        result = await call_action(qualname, ctx, server_name="NOPE-9", extension=EXTENSION)
        assert result.success is False
        assert result.message == "Server 'NOPE-9' not found."
    assert messages(audit_bot) == ["Server 'NOPE-9' not found.", "Server 'NOPE-9' not found."]


# ------------------------------------------------------- 5. the Discord commands delegate (the pin)

class _Message:
    """A Discord message double: the command's own reply object, and what it was edited with."""

    def __init__(self):
        self.edits: list[str | None] = []

    async def edit(self, content: str | None = None, **kwargs):
        self.edits.append(content)


class _Followup:
    def __init__(self):
        self.sent: list[str | None] = []
        self.last: _Message | None = None

    async def send(self, content: str | None = None, **kwargs):
        self.sent.append(content)
        self.last = _Message()
        return self.last


class _Response:
    def __init__(self):
        self.sent: list[str | None] = []

    async def send_message(self, content: str | None = None, **kwargs):
        self.sent.append(content)


class _Client:
    """``utils.get_ephemeral`` asks the bot for the interaction's server: none means ephemeral."""

    node = None

    def get_server(self, interaction):
        return None

    def get_admin_channel(self, server):
        return None


class _Interaction:
    def __init__(self):
        self.message = _Message()
        self.response = _Response()
        self.followup = _Followup()
        self.channel = None
        self.client = _Client()
        self.user = types.SimpleNamespace(display_name="probe", roles=())

    async def original_response(self):
        return self.message


class _Plugin:
    """The commands' ``self``: the pieces ``/extension enable`` and ``/extension disable`` touch."""

    def __init__(self, config=PAIR):
        self.config = config
        self.configured: tuple | None = None

    async def _configure_extension(self, interaction, server, extension):
        self.configured = (server.name, extension)
        return self.config


def _stub_call_action(monkeypatch, message: str = "the action's own sentence"):
    """Replace the seam in the COMMANDS module and record what the command handed it."""
    import plugins.admin.commands as commands

    calls: list[tuple] = []

    async def fake_call_action(qualname, ctx, /, **params):
        calls.append((qualname, params))
        return types.SimpleNamespace(success=True, message=message)

    monkeypatch.setattr(commands, "call_action", fake_call_action)
    return commands, calls


@pytest.mark.asyncio
async def test_the_enable_command_calls_the_action_with_the_modal_pair(monkeypatch):
    """``/extension enable`` keeps its modal step and hands the answer to the ACTION."""
    commands, calls = _stub_call_action(monkeypatch)
    server = ExtensionServer()
    plugin, interaction = _Plugin(), _Interaction()

    await commands.Admin.enable.callback(plugin, interaction, server, EXTENSION)

    assert plugin.configured == (SERVER, EXTENSION), "the modal step still runs, and it ran first"
    assert len(calls) == 1
    qualname, params = calls[0]
    assert qualname == "enable_extension"
    assert params["server_name"] == SERVER
    assert params["extension"] == EXTENSION
    assert params["config"] == PAIR, "the collected pair travels as the action's config"
    assert server.calls == [], "the command no longer calls Server.enable_extension itself"


@pytest.mark.asyncio
async def test_the_enable_command_renders_the_action_s_message(monkeypatch):
    """The reply is ``result.message`` — the action words the outcome, once."""
    commands, _calls = _stub_call_action(monkeypatch, message="Extension 'SRS' enabled.")
    plugin, interaction = _Plugin(), _Interaction()

    await commands.Admin.enable.callback(plugin, interaction, ExtensionServer(), EXTENSION)

    assert interaction.followup.sent == [f"Enabling extension {EXTENSION}..."], \
        "the progress line is still posted before the work"
    assert interaction.followup.last is not None
    assert interaction.followup.last.edits == ["Extension 'SRS' enabled."]


@pytest.mark.asyncio
async def test_the_disable_command_calls_the_action_and_passes_no_config(monkeypatch):
    """``/extension disable`` has no modal step: the action gets the server and the name only."""
    commands, calls = _stub_call_action(monkeypatch, message="Extension 'SRS' disabled.")
    server = ExtensionServer()
    plugin, interaction = _Plugin(), _Interaction()

    await commands.Admin.disable.callback(plugin, interaction, server, EXTENSION)

    assert len(calls) == 1
    qualname, params = calls[0]
    assert qualname == "disable_extension"
    assert params == {"server_name": SERVER, "extension": EXTENSION}
    assert server.calls == [], "the command no longer calls Server.disable_extension itself"
    assert server.initialised == 1, "the registry is initialised before the name is looked up"
    assert interaction.response.sent == [f"Disabling extension {EXTENSION}..."]
    assert interaction.message.edits == ["Extension 'SRS' disabled."], \
        "the reply is the action's own sentence"


# ------------------------------------------------------- 6. the source pins (one call site only)

def test_the_admin_plugin_calls_the_server_lifecycle_from_the_action_module_only():
    """``plugins/admin`` holds ONE call site of the two server methods: the actions module."""
    admin = REPO / "plugins" / "admin"
    callers = {path.name for path in admin.rglob("*.py")
               if re.search(r"\.(enable|disable)_extension\(", path.read_text(encoding="utf-8"))}
    assert callers == {"actions.py"}


def test_the_discord_commands_name_the_two_actions():
    """The commands reach the seam by qualname, and hold no direct server call."""
    source = (REPO / "plugins" / "admin" / "commands.py").read_text(encoding="utf-8")
    assert '"enable_extension"' in source and '"disable_extension"' in source
    assert "server.enable_extension" not in source
    assert "server.disable_extension" not in source


# --------------------------------------------------------------- 7. the registry is undisturbed

@pytest.mark.asyncio
async def test_the_sibling_actions_still_register_and_run(audit_bot):
    """CONTROL: this card adds two actions and disturbs no other one."""
    registry = discover_actions(["admin"])
    for qualname in ("restart_node", "shutdown_node", "upgrade_node", "take_node_offline",
                     "bring_node_online", "add_server", "delete_server", "update_dcs",
                     "enable_extension", "disable_extension"):
        assert qualname in registry, f"{qualname} must still be registered"

    result = await call_action("shutdown_node", ctx_for([]), node_name="NOPE-9")
    assert result.success is False
    assert result.message == "Node 'NOPE-9' is offline or unknown.", "a sibling still runs"
