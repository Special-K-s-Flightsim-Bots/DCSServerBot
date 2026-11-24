"""``Node.bind_server``: give an instance that carries NO server one, and name it.

``add_instance`` mints a NEW instance (its ports, its Saved Games directory, its ``nodes.yaml``
block), so it cannot serve an instance that ALREADY exists. ``bind_server`` is that operation's
other half: it keeps the instance's own ports and does ``add_instance``'s TAIL — a blank server on
the instance's ``bot_port``, the instance told it carries it, the server registered on the bus and
reported to the master — and then names it and writes its channels.

Pinned here, with a real ``NodeImpl`` whose collaborators are doubles:

* the instance keeps its OWN ``bot_port`` (nothing is minted) and its ``dcs_port`` reaches the new
  server's ``settings['port']`` — read from the instance's ``locals``, because ``instance.dcs_port``
  would resolve through the server being bound;
* the server is registered on the bus under the reserved blank name and ``send_init`` is called
  exactly when this node is NOT the master (an agent reports the new server to the master);
* the server is NAMED (``rename(name, update_settings=True)``) and its channels written afterwards;
* an instance that already carries a server is REFUSED, naming it; an unknown instance name is
  refused by name too, and neither refusal touches the instance registry;
* a rename that fails leaves NO half-state: the instance is unbound again and the blank server is
  dropped from the bus — and the same holds for a failed ``send_init``, so the retry that follows
  is not refused as "already carries server 'an unnamed server'".

What these tests do NOT cover: no real DCS install and no real cluster — ``ServiceRegistry``,
``DataObjectFactory`` and the instance are doubles, so "the directory really exists" and "a remote
agent really answers the RPC" are the operator's own checks.
"""
from __future__ import annotations

import logging
import types
from pathlib import Path

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.impl.serverimpl import ServerImpl

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_NODE = "dcs-a-01"
BLANK = "n/a"


# -------------------------------------------------------------------------------------- doubles

class _Bus:
    def __init__(self):
        self.servers: dict = {}
        self.sent: list = []
        self.fail_init: str = ""

    async def send_init(self, server):
        if self.fail_init:
            raise RuntimeError(self.fail_init)
        self.sent.append(server)


class _Registry:
    bus = _Bus()

    @classmethod
    def get(cls, t):
        return cls.bus


class _Factory:
    """``DataObjectFactory`` double: hands back the ONE server the test already holds."""

    def __init__(self, server):
        self._server = server
        self.calls: list[dict] = []

    def new(self, cls, **kwargs):
        self.calls.append(kwargs)
        self._server.port = kwargs.get("port")
        self._server.node = kwargs.get("node")
        self._server.bus = kwargs.get("bus")
        return self._server


class _FakeServer:
    """The blank server ``bind_server`` builds: what it is told, and what a failure looks like."""

    def __init__(self, *, fail_rename: str = "", status=None):
        self.name = BLANK
        self.settings: dict = {}
        self.instance = None
        self.status = status
        self.renamed: list[tuple[str, bool]] = []
        self.channels: list[dict] = []
        self.unlinked = 0
        self._fail_rename = fail_rename

    async def rename(self, new_name, update_settings=False):
        if self._fail_rename:
            # a refused or failed name changes NOTHING, exactly like ``ServerImpl.rename``'s guard
            raise ValueError(self._fail_rename)
        self.renamed.append((new_name, update_settings))
        self.name = new_name

    async def update_channels(self, channels):
        self.channels.append(dict(channels))

    async def unlink(self):
        self.unlinked += 1
        if self.instance is not None:
            self.instance.server = None


class _Instance:
    """An instance double: assigning ``server`` also tells the server whose it is, exactly as
    ``InstanceImpl.set_server`` does (so ``unlink`` can unbind it again)."""

    def __init__(self, name, *, bot_port=6666, dcs_port=10308, server=None):
        self.name = name
        self.bot_port = bot_port
        self.locals = {"bot_port": bot_port, "dcs_port": dcs_port}
        self._server = None
        self.server = server

    @property
    def server(self):
        return self._server

    @server.setter
    def server(self, value):
        self._server = value
        if value is not None:
            value.instance = self


def _instance(name, *, bot_port=6666, dcs_port=10308, server=None):
    return _Instance(name, bot_port=bot_port, dcs_port=dcs_port, server=server)


def _node(monkeypatch, *, instances=None, master=True):
    """A real ``NodeImpl`` in its local shape; the bind collaborators are doubles."""
    bus = _Bus()
    _Registry.bus = bus
    monkeypatch.setattr(nodeimpl, "ServiceRegistry", _Registry)
    node = object.__new__(NodeImpl)
    node.name = TARGET_NODE
    node.config_dir = str(REPO_ROOT / "config")
    node._master = master
    node.locals = {}
    node.node = types.SimpleNamespace(name=TARGET_NODE, master=master)
    node.log = logging.getLogger("tests.node_bind_server")
    node.instances = dict(instances or {})
    return node


def _bind(monkeypatch, node, *, fail_rename="", status=None):
    """Wire a ``_Factory`` around ONE fake server and return ``(server, factory)``."""
    server = _FakeServer(fail_rename=fail_rename, status=status)
    factory = _Factory(server)
    monkeypatch.setattr(nodeimpl, "DataObjectFactory", lambda: factory)
    return server, factory


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. the bind: the instance's own port, the bus, the name and the channels

