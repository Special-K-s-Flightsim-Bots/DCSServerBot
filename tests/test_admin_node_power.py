"""The node power pair (``/node offline`` / ``/node online``) built on the maintenance engine.

``offline`` takes a node's in-service servers out of service through the engine's popup chain and
unless told otherwise flags the ones not already flagged, then records what it did; ``online``
reverts exactly that record. The engine is ``core/data/maintenance.py``; the actions are the
``@action`` functions in ``plugins/admin/actions.py`` (the Discord commands reach the same
functions through ``call_action``).

Invariants: a hand-set flag survives offline and online; a flagged server is never released by a
power cycle; only servers ``offline`` stopped are released, and the SCHEDULER starts them; a second ``offline`` unions into the
record; with no record the online half clears nothing and says so. Servers are doubles whose
``status`` moves like the real ones'. Also covered: the FORCED clear (``online`` with ``clear_all``),
the one path that clears a flag the process holds no record of, and the upgrade action's major-version
guard, which refuses a major upgrade while servers are in service."""
from __future__ import annotations

import asyncio
import inspect
import re
import types
from pathlib import Path

import pytest

import core.actions as actions_module
from core import Status
from core.actions import ActionContext, call_action, discover_actions, reset_in_flight
from core.data.maintenance import (ServerMaintenanceManager, power_record, process_started_at,
                                   reset_power_records)
from core.data.resource_window import ResourceWindowProtocol

REPO = Path(__file__).resolve().parents[1]
NODE = "dcs-a-01"
OTHER = "dcs-b-01"


# ------------------------------------------------------------------------------- test doubles

class _Logger:
    """A node's logger double — the engine reads ``node.log`` when it has one."""

    def __init__(self):
        self.lines: list[str] = []

    def _record(self, msg, *args, **kwargs):
        self.lines.append(str(msg) % args if args else str(msg))

    debug = info = warning = error = exception = _record


class PowerNode:
    """The ``Node`` double the engine reads: a name, an ``instances`` mapping and a logger."""

    def __init__(self, name, servers=()):
        self.name = name
        self.log = _Logger()
        self.instances = {f"i{index}": types.SimpleNamespace(server=server)
                          for index, server in enumerate(servers)}


class PowerServer:
    """A server double shaped where the engine and the action read it.

    ``status`` MOVES when ``shutdown``/``startup`` is called, exactly as the real objects' do — the
    online half's "already up?" test depends on it. ``players`` makes the server *populated*, which is
    what triggers the engine's popup chain.
    """

    def __init__(self, name, node_name, *, status=Status.RUNNING, maintenance=False, players=0):
        self.name = name
        self.display_name = name
        self.status = status
        self.maintenance = maintenance
        self.players = players
        self.node = types.SimpleNamespace(name=node_name)
        self.calls: list[str] = []

    def is_populated(self) -> bool:
        return bool(self.players)

    async def sendPopupMessage(self, *args, **kwargs):
        self.calls.append("popup")

    async def shutdown(self):
        self.calls.append("shutdown")
        self.status = Status.SHUTDOWN

    async def startup(self):
        self.calls.append("startup")
        self.status = Status.RUNNING


class StubbornServer(PowerServer):
    """A server whose maintenance flag cannot be SET — the per-server failure path of ``offline``."""

    @property
    def maintenance(self):
        return self._maintenance

    @maintenance.setter
    def maintenance(self, value):
        if value:
            raise RuntimeError("this flag refuses to be written")
        self._maintenance = value


