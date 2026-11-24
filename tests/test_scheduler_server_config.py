"""The DCS-face config actions: ``get_server_config`` / ``set_server_config``.

Acceptance, one section per criterion: authorise inside the action (Admin only, no scope grant);
refuse while the server is up (the full state matrix); validate before write (key set, types, ranges,
``unique`` sequences, ``missionList`` read-only, the DCS-level cross-field ``port`` collision); apply
the ``servers.yaml`` override and report ``overridden``; secrets write-only (redacted in the read,
blank means unchanged, ``None`` clears); one writer at a time (per-server lock); old -> new for every
applied key; each write audited with actor, server and KEYS.

The actions are driven directly except once through ``call_action`` for the transport dispatch; the
in-flight guard would refuse a second concurrent call before the action, so the lock under test is
the action's own. Servers are doubles shaped where the action reads them."""
from __future__ import annotations

import asyncio
import inspect
import types
from pathlib import Path

import pytest
from ruamel.yaml import YAML

import core.actions as actions_module
import core.services.registry as registry_module
from core import Status
from core.actions import (ActionContext, call_action, discover_actions, reset_in_flight,
                          reset_write_locks)

SERVER = "SRS-1"
OTHER = "SRS-2"


# ---------------------------------------------------------------------------------- test doubles

class FakeSettings(dict):
    """The slice of ``SettingsDict`` the action uses: ``get``, ``__getitem__``, ``__setitem__``.

    A plain dict supplies all three, and — because the action REASSIGNS ``settings['advanced']`` as a
    whole (never a nested mutation) — a real dict is a faithful stand-in for the write path.
    """


class ConfigServer:
    """A server double: the attributes the action reads — name, status, settings, locals, node.

    ``node`` (with a ``config_dir``) and ``reload`` exist for the override sync, which mirrors the
    Discord Save's ``servers.yaml`` update: the double records every ``reload()`` and every event the
    lock test watches, in order.
    """

    def __init__(self, name, *, status=Status.SHUTDOWN, settings=None, locals_=None, node=None,
                 refuse_rename: str | None = None):
        self.name = name
        self.display_name = name
        self.status = status
        self.settings = FakeSettings(settings or {})
        self.locals = locals_ or {}
        self.node = node
        self.reload_calls = 0
        self.events: list[tuple] = []
 #: the rename calls the action made, ``(new_name, update_settings)`` — asserted by the tests.
        self.rename_calls: list[tuple] = []
 #: when set, ``rename`` raises it AND leaves ``name`` unchanged: the shape of a REFUSED rename
 #: (the real Proxy re-raises; the real ServerImpl swallows — the action treats either as refused).
        self.refuse_rename = refuse_rename

    async def rename(self, new_name, update_settings=False):
        """The bot's own rename, as the action calls it — recorded, and it may be refused.

        A refusal has two real shapes: ``ServerProxy.rename`` forwards and re-raises, and
        ``ServerImpl.rename`` swallows its own failure and returns with ``name`` unmoved. This double
        models the swallowed shape by leaving ``name`` alone when ``refuse_rename`` is set, so the
        action's "the name did not move → refused" branch is exercised too, not just the raise.
        """
        self.rename_calls.append((new_name, update_settings))
        self.events.append(("rename", new_name))
        if self.refuse_rename is not None:
            raise RuntimeError(self.refuse_rename)
        await asyncio.sleep(0)          # the window the lock must close around the rename
        self.name = new_name            # set LATE, like the real rename (after its async work)

    async def reload(self):
        """The reload the override sync performs — recorded, and a yield so the lock is observable."""
        self.reload_calls += 1
        self.events.append(("reload",))
        await asyncio.sleep(0)


def node_for(tmp_path) -> types.SimpleNamespace:
    """The slice of a Node the override sync reads: ``config_dir``."""
    return types.SimpleNamespace(config_dir=str(tmp_path))


def write_servers_yaml(tmp_path, mapping) -> Path:
    """Write a ``servers.yaml`` the way the operator's file is written (ruamel round-trip)."""
    path = Path(tmp_path) / "servers.yaml"
    with path.open("w", encoding="utf-8") as f:
        YAML().dump(mapping, f)
    return path


def read_servers_yaml(tmp_path) -> dict:
    """Read the ``servers.yaml`` back the way the code under test does."""
    return YAML().load((Path(tmp_path) / "servers.yaml").read_text(encoding="utf-8"))


class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry (not the no-op)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.kwargs: list[dict] = []

    async def audit(self, event, server=None, **kwargs):
        self.calls.append((event, getattr(server, "name", None)))
        self.kwargs.append(kwargs)


def ctx_for(servers, *, roles=("Admin",), scope=None, actor="probe") -> ActionContext:
    """The seam's context over a stub cluster — the shape the console builds (duck-typed)."""
    bus = types.SimpleNamespace(servers={server.name: server for server in servers})
    identity = types.SimpleNamespace(roles=set(roles), scope=scope, shown_name=actor,
                                     backend="local", subject=actor)
    return ActionContext.from_web(identity, None, bus)


# ---- the other three transports: one builder each, over the same stub cluster ----

@pytest.fixture
def registry(monkeypatch):
    """Stub the seam's service registry so ``from_plugin``/``from_service``/``from_interaction`` build.

    Unlike ``from_web`` (which takes the bus as an argument), those three read it from
    ``ServiceRegistry.get(ServiceBus)`` — process state a test has no cluster to fill. Only that ONE
    lookup is stubbed, so the real constructors run and the transport they stamp is exercised.
    """
    holder: dict[str, types.SimpleNamespace] = {}
    monkeypatch.setattr(registry_module.ServiceRegistry, "get",
                        classmethod(lambda cls, t: holder.get("bus")))

    def _install(servers) -> types.SimpleNamespace:
        holder["bus"] = types.SimpleNamespace(servers={server.name: server for server in servers})
        return holder["bus"]

    return _install


def plugin_ctx(servers, registry) -> ActionContext:
    """A REST-surface context — ``from_plugin`` (actor ``API``), transport ``plugin``."""
    registry(servers)
    return ActionContext.from_plugin(types.SimpleNamespace(node=None))


def service_ctx(servers, registry) -> ActionContext:
    """An MCP context — ``from_service`` (actor ``MCP``), transport ``service``."""
    registry(servers)
    return ActionContext.from_service(types.SimpleNamespace(node=None))


def discord_ctx(servers, registry, *, roles=("Admin",), display_name="member") -> ActionContext:
    """A Discord context — ``from_interaction``, whose roles ARE the member's real role names."""
    registry(servers)
    held = [types.SimpleNamespace(name=role) for role in roles]
    user = types.SimpleNamespace(display_name=display_name, roles=held)
    interaction = types.SimpleNamespace(client=types.SimpleNamespace(node=None), user=user)
    return ActionContext.from_interaction(interaction)


