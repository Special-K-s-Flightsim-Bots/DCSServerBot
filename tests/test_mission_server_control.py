"""The website's Shutdown / Startup follow Discord's maintenance defaults.

The four actions live in ``plugins/mission/actions.py``: ``shutdown_server`` / ``startup_server``
(the DCS-level pair, SHUTDOWN <-> running) and ``start_server`` / ``stop_server`` (the process-level
pair, STOPPED <-> running). Contract: ``shutdown_server`` sets the maintenance flag by default
(``maintenance=False`` leaves/clears it); ``startup_server`` clears it by default
(``maintenance=False`` leaves it); ``start_server`` / ``stop_server`` touch no flag; a shutdown that
could not flag does not report a clean success. Driven through the discovered ``@action`` registry."""
from __future__ import annotations

import inspect
import sys
import types

import pytest

import core.actions as actions_module
from core import Status
from core.actions import ActionContext, discover_actions, action_registry


# ------------------------------------------------------------------------------- test doubles

class ServerDouble:
    """A ``Server`` double holding the flag through a REAL property, as ``core.data.server.Server``.

    ``shutdown`` / ``startup`` / ``start`` / ``stop`` move ``status`` exactly like the domain methods
    after the DCS broadcast, and every call is recorded so a test can prove WHICH action ran.
    """

    def __init__(self, name, status=Status.RUNNING, maintenance=False):
        self.name = name
        self.display_name = name
        self.status = status
        self._maintenance = maintenance
        self.calls: list[tuple] = []

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, value):
        self._maintenance = bool(value)

    async def shutdown(self, force: bool = False, **kwargs):
        self.calls.append(("shutdown", force))
        self.status = Status.SHUTDOWN

    async def startup(self, **kwargs):
        self.calls.append(("startup", kwargs))
        self.status = Status.RUNNING

    async def start(self, **kwargs):
        self.calls.append(("start",))
        self.status = Status.RUNNING

    async def stop(self, **kwargs):
        self.calls.append(("stop",))
        self.status = Status.STOPPED


class FlagRefusingServer(ServerDouble):
    """A server whose maintenance flag cannot be WRITTEN: the shutdown succeeds but only the flag write
    raises, so a shutdown that could not flag must not be reported as a clean success.
    """

    @property
    def maintenance(self) -> bool:
        return self._maintenance

    @maintenance.setter
    def maintenance(self, value):
        raise RuntimeError("this flag refuses to be written")


# ---------------------------------------------------------------------------------- the harness

@pytest.fixture
def actions():
    """The mission plugin's actions, freshly discovered, with the process registry restored after.

    Discovery works by IMPORTING the module (the decorators run once per process), so the module is
    popped from the cache first — otherwise the second test would observe a registry an earlier test
    populated rather than a genuine registration.
    """
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.mission.actions", None)
    discover_actions(["mission"])
    registry = action_registry()
    assert {"startup_server", "shutdown_server", "start_server", "stop_server"} <= set(registry)
    try:
        yield registry
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.mission.actions", None)


def ctx_for(server) -> ActionContext:
    """The seam's context over a one-server stub cluster — the shape the console builds (duck-typed)."""
    bus = types.SimpleNamespace(servers={server.name: server})
    return ActionContext(node=None, bus=bus)


async def run(actions, qualname, server, **params):
    """Invoke a discovered action by its qualname, resolving *server* by name as a transport would."""
    return await actions[qualname](ctx_for(server), server_name=server.name, **params)


# ------------------------------------------------------- 1. shutdown sets the flag by default

@pytest.mark.asyncio
async def test_shutdown_sets_the_maintenance_flag_by_default(actions):
    """``/server shutdown`` marks the server: the website's Shutdown must do the same.

    A deliberate shutdown that leaves the flag clear is undone by the next scheduled start
    (``plugins/scheduler/commands.py``), so the default is TRUE.
    """
    server = ServerDouble("SRS-1", status=Status.RUNNING, maintenance=False)
    result = await run(actions, "shutdown_server", server)

    assert result.success is True
    assert ("shutdown", False) in server.calls, "the domain shutdown ran, force staying False"
    assert server.maintenance is True, "the flag is set by default"
    assert result.message == "Server 'SRS-1' shut down."


@pytest.mark.asyncio
async def test_shutdown_with_maintenance_off_leaves_the_flag_clear(actions):
    """``maintenance=False`` and a clear flag: the parameter says leave it — and it stays clear."""
    server = ServerDouble("SRS-1", status=Status.RUNNING, maintenance=False)
    result = await run(actions, "shutdown_server", server, maintenance=False)

    assert result.success is True
    assert server.maintenance is False