class RecordingBot:
    """A bot double whose ``audit()`` records, so the seam writes a REAL entry (not the no-op)."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def audit(self, event, server=None, **kwargs):
        self.calls.append((event, getattr(server, "name", None)))


def ctx_for(servers, nodes, *, actor="probe"):
    """The seam's context over a stub cluster — exactly the shape the console builds (duck-typed).

    ``bus.nodes`` is the caller's SCOPED view, so ``resolve_node`` answers from it; a name missing
    from it is the offline/unknown refusal.
    """
    bus = types.SimpleNamespace(servers={server.name: server for server in servers}, nodes=nodes)
    identity = types.SimpleNamespace(roles=(), scope=None, shown_name=actor, backend="local",
                                     subject=actor)
    return ActionContext.from_web(identity, None, bus)


@pytest.fixture(autouse=True)
def clean_power_state():
    """The record registry and the in-flight guard are module state: reset them around every test."""
    reset_power_records()
    reset_in_flight()
    try:
        yield
    finally:
        reset_power_records()
        reset_in_flight()


@pytest.fixture(autouse=True)
def admin_actions_registered():
    """Import the admin actions once per test, from a clean module (the registry is process state)."""
    import sys

    sys.modules.pop("plugins.admin.actions", None)
    discover_actions(["admin"])
    try:
        yield
    finally:
        sys.modules.pop("plugins.admin.actions", None)


@pytest.fixture
def audit_bot(monkeypatch):
    """Make the seam's audit resolvable, so the entries are RECORDED and can be asserted."""
    bot = RecordingBot()
    monkeypatch.setattr(actions_module, "_resolve_bot", lambda ctx: bot)
    return bot


@pytest.fixture(autouse=True)
def admin_action_module(admin_actions_registered):
    """Depends on :func:`admin_actions_registered` on purpose: that fixture imports (and later drops)
    the actions module, so the action functions read from the SAME module object in every test."""
    import plugins.admin.actions  # noqa: F401


def messages(bot: RecordingBot) -> list[str]:
    return [event for event, _server in bot.calls]


# ------------------------------------------------------------------ 1. the dangerous direction

@pytest.mark.asyncio
async def test_a_hand_set_flag_survives_a_full_offline_online_cycle(audit_bot):
    """A flagged-by-hand and stopped, plus B running.

    ``offline`` leaves A's flag ALONE and records A as already flagged; ``online`` clears only the flag
    the operation set (B's) and starts only B. A is untouched throughout.
    """
    a = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN, maintenance=True)
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([a, b], {NODE: PowerNode(NODE)})

    offline = await call_action("take_node_offline", ctx, node_name=NODE)
    assert offline.success is True
    assert a.maintenance is True and a.calls == [], "A's alone flag and its stopped state survive"
    assert b.maintenance is True and b.calls == ["shutdown"]
    record = power_record(NODE)
    assert record is not None
    assert record.stopped == ["SRS-B"]
    assert record.flagged == ["SRS-B"]
    assert record.already_flagged == ["SRS-A"]
    assert offline.message == ("Node 'dcs-a-01': 1 server(s) marked as maintenance and stopped, "
                               "1 already in maintenance, left as it was, 1 already stopped.")

    online = await call_action("bring_node_online", ctx, node_name=NODE)
    assert online.success is True
    assert a.maintenance is True, "a flag this operation did not set is NEVER cleared"
    assert a.calls == [], "...and its server is never started"
    assert b.maintenance is False and b.calls == ["shutdown"], \
        "the start is the scheduler's job, never this operation's"
    assert power_record(NODE) is None, "the record is spent once it has been reverted"
    assert online.message == ("Node 'dcs-a-01': 1 maintenance flag(s) cleared, "
                              "1 server(s) left for the scheduler to start.")
    assert [m for m in messages(audit_bot)] == [
        "Node 'dcs-a-01': 1 server(s) marked as maintenance and stopped, "
        "1 already in maintenance, left as it was, 1 already stopped.",
        "Node 'dcs-a-01': 1 maintenance flag(s) cleared, "
        "1 server(s) left for the scheduler to start.",
    ]


@pytest.mark.asyncio
async def test_a_server_still_in_maintenance_is_never_started_by_online(audit_bot):
    """A flagged server that was RUNNING when ``offline`` ran is stopped, but not brought back.

    Its flag is not the operation's, so ``online`` neither clears it nor releases the server it stopped --
    and it says so rather than reporting a bare success.
    """
    a = PowerServer("SRS-A", NODE, status=Status.RUNNING, maintenance=True)
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([a, b], {NODE: PowerNode(NODE)})

    await call_action("take_node_offline", ctx, node_name=NODE)
    assert a.calls == ["shutdown"] and a.maintenance is True

    online = await call_action("bring_node_online", ctx, node_name=NODE)
    assert a.maintenance is True, "the flag is not ours to clear"
    assert a.calls == ["shutdown"], "never started again"
    assert b.maintenance is False and b.calls == ["shutdown"]
    assert online.message == ("Node 'dcs-a-01': 1 maintenance flag(s) cleared, "
                              "1 server(s) left for the scheduler to start, "
                              "1 left alone (already running or still in maintenance).")