# -------------------------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def clean_state():
    """The write-lock registry and the in-flight guard are module state: reset them around every test."""
    reset_write_locks()
    reset_in_flight()
    try:
        yield
    finally:
        reset_write_locks()
        reset_in_flight()


@pytest.fixture
def audit_bot(monkeypatch):
    """Make the seam's audit resolvable, so the entries are RECORDED and can be asserted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


@pytest.fixture
def sa():
    """The scheduler plugin's actions, freshly discovered, with the process registry restored after.

    Discovery works by IMPORTING the module (the decorators run once per process), so the module is
    popped from the cache first — otherwise the second test would observe a registry an earlier test
    populated rather than a genuine registration.
    """
    import importlib
    import sys

    saved = dict(actions_module._ACTIONS)
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.scheduler.actions", None)
    discover_actions(["scheduler"])
    module = importlib.import_module("plugins.scheduler.actions")
    try:
        yield module
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.scheduler.actions", None)


# ---------------------------------------------------------------- 1. authorise inside the action

@pytest.mark.asyncio
async def test_a_dcs_admin_is_refused_with_a_typed_reason(sa):
    """The config capability is Admin-only with NO scope grant; a DCS Admin is NOT an Admin here."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server], roles=("DCS Admin",))

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False and result.success is False
    assert result.refused == sa.ADMIN_ONLY_SENTENCE
    assert result.applied == {} and result.skipped == {}
    assert server.settings["maxPlayers"] == 16, "a refused caller changes nothing"


@pytest.mark.asyncio
async def test_a_scoped_manager_may_write_allowed_keys_and_is_denied_the_port(sa, audit_bot):
    """A manager (a restricted scope, no Admin role) is ADMITTED — with the MANAGER DENY-LIST.

    The allowed keys land, and the ONE denied key (``port``) is refused for the WHOLE write with the
    declaration's sentence — nothing written, never a silent drop and never a 500.
    """
    scope = types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=False)
    server = ConfigServer(SERVER, settings={"maxPlayers": 16, "port": 10308})
    ctx = ctx_for([server], roles=(), scope=scope)

    allowed = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})
    assert allowed.ok is True
    assert server.settings["maxPlayers"] == 24

    denied = await sa.set_server_config(ctx, SERVER, {"port": 10309})
    assert denied.ok is False
    assert denied.refused == sa.MANAGER_DENIED["port"]
    assert denied.applied == {} and server.settings["port"] == 10308, "the denied key never lands"

 # a denied key bundled with an allowed one refuses the WHOLE write (nothing half-applied)
    bundled = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 32, "port": 10309})
    assert bundled.ok is False and bundled.refused == sa.MANAGER_DENIED["port"]
    assert server.settings["maxPlayers"] == 24 and server.settings["port"] == 10308


@pytest.mark.asyncio
async def test_a_scoped_manager_may_rename_their_server(sa, audit_bot):
    """A manager's rename path works end to end (through the bot's own rename)."""
    scope = types.SimpleNamespace(allows=lambda managed_by: True, unrestricted=False)
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "description": "old"})
    ctx = ctx_for([server], roles=(), scope=scope)

    result = await sa.set_server_config(ctx, SERVER, {"name": "SRS-1-RENAMED", "description": "new"})

    assert result.ok is True
    assert server.rename_calls == [("SRS-1-RENAMED", True)]
    assert result.applied["description"] == {"from": "old", "to": "new"}
 #...and the manager's write left its ONE audit entry, naming the WEB actor
    assert len(audit_bot.calls) == 1, audit_bot.calls
    assert audit_bot.kwargs[0].get("actor") == "[web:local/probe]", audit_bot.kwargs[0]


@pytest.mark.asyncio
async def test_an_admin_may_still_change_the_port(sa, audit_bot):
    """The deny-list is a MANAGER's rule, not a global one — an Admin keeps the port."""
    server = ConfigServer(SERVER, settings={"port": 10308})
    ctx = ctx_for([server], roles=("Admin",))

    result = await sa.set_server_config(ctx, SERVER, {"port": 10309})

    assert result.ok is True and server.settings["port"] == 10309


@pytest.mark.asyncio
async def test_the_manager_deny_list_is_one_declaration_read_by_the_action(sa):
    """The denied set is DECLARED once (``core.server_config``) and re-exported beside the action.

    The action reads the SAME mapping the console page does, so a future restriction is a one-line
    change and the two cannot disagree about what a manager may touch.
    """
    from core import server_config as declaration

    assert sa.MANAGER_DENIED is declaration.MANAGER_DENIED
    assert set(sa.MANAGER_DENIED_KEYS) == {"port"}
    assert "port" in sa.MANAGER_DENIED_KEYS
    assert sa.CHANNELS_ITEM in sa.MANAGER_DENIED, "the channels write is denied as a whole"


@pytest.mark.asyncio
async def test_an_admin_may_write(sa, audit_bot):
    """The positive control: the same request from an Admin applies."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server], roles=("Admin",))

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is True
    assert server.settings["maxPlayers"] == 24


@pytest.mark.asyncio
async def test_the_read_requires_admin_too(sa):
    """The READ (``servers.config``) is Admin-only as well: the action enforces it server-side."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server], roles=("DCS Admin",))

    result = await sa.get_server_config(ctx, SERVER)

    assert result.ok is False
    assert result.refused == sa.ADMIN_ONLY_SENTENCE


# ---------------------------------------------------------- 2. the state matrix (refuse while up)

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.SHUTDOWN, Status.STOPPED, Status.UNREGISTERED])
async def test_a_writable_state_allows_the_write(sa, audit_bot, status):
    """SHUTDOWN, STOPPED and UNREGISTERED are the writable states."""
    server = ConfigServer(SERVER, status=status, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is True, f"{status} must be writable"
    assert server.settings["maxPlayers"] == 24


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [Status.RUNNING, Status.PAUSED, Status.LOADING,
                                    Status.SHUTTING_DOWN])