@pytest.mark.asyncio
async def test_shutdown_with_maintenance_off_clears_a_set_flag(actions):
    """The other half of "set it to the parameter": ``False`` CLEARS a flag that was set.

    ``/server shutdown`` does exactly this (``plugins/scheduler/commands.py``: a ``False`` maintenance
    clears the flag on a successful shutdown), so the two surfaces agree.
    """
    server = ServerDouble("SRS-1", status=Status.RUNNING, maintenance=True)
    result = await run(actions, "shutdown_server", server, maintenance=False)

    assert result.success is True
    assert server.maintenance is False, "the parameter is the desired value, not just a set/no-op"


# ------------------------------------------------------ 2. startup clears the flag by default

@pytest.mark.asyncio
async def test_startup_clears_the_maintenance_flag_by_default(actions):
    """``/server startup`` cleans the flag: the website's Startup must do the same.

    A server that stays flagged after an explicit startup would be skipped by the scheduler.
    """
    server = ServerDouble("SRS-1", status=Status.SHUTDOWN, maintenance=True)
    result = await run(actions, "startup_server", server)

    assert result.success is True
    assert server.calls == [("startup", {"modify_mission": True, "use_orig": True, "preset": None})]
    assert server.maintenance is False, "the flag is cleared by default"
    assert result.message == "Server 'SRS-1' started up."


@pytest.mark.asyncio
async def test_startup_with_maintenance_off_leaves_a_set_flag(actions):
    """``maintenance=False``: the flag is left exactly as it was — here, set."""
    server = ServerDouble("SRS-1", status=Status.SHUTDOWN, maintenance=True)
    result = await run(actions, "startup_server", server, maintenance=False)

    assert result.success is True
    assert server.maintenance is True


# --------------------------------------------------------- 3. start / stop touch no flag

@pytest.mark.asyncio
async def test_start_and_stop_change_no_maintenance_flag(actions):
    """The PROCESS-level pair never touches the maintenance flag, set or clear."""
    stopped = ServerDouble("SRS-1", status=Status.STOPPED, maintenance=True)
    started = await run(actions, "start_server", stopped)
    assert started.success is True and stopped.maintenance is True

    plain = ServerDouble("SRS-1", status=Status.STOPPED, maintenance=False)
    await run(actions, "start_server", plain)
    assert plain.maintenance is False

    running = ServerDouble("SRS-1", status=Status.RUNNING, maintenance=True)
    stopped_ok = await run(actions, "stop_server", running)
    assert stopped_ok.success is True and running.maintenance is True

    clear = ServerDouble("SRS-1", status=Status.RUNNING, maintenance=False)
    await run(actions, "stop_server", clear)
    assert clear.maintenance is False


# ------------------------------------------------------ 4. a failed flag write is honest

@pytest.mark.asyncio
async def test_a_shutdown_that_could_not_flag_does_not_report_a_clean_success(actions):
    """The shutdown happened, but the flag write raised: the result must say so — and not succeed.

    Reporting a bare "shut down." here would be a clean success over a server the scheduler will
    bring back on the next tick.
    """
    server = FlagRefusingServer("SRS-1", status=Status.RUNNING)
    result = await run(actions, "shutdown_server", server)

    assert server.calls == [("shutdown", False)], "the shutdown itself did happen"
    assert result.success is False, "a shutdown that could not flag is not a clean success"
    assert result.message != "Server 'SRS-1' shut down."
    assert "shut down" in result.message, "it still reports that the shutdown happened"
    assert "could not be set" in result.message
    assert "refuses to be written" in result.message, "the failure reason is carried through"


# ------------------------------------------------------------ 5. the parameters are the contract

def test_the_maintenance_parameter_is_the_website_dialog_contract(actions):
    """The NAMES and DEFAULTS the website dialog reads, pinned on the live signatures.

    The ``@action`` docstring is the schema every transport reads, so a renamed parameter or a
    flipped default silently changes what the console can offer — this is where that fails instead.
    """
    shutdown = inspect.signature(actions["shutdown_server"]).parameters
    assert shutdown["maintenance"].default is True
    assert shutdown["force"].default is False

    startup = inspect.signature(actions["startup_server"]).parameters
    assert startup["maintenance"].default is True
    assert startup["modify_mission"].default is True
    assert startup["use_orig"].default is True

 # start / stop carry no maintenance parameter at all (the pair never touches the flag).
    for qualname in ("start_server", "stop_server"):
        assert "maintenance" not in inspect.signature(actions[qualname]).parameters, qualname