# ----------------------------------------------------------------- 2. only what it stopped starts

@pytest.mark.asyncio
async def test_an_already_shutdown_server_is_never_stopped_or_recorded(audit_bot):
    """A SHUTDOWN server is not in service: it is not flagged, not stopped, not in the record."""
    down = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN)
    up = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([down, up], {NODE: PowerNode(NODE)})

    await call_action("take_node_offline", ctx, node_name=NODE)
    assert down.calls == [] and down.maintenance is False
    record = power_record(NODE)
    assert "SRS-A" not in record.stopped
    assert record.stopped == ["SRS-B"]


@pytest.mark.asyncio
async def test_an_unregistered_server_is_not_in_service(audit_bot):
    """``UNREGISTERED`` is the initial status of a never-started object — flagging it was the bug."""
    fresh = PowerServer("SRS-A", NODE, status=Status.UNREGISTERED)
    ctx = ctx_for([fresh], {NODE: PowerNode(NODE)})

    result = await call_action("take_node_offline", ctx, node_name=NODE)
    assert result.success is True
    assert fresh.maintenance is False and fresh.calls == []
    assert power_record(NODE).stopped == []


@pytest.mark.asyncio
async def test_a_shutting_down_server_counts_as_in_service(audit_bot):
    """The process is still up mid-stop, so it is taken out of service and released later."""
    stopping = PowerServer("SRS-A", NODE, status=Status.SHUTTING_DOWN)
    ctx = ctx_for([stopping], {NODE: PowerNode(NODE)})

    await call_action("take_node_offline", ctx, node_name=NODE)
    assert stopping.calls == ["shutdown"]
    assert power_record(NODE).stopped == ["SRS-A"]

    await call_action("bring_node_online", ctx, node_name=NODE)
    assert stopping.calls == ["shutdown"], "released, not started"


@pytest.mark.asyncio
async def test_a_server_started_by_hand_between_the_halves_is_skipped_not_restarted(audit_bot):
    """The record's stopped server is up again when ``online`` runs: report it, do not start it twice."""
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([b], {NODE: PowerNode(NODE)})
    await call_action("take_node_offline", ctx, node_name=NODE)

    b.status = Status.RUNNING          # an admin started it by hand in between
    b.calls.clear()
    online = await call_action("bring_node_online", ctx, node_name=NODE)
    assert b.calls == [], "no second start"
    assert b.maintenance is False, "the flag our offline set is still cleared"
    assert "1 left alone" in online.message


# ------------------------------------------------------------------------- 3. the record unions

@pytest.mark.asyncio
async def test_a_second_offline_unions_into_the_record(audit_bot):
    """A second ``offline`` adds to the record; it never replaces it and never moves a name."""
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([b], {NODE: PowerNode(NODE)})
    await call_action("take_node_offline", ctx, node_name=NODE)
    assert power_record(NODE).stopped == ["SRS-B"]

 # B was started again by hand, and a NEW server C appeared on the node
    b.status = Status.RUNNING
    c = PowerServer("SRS-C", NODE, status=Status.RUNNING)
    ctx = ctx_for([b, c], {NODE: PowerNode(NODE)})
    await call_action("take_node_offline", ctx, node_name=NODE)

    record = power_record(NODE)
    assert record.stopped == ["SRS-B", "SRS-C"]
    assert record.flagged == ["SRS-B", "SRS-C"]
    assert record.already_flagged == [], "B is OURS: a later pass must not demote it"
    assert b.maintenance is True and c.maintenance is True


# ----------------------------------------------------------------- 4. the no-record online path

