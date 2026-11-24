"""The registration handshake reconciles a server status; it never issues one.

A node that registers a server reports the status it holds. A freshly created node-side server reports
``Unregistered``, which means "not known yet" rather than a measurement, and it must not replace a
status that is already known on the other side. Neither may the handshake push the master's copy back
down to the answering node: only a real change is published, so a wrong value landing on one side
stays there until a restart.

Pinned here, through the real ``Server.set_status``/``Server.maintenance`` code:

* ``ServiceBus.init_remote_server`` with ``Unregistered`` leaves a proxy holding ``Shutdown`` alone and
  emits no RPC for that server; a concrete status is still applied.
* ``ServiceBus.rpc`` (the ``params`` branch) does not lower a known status to ``Unregistered``; a
  concrete value is still applied.
* ``ServiceBus.unregister_remote_node`` stays authoritative: it sets ``Unregistered`` and drops the
  entry.

Not covered: no real cluster and no wire — the bus, the nodes and the proxies are doubles, so the
end-to-end node/master exchange remains the operator's own check.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pytest

from core import Status
from core.data.server import Server
from services.servicebus.service import ServiceBus


# --------------------------------------------------------------------------------------------- doubles


class _Node:
    """A node double: the pieces the handshake and the RPC entry read."""

    def __init__(self, name: str, *, master: bool = False):
        self.name = name
        self.master = master
        self.instances: dict = {}
        self.all_nodes: dict = {name: self}


class _Bus:
    """The transport double: every ``send_to_node`` call is recorded, nothing leaves the process."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.sent: list[tuple[str | None, dict]] = []

    async def send_to_node(self, data: dict, *, node=None):
        self.sent.append((node, data))

    def rpcs_for(self, server_name: str) -> list[dict]:
        return [data for _, data in self.sent if data.get('server_name') == server_name]


class _UdpServer:
    def __init__(self):
        self.message_queue: dict = {}


class _Proxy:
    """A ``ServerProxy`` double carrying the REAL status and maintenance code paths."""

    status = Server.status
    set_status = Server.set_status
    reconcile_status = Server.reconcile_status
    maintenance = Server.maintenance
    set_maintenance = Server.set_maintenance

    def __init__(self, name: str, node: _Node, bus: _Bus, *, status: Status = Status.UNREGISTERED,
                 maintenance: bool = False):
        self.name = name
        self.node = node
        self.bus = bus
        self.is_remote = True
        self._status = status
        self._maintenance = maintenance
        self.status_change = asyncio.Event()
        self.last_seen = datetime.now(timezone.utc)
        self.settings: dict = {}
        self.options: dict = {}
        self.locals: dict = {}
        self.instance = None
        self.channels: dict = {}


class _MasterBus:
    """A bare ``ServiceBus`` (the real class) carrying only the state the three paths read."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.bus = ServiceBus.__new__(ServiceBus)
        self.bus.node = _Node("MASTER", master=True)
        self.bus.servers = {}
        self.bus.udp_server = _UdpServer()
        self.bus.log = logging.getLogger("test.cluster_handshake_status")
        self.wire = _Bus(loop)


def _register_proxy(bus: _MasterBus, remote: _Node, name: str, status: Status) -> _Proxy:
    proxy = _Proxy(name, remote, bus.wire, status=status)
    bus.bus.servers[name] = proxy
    bus.bus.udp_server.message_queue[name] = asyncio.Queue()
    return proxy


async def _init_remote_server(bus: _MasterBus, remote: _Node, name: str, status: str) -> None:
    await bus.bus.init_remote_server(
        server_name=name,
        status=status,
        instance="Host-A",
        home="/dcs/host-a",
        settings={},
        options={},
        node=remote,
        channels={},
        dcs_port=10308,
        webgui_port=8088,
        maintenance=False,
        config={},
    )
    # let any fire-and-forget publication run
    await asyncio.sleep(0)


# --------------------------------------------------------------------------------------------- pins


@pytest.mark.asyncio
async def test_handshake_with_unregistered_keeps_a_known_status_and_stays_silent():
    """ACCEPTANCE — the handshake does not lower ``Shutdown`` and issues no order back."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    remote = _Node("DCS-A")
    proxy = _register_proxy(bus, remote, "SRS-1", Status.SHUTDOWN)

    await _init_remote_server(bus, remote, "SRS-1", Status.UNREGISTERED.value)

    assert proxy.status == Status.SHUTDOWN, "Unregistered is 'not known yet', not a measurement"
    assert bus.wire.rpcs_for("SRS-1") == [], "the handshake must not publish a status back"


