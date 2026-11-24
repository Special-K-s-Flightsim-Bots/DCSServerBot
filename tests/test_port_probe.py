"""Port refusal: a taken port must be refused, and a false refusal must be impossible.

Two bots on one PC hand the same default ports to their instances, and the failure is silent: the
UDP sockets set ``SO_REUSEADDR``, so the second bind *succeeds* and one cluster then loses its
servers' messages (measured: two sockets bound the bot's way both bound; a datagram reached only
the second one). DCS's own ports fail late instead - the server simply never comes up.

The two protocols need different tests, and the platforms disagree about the flags, so the rule is
measured here on real sockets rather than asserted from the docs:

* UDP - a flagless bind: a holder that set ``SO_REUSEADDR`` still fails it, measured on Linux and on
  Windows (a TCP-only build of this check passed a live listener on Windows, which is the bug this
  file now pins).
* TCP - a *connection* test, not a bind. A bind answers wrong either way: **with** the flag it
  returns BOUND against a live listener on Windows, and **without** it, a port merely left in
  ``TIME_WAIT`` by the server that just stopped fails on Linux - refusing every quick restart. A
  listener answers a connection on both platforms and a ``TIME_WAIT`` socket refuses it.

On top of the probe, both refusals are exercised: the node's listener port (the node must not come
up at all) and an instance's ports (that one server must not start, and the node keeps running).
"""
from __future__ import annotations

import socket
import types

import pytest

from core import FatalException, Status
from core.data.impl import serverimpl
from core.data.impl.serverimpl import PortConflictError, ServerAlreadyRunningError, ServerImpl
from core.utils import network
from core.utils.network import is_port_free
from services.servicebus.service import ServiceBus

HOST = "127.0.0.1"


# ---------------------------------------------------------------------------------------- helpers