@pytest.mark.asyncio
async def test_online_with_no_record_clears_nothing_and_starts_down_unflagged_servers(audit_bot):
    """A restarted process has no record: the RULE applies, and the message says exactly that."""
    flagged = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN, maintenance=True)
    down = PowerServer("SRS-B", NODE, status=Status.SHUTDOWN)
    up = PowerServer("SRS-C", NODE, status=Status.RUNNING)
    ctx = ctx_for([flagged, down, up], {NODE: PowerNode(NODE)})

    result = await call_action("bring_node_online", ctx, node_name=NODE)
    assert result.success is True
    assert flagged.maintenance is True and flagged.calls == [], "no flag is cleared, nothing released"
    assert down.calls == [], "the scheduler starts it, not this operation"
    assert up.calls == [], "a running server is not touched"
    started_at = process_started_at().strftime("%H:%M")
    assert result.message == (
        f"Node 'dcs-a-01': no power-off is recorded for this node since the bot started at "
        f"{started_at}; 0 maintenance flag(s) cleared; 1 server(s) that were down and not in "
        f"maintenance are left for the scheduler to start.")


@pytest.mark.asyncio
async def test_online_twice_hits_the_no_record_rule_on_the_second_press(audit_bot):
    """The first ``online`` spends the record; the second is the rule path with its honest message."""
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([b], {NODE: PowerNode(NODE)})
    await call_action("take_node_offline", ctx, node_name=NODE)

    first = await call_action("bring_node_online", ctx, node_name=NODE)
    second = await call_action("bring_node_online", ctx, node_name=NODE)
    assert "1 maintenance flag(s) cleared" in first.message
    assert "no power-off is recorded" in second.message
    assert b.calls == ["shutdown"], "no press starts a server - the scheduler owns every start"


# ------------------------------------------------------- 5. the flag option and the failures

@pytest.mark.asyncio
async def test_offline_with_maintenance_off_stops_without_flagging(audit_bot):
    """The flag is optional; the STOP is what the control is — so the record owns no flag."""
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    ctx = ctx_for([b], {NODE: PowerNode(NODE)})

    result = await call_action("take_node_offline", ctx, node_name=NODE, maintenance=False)
    assert b.maintenance is False, "no flag was set"
    assert b.calls == ["shutdown"]
    record = power_record(NODE)
    assert record.stopped == ["SRS-B"] and record.flagged == []
    assert result.message == "Node 'dcs-a-01': 1 server(s) stopped, no maintenance flag was set."

    online = await call_action("bring_node_online", ctx, node_name=NODE)
    assert b.calls == ["shutdown"], "the flag goes; the start is the scheduler's"
    assert "0 maintenance flag(s) cleared" in online.message


@pytest.mark.asyncio
async def test_a_server_whose_flag_cannot_be_written_is_counted_not_fatal(audit_bot):
    """One broken object costs one server: it is still stopped, and the report says it failed."""
    stubborn = StubbornServer("SRS-A", NODE, status=Status.RUNNING)
    ctx = ctx_for([stubborn], {NODE: PowerNode(NODE)})

    result = await call_action("take_node_offline", ctx, node_name=NODE)
    assert result.success is False
    assert stubborn.calls == ["shutdown"]
    assert "0 server(s) marked as maintenance and stopped" in result.message
    assert "1 server(s) could not be marked as maintenance" in result.message


@pytest.mark.asyncio
async def test_a_recorded_server_that_no_longer_exists_is_reported_not_raised(audit_bot):
    """A renamed/migrated/removed server is a sentence in the message, never an exception."""
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    await call_action("take_node_offline", ctx_for([b], {NODE: PowerNode(NODE)}), node_name=NODE)

    empty = ctx_for([], {NODE: PowerNode(NODE)})          # the server is gone from the view
    result = await call_action("bring_node_online", empty, node_name=NODE)
    assert result.success is True
    assert result.message == ("Node 'dcs-a-01': 0 maintenance flag(s) cleared, 0 server(s) "
                              "left for the scheduler to start, 1 recorded server(s) no longer "
                              "exist on this node.")


@pytest.mark.asyncio
async def test_both_halves_refuse_an_unknown_node_and_audit_it(audit_bot):
    """The typed refusal is unchanged, and a refusal is still an attempt in the trail."""
    ctx = ctx_for([], {})
    for qualname in ("take_node_offline", "bring_node_online"):
        result = await call_action(qualname, ctx, node_name="NOPE-9")
        assert result.success is False
        assert result.message == "Node 'NOPE-9' is offline or unknown."
    assert messages(audit_bot) == [
        "Node 'NOPE-9' is offline or unknown.",
        "Node 'NOPE-9' is offline or unknown.",
    ]


