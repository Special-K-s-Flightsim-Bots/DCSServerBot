"""The start gate: a server must not start while another cluster is taking its installation down.

The gate sits at the very top of ``ServerImpl.startup()`` so every start path inherits it — cron, the scheduler,
voting, the tournament plugin, the mission actions, and the maintenance manager's own power-on. That placement is the
point: spread across the call sites, the one that gets missed is the restore.

``ServerImpl`` is exercised unbound against a double, the way the port probe is: the gate reads only the installation,
the node and the log, so no bot, no DCS and no database is needed.
"""
from __future__ import annotations

import types

import pytest

from core import Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import ResourceInUseError, ServerImpl
from core.data.resource_window import OpenWindow

INSTALLATION = 'C:/Program Files/Eagle Dynamics/DCS World Testing'


class _Log:
    def __init__(self):
        self.lines = []

    def warning(self, msg, *args, **kwargs):
        self.lines.append(msg % args if args else msg)


class _Node:
    def __init__(self, installation: str | None = INSTALLATION):
        self.installation = installation
        self.guild_id = 42
        self.name = 'node-1'
        self.locals = {}
        self.cpool = object()        # only handed to ResourceWindows, never used in these tests
        self.log = _Log()


def _server(installation: str | None = INSTALLATION):
    node = _Node(installation=installation)
 # SHUTDOWN: the state a launch actually starts from — a server that is already up returns before the gate
 # (a duplicate start is a no-op), so testing the gate's ORDER needs the status a launch really has.
    return types.SimpleNamespace(node=node, log=node.log, status=Status.SHUTDOWN), node


def _identity_stub(resource_id='resource-1'):
    """The identity factory, without touching a real installation — and it refuses a blank path, like the real one."""
    class _Identity:
        @staticmethod
        def of(path, resource_type, **_kwargs):
            if not str(path).strip():
                raise ValueError("A resource path must not be empty.")
            return types.SimpleNamespace(id=resource_id, path=str(path).strip(), resource_type=resource_type)

    return types.SimpleNamespace(ResourceIdentity=_Identity)


@pytest.fixture
def window(monkeypatch):
    """Give the gate an identity and a window row, with no database behind it."""
    monkeypatch.setattr(serverimpl, 'utils', _identity_stub())
    state: dict = {}

    async def _state(self, resource_id):
        return state.get('window')

    monkeypatch.setattr(serverimpl.ResourceWindows, 'state', _state)
    return state


# ------------------------------------------------------------------ refusing

@pytest.mark.asyncio
async def test_a_live_window_refuses_the_start_and_says_why(window):
    window['window'] = OpenWindow(42, 'node-1', 'update', 'server', True)
    server, _node = _server()
    with pytest.raises(ResourceInUseError) as ex:
        await ServerImpl._assert_resource_available(server)
    message = str(ex.value)
    assert 'node-1' in message        # who holds it
    assert '42' in message            # which guild
    assert 'update' in message        # what it is for
    assert INSTALLATION in message    # which resource


# ------------------------------------------------------------------ the deliberate non-refusals

@pytest.mark.asyncio
async def test_no_window_starts_normally(window):
    server, _node = _server()
    await ServerImpl._assert_resource_available(server)


@pytest.mark.asyncio
async def test_a_window_whose_holder_died_does_not_refuse(window):
    """Same predicate as the restore rule: a taker that died must not keep every other cluster dark."""
    window['window'] = OpenWindow(42, 'gone', 'update', 'server', False)      # holder_live False
    server, _node = _server()
    await ServerImpl._assert_resource_available(server)


@pytest.mark.asyncio
async def test_an_unidentifiable_installation_does_not_stop_the_server(window):
    """This is a guard, not the correctness guarantee: a broken identity must never be what keeps servers down."""
    server, node = _server(installation='   ')
    await ServerImpl._assert_resource_available(server)
    assert node.log.lines, 'the non-refusal has to be visible in the log'
    assert '   ' in node.log.lines[0]


@pytest.mark.asyncio
async def test_no_installation_is_not_a_refusal(window):
    server, _node = _server(installation=None)
    await ServerImpl._assert_resource_available(server)


# ------------------------------------------------------------------ where it sits

@pytest.mark.asyncio
async def test_the_gate_runs_before_the_port_check(window, monkeypatch):
    """A window is the more specific reason, and it must be reached before startup touches anything at all."""
    called = []
    window['window'] = OpenWindow(42, 'node-1', 'update', 'server', True)
    server, _node = _server()
 # both are bound on the double, as startup calls them on itself
    server._assert_resource_available = types.MethodType(ServerImpl._assert_resource_available, server)
    server._assert_ports_free = types.MethodType(lambda self: called.append('ports'), server)
    with pytest.raises(ResourceInUseError):
        await ServerImpl.startup(server)
    assert called == []