@pytest.mark.asyncio
async def test_set_status_still_publishes_one_order_and_a_no_op_publishes_none():
    """CONTROL — the ordinary path still publishes: one RPC per real change, none for a re-set.

    Every "no RPC" pin above would stay green if the publication were silenced altogether.
    """
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    remote = _Node("DCS-A")
    proxy = _register_proxy(bus, remote, "SRS-1", Status.SHUTDOWN)

    proxy.set_status(Status.RUNNING)
    await asyncio.sleep(0)

    sent = bus.wire.sent
    assert len(sent) == 1, sent
    node_name, message = sent[0]
    assert node_name == "DCS-A", "the order must address the node the server runs on"
    assert message == {
        "command": "rpc",
        "object": "Server",
        "server_name": "SRS-1",
        "params": {"status": Status.RUNNING.value},
    }, message
    assert proxy.status == Status.RUNNING

    proxy.set_status(Status.RUNNING)
    await asyncio.sleep(0)

    assert bus.wire.sent == sent, "a re-set of the same status must not publish"


@pytest.mark.asyncio
async def test_handshake_with_a_concrete_status_still_updates_the_proxy():
    """ACCEPTANCE — a status the answering node actually measured is applied."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    remote = _Node("DCS-A")
    proxy = _register_proxy(bus, remote, "SRS-1", Status.SHUTDOWN)

    await _init_remote_server(bus, remote, "SRS-1", Status.RUNNING.value)

    assert proxy.status == Status.RUNNING
    assert bus.wire.rpcs_for("SRS-1") == [], "reconciling the master's copy is not an order"


@pytest.mark.asyncio
async def test_handshake_applies_a_concrete_status_to_a_fresh_proxy():
    """A concrete status also lands on a proxy that has not learned anything yet."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    remote = _Node("DCS-A")
    proxy = _register_proxy(bus, remote, "SRS-1", Status.UNREGISTERED)

    await _init_remote_server(bus, remote, "SRS-1", Status.SHUTDOWN.value)

    assert proxy.status == Status.SHUTDOWN


@pytest.mark.asyncio
async def test_inbound_unregistered_params_does_not_lower_the_answering_side():
    """ACCEPTANCE — the answering side's own status wins over an inbound ``Unregistered``."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    proxy = _register_proxy(bus, _Node("DCS-A"), "SRS-1", Status.SHUTDOWN)

    await bus.bus.rpc(proxy, {"params": {"status": Status.UNREGISTERED.value}})

    assert proxy.status == Status.SHUTDOWN


@pytest.mark.asyncio
async def test_inbound_concrete_params_still_applies():
    """ACCEPTANCE — a concrete inbound value is still applied."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    proxy = _register_proxy(bus, _Node("DCS-A"), "SRS-1", Status.SHUTDOWN)

    await bus.bus.rpc(proxy, {"params": {"status": Status.RUNNING.value}})

    assert proxy.status == Status.RUNNING


@pytest.mark.asyncio
async def test_inbound_unregistered_params_still_lands_on_an_unregistered_object():
    """The exemption is narrow: an object that knows nothing is still written."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    proxy = _register_proxy(bus, _Node("DCS-A"), "SRS-1", Status.UNREGISTERED)

    await bus.bus.rpc(proxy, {"params": {"status": Status.UNREGISTERED.value}})

    assert proxy.status == Status.UNREGISTERED


@pytest.mark.asyncio
async def test_inbound_other_params_are_still_written():
    """The ``params`` branch is unchanged for every key other than ``status``."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    proxy = _register_proxy(bus, _Node("DCS-A"), "SRS-1", Status.SHUTDOWN)

    await bus.bus.rpc(proxy, {"params": {"num_players": 7}})

    assert proxy.num_players == 7


@pytest.mark.asyncio
async def test_unregister_remote_node_stays_authoritative():
    """ACCEPTANCE — the authoritative removal still sets ``Unregistered`` and drops the entry."""
    loop = asyncio.get_running_loop()
    bus = _MasterBus(loop)
    remote = _Node("DCS-A")
    proxy = _register_proxy(bus, remote, "SRS-1", Status.SHUTDOWN)
    bus.bus.node.all_nodes[remote.name] = remote

    await bus.bus.unregister_remote_node(remote)

    assert proxy.status == Status.UNREGISTERED
    assert "SRS-1" not in bus.bus.servers
    assert bus.bus.node.all_nodes[remote.name] is None