# ------------------------------------------------------------------------- 6. the engine itself

def test_in_service_is_the_two_terminal_statuses_and_nothing_else():
    """SHUTDOWN and UNREGISTERED are out of service; everything else is in."""
    for status in (Status.SHUTDOWN, Status.UNREGISTERED):
        assert ServerMaintenanceManager.in_service(types.SimpleNamespace(status=status)) is False
    for status in (Status.LOADING, Status.RUNNING, Status.PAUSED, Status.STOPPED,
                   Status.SHUTTING_DOWN):
        assert ServerMaintenanceManager.in_service(types.SimpleNamespace(status=status)) is True


@pytest.mark.asyncio
async def test_the_context_manager_keeps_its_old_behaviour():
    """Every pre-existing call site: flag + stop on enter; unflag + start on exit, already-flagged included."""
    a = PowerServer("SRS-A", NODE, status=Status.RUNNING, maintenance=True)
    b = PowerServer("SRS-B", NODE, status=Status.RUNNING)
    node = PowerNode(NODE, [a, b])

    manager = ServerMaintenanceManager(node, shutdown=True)
    async with manager:
        assert b.maintenance is True, "the engine flags what is not flagged"
        assert a.maintenance is True, "and leaves a hand-set flag alone"
        assert a.calls == ["shutdown"] and b.calls == ["shutdown"]
        assert [server.name for server in manager.to_start] == ["SRS-A", "SRS-B"]
        assert [server.name for server in manager.in_maintenance] == ["SRS-A"]

    assert b.maintenance is False, "the flag it set is cleared"
    assert a.maintenance is True, "...and the one it did not set is not"
    assert a.calls == ["shutdown", "startup"], "the context manager restarts what it stopped"
    assert b.calls == ["shutdown", "startup"]


@pytest.mark.asyncio
async def test_a_populated_server_is_stopped_through_the_popup_chain():
    """The stop announces itself in-game (one popup for a one-second warn time), then shuts down."""
    server = PowerServer("SRS-A", NODE, status=Status.RUNNING, players=1)
    node = PowerNode(NODE, [server])

    await ServerMaintenanceManager(node, warn_times=[1]).power_off([server], stop=True)

    assert server.calls == ["popup", "shutdown"], "the chain ran BEFORE the shutdown"
    assert server.status is Status.SHUTDOWN


@pytest.mark.asyncio
async def test_online_starts_nothing_at_all(audit_bot):
    """The whole point of the split: ``online`` clears flags and NOTHING else. Two servers that are down
    and unflagged are released to the scheduler, which is the only thing that starts a server."""
    one = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN)
    two = PowerServer("SRS-B", NODE, status=Status.SHUTDOWN)
    ctx = ctx_for([one, two], {NODE: PowerNode(NODE)})

    result = await call_action("bring_node_online", ctx, node_name=NODE)
    assert "2 server(s) that were down and not in maintenance are left for the scheduler to start" \
        in result.message
    assert one.calls == [] and two.calls == [], "the action starts nothing, synchronously or later"
    await asyncio.sleep(0.05)
    assert one.calls == [] and two.calls == [], "...and still nothing, once the loop has run"


# ----------------------------------------------------------------------- 7. the migration pins

def test_the_actions_carry_the_renamed_parameters():
    """The renamed parameters: ``maintenance`` in place of ``shutdown`` on offline; none on online."""
    import plugins.admin.actions as admin_actions

    offline = inspect.signature(admin_actions.take_node_offline).parameters
    assert "maintenance" in offline and offline["maintenance"].default is True
    assert "shutdown" not in offline
    online = inspect.signature(admin_actions.bring_node_online).parameters
    assert "startup" not in online


def test_the_discord_commands_hold_no_second_implementation():
    """``/node offline`` / ``/node online`` reach the SAME action functions -- and touch no flag."""
    source = (REPO / "plugins/admin/commands.py").read_text(encoding="utf-8")
    assert "from core.actions import ActionContext, call_action" in source
    assert re.search(r'call_action\(qualname, ctx, node_name=name', source)
    assert re.search(r'"take_node_offline"', source) and re.search(r'"bring_node_online"', source)
 # the old blanket body is gone: no flag write and no server stop/start in the commands
    assert "server.maintenance =" not in source
    assert "asyncio.create_task(server.shutdown())" not in source
    assert "asyncio.create_task(_startup(server))" not in source