async def test_a_live_state_is_refused_with_the_stop_first_sentence(sa, audit_bot, status):
    """A running/paused/loading/stopping server is refused, server-side, with the sentence."""
    server = ConfigServer(SERVER, status=status, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False
    assert sa.STOP_FIRST_SENTENCE in result.refused
    assert result.applied == {}
    assert server.settings["maxPlayers"] == 16, "nothing is written while the server is up"


# -------------------------------------------------------------------- 3. validation before write

@pytest.mark.asyncio
async def test_an_unknown_key_is_skipped_never_written(sa, audit_bot):
    """The key must be in the curated editable set."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"not_a_setting": 1})

    assert result.applied == {}
    assert "not_a_setting" in result.skipped
    assert "not an editable setting" in result.skipped["not_a_setting"]
    assert "not_a_setting" not in server.settings


@pytest.mark.asyncio
async def test_a_wrong_type_is_skipped(sa, audit_bot):
    """The type check is the guard the serializer is not — a str for an int is refused."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": "lots"})

    assert result.applied == {}
    assert "expects a whole number" in result.skipped["maxPlayers"]
    assert server.settings["maxPlayers"] == 16, "the bad value never lands"


@pytest.mark.asyncio
async def test_an_out_of_range_value_is_skipped(sa, audit_bot):
    """Ranges are enforced, where the field table has them."""
    server = ConfigServer(SERVER, settings={"port": 10308})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"port": 0})

    assert "port" in result.skipped
    assert "at least 1" in result.skipped["port"]
    assert server.settings["port"] == 10308


@pytest.mark.asyncio
async def test_a_port_collision_is_refused_naming_the_other_server(sa, audit_bot):
    """The DCS-level cross-field check — a port another server of the master holds."""
    one = ConfigServer(SERVER, settings={"port": 10308})
    two = ConfigServer(OTHER, settings={"port": 10309})
    ctx = ctx_for([one, two])

    result = await sa.set_server_config(ctx, SERVER, {"port": 10309})

    assert result.applied == {}
    assert OTHER in result.skipped["port"], "the colliding server is named"
    assert "already used" in result.skipped["port"]
    assert one.settings["port"] == 10308


@pytest.mark.asyncio
async def test_mission_list_is_read_only_and_never_written(sa, audit_bot):
    """``missionList`` is shown read-only and is never written."""
    server = ConfigServer(SERVER, settings={"missionList": ["a.miz"]})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"missionList": ["b.miz"]})

    assert result.applied == {}
    assert "read-only" in result.skipped["missionList"]
    assert server.settings["missionList"] == ["a.miz"]


def test_a_unique_sequence_with_duplicates_is_refused(sa):
    """A ``unique`` sequence is deduped/refused — exercised on the validator directly, because the
    one ``unique`` field of the DCS face (``missionList``) is read-only."""
    field = sa.DCSField("things", sa.KIND_SEQ, unique=True)
    assert sa.validate_value(field, ["a", "b"]) is None
    reason = sa.validate_value(field, ["a", "a"])
    assert reason is not None and "duplicate" in reason
    assert "expects a list" in sa.validate_value(field, "a")


# ------------------------------------- 4. the override: APPLIED and kept in sync (mirror Discord)

@pytest.mark.asyncio
async def test_a_pinned_key_applies_and_the_override_block_is_updated(sa, audit_bot, tmp_path):
    """Mirror the Discord Save — a pinned key IS applied to F1 AND the override is updated.

    The key lands in ``server.settings``, the ``servers.yaml`` entry's ``serverSettings`` block
    carries the new value, and the server reloaded.
    """
    write_servers_yaml(tmp_path, {"default": {},
                                  SERVER: {"serverSettings": {"maxPlayers": 32,
                                                              "advanced": {"allow_change_skin": False}}}})
    server = ConfigServer(SERVER,
                          settings={"maxPlayers": 16, "advanced": {"allow_change_skin": True}},
                          locals_={"serverSettings": {"maxPlayers": 32,
                                                      "advanced": {"allow_change_skin": False}}},
                          node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER,
                                        {"maxPlayers": 24, "advanced.allow_change_skin": False})

    assert result.applied == {"maxPlayers": {"from": 16, "to": 24},
                              "advanced.allow_change_skin": {"from": True, "to": False}}
    assert server.settings["maxPlayers"] == 24, "the pinned key IS written to F1"
    assert not any("pinned by servers.yaml" in reason for reason in result.skipped.values()), \
        "nothing claims the pinned key is uneditable"
    data = read_servers_yaml(tmp_path)
    assert data[SERVER]["serverSettings"]["maxPlayers"] == 24, "the override block is updated in place"
    assert data[SERVER]["serverSettings"]["advanced"]["allow_change_skin"] is False
    assert server.reload_calls == 1, "the reload Discord does so the value never reverts"


@pytest.mark.asyncio
async def test_the_override_sync_only_touches_the_keys_already_in_the_block(sa, audit_bot, tmp_path):
    """``utils.update_in_place`` — the block is updated, never grown: an unpinned key is not added."""
    write_servers_yaml(tmp_path, {SERVER: {"serverSettings": {"maxPlayers": 32}}})
    server = ConfigServer(SERVER, settings={"maxPlayers": 16, "description": "old"},
                          locals_={"serverSettings": {"maxPlayers": 32}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24, "description": "new"})

    block = read_servers_yaml(tmp_path)[SERVER]["serverSettings"]
    assert block["maxPlayers"] == 24
    assert "description" not in block, "a key the block does not pin is never added to it"


