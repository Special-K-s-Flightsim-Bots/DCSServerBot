"""The follower: what a node does when somebody else takes the installation it runs on.

Two rules carry the whole thing, and both are pinned here.

* **The decision** (``follower_action``) is read from the CURRENT window every time, and a window counts only while
  its holder is alive. A taker that died leaves its row behind forever, and a new taker can open a new window while the
  old followers are still dark — so a follower that remembered instead of re-reading would either stay down for ever or
  get up into an update.
* **What it stopped is recorded in the table, not in memory.** The window is most likely to be open while this node is
  rebooting — that is what an update does — so the restore has to find its list in the database.

The stopping itself is the maintenance manager's and is stubbed here; its own semantics are pinned in its own tests.
"""
from __future__ import annotations

import types

import pytest

from core.data import resource_window
from core.data.resource_window import OpenWindow, ResourceWindowProtocol, follower_action

RESOURCE = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'


# --------------------------------------------------------------------------------------- doubles

def servers(*specs):
    """``(name, stops)`` pairs, as the objects the protocol names and the manager stops."""
    return [types.SimpleNamespace(name=name, in_service=True, stops=stops) for name, stops in specs]


class _FakeManager:
    """The maintenance manager's surface, and a record of what it was asked to do."""

    servers: list = []
    calls: list = []

    def __init__(self, node, *, message=None, **_kwargs):
        self.message = message
        _FakeManager.calls.append(('init', message))

    def node_servers(self):
        return list(_FakeManager.servers)

    @staticmethod
    def in_service(server) -> bool:
        return bool(server.in_service)

    async def power_off(self, servers, **kwargs):
        _FakeManager.calls.append(('power_off', kwargs, [s.name for s in servers]))
        for server in servers:
            if server.stops:
                server.in_service = False
        return types.SimpleNamespace(stopped=list(servers), handled=list(servers))

    async def power_on(self, servers, **kwargs):
        _FakeManager.calls.append(('power_on', kwargs, [s.name for s in servers]))
        started = [s for s in servers if not s.in_service]
        for server in started:
            server.in_service = True
        return types.SimpleNamespace(started=started, skipped=[s for s in servers if s not in started], cleared=[])


class _Registry:
    def __init__(self, dependents=(), counts=(0,)):
        self.dependents_rows = list(dependents)
        self.counts = list(counts)
        self.state_calls: list = []
        self.up_calls: list = []
        self.dependents_calls: list = []

    async def dependents(self, resource_id, **kwargs):
        self.dependents_calls.append((resource_id, kwargs))
        if kwargs.get('states') == ('stepped_down',):
            return [row for row in self.dependents_rows if row[2] == 'stepped_down']
        return list(self.dependents_rows)

    async def set_dependents_state(self, resource_id, state, names):
        self.state_calls.append((state, set(names)))

    async def set_servers_up(self, resource_id, servers_up):
        self.up_calls.append(servers_up)

    async def in_service_count(self, resource_id):
        return self.counts.pop(0) if len(self.counts) > 1 else self.counts[0]


class _Windows:
    def __init__(self, window=None, acked=False):
        self.acks: list = []
        self.acked_flag = acked
        self.window = window

    async def state(self, resource_id):
        return self.window

    async def ack(self, resource_id):
        self.acks.append(resource_id)
        self.acked_flag = True          # the row the real one writes, and what the next cycle reads

    async def unack(self, resource_id):
        self.acked_flag = False

    async def acked(self, resource_id):
        return self.acked_flag