# --------------------------------------------------- the federation window's own step-down record

class _WindowRegistry:
    """The registry double the window protocol reads and writes: dependents, the record, the count."""

    def __init__(self, dependents):
        self._dependents = dependents
        self.stepped_down: list[str] | None = None
        self.servers_up: int | None = None

    async def dependents(self, resource_id, states=None):
        if states and "stepped_down" not in states:
            return []
        return list(self._dependents)

    async def set_dependents_state(self, resource_id, state, names):
        if state == "stepped_down":
            self.stepped_down = list(names)

    async def set_servers_up(self, resource_id, count):
        self.servers_up = count


class _WindowAck:
    def __init__(self):
        self.acked = False

    async def ack(self, resource_id):
        self.acked = True


class RefusingServer(PowerServer):
    """A server whose stop does not take effect — the taker's residue check must still see it as up."""

    async def shutdown(self):
        self.calls.append("shutdown")
 # status deliberately unchanged: the stop did not take


def _window(node, dependents):
    registry = _WindowRegistry(dependents)
    ack = _WindowAck()
    protocol = ResourceWindowProtocol(node, registry=registry, windows=ack)  # type: ignore[arg-type]
    return protocol, registry, ack


@pytest.mark.asyncio
async def test_a_server_that_was_already_down_is_not_recorded_as_stepped_down():
    """REPORTED: two servers were SHUTDOWN before ``/dcs update``, and the update started them back.

    ``step_down`` may only write down what IT stopped. ``restore`` starts exactly the recorded names, so an
    already-down server is started by the act of recording it — which is the reported defect.
    """
    already_down = PowerServer("Alpha", NODE, status=Status.SHUTDOWN)
    running = PowerServer("Beta", NODE, status=Status.RUNNING)
    protocol, registry, ack = _window(PowerNode(NODE, [already_down, running]),
                                      [("server", "Alpha", "in_service"),
                                       ("server", "Beta", "in_service")])

    stopped = await protocol.step_down("resource-1", "server", [0])

    assert stopped == 1
    assert registry.stepped_down == ["Beta"], \
        "only the server this operation stopped may be recorded — Alpha was already down"
    assert already_down.calls == [], "an already-down server is not stopped, flagged or touched"
    assert already_down.status == Status.SHUTDOWN, "and it stays down"
    assert running.calls == ["shutdown"], "the running one is stopped by the window"
    assert registry.servers_up == 0, "nothing of ours is up while the window holds"
    assert ack.acked is True


@pytest.mark.asyncio
async def test_a_server_that_refuses_to_stop_is_still_published_as_up():
    """The residue count stays the POST-state one: a stop that did not take effect must remain visible."""
    already_down = PowerServer("Alpha", NODE, status=Status.SHUTDOWN)
    refusing = RefusingServer("Beta", NODE, status=Status.RUNNING)
    protocol, registry, _ack = _window(PowerNode(NODE, [already_down, refusing]),
                                       [("server", "Alpha", "in_service"),
                                        ("server", "Beta", "in_service")])

    await protocol.step_down("resource-1", "server", [0])

    assert refusing.calls == ["shutdown"]
    assert registry.servers_up == 1, "a server that refused to stop is exactly what the taker has to see"
    assert registry.stepped_down == [], \
        "a stop that did not take is not a step-down: restore() must not be told to bring it back"


# ------------------------------------------------------------- the forced clear (``clear_all``)

class UpgradeNode(PowerNode):
    """A node double that answers the upgrade checks as the node object does.

    ``PowerNode`` has neither method, which is what keeps the power pair away from the check; this
    double is what gives the upgrade action something to refuse on.
    """

    def __init__(self, name, servers=(), *, pending=True, major=False):
        super().__init__(name, servers)
        self.pending = pending
        self.major = major
        self.calls: list[str] = []

    async def upgrade_pending(self) -> bool:
        self.calls.append("upgrade_pending")
        return self.pending

    async def upgrade_is_major(self) -> bool:
        self.calls.append("upgrade_is_major")
        return self.major

    async def upgrade(self):
        self.calls.append("upgrade")