@pytest.mark.asyncio
async def test_a_server_without_an_override_block_never_writes_servers_yaml(sa, audit_bot, tmp_path):
    """The ``if server.locals.get('serverSettings')`` guard: no block → the file is not opened."""
    path = write_servers_yaml(tmp_path, {SERVER: {"instances": {}}})
    before = path.read_text(encoding="utf-8")
    before_mtime = path.stat().st_mtime_ns
    server = ConfigServer(SERVER, settings={"maxPlayers": 16}, locals_={}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.applied["maxPlayers"]["to"] == 24
    assert path.read_text(encoding="utf-8") == before, "servers.yaml is not rewritten"
    assert path.stat().st_mtime_ns == before_mtime
    assert server.reload_calls == 0, "no override → no reload"


@pytest.mark.asyncio
async def test_the_lock_covers_the_override_sync_and_the_reload(sa, monkeypatch, audit_bot, tmp_path):
    """The whole read-modify-write — INCLUDING the ``servers.yaml`` update and the reload —
    is one critical section; a concurrent writer cannot interleave.

    A yield is planted in ``_apply_setting`` (the producer) and the double's ``reload`` yields too,
    so without the lock the two writers' apply/reload events would interleave. The assertion is on
    the ORDER: apply, reload, apply, reload — one complete read-modify-write per writer.
    """
    write_servers_yaml(tmp_path, {SERVER: {"serverSettings": {"maxPlayers": 32}}})
    real_write = sa._write_setting

    async def slow_apply(server, key, value):
        server.events.append(("apply", value))
        await asyncio.sleep(0)          # the window the lock must close
        real_write(server.settings, key, value)

    monkeypatch.setattr(sa, "_apply_setting", slow_apply)
    server = ConfigServer(SERVER, settings={"maxPlayers": 16},
                          locals_={"serverSettings": {"maxPlayers": 32}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    first, second = await asyncio.gather(
        sa.set_server_config(ctx, SERVER, {"maxPlayers": 20}),
        sa.set_server_config(ctx, SERVER, {"maxPlayers": 24}),
    )

    assert first.applied["maxPlayers"] == {"from": 16, "to": 20}
    assert second.applied["maxPlayers"] == {"from": 20, "to": 24}, "the writes did not interleave"
    assert server.events == [("apply", 20), ("reload",), ("apply", 24), ("reload",)], \
        "the servers.yaml write and the reload sit inside the same critical section"
    assert read_servers_yaml(tmp_path)[SERVER]["serverSettings"]["maxPlayers"] == 24


@pytest.mark.asyncio
async def test_the_read_reports_overridden_as_information_not_a_prohibition(sa, tmp_path):
    """``overridden`` stays in the read so a page can say "a change updates both"."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16},
                          locals_={"serverSettings": {"maxPlayers": 32,
                                                      "advanced": {"allow_change_skin": False}}},
                          node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.get_server_config(ctx, SERVER)

    assert result.data["overridden"] == ["advanced.allow_change_skin", "maxPlayers"]
    assert "would revert" not in f"{result.message} {result.data}"


@pytest.mark.asyncio
async def test_the_read_marks_nothing_read_only_for_an_override(sa, tmp_path):
    """The positive control for the read: no override block → ``overridden`` is empty."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16}, locals_={}, node=node_for(tmp_path))

    result = await sa.get_server_config(ctx_for([server]), SERVER)

    assert result.data["overridden"] == []


# ------------------------------------------- 5. the modals' own bounds, mirrored

@pytest.mark.asyncio
async def test_the_description_bound_mirrors_the_modal(sa, audit_bot):
    """The modal slices the description at 2000 (``views.py:47``): 2000 granted, 2001 refused."""
    server = ConfigServer(SERVER, settings={"description": "x"})
    ctx = ctx_for([server])

    granted = await sa.set_server_config(ctx, SERVER, {"description": "d" * 2000})
    assert granted.applied["description"]["to"] == "d" * 2000

    refused = await sa.set_server_config(ctx, SERVER, {"description": "d" * 2001})
    assert refused.applied == {}
    assert "at most 2000 characters" in refused.skipped["description"]
    assert server.settings["description"] == "d" * 2000, "the oversized value never lands"


@pytest.mark.asyncio
async def test_the_password_bound_mirrors_the_modal(sa, audit_bot):
    """The modal slices the password at 80 (``views.py:57``): 80 granted, 81 refused — and redacted."""
    server = ConfigServer(SERVER, settings={"password": "old"})
    ctx = ctx_for([server])

    granted = await sa.set_server_config(ctx, SERVER, {"password": "p" * 80})
    assert granted.applied["password"] == {"from": sa.SET_SENTINEL, "to": sa.SET_SENTINEL}
    assert server.settings["password"] == "p" * 80

    refused = await sa.set_server_config(ctx, SERVER, {"password": "p" * 81})
    assert refused.applied == {}
    assert "at most 80 characters" in refused.skipped["password"]
    assert "p" * 81 not in f"{refused.message} {refused.skipped}"
    assert server.settings["password"] == "p" * 80


def test_the_editable_set_is_exactly_the_22_discord_keys(sa):
    """The editable set is FIXED — the 22 ``DCS_FIELDS``, the Discord set, and no more.

    ``missionList`` stays read-only, and ``current``/``listStartIndex`` belong to the mission-list
    feature — this action never writes them.
    """
    keys = {field.key for field in sa.DCS_FIELDS}
    assert len(sa.DCS_FIELDS) == 22
    assert len(keys) == 22, "no duplicate key in the curated table"
    for absent in ("missionList", "current", "listStartIndex"):
        assert absent not in keys
    assert {"name", "description", "password", "port", "maxPlayers",
            "advanced.resume_mode", "advanced.maxPing"} <= keys


# ------------------------------------------------------------------------------- 5. secret rules

@pytest.mark.asyncio
async def test_secrets_are_redacted_in_the_read(sa):
    """``password`` and the coalition hashes read set/not set — the VALUE is never returned."""
    server = ConfigServer(SERVER, settings={"password": "s3cr3t",
                                            "advanced": {"bluePasswordHash": "deadbeefhash",
                                                         "redPasswordHash": ""}})
    ctx = ctx_for([server])

    result = await sa.get_server_config(ctx, SERVER)

    assert result.data["values"]["password"] == sa.SET_SENTINEL
    assert result.data["values"]["advanced.bluePasswordHash"] == sa.SET_SENTINEL
    assert result.data["values"]["advanced.redPasswordHash"] == sa.UNSET_SENTINEL
    rendered = f"{result.message} {result.data} {result.to_dict()}"
    assert "s3cr3t" not in rendered and "deadbeefhash" not in rendered


@pytest.mark.asyncio
async def test_a_blank_secret_is_unchanged(sa, audit_bot):
    """A submitted blank does NOT wipe the password."""
    server = ConfigServer(SERVER, settings={"password": "s3cr3t"})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"password": ""})

    assert result.applied == {}
    assert "password" in result.skipped
    assert server.settings["password"] == "s3cr3t"


@pytest.mark.asyncio
async def test_none_is_the_explicit_clear_and_is_redacted(sa, audit_bot):
    """Clearing is its own explicit value (``None``); the applied pair carries no value."""
    server = ConfigServer(SERVER, settings={"password": "s3cr3t"})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"password": None})

    assert server.settings["password"] == ""
    assert result.applied["password"] == {"from": sa.SET_SENTINEL, "to": sa.UNSET_SENTINEL}
    assert "s3cr3t" not in f"{result.message} {result.applied} {result.skipped}"


@pytest.mark.asyncio
async def test_a_new_secret_is_written_but_never_echoed(sa, audit_bot):
    """The write really lands the value; the RESULT carries the sentinel, never the value."""
    server = ConfigServer(SERVER, settings={"password": "old"})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"password": "brandnew"})

    assert server.settings["password"] == "brandnew"
    assert result.applied["password"] == {"from": sa.SET_SENTINEL, "to": sa.SET_SENTINEL}
    assert "brandnew" not in f"{result.message} {result.applied} {result.skipped}"


# ------------------------------------------------------------------- 7. old -> new for a revert