def free_port(*, udp: bool = True) -> int:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    s = socket.socket(socket.AF_INET, kind)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def udp_holder(port: int, host: str = HOST) -> socket.socket:
    """A UDP socket bound the way the service bus binds its listener."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    return s


def tcp_listener(port: int, host: str = HOST) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(1)
    return s


def _node(port: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(listen_address=HOST, listen_port=types.SimpleNamespace(port=port))


async def _constant(value: bool) -> bool:
    return value


class _Log:
    """Keeps the lines, so a test can tell a quiet no-op from a refusal."""

    def __init__(self):
        self.lines: list[str] = []

    def info(self, msg, *args):
        self.lines.append(msg % args if args else msg)


def _server(*, dcs_port: int, webgui_port: int, bot_port: int, own_process: bool = False,
            status=None) -> types.SimpleNamespace:
    """The slice of ``ServerImpl`` the port check reads - no constructor, no bot, no DCS."""
    return types.SimpleNamespace(
        name='WW2',
        instance=types.SimpleNamespace(
            dcs_host=HOST,
            name='WW2',
            dcs_port=types.SimpleNamespace(port=dcs_port),
            webgui_port=types.SimpleNamespace(port=webgui_port),
            bot_port=types.SimpleNamespace(port=bot_port),
        ),
        settings={},
        status=status if status is not None else Status.SHUTDOWN,
        log=_Log(),
        is_running=lambda: _constant(own_process),
    )


# ------------------------------------------------------------------------------------ the probe

def test_udp_probe_reports_a_reuseaddr_holder():
    """The case the flag-free probe exists for: the holder set SO_REUSEADDR itself."""
    port = free_port()
    with udp_holder(port):
        assert is_port_free(HOST, port, udp=True) is False


def test_tcp_probe_reports_a_listener():
    port = free_port(udp=False)
    with tcp_listener(port):
        assert is_port_free(HOST, port) is False


def test_tcp_probe_tolerates_time_wait_from_the_server_that_just_stopped():
    """A restart right after a shutdown must not be refused: the port is only in TIME_WAIT."""
    port = free_port(udp=False)
    listener = tcp_listener(port)
    client = socket.create_connection((HOST, port))
    conn, _ = listener.accept()
    conn.close()            # server-initiated close leaves the listening port in TIME_WAIT
    client.close()
    listener.close()
    assert is_port_free(HOST, port) is True


def test_tcp_probe_reports_a_listener_bound_to_every_interface():
    """The cross-cluster shape: the other cluster's server holds the wildcard. A flagless wildcard
    bind would not have seen a loopback-only holder on Windows (measured: BOUND), which is why the
    verdict comes from a connection."""
    port = free_port(udp=False)
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("0.0.0.0", port))
    holder.listen(5)
    try:
        assert is_port_free("0.0.0.0", port) is False
    finally:
        holder.close()


def test_free_ports_report_free():
    port = free_port()
    assert is_port_free(HOST, port, udp=True) is True
    assert is_port_free(HOST, port) is True


def test_the_tcp_verdict_is_not_a_bind(monkeypatch):
    """The shipped bug in one line: on Windows a flag-set bind returns BOUND over a live listener, so
    the TCP verdict must not come from a bind at all. Pinned structurally, because Linux cannot
    reproduce the Windows result it was found by."""
    calls = []
    monkeypatch.setattr(network, "_can_bind", lambda *a, **kw: calls.append(a) or True)
    port = free_port(udp=False)
    with tcp_listener(port):
        assert network.is_port_free(HOST, port) is False
    assert calls == []


def test_the_probe_binds_the_address_it_is_given():
    """A holder on one address is found from the wildcard too - the probe binds what the component
    binds, so its verdict is the verdict of the real bind, not a reachability guess."""
    port = free_port()
    with udp_holder(port):
        assert is_port_free("0.0.0.0", port, udp=True) is False


# --------------------------------------------------------------------------- the node's listener

def test_node_refuses_to_start_when_its_listener_port_is_taken():
    port = free_port()
    bus = types.SimpleNamespace(node=_node(port))
    with udp_holder(port):
        with pytest.raises(FatalException) as ex:
            ServiceBus._assert_listen_port_free(bus)
    assert str(port) in str(ex.value)
    assert "listen_port" in str(ex.value)


def test_node_accepts_a_free_listener_port():
    bus = types.SimpleNamespace(node=_node(free_port()))
    ServiceBus._assert_listen_port_free(bus)


# ------------------------------------------------------------------------- an instance's ports

@pytest.mark.asyncio
async def test_server_refuses_when_its_dcs_port_is_taken():
    dcs = free_port()
    server = _server(dcs_port=dcs, webgui_port=free_port(udp=False), bot_port=free_port())
    with udp_holder(dcs):          # DCS binds its port for UDP as well as TCP
        with pytest.raises(PortConflictError) as ex:
            await ServerImpl._assert_ports_free(server)
    assert str(dcs) in str(ex.value)
    assert "nodes.yaml" in str(ex.value)


@pytest.mark.asyncio
async def test_server_refuses_when_its_webgui_port_is_taken():
    webgui = free_port(udp=False)
    server = _server(dcs_port=free_port(), webgui_port=webgui, bot_port=free_port())
    with tcp_listener(webgui):
        with pytest.raises(PortConflictError) as ex:
            await ServerImpl._assert_ports_free(server)
    assert str(webgui) in str(ex.value)


@pytest.mark.asyncio
async def test_server_accepts_its_ports_when_they_are_free():
    server = _server(dcs_port=free_port(), webgui_port=free_port(udp=False), bot_port=free_port())
    await ServerImpl._assert_ports_free(server)


@pytest.mark.asyncio
async def test_a_server_without_an_instance_is_not_refused():
    await ServerImpl._assert_ports_free(types.SimpleNamespace(instance=None, settings={}))


@pytest.mark.asyncio
async def test_startup_itself_refuses_a_taken_port():
    """The refusal must be in the path a start actually takes, and must come before anything is
    touched - the double has no node, no pool and no extensions, so anything reaching past the port
    check would blow up instead of returning this error."""
    dcs = free_port()
    server = _server(dcs_port=dcs, webgui_port=free_port(udp=False), bot_port=free_port())
    server._assert_ports_free = types.MethodType(ServerImpl._assert_ports_free, server)
 # startup first asks whether the installation is taken for a maintenance window; a server with no
 # installation configured has nothing to ask about, so the gate returns before it touches anything
    server.node = types.SimpleNamespace(installation=None)
    server._assert_resource_available = types.MethodType(ServerImpl._assert_resource_available, server)
    with udp_holder(dcs):
        with pytest.raises(PortConflictError):
            await ServerImpl.startup(server)


# --------------------------------- our own process is reported at once, never waited for

@pytest.mark.asyncio
async def test_a_process_of_ours_while_the_bot_says_down_is_reported_at_once(monkeypatch):
    """A start request must not contain a shutdown wait. When our own process holds the port but the bot has
    the server as down, state and reality disagree - a crash that has not exited, or a DCS left behind by an
    earlier bot session - and the operator needs to hear that immediately, not after a stall."""
    dcs = free_port()
    server = _server(dcs_port=dcs, webgui_port=free_port(udp=False), bot_port=free_port(),
                     own_process=True, status=Status.SHUTDOWN)
    probes = []

    def held(host, port, *, udp=False):
        probes.append(port)
        return port != dcs

    monkeypatch.setattr(serverimpl.utils, 'is_port_free', held)
    with pytest.raises(ServerAlreadyRunningError) as ex:
        await ServerImpl._assert_ports_free(server)
    assert len(probes) == 1, 'one probe: nothing is waited for'
    assert 'Shutdown' in str(ex.value)                 # it says which status the bot believed
    assert str(dcs) in str(ex.value)                   # and names the port
    assert 'another DCS server' not in str(ex.value)   # without dressing it up as somebody else's conflict


@pytest.mark.asyncio
async def test_a_stranger_on_the_port_is_refused_at_once_when_no_process_of_ours_is_alive(monkeypatch):
    """The case the check exists for: somebody else holds the port — and it is still reported immediately."""
    dcs = free_port()
    server = _server(dcs_port=dcs, webgui_port=free_port(udp=False), bot_port=free_port())
    probes = []

    def held(host, port, *, udp=False):
        probes.append(port)
        return port != dcs

    monkeypatch.setattr(serverimpl.utils, 'is_port_free', held)
    with pytest.raises(PortConflictError) as ex:
        await ServerImpl._assert_ports_free(server)
    assert len(probes) == 1
    assert 'Another DCS server' in str(ex.value) and str(dcs) in str(ex.value)


@pytest.mark.parametrize('status', [Status.LOADING, Status.RUNNING, Status.PAUSED])
@pytest.mark.asyncio
async def test_a_duplicate_start_while_the_server_is_up_is_a_no_op(status):
    """The boot sequence, the state loop, a node coming back online and an operator's own command can all ask
    for the same server within seconds, and the second ask finds it already up or coming up. It must do
    nothing and say so: not raise, and not reach the gates.

    The double has no node, no pool and no extensions, so anything getting past the guard would blow up -
    reaching the end quietly IS the assertion.
    """
    server = _server(dcs_port=free_port(), webgui_port=free_port(udp=False), bot_port=free_port(),
                     status=status, own_process=True)

    async def explode(*args, **kwargs):
        raise AssertionError('a duplicate start must not run the startup gates')

    server._assert_resource_available = explode
    server._assert_ports_free = explode
    await ServerImpl.startup(server)
    assert any(status.value in line and 'ignoring this start request' in line for line in server.log.lines)


@pytest.mark.parametrize('status', [Status.LOADING, Status.RUNNING, Status.PAUSED])
@pytest.mark.asyncio
async def test_the_schedulers_own_launch_is_never_mistaken_for_a_duplicate(status):
    """The state loop sets ``status = LOADING`` and THEN schedules the launch that starts the server, so when
    ``startup()`` runs the status already reads Loading with NO process alive yet. It must go through the
    gates: a duplicate is a start request whose OWN PROCESS is already up, nothing less. A status-only test
    skipped this very launch and left every scheduled server sitting in LOADING."""
    reached = []

    async def record(*args, **kwargs):
        reached.append(True)

    server = _server(dcs_port=free_port(), webgui_port=free_port(udp=False), bot_port=free_port(),
                     status=status, own_process=False)
    server._assert_resource_available = record
    server._assert_ports_free = record
 # Anything past the gates needs doublings this test does not build (a node, extensions, a DCS install),
 # so it is expected to blow up there - reaching the gates at all is the assertion.
    with pytest.raises(Exception):
        await ServerImpl.startup(server)
    assert reached, 'nothing is alive, so this start must not be skipped as a duplicate'


@pytest.mark.asyncio
async def test_a_stopped_server_whose_process_is_alive_is_refused_at_once(monkeypatch):
    """Stopped is the one status the duplicate-start no-op does not cover: a cold start would put a second DCS
    process on the port, so it is refused at once - with the hint that fits a stopped server.

    ``len(probes) == 1`` is the point — one probe, and no waiting for anything."""
    status = Status.STOPPED
    dcs = free_port()
    server = _server(dcs_port=dcs, webgui_port=free_port(udp=False), bot_port=free_port(),
                     own_process=True, status=status)
    probes = []

    def held(host, port, *, udp=False):
        probes.append(port)
        return port != dcs

    monkeypatch.setattr(serverimpl.utils, 'is_port_free', held)
    with pytest.raises(ServerAlreadyRunningError) as ex:
        await ServerImpl._assert_ports_free(server)
    assert len(probes) == 1, 'nothing to wait for here'
    assert status.value in str(ex.value) and str(dcs) in str(ex.value)
    assert '/server start' in str(ex.value)


@pytest.mark.asyncio
async def test_a_free_port_never_asks_whether_our_process_is_alive():
    """No conflict means no process search: the check costs nothing on the path that starts servers."""
    async def explode():
        raise AssertionError("is_running must not be called when nothing is held")

    server = _server(dcs_port=free_port(), webgui_port=free_port(udp=False), bot_port=free_port())
    server.is_running = explode
    await ServerImpl._assert_ports_free(server)