class _Log:
    def __init__(self):
        self.lines: list = []

    def _record(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._record('info', msg, *args)

    def warning(self, msg, *args):
        self._record('warning', msg, *args)


class _Node:
    def __init__(self):
        self.log = _Log()
        self.name = 'node-1'
        self.guild_id = 42
        self.locals = {}


@pytest.fixture
def manager(monkeypatch):
    _FakeManager.calls = []
    _FakeManager.servers = []
    monkeypatch.setattr(resource_window, 'ServerMaintenanceManager', _FakeManager)
    return _FakeManager


@pytest.fixture
def fast_poll(monkeypatch):
    """The wait loop is not what is under test — do not spend its interval in a test."""
    monkeypatch.setattr(resource_window, 'POLL_INTERVAL', 0)


def protocol(registry=None, windows=None):
    return ResourceWindowProtocol(_Node(), registry=registry or _Registry(), windows=windows or _Windows())


def manager_calls(manager_):
    return [call for call in manager_.calls if call[0] != 'init']


# ----------------------------------------------------------------------------------- the decision

@pytest.mark.parametrize('window, stepped_down, expected', [
    (OpenWindow(42, 'node-1', 'update', 'server', True), False, 'step_down'),
    (OpenWindow(42, 'node-1', 'update', 'server', True), True, 'idle'),
    (OpenWindow(42, 'gone', 'update', 'server', False), True, 'restore'),
    (OpenWindow(42, 'gone', 'update', 'server', False), False, 'idle'),
    (None, True, 'restore'),
    (None, False, 'idle'),
])
def test_what_a_follower_does(window, stepped_down, expected):
    assert follower_action(window, stepped_down) == expected


def test_a_dead_holders_window_does_not_keep_a_node_down():
    """The case the rule exists for: the row outlives the session that wrote it."""
    assert follower_action(OpenWindow(42, 'gone', 'update', 'server', False), stepped_down=True) == 'restore'


# --------------------------------------------------------------------------------- stepping down

@pytest.mark.asyncio
async def test_stepping_down_stops_only_this_nodes_dependents(manager):
    manager.servers = servers(('SRS-1', True), ('SRS-2', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service'), ('server', 'SRS-2', 'in_service')])
    assert await protocol(registry=registry).step_down(RESOURCE) == 2
    assert manager_calls(manager)[0] == ('power_off', {'flag': False, 'stop': True}, ['SRS-1', 'SRS-2'])


@pytest.mark.asyncio
async def test_stepping_down_never_sets_the_maintenance_flag(manager):
    """The start gate is the guard; a second guard would need ownership tracking across a reboot to clear."""
    manager.servers = servers(('SRS-1', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    await protocol(registry=registry).step_down(RESOURCE)
    assert manager_calls(manager)[0][1]['flag'] is False


@pytest.mark.asyncio
async def test_a_server_that_refused_to_stop_is_published_as_still_running(manager):
    """A hopeful 0 here is what would let an update run under a live server."""
    manager.servers = servers(('SRS-1', True), ('SRS-2', False))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service'), ('server', 'SRS-2', 'in_service')])
    windows = _Windows()
    left = await protocol(registry=registry, windows=windows).step_down(RESOURCE)
    assert left == 1                                   # only the one that actually went down
    assert registry.up_calls[-1] == 1                  # ... and the taker can see the other
    assert registry.state_calls[-1] == ('stepped_down', {'SRS-1'})
    assert windows.acks == [RESOURCE]                  # it still answers: it did what it could


@pytest.mark.asyncio
async def test_stepping_down_with_nothing_in_scope_publishes_zero_and_acks(manager):
    manager.servers = servers(('SRS-9', True))         # not a dependent of this resource
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    windows = _Windows()
    await protocol(registry=registry, windows=windows).step_down(RESOURCE)
    assert manager_calls(manager) == []                # nothing was stopped
    assert registry.up_calls == [0]
    assert windows.acks == [RESOURCE]


@pytest.mark.asyncio
async def test_a_kind_that_is_not_implemented_steps_nothing_down(manager):
    """Typed from day one is not implemented from day one — and the log has to say so."""
    manager.servers = servers(('SRS-1', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    windows = _Windows()
    subject = protocol(registry=registry, windows=windows)
    assert await subject.step_down(RESOURCE, scope='service') == 0
    assert manager_calls(manager) == [] and registry.up_calls == [] and windows.acks == []
    assert any('not implemented' in line for _level, line in subject.log.lines)


# ------------------------------------------------------------------------------------ restoring

@pytest.mark.asyncio
async def test_restoring_reads_the_table_and_starts_exactly_what_was_stopped(manager):
    manager.servers = servers(('SRS-1', True), ('SRS-2', False))
    manager.servers[0].in_service = False              # SRS-1 stepped down; SRS-2 was never stopped
    registry = _Registry(dependents=[('server', 'SRS-1', 'stepped_down'), ('server', 'SRS-2', 'in_service')])
    assert await protocol(registry=registry).restore(RESOURCE) == 1
    call = manager_calls(manager)[0]
    assert call[0] == 'power_on'
    assert [server.name for server in call[1]['start']] == ['SRS-1']      # never the one it did not stop
    assert registry.dependents_calls[0][1] == {'states': ('stepped_down',)}
    assert registry.state_calls[-1] == ('in_service', {'SRS-1'})


@pytest.mark.asyncio
async def test_restoring_never_clears_a_maintenance_flag(manager):
    """This operation set none, so none is its to clear — a hand-set flag keeps its server down."""
    manager.servers = servers(('SRS-1', True))
    manager.servers[0].in_service = False
    registry = _Registry(dependents=[('server', 'SRS-1', 'stepped_down')])
    await protocol(registry=registry).restore(RESOURCE)
    assert 'clear' not in manager_calls(manager)[0][1]


@pytest.mark.asyncio
async def test_restoring_republishes_what_is_running_now(manager):
    manager.servers = servers(('SRS-1', True), ('SRS-2', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'stepped_down'), ('server', 'SRS-2', 'stepped_down')])
    await protocol(registry=registry).restore(RESOURCE)
    assert registry.up_calls[-1] == 2


# ------------------------------------------------------------------------------------- waiting

@pytest.mark.asyncio
async def test_waiting_returns_immediately_when_nothing_is_left(fast_poll):
    registry = _Registry(counts=[0])
    assert await protocol(registry=registry).wait_for_clear(RESOURCE, timeout=30) == 0


@pytest.mark.asyncio
async def test_waiting_returns_the_residue_instead_of_waiting_it_out(fast_poll):
    """A residue is named, never silently waited out: the installer must not run, and the operator must know why."""
    registry = _Registry(counts=[2, 2, 2])
    assert await protocol(registry=registry).wait_for_clear(RESOURCE, timeout=0) == 2


@pytest.mark.asyncio
async def test_waiting_keeps_polling_until_the_members_are_down(fast_poll):
    registry = _Registry(counts=[2, 1, 0])
    assert await protocol(registry=registry).wait_for_clear(RESOURCE, timeout=30) == 0


# --------------------------------------------------------------------------------- the whole cycle

@pytest.mark.asyncio
async def test_a_cycle_steps_down_while_the_window_is_open(manager):
    """One read, one decision, one action — and the action matches the decision."""
    manager.servers = servers(('SRS-1', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    windows = _Windows(OpenWindow(42, 'node-a', 'update', 'server', True))
    subject = protocol(registry=registry, windows=windows)
    assert await subject.follow(RESOURCE) == 'step_down'
    assert manager_calls(manager)[0][0] == 'power_off'
    assert registry.state_calls[-1][0] == 'stepped_down'


@pytest.mark.asyncio
async def test_a_cycle_restores_once_the_window_is_gone(manager):
    manager.servers = servers(('SRS-1', True))
    manager.servers[0].in_service = False
    registry = _Registry(dependents=[('server', 'SRS-1', 'stepped_down')])
    subject = protocol(registry=registry, windows=_Windows(None, acked=True))
    assert await subject.follow(RESOURCE) == 'restore'
    assert manager_calls(manager)[0][0] == 'power_on'
    assert registry.state_calls[-1][0] == 'in_service'


@pytest.mark.asyncio
async def test_a_cycle_publishes_what_this_node_is_running(manager):
    """The count a taker sums is only trustworthy if every node says its own, every cycle — not only when a window
    opens. Before this, a node that had been running servers all along still read as 0."""
    manager.servers = servers(('SRS-1', True), ('SRS-2', True))
    manager.servers[1].in_service = False
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service'), ('server', 'SRS-2', 'in_service')])
    assert await protocol(registry=registry).follow(RESOURCE) == 'idle'
    assert registry.up_calls == [1]          # one of the two is up, and the node says so


@pytest.mark.asyncio
async def test_a_cycle_does_nothing_when_this_node_is_already_down(manager):
    """The steady state during a window: it must not stop the same servers twice, nor restore them."""
    manager.servers = servers(('SRS-1', True))
    manager.servers[0].in_service = False
    registry = _Registry(dependents=[('server', 'SRS-1', 'stepped_down')])
    windows = _Windows(OpenWindow(42, 'node-a', 'update', 'server', True), acked=True)
    subject = protocol(registry=registry, windows=windows)
    assert await subject.follow(RESOURCE) == 'idle'
    assert manager_calls(manager) == []      # no stop, no start
    assert registry.state_calls == []        # and no bookkeeping rewritten
    assert registry.up_calls == [0]          # it still publishes its honest count


@pytest.mark.asyncio
async def test_a_cycle_with_nothing_in_service_is_done_after_the_first_step_down(manager):
    """REPORTED: a node with nothing running re-ran the whole step-down once per cycle, for a whole update.

    The record it used as its "done" flag is empty BOTH before such a step-down and after it — there was nothing
    in service to record — so it never read as finished. The ack is what says the step-down is complete.
    """
    manager.servers = servers(('SRS-1', False))        # already down when the window opened
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    windows = _Windows(OpenWindow(42, 'node-a', 'update', 'server', True))
    subject = protocol(registry=registry, windows=windows)

    assert await subject.follow(RESOURCE) == 'step_down'
    assert registry.state_calls[-1] == ('stepped_down', set())     # nothing was this node's to stop
    assert windows.acks == [RESOURCE]                              # and it answered all the same

    assert await subject.follow(RESOURCE) == 'idle'
    assert [call[0] for call in manager_calls(manager)].count('power_off') == 1
    assert registry.state_calls == [('stepped_down', set())]       # no second round of bookkeeping


@pytest.mark.asyncio
async def test_a_cycle_does_nothing_when_there_is_nothing_to_do(manager):
    manager.servers = servers(('SRS-1', True))
    registry = _Registry(dependents=[('server', 'SRS-1', 'in_service')])
    subject = protocol(registry=registry, windows=_Windows(None))
    assert await subject.follow(RESOURCE) == 'idle'
    assert manager_calls(manager) == []