@pytest.mark.asyncio
async def test_applied_carries_old_to_new_for_every_written_key(sa, audit_bot):
    """The console offers a revert by re-submitting these old values through the same action."""
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16,
                                            "advanced": {"maxPing": 200}})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER,
                                        {"name": "New", "maxPlayers": 24, "advanced.maxPing": 0})

    assert result.applied == {
        "name": {"from": "SRS-1", "to": "New"},
        "maxPlayers": {"from": 16, "to": 24},
        "advanced.maxPing": {"from": 200, "to": 0},
    }


@pytest.mark.asyncio
async def test_a_successful_write_really_lands_in_the_settings(sa, audit_bot):
    """The value reaches ``server.settings`` — top-level and nested ``advanced`` alike."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16, "advanced": {"maxPing": 200}})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER,
                                        {"maxPlayers": 24, "advanced.allow_change_skin": False})

    assert result.ok is True
    assert server.settings["maxPlayers"] == 24
    assert server.settings["advanced"]["allow_change_skin"] is False
    assert server.settings["advanced"]["maxPing"] == 200, "the other advanced keys survive"


# ------------------------------------------------------------------------ 6. one writer at a time

@pytest.mark.asyncio
async def test_two_concurrent_writes_serialise_on_the_per_server_lock(sa, monkeypatch, audit_bot):
    """The read-modify-write is atomic — the second write sees the FIRST write's value.

    A yield point is planted between the read and the write (``_apply_setting``), so without the lock
    both writers would read the same old value. The assertion is on the ORDER: the second ``from`` is
    the first ``to``.
    """
    real_write = sa._write_setting

    async def slow_apply(server, key, value):
        await asyncio.sleep(0)          # the window the lock must close
        real_write(server.settings, key, value)

    monkeypatch.setattr(sa, "_apply_setting", slow_apply)
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    first, second = await asyncio.gather(
        sa.set_server_config(ctx, SERVER, {"maxPlayers": 20}),
        sa.set_server_config(ctx, SERVER, {"maxPlayers": 24}),
    )

    assert first.applied["maxPlayers"] == {"from": 16, "to": 20}
    assert second.applied["maxPlayers"] == {"from": 20, "to": 24}, "the writes did not interleave"
    assert server.settings["maxPlayers"] == 24


# ------------------------------------------------------------------------------- 8. the audit

@pytest.mark.asyncio
async def test_the_write_is_audited_with_the_actor_the_server_and_the_keys(sa, audit_bot):
    """One entry per write, naming the KEYS — and never a value."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16, "password": "s3cr3t"})
    ctx = ctx_for([server], actor="probe")

    await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24, "password": "brandnew"})

    assert len(audit_bot.calls) == 1
    event, audited_server = audit_bot.calls[0]
    assert audit_bot.kwargs[0].get("actor") == "[web:local/probe]", audit_bot.kwargs[0]
    assert audited_server == SERVER
    assert "maxPlayers" in event and "password" in event
    assert "s3cr3t" not in event and "brandnew" not in event


@pytest.mark.asyncio
async def test_a_refusal_is_audited_too(sa, audit_bot):
    """One entry per ATTEMPT — a refused caller still leaves a trail."""
    server = ConfigServer(SERVER, status=Status.RUNNING, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert len(audit_bot.calls) == 1
    assert audit_bot.calls[0][1] == SERVER


# --------------------------------------------------------------------- the read's own shape

@pytest.mark.asyncio
async def test_the_read_returns_status_editable_values_and_a_typed_unknown(sa):
    """The read returns the curated surface; an unknown name is the seam's own refusal."""
    server = ConfigServer(SERVER, status=Status.STOPPED, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    ok = await sa.get_server_config(ctx, SERVER)
    assert ok.ok is True
    assert ok.data["status"] == "Stopped"
    assert "maxPlayers" in ok.data["editable"]
    assert "password" in ok.data["editable"]
    assert ok.data["values"]["maxPlayers"] == 16

    missing = await sa.get_server_config(ctx, "NOPE-9")
    assert missing.ok is False and missing.refused == "Server 'NOPE-9' not found."


# ------------------------------------------------------- registration and transport dispatch

@pytest.mark.asyncio
async def test_both_actions_are_registered_and_dispatch_through_call_action(sa):
    """The MCP/REST/console seam must find them: registration is the one declaration every transport reads."""
    from core.actions import action_registry

    registry = action_registry()
    assert "get_server_config" in registry and "set_server_config" in registry

    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server])
    result = await call_action("set_server_config", ctx, server_name=SERVER, values={"maxPlayers": 24})
    assert result.success is True
    assert server.settings["maxPlayers"] == 24


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the non-dict payload guard, the per-transport authorisation rule, and the follow-ups
# ═════════════════════════════════════════════════════════════════════════════════════════════════

# -----------------------------------------------------------------------------------------------
# a non-mapping ``values`` is a TYPED refusal, never an ``AttributeError``
# -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [["maxPlayers"], "maxPlayers=24", 42], ids=["list", "str", "int"])
async def test_a_non_mapping_values_payload_is_a_typed_refusal(sa, audit_bot, payload):
    """A list/str/int ``values`` gets the SAME typed skip shape an unknown key does — no exception.

    The action refuses the whole payload as a typed skip, writes nothing, and still leaves its one
    audit entry.
    """
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, payload)

    assert result.ok is False and result.success is False
    assert result.refused is None, "the skip shape is the one an unknown key uses, not the whole-action one"
    assert result.applied == {}, "nothing is written"
    assert "values" in result.skipped and "must be a mapping" in result.skipped["values"]
    assert server.settings["maxPlayers"] == 16, "the settings are untouched"
    assert len(audit_bot.calls) == 1, "the attempt is still trailed once"


@pytest.mark.asyncio
async def test_a_refused_non_mapping_payload_never_reads_the_settings(sa, audit_bot):
    """The guard runs before the write loop: a non-mapping payload is refused without touching the map.

    The three refusal arms (not Admin, server up, bad payload) all return before ``server.settings``
    is ever read for a value — proved here by a settings object whose ``get`` raises.
    """
    class ExplodingSettings(FakeSettings):
        def get(self, key, default=None):
            raise AssertionError("the settings were read for a refused payload")

    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    server.settings = ExplodingSettings()
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, ["maxPlayers"])

    assert result.ok is False and "must be a mapping" in result.skipped["values"]