@pytest.mark.asyncio
async def test_bind_keeps_the_instances_own_port_and_names_the_server(monkeypatch):
    instance = _instance("Host-B", bot_port=7001, dcs_port=10350)
    node = _node(monkeypatch, instances={"Host-B": instance})
    server, factory = _bind(monkeypatch, node)

    await node.bind_server("Host-B", "SRS-2", {"status": 123, "chat": -1})

    assert factory.calls[0]["port"] == 7001, "the instance's OWN bot port, never a minted one"
    assert factory.calls[0]["name"] == BLANK, "the server is created under the reserved blank name"
    assert instance.server is server, "the instance must be told it carries the server"
    assert _Registry.bus.servers.get(BLANK) is server, "the bus must know the blank server"
    assert server.settings.get("port") == 10350, "the DCS port comes from the instance's locals"
    assert server.renamed == [("SRS-2", True)], "the server must be NAMED"
    assert server.channels == [{"status": 123, "chat": -1}]
    assert node.instances["Host-B"] is instance


@pytest.mark.asyncio
async def test_bind_writes_no_channels_when_none_are_stated(monkeypatch):
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance})
    server, _ = _bind(monkeypatch, node)

    await node.bind_server("Host-B", "SRS-2")

    assert server.renamed == [("SRS-2", True)]
    assert server.channels == [], "an unstated channel is not written at all"


@pytest.mark.asyncio
async def test_an_agent_reports_the_new_server_to_the_master(monkeypatch):
    """``send_init`` runs exactly when this node is NOT the master."""
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance}, master=False)
    server, _ = _bind(monkeypatch, node)

    await node.bind_server("Host-B", "SRS-2")

    assert _Registry.bus.sent == [server], "an agent hands the new server to the master"


@pytest.mark.asyncio
async def test_the_master_binds_without_reporting_to_itself(monkeypatch):
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance}, master=True)
    server, _ = _bind(monkeypatch, node)

    await node.bind_server("Host-B", "SRS-2")

    assert _Registry.bus.sent == [], "the master builds the server itself; no send_init"
    assert node.instances["Host-B"].server is server


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. the two refusals: a taken instance (naming it) and an unknown instance name

@pytest.mark.asyncio
async def test_a_taken_instance_is_refused_and_its_server_named(monkeypatch):
    bound = types.SimpleNamespace(name="SRS-1")
    instance = _instance("Host-C", server=bound)
    node = _node(monkeypatch, instances={"Host-C": instance})
    server, factory = _bind(monkeypatch, node)

    with pytest.raises(ValueError) as caught:
        await node.bind_server("Host-C", "SRS-9")

    assert "Host-C" in str(caught.value) and "SRS-1" in str(caught.value)
    assert factory.calls == [], "nothing may be built for a taken instance"
    assert instance.server is bound, "the instance keeps the server it already carries"
    assert _Registry.bus.servers == {}


@pytest.mark.asyncio
async def test_an_unknown_instance_name_is_refused(monkeypatch):
    node = _node(monkeypatch, instances={})
    server, factory = _bind(monkeypatch, node)

    with pytest.raises(ValueError) as caught:
        await node.bind_server("Nowhere", "SRS-2")

    assert "Nowhere" in str(caught.value) and TARGET_NODE in str(caught.value)
    assert factory.calls == [] and node.instances == {} and _Registry.bus.servers == {}


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. a failed rename leaves no half-state

@pytest.mark.asyncio
async def test_a_failed_rename_unbinds_the_instance_and_drops_the_blank_server(monkeypatch):
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance})
    server, _ = _bind(monkeypatch, node, fail_rename="reserved")

    with pytest.raises(ValueError):
        await node.bind_server("Host-B", "n/a")

    assert server.unlinked == 1, "the blank server is unlinked"
    assert instance.server is None, "the instance is left as it was found"
    assert _Registry.bus.servers == {}, "no blank server may survive on the bus"
    assert server.renamed == []


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 4. a failed bus registration leaves no half-state either

@pytest.mark.asyncio
async def test_a_failed_registration_unbinds_the_instance_and_drops_the_blank_server(monkeypatch):
    """``send_init`` is the call after the instance was told it carries the server: a failure there
    must unwind exactly like a failed rename, or the instance sticks on a blank ``n/a`` server."""
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance}, master=False)
    server, _ = _bind(monkeypatch, node)
    _Registry.bus.fail_init = "no route to the master"

    with pytest.raises(RuntimeError, match="no route to the master"):
        await node.bind_server("Host-B", "SRS-2")

    assert server.unlinked == 1, "the blank server is unlinked"
    assert instance.server is None, "the instance is left as it was found"
    assert _Registry.bus.servers == {}, "no blank server may survive on the bus"
    assert server.renamed == [], "the name is never written after the registration failed"
    assert instance.name in node.instances


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_registration_succeeds(monkeypatch):
    """The operator's retry: once the bus answers again, the instance is bindable — no
    ''already carries server 'an unnamed server''' refusal."""
    instance = _instance("Host-B")
    node = _node(monkeypatch, instances={"Host-B": instance}, master=False)
    failed, _ = _bind(monkeypatch, node)
    _Registry.bus.fail_init = "no route to the master"

    with pytest.raises(RuntimeError):
        await node.bind_server("Host-B", "SRS-2")

    _Registry.bus.fail_init = ""
    retried, factory = _bind(monkeypatch, node)
    await node.bind_server("Host-B", "SRS-2")

    assert retried is not failed
    assert instance.server is retried, "the retry binds the instance"
    assert retried.name == "SRS-2" and retried.renamed == [("SRS-2", True)]
    # the server is registered under the reserved blank name; re-keying it to the new name is
    # ``ServerImpl.rename``'s own business (via ``ServiceBus.rename_server``), which the double omits
    assert list(_Registry.bus.servers.values()) == [retried], "only the retried server is on the bus"
    assert _Registry.bus.sent == [retried]
    assert factory.calls[0]["port"] == instance.bot_port
