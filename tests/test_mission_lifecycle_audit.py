"""The eight lifecycle actions leave exactly ONE audit entry per exit — success and refusal alike.

``start_server`` / ``stop_server`` / ``restart_server`` / ``pause_mission`` / ``unpause_mission`` /
``restart_mission`` / ``rotate_mission`` / ``load_mission`` used to reach the console with no trail at
all, so a caller stamped ``[mcp:key/<label>]`` wrote into nothing and ``call_action`` logged its
"returned no audit entry" warning on every one of them.

Every test drives the SHIPPED action over a stub cluster and a bot double whose ``audit()`` records,
so the entry counted here is the one the audit seam really wrote. ``startup_server`` /
``shutdown_server`` are deliberately absent: they carry launch-policy and dual-write semantics and are
out of this slice.
"""
from __future__ import annotations

import inspect
import logging
import sys
import types

import pytest

import core.actions as actions_module
import core.services.registry as registry_module
from core import Status
from core.actions import (AUDIT_RECORDED, ActionContext, action_registry, call_action,
                          discover_actions, reset_in_flight)


# ------------------------------------------------------------------------------- test doubles

class MissionDouble:
    """A ``Mission`` double: the two calls the pause pair makes, recorded."""

    def __init__(self, name="Mission-A"):
        self.name = name
        self.calls: list[str] = []

    async def pause(self):
        self.calls.append("pause")

    async def unpause(self):
        self.calls.append("unpause")


class ServerDouble:
    """A ``Server`` double shaped where the eight actions read it.

    ``status`` MOVES like the domain methods' own, and every domain call is recorded, so a success
    test proves the operation really ran rather than only that the action returned.
    """

    def __init__(self, name="SRS-1", *, status=Status.RUNNING, mission: MissionDouble | None | ellipsis = ...):
        self.name = name
        self.display_name = name
        self.status = status
        self.locals: dict = {}
        self.current_mission: MissionDouble | None = (
            MissionDouble() if mission is ... else mission)
        self.calls: list = []

    async def start(self):
        self.calls.append("start")
        self.status = Status.RUNNING

    async def stop(self):
        self.calls.append("stop")
        self.status = Status.STOPPED

    async def restart(self, **kwargs):
        self.calls.append(("restart", kwargs))
        self.status = Status.RUNNING

    async def loadNextMission(self, **kwargs):
        self.calls.append(("loadNextMission", kwargs))

    async def getMissionList(self):
        return ["/missions/one.miz", "/missions/two.miz"]

    async def get_missions_dir(self):
        return "/missions"

    async def loadMission(self, mission_file, **kwargs):
        self.calls.append(("loadMission", mission_file, kwargs))
        return True


class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry (not the no-op)."""

    def __init__(self):
        self.entries: list[dict] = []

    async def audit(self, event, server=None, **kwargs):
        self.entries.append({
            "event": event,
            "server": getattr(server, "name", None),
            "actor": kwargs.get("actor"),
        })


# ---------------------------------------------------------------------------------- the harness

@pytest.fixture
def audit_bot(monkeypatch):
    """Make the seam's audit resolvable, so an entry is recorded and can be counted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


@pytest.fixture(autouse=True)
def clean_in_flight():
    reset_in_flight()
    try:
        yield
    finally:
        reset_in_flight()


@pytest.fixture
def mission_actions():
    """The mission plugin's actions, freshly discovered, with the process registry restored after."""
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def ctx_for(server) -> ActionContext:
    """The seam's context over a one-server stub cluster — the shape a transport builds."""
    bus = types.SimpleNamespace(servers={server.name: server})
    return ActionContext(node=None, bus=bus)


async def run(actions, qualname, server, **params):
    return await actions[qualname](ctx_for(server), server_name=server.name, **params)


# ------------------------------------------------------------------ 1. one entry on success

@pytest.mark.asyncio
async def test_start_server_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.STOPPED, mission=None)
    result = await run(mission_actions, "start_server", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert audit_bot.entries[0]["server"] == "SRS-1"
    assert result.data["audit"] == AUDIT_RECORDED


@pytest.mark.asyncio
async def test_stop_server_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "stop_server", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert "stop" in server.calls


@pytest.mark.asyncio
async def test_restart_server_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "restart_server", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert server.calls == ["stop", "start"]


@pytest.mark.asyncio
async def test_pause_mission_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "pause_mission", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    mission = server.current_mission
    assert mission is not None and mission.calls == ["pause"]


@pytest.mark.asyncio
async def test_unpause_mission_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.PAUSED)
    result = await run(mission_actions, "unpause_mission", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    mission = server.current_mission
    assert mission is not None and mission.calls == ["unpause"]


@pytest.mark.asyncio
async def test_restart_mission_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "restart_mission", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert server.calls[0][0] == "restart"


@pytest.mark.asyncio
async def test_rotate_mission_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "rotate_mission", server)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert server.calls[0][0] == "loadNextMission"


@pytest.mark.asyncio
async def test_load_mission_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING, mission=None)
    result = await run(mission_actions, "load_mission", server, mission="one.miz")

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert server.calls[0][0] == "loadMission"


# ---------------------------------------------------------------- 2. one entry on each refusal