@pytest.mark.asyncio
async def test_a_forced_clear_clears_a_hand_set_flag_and_releases_the_server(audit_bot):
    """``clear_all`` is the ONE path that clears a flag this process never set.

    Both servers were flagged by hand and are down — the state a restart between the halves leaves
    behind — and neither is in a record. The forced clear empties both flags and releases both to the
    scheduler, and the report says ``(forced)`` so it cannot be mistaken for the ordinary revert.
    """
    a = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN, maintenance=True)
    b = PowerServer("SRS-B", NODE, status=Status.SHUTDOWN, maintenance=True)
    ctx = ctx_for([a, b], {NODE: PowerNode(NODE)})

    result = await call_action("bring_node_online", ctx, node_name=NODE, clear_all=True)

    assert result.success is True
    assert a.maintenance is False and b.maintenance is False, "the forced clear empties EVERY flag"
    assert a.calls == [] and b.calls == [], "flags only: the scheduler starts them"
    assert result.message == ("Node 'dcs-a-01': 2 maintenance flag(s) cleared (forced), "
                              "2 server(s) left for the scheduler to start.")
    assert messages(audit_bot) == ["" + result.message]


@pytest.mark.asyncio
async def test_a_forced_clear_leaves_a_running_server_alone(audit_bot):
    """A server already up is not started by the forced clear, and is reported as left alone."""
    up = PowerServer("SRS-UP", NODE, status=Status.RUNNING, maintenance=True)
    down = PowerServer("SRS-DOWN", NODE, status=Status.SHUTDOWN, maintenance=True)
    ctx = ctx_for([up, down], {NODE: PowerNode(NODE)})

    result = await call_action("bring_node_online", ctx, node_name=NODE, clear_all=True)

    assert up.maintenance is False and up.calls == [], "its flag goes, its process does not move"
    assert down.maintenance is False and down.calls == [], "released, never started here"
    assert result.message == ("Node 'dcs-a-01': 2 maintenance flag(s) cleared (forced), "
                              "1 server(s) left for the scheduler to start, "
                              "1 left alone (already running).")


# --------------------------------------------------- the major-upgrade guard (the upgrade action)

@pytest.mark.asyncio
async def test_a_major_upgrade_is_refused_while_servers_are_in_service(audit_bot):
    """The action refuses with the instruction, so no transport can skip the offline step."""
    running = PowerServer("SRS-A", NODE, status=Status.RUNNING)
    node = UpgradeNode(NODE, [running], major=True)
    ctx = ctx_for([running], {NODE: node})

    result = await call_action("upgrade_node", ctx, node_name=NODE)

    assert result.success is False
    assert result.message == ("Node 'dcs-a-01': 1 server(s) are in service and this is a major "
                              "upgrade — DCS has to be down while the bot updates. Take the node "
                              "offline first.")
    assert node.calls == ["upgrade_pending", "upgrade_is_major"], "the node was NOT told to update"


@pytest.mark.asyncio
async def test_a_major_upgrade_proceeds_once_nothing_is_in_service(audit_bot):
    """With DCS down the same major upgrade runs — what ``/node upgrade`` does after the offline."""
    stopped = PowerServer("SRS-A", NODE, status=Status.SHUTDOWN, maintenance=True)
    node = UpgradeNode(NODE, [stopped], major=True)
    ctx = ctx_for([stopped], {NODE: node})

    result = await call_action("upgrade_node", ctx, node_name=NODE)

    assert result.success is True
    assert node.calls == ["upgrade_pending", "upgrade_is_major", "upgrade"]


@pytest.mark.asyncio
async def test_a_hotfix_upgrade_needs_no_offline_step(audit_bot):
    """A non-major update changes no hook version, so a running server does not block it."""
    running = PowerServer("SRS-A", NODE, status=Status.RUNNING)
    node = UpgradeNode(NODE, [running], major=False)
    ctx = ctx_for([running], {NODE: node})

    result = await call_action("upgrade_node", ctx, node_name=NODE)

    assert result.success is True
    assert node.calls == ["upgrade_pending", "upgrade_is_major", "upgrade"]