# -----------------------------------------------------------------------------------------------
# authorisation across transports
# -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_plugin_rest_transport_is_allowed_to_read_and_write(sa, registry, audit_bot):
    """``from_plugin`` (REST / installed plugins) is ALLOWED: its own scheme has already authenticated."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = plugin_ctx([server], registry)
    assert ctx.transport == sa.TRANSPORT_PLUGIN

    read = await sa.get_server_config(ctx, SERVER)
    assert read.ok is True and read.data["values"]["maxPlayers"] == 16, "REST reaches the read too"

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is True
    assert server.settings["maxPlayers"] == 24
    assert audit_bot.kwargs[-1].get("actor") == "API", "the automated write is attributed to the API actor"


@pytest.mark.asyncio
async def test_the_service_mcp_transport_is_refused_with_a_typed_reason(sa, registry, audit_bot):
    """``from_service`` (MCP) is REFUSED: no service authentication exists yet to attribute a caller."""
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = service_ctx([server], registry)
    assert ctx.transport == sa.TRANSPORT_SERVICE

    read = await sa.get_server_config(ctx, SERVER)
    assert read.ok is False and read.refused == sa.SERVICE_REFUSAL
    assert read.data == {}, "a refused read returns no configuration"

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False
    assert result.refused == sa.SERVICE_REFUSAL
    assert result.applied == {} and server.settings["maxPlayers"] == 16, "no write on a refused call"
    assert "no authentication yet" in sa.SERVICE_REFUSAL and "cannot attribute" in sa.SERVICE_REFUSAL


@pytest.mark.asyncio
async def test_a_discord_admin_may_write_and_a_plain_member_may_not(sa, registry, audit_bot):
    """Discord keeps its door: a member holding the Admin role passes, one without is refused."""
    admin = ConfigServer(SERVER, settings={"maxPlayers": 16})
    allowed = await sa.set_server_config(discord_ctx([admin], registry, roles=("Admin",)),
                                         SERVER, {"maxPlayers": 24})
    assert allowed.ok is True and admin.settings["maxPlayers"] == 24

    member = ConfigServer(OTHER, settings={"maxPlayers": 16})
    refused = await sa.set_server_config(discord_ctx([member], registry, roles=("DCS Admin",)),
                                         OTHER, {"maxPlayers": 24})
    assert refused.ok is False and refused.refused == sa.ADMIN_ONLY_SENTENCE
    assert member.settings["maxPlayers"] == 16


@pytest.mark.asyncio
async def test_the_rule_is_keyed_on_the_transport_not_the_actor(sa, registry, audit_bot):
    """A Discord member whose display name reads ``API`` is NOT treated as the plugin transport.

    This is why the rule reads ``ctx.transport`` and not the audit actor (``from_plugin`` stamps the
    actor ``"API"``): keying it on the actor would let this member borrow the REST allowance.
    """
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = discord_ctx([server], registry, roles=(), display_name="API")
    assert ctx.transport == actions_module.TRANSPORT_DISCORD and ctx.audit_actor == "API"

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False and result.refused == sa.ADMIN_ONLY_SENTENCE
    assert server.settings["maxPlayers"] == 16


@pytest.mark.asyncio
async def test_the_plugin_transport_can_never_be_manager_shaped(sa, registry, audit_bot):
    """``from_plugin`` carries NO scope, so a REST caller can never be a MANAGER — the deny-list never
    applies to the plugin transport, and no second declaration is added on that path.

    The manager deny-list is the MANAGER's rule: it is reached only through ``_manages_only``, which
    reads the context's restricted scope. ``from_plugin`` stamps the transport and the audit actor and
    nothing else — there is no parameter that could put a manager's scope onto a plugin context — so a
    plugin context is structurally incapable of being manager-shaped. The ONE live ``from_plugin``
    caller (``plugins/restapi/commands.py::mission_upload``) reaches ``upload_mission``, a mission-FILE
    write that touches no config key and no channels item, so it cannot reach a ``MANAGER_DENIED``
    write even in principle.

    Pinned here so a future transport cannot quietly make the REST surface manager-shaped (which would
    silently start denying it keys an Admin may write): the constructor admits a plugin caller as the
    Admin-equivalent service identity its own scheme authenticated.
    """
 # the structural gate: no constructor parameter can carry a manager's scope onto a plugin context
    params = inspect.signature(ActionContext.from_plugin).parameters
    assert "scope" not in params, "from_plugin must not accept a scope — it can never be a manager"

    server = ConfigServer(SERVER, settings={"maxPlayers": 16, "port": 10308})
    ctx = plugin_ctx([server], registry)
    assert ctx.transport == sa.TRANSPORT_PLUGIN
    assert ctx.manages_only is False, "a plugin context is never managed-only"
    assert ctx.roles == frozenset(), "and it carries no roles"

 # the plugin caller is the authenticated service identity: the deny-list (keyed on manages_only)
 # does not and cannot apply to it, so even a denied KEY is written as an Admin's would be.
    result = await sa.set_server_config(ctx, SERVER, {"port": 10309})
    assert result.ok is True and server.settings["port"] == 10309


# -----------------------------------------------------------------------------------------------
# the follow-ups
# -----------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_name_with_control_characters_is_skipped(sa, audit_bot):
    """NUL/newline in a str field is refused before the serializer writes it."""
    server = ConfigServer(SERVER, settings={"name": "SRS-1"})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"name": "x\ny\x00z"})

    assert result.applied == {}
    assert "control characters" in result.skipped["name"]
    assert server.settings["name"] == "SRS-1"


@pytest.mark.asyncio
async def test_a_whitespace_only_secret_is_unchanged_and_reads_as_unset(sa, audit_bot):
    """A space-only secret is neither applied as a password nor reported as "set"."""
    server = ConfigServer(SERVER, settings={"password": "s3cr3t"})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"password": "   "})

    assert result.applied == {}
    assert "password" in result.skipped and "unchanged" in result.skipped["password"]
    assert server.settings["password"] == "s3cr3t"

    whitespace = ConfigServer(OTHER, settings={"password": "   "})
    read = await sa.get_server_config(ctx_for([whitespace]), OTHER)
    assert read.data["values"]["password"] == sa.UNSET_SENTINEL


def test_the_action_has_no_confirmed_parameter(sa):
    """``confirmed`` was accepted-and-ignored; it is removed, not left as a decoy.

    The parameter is gone from the signature AND the docstring says why (no behaviour was ever
    designed for it; the console's confirm step belongs to its own route).
    """
    parameters = inspect.signature(sa.set_server_config).parameters
    assert "confirmed" not in parameters
    assert "confirmed" in sa.set_server_config.__doc__ and "NO" in sa.set_server_config.__doc__


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the name goes through the bot's own rename
# ═════════════════════════════════════════════════════════════════════════════════════════════════

def nameless_transport_ctx(servers, transport: str = ""):
    """A context NOT built by a ``from_*`` builder — its transport is '' or an unknown label.

    ``ActionContext``'s ``_transport`` defaults to ``""``, the value a hand-built context carries; a
    non-empty but unknown label models a future/foreign transport this rule has no branch for. Both
    must fail CLOSED: the plugin allowance keys on the EXACT ``plugin`` label, never on absence.
    """
    bus = types.SimpleNamespace(servers={server.name: server for server in servers})
    ctx = ActionContext(node=None, bus=bus)
    ctx._transport = transport
    return ctx


@pytest.mark.asyncio
async def test_the_name_goes_through_the_bots_rename(sa, audit_bot):
    """A differing ``name`` is applied by ``Server.rename``, not by a bare settings write.

    The rename is CALLED (``rename(new_name=…, update_settings=True)``), the object is renamed, the old
    key leaves the master's mapping and the new one is present.
    """
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"name": "New-Name", "maxPlayers": 24})

    assert server.rename_calls == [("New-Name", True)], "the rename went through rename(), with the settings"
    assert server.name == "New-Name", "the Server object is renamed"
    assert result.applied["name"] == {"from": "SRS-1", "to": "New-Name"}
    assert "New-Name" in ctx.servers and ctx.servers["New-Name"] is server, "the master's mapping re-keyed"
    assert SERVER not in ctx.servers, "the old key left the master's mapping"
    assert result.applied["maxPlayers"] == {"from": 16, "to": 24}, "the remaining keys are still written"


@pytest.mark.asyncio
async def test_a_same_name_is_not_renamed(sa, audit_bot):
    """The positive control: submitting the name the server already has calls no rename at all."""
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16})
    ctx = ctx_for([server])

    await sa.set_server_config(ctx, SERVER, {"name": "SRS-1", "maxPlayers": 24})

    assert server.rename_calls == [], "a same-name submission is not a rename"
    assert server.settings["maxPlayers"] == 24


@pytest.mark.asyncio
async def test_a_refused_rename_leaves_settings_and_servers_yaml_untouched(sa, audit_bot, tmp_path):
    """A rename the bot refuses is a WHOLE-ACTION typed refusal — nothing written, no half state.

    ``ServerProxy.rename`` re-raises on failure; a refusal must surface as a typed refusal with the
    settings AND the ``servers.yaml`` override block left exactly as they were (the sync is never
    reached, because the rename is the first thing inside the lock).
    """
    path = write_servers_yaml(tmp_path, {SERVER: {"serverSettings": {"name": "SRS-1",
                                                                    "maxPlayers": 32}}})
    before = path.read_text(encoding="utf-8")
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16},
                          locals_={"serverSettings": {"name": "SRS-1", "maxPlayers": 32}},
                          node=node_for(tmp_path), refuse_rename="name already exists")
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"name": "Duplicate", "maxPlayers": 24})

    assert result.ok is False and result.refused, "a refused rename is a typed whole-action refusal"
    assert result.applied == {} and result.skipped == {}
    assert server.settings["name"] == "SRS-1", "the name is not half-written"
    assert server.settings["maxPlayers"] == 16, "no remaining key is written either"
    assert path.read_text(encoding="utf-8") == before, "servers.yaml is untouched"
    assert server.reload_calls == 0, "no override sync, no reload"
    assert SERVER in ctx.servers and "Duplicate" not in ctx.servers, "the mapping is not re-keyed"


@pytest.mark.asyncio
async def test_a_swallowed_rename_refusal_is_caught_too(sa, audit_bot):
    """``ServerImpl.rename`` never raises — it logs and returns with ``name`` unmoved. That is caught.

    The action treats "the rename neither raised nor moved the name" as a refusal, so the real
    swallowing implementation cannot slip a refused rename through as if it had landed.
    """
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16})

    async def swallowing_rename(new_name, update_settings=False):
        server.rename_calls.append((new_name, update_settings))
 # a swallowed failure: returns normally, name stays old (serverimpl.py:588-590)

    server.rename = swallowing_rename
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"name": "Duplicate", "maxPlayers": 24})

    assert result.ok is False and "refused the rename" in result.refused
    assert result.applied == {}
    assert server.settings["name"] == "SRS-1" and server.settings["maxPlayers"] == 16


@pytest.mark.asyncio
async def test_the_rename_is_inside_the_write_lock(sa, monkeypatch, audit_bot):
    """The rename is inside the SAME critical section as the settings write.

    A yield is planted in ``rename`` and in ``_apply_setting``; two concurrent writers (one renaming,
    one not) run against the same server. If the lock did not cover the rename, the second writer's
    apply would interleave before the first writer's apply; the assertion is on the ORDER — one
    complete rename-then-apply per writer, the renamer first.
    """
    real_write = sa._write_setting

    async def slow_apply(server, key, value):
        server.events.append(("apply", value))
        await asyncio.sleep(0)          # the window the lock must close
        real_write(server.settings, key, value)

    monkeypatch.setattr(sa, "_apply_setting", slow_apply)
    server = ConfigServer(SERVER, settings={"name": "SRS-1", "maxPlayers": 16})
    ctx = ctx_for([server])

    first, second = await asyncio.gather(
        sa.set_server_config(ctx, SERVER, {"name": "New-Name", "maxPlayers": 20}),
        sa.set_server_config(ctx, SERVER, {"maxPlayers": 24}),
    )

    assert first.applied["name"] == {"from": "SRS-1", "to": "New-Name"}
    assert second.applied["maxPlayers"] == {"from": 20, "to": 24}, "the writes did not interleave"
    assert server.events == [("rename", "New-Name"), ("apply", 20), ("apply", 24)], \
        "the rename sits inside the same critical section as the settings write"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# the fail-closed transport rule
# ═════════════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_an_unnamed_transport_is_refused_on_the_read_and_the_write(sa, audit_bot):
    """A context whose transport nothing named (``""``) takes the ADMIN branch — never the plugin one.

    Both the read and the write are refused with the typed Admin-only reason, and nothing is written.
    """
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = nameless_transport_ctx([server])
    assert ctx.transport == ""

    read = await sa.get_server_config(ctx, SERVER)
    assert read.ok is False and read.refused == sa.ADMIN_ONLY_SENTENCE
    assert read.data == {}

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False and result.refused == sa.ADMIN_ONLY_SENTENCE
    assert result.applied == {} and server.settings["maxPlayers"] == 16, "nothing is written"
    assert len(audit_bot.calls) >= 1, "the attempt is still trailed"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["carrier-pigeon", "PLUGIN", "REST", "rest", "plugin "])
async def test_an_unknown_transport_is_refused_on_the_read_and_the_write(sa, audit_bot, transport):
    """An unknown transport label fails CLOSED: only the EXACT ``plugin`` label gets the REST allowance.

    Covers a hand-built label, a wrong case, and a near-miss (``plugin `` with a trailing space), so
    the rule cannot be tricked by a transport that merely LOOKS like the plugin one.
    """
    server = ConfigServer(SERVER, settings={"maxPlayers": 16})
    ctx = nameless_transport_ctx([server], transport=transport)
    assert ctx.transport == transport

    read = await sa.get_server_config(ctx, SERVER)
    assert read.ok is False and read.refused == sa.ADMIN_ONLY_SENTENCE

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is False and result.refused == sa.ADMIN_ONLY_SENTENCE
    assert result.applied == {} and server.settings["maxPlayers"] == 16


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# a rename must not be allowed to steal or destroy another server
# ═════════════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_a_colliding_name_is_refused_before_the_rename(sa, audit_bot, tmp_path):
    """A name another server already carries is refused as a WHOLE-ACTION typed refusal.

    The refusal names the colliding server, the rename is never called, and NOTHING is written — the
    settings, ``servers.yaml`` and the mapping are intact.
    """
    path = write_servers_yaml(tmp_path, {SERVER: {"serverSettings": {"maxPlayers": 32}}})
    before = path.read_text(encoding="utf-8")
    one = ConfigServer(SERVER, settings={"name": SERVER, "maxPlayers": 16},
                       locals_={"serverSettings": {"maxPlayers": 32}}, node=node_for(tmp_path))
    two = ConfigServer(OTHER, settings={"name": OTHER})
    ctx = ctx_for([one, two])

    result = await sa.set_server_config(ctx, SERVER, {"name": OTHER, "maxPlayers": 24})

    assert result.ok is False and result.refused, "a colliding name is a whole-action refusal"
    assert OTHER in result.refused and "already used" in result.refused, "the colliding server is named"
    assert result.applied == {} and result.skipped == {}
    assert one.rename_calls == [], "the bot's rename is never reached for a colliding name"
    assert one.settings["name"] == SERVER and one.settings["maxPlayers"] == 16, "nothing is half-written"
    assert path.read_text(encoding="utf-8") == before, "servers.yaml is untouched"
    assert one.reload_calls == 0, "no override sync, no reload"
    assert ctx.servers[SERVER] is one and ctx.servers[OTHER] is two, "the other server keeps its entry"


@pytest.mark.asyncio
async def test_a_collision_with_a_server_the_caller_cannot_see_is_still_refused(sa, monkeypatch,
                                                                               audit_bot):
    """The console's ``ctx.servers`` is a SCOPED view — an invisible collision is still one.

    The caller may only see ``SERVER`` (its bus holds one server), while the PROCESS's own registry
    holds the colliding ``OTHER``. The action reads BOTH, so the rename cannot steal the entry of a
    server the caller was never shown (``ServiceBus.rename_server`` would clobber it regardless).
    """
    hidden = ConfigServer(OTHER, settings={"name": OTHER})
    live = types.SimpleNamespace(servers={OTHER: hidden})
    monkeypatch.setattr(registry_module.ServiceRegistry, "get",
                        classmethod(lambda cls, t: live))
    visible = ConfigServer(SERVER, settings={"name": SERVER, "maxPlayers": 16})
    ctx = ctx_for([visible])
    assert OTHER not in ctx.servers, "the colliding server is outside the caller's view"

    result = await sa.set_server_config(ctx, SERVER, {"name": OTHER, "maxPlayers": 24})

    assert result.ok is False and result.refused
    assert OTHER in result.refused and "already used" in result.refused
    assert visible.rename_calls == []
    assert visible.settings["maxPlayers"] == 16


@pytest.mark.asyncio
async def test_the_reserved_sentinel_name_is_refused(sa, audit_bot):
    """The sentinel ``n/a`` is the code's "unnamed" marker — a server must not be renamed to it.

    It is a typed refusal before anything is written, and the rename is never reached.
    """
    server = ConfigServer(SERVER, settings={"name": SERVER, "maxPlayers": 16})
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"name": "n/a", "maxPlayers": 24})

    assert result.ok is False and result.refused
    assert "n/a" in result.refused and "reserved" in result.refused
    assert result.applied == {} and server.rename_calls == []
    assert server.settings["name"] == SERVER and server.settings["maxPlayers"] == 16


@pytest.mark.asyncio
async def test_the_override_sync_skips_a_server_with_no_entry_in_servers_yaml(sa, audit_bot, tmp_path):
    """The ``data[server.name]`` ``KeyError`` is guarded — the sync SKIPS and says so.

    The settings write stands, the sync is skipped, ``servers.yaml`` is left untouched, no reload
    runs, and the outcome is a normal success (documented behaviour: the pin may still revert at the
    next start, and the log says why).
    """
    path = write_servers_yaml(tmp_path, {"somebody-else": {"serverSettings": {"maxPlayers": 1}}})
    before = path.read_text(encoding="utf-8")
    server = ConfigServer(SERVER, settings={"name": SERVER, "maxPlayers": 16},
                          locals_={"serverSettings": {"maxPlayers": 32}}, node=node_for(tmp_path))
    ctx = ctx_for([server])

    result = await sa.set_server_config(ctx, SERVER, {"maxPlayers": 24})

    assert result.ok is True and result.applied["maxPlayers"] == {"from": 16, "to": 24}
    assert server.settings["maxPlayers"] == 24, "the settings write still lands"
    assert path.read_text(encoding="utf-8") == before, "no entry → servers.yaml is left untouched"
    assert server.reload_calls == 0, "no entry → no reload"


@pytest.mark.asyncio
async def test_the_name_guard_runs_inside_the_write_lock(sa, monkeypatch, audit_bot):
    """The guard sits inside the per-server write lock, in the same critical section as the rename.

    A spy around ``_name_collision`` records whether the lock is HELD when the guard runs; if the guard
    were hoisted before the ``async with`` the lock would be free and a concurrent writer could rename
    between the check and the write.
    """
    observed: dict[str, bool] = {}
    real_guard = sa._name_collision

    def spy(ctx, server, new_name):
        observed["locked"] = sa.server_write_lock(str(server.name)).locked()
        return real_guard(ctx, server, new_name)

    monkeypatch.setattr(sa, "_name_collision", spy)
    one = ConfigServer(SERVER, settings={"name": SERVER, "maxPlayers": 16})
    two = ConfigServer(OTHER, settings={"name": OTHER})
    ctx = ctx_for([one, two])

    result = await sa.set_server_config(ctx, SERVER, {"name": OTHER})

    assert result.ok is False and "already used" in result.refused
    assert observed.get("locked") is True, "the name guard runs while the write lock is held"