@pytest.mark.asyncio
async def test_a_refused_start_writes_exactly_one_entry(audit_bot, mission_actions):
    """The server is RUNNING: start refuses, and the refusal is on the trail."""
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "start_server", server)

    assert result.success is False
    assert "not stopped" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_stop_writes_exactly_one_entry(audit_bot, mission_actions):
    """A refused stop is on the trail as well as a performed one."""
    server = ServerDouble(status=Status.STOPPED)
    result = await run(mission_actions, "stop_server", server)

    assert result.success is False
    assert "not running or paused" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_restart_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.LOADING)
    result = await run(mission_actions, "restart_server", server)

    assert result.success is False
    assert "loading" in result.message.lower()
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_pause_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.PAUSED)
    result = await run(mission_actions, "pause_mission", server)

    assert result.success is False
    assert "not running" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_unpause_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "unpause_mission", server)

    assert result.success is False
    assert "not paused" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_mission_restart_writes_exactly_one_entry(audit_bot, mission_actions):
    """The server is up but carries no mission: the refusal names that, and the trail records it."""
    server = ServerDouble(status=Status.RUNNING, mission=None)
    result = await run(mission_actions, "restart_mission", server)

    assert result.success is False
    assert "No active mission" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_rotate_writes_exactly_one_entry(audit_bot, mission_actions):
    server = ServerDouble(status=Status.LOADING)
    result = await run(mission_actions, "rotate_mission", server)

    assert result.success is False
    assert "loading" in result.message.lower()
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message


@pytest.mark.asyncio
async def test_a_refused_load_writes_exactly_one_entry(audit_bot, mission_actions):
    """A weather pick with the extensions off is refused before anything loads — and audited."""
    server = ServerDouble(status=Status.RUNNING)
    result = await run(mission_actions, "load_mission", server, mission="one.miz",
                       modify_mission=False, preset="fog")

    assert result.success is False
    assert "extensions are off" in result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["event"] == result.message
    assert server.calls == [], "the refusal ran nothing"


# ------------------------------------------------- 3. call_action no longer warns about the trail

LIFECYCLE_SUCCESS = (
    ("start_server", Status.STOPPED, None, {}),
    ("stop_server", Status.RUNNING, ..., {}),
    ("restart_server", Status.RUNNING, ..., {}),
    ("pause_mission", Status.RUNNING, ..., {}),
    ("unpause_mission", Status.PAUSED, ..., {}),
    ("restart_mission", Status.RUNNING, ..., {}),
    ("rotate_mission", Status.RUNNING, ..., {}),
    ("load_mission", Status.RUNNING, None, {"mission": "one.miz"}),
)


@pytest.mark.asyncio
async def test_call_action_reports_no_missing_trail_for_any_of_the_eight(audit_bot, mission_actions,
                                                                       caplog):
    """The seam's warning is the symptom this slice removes: none of the eight may trigger it."""
    with caplog.at_level(logging.WARNING, logger="core.actions"):
        for qualname, status, mission, params in LIFECYCLE_SUCCESS:
            server = ServerDouble(status=status, mission=mission)
            result = await call_action(qualname, ctx_for(server),
                                       server_name=server.name, **params)
            assert result.success is True, (qualname, result.message)

    assert "returned no audit entry" not in caplog.text, caplog.text
    assert len(audit_bot.entries) == len(LIFECYCLE_SUCCESS), audit_bot.entries


# ------------------------------------------------------- 4. the entry carries the caller's actor

def _service_registry(monkeypatch, server):
    holder = {"bus": types.SimpleNamespace(servers={server.name: server})}
    monkeypatch.setattr(registry_module.ServiceRegistry, "get",
                        classmethod(lambda cls, wanted: holder["bus"]))


@pytest.mark.asyncio
async def test_a_service_context_stamps_its_actor_on_the_entry(audit_bot, mission_actions,
                                                               monkeypatch):
    """``from_service`` is what an MCP caller builds: the entry carries the actor it stamped."""
    server = ServerDouble(status=Status.RUNNING)
    _service_registry(monkeypatch, server)
    ctx = ActionContext.from_service(types.SimpleNamespace(node=None))
    assert ctx.audit_actor == "MCP"

    result = await call_action("stop_server", ctx, server_name=server.name)

    assert result.success is True, result.message
    assert len(audit_bot.entries) == 1, audit_bot.entries
    assert audit_bot.entries[0]["actor"] == "MCP"


@pytest.mark.asyncio
async def test_a_web_context_stamps_its_declared_prefix(audit_bot, mission_actions):
    """``[web:backend/subject]`` reaches the entry's own actor column, not just the event text."""
    server = ServerDouble(status=Status.STOPPED, mission=None)
    identity = types.SimpleNamespace(roles=(), scope=None, shown_name="specialk",
                                     backend="local", subject="specialk")
    ctx = ActionContext.from_web(identity, None, types.SimpleNamespace(servers={server.name: server}))

    result = await call_action("start_server", ctx, server_name=server.name)

    assert result.success is True, result.message
    assert audit_bot.entries[0]["actor"] == "[web:local/specialk]", audit_bot.entries


# ------------------------------------------------------------------ 5. the slice's own boundary

def test_startup_and_shutdown_are_not_audited_by_this_slice(mission_actions):
    """The DCS-level pair is out of scope: its exits must not have gained an audit call here."""
    for qualname in ("startup_server", "shutdown_server"):
        source = inspect.getsource(mission_actions[qualname])
        assert "_audited(" not in source, qualname
