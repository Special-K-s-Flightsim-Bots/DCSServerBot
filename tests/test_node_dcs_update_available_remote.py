"""A REMOTE node's answer to "is a DCS update available" travels as one ``Node`` RPC.

The question is asked on the master and the DCS installation sits on any node, so the answer has to
cross the ServiceBus. These pin the three legs of that: the ABC contract a node must honour, the
payload the proxy puts on the wire, and the value that comes back — the newer version string, or the
``None`` an unreachable ED endpoint produces.
"""
from __future__ import annotations

import types
from typing import Any

import aiohttp
import pytest

from core.data.impl.nodeimpl import NodeImpl
from core.data.node import Node
from core.data.proxy.nodeproxy import NodeProxy

METHOD = "is_dcs_update_available"
NODE_NAME = "node-1"


def _log() -> types.SimpleNamespace:
    """The three log levels the node methods below reach for."""
    return types.SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None,
                                 warning=lambda *a, **k: None, exception=lambda *a, **k: None)


class RecordingBus:
    """Records what a proxy puts on the wire and answers with the scripted value."""

    def __init__(self, *answers: Any, raise_: BaseException | None = None):
        self.answers = list(answers)
        self.raise_ = raise_
        self.calls: list[dict] = []
        self.kwargs: list[dict] = []

    async def send_to_node_sync(self, message: dict, **kwargs):
        self.calls.append(message)
        self.kwargs.append(kwargs)
        if self.raise_ is not None:
            raise self.raise_
        return self.answers.pop(0) if self.answers else None


class DispatchingBus(RecordingBus):
    """Answers an RPC the way the node-side handler does: by name, off the target object.

    ``ServiceBus.rpc`` resolves the method with ``getattr`` on the object ``data['object']`` names —
    there is no registry, so a name nothing registered still reaches a method that exists. This bus
    reproduces that dispatch so the round trip is exercised rather than stubbed.
    """

    def __init__(self, node: Node):
        super().__init__()
        self.node = node

    async def send_to_node_sync(self, message: dict, **kwargs):
        self.calls.append(message)
        self.kwargs.append(kwargs)
        return await getattr(self.node, message["method"])(**message.get("params", {}))


def remote_proxy(bus: RecordingBus, *, slow_system: bool = False) -> NodeProxy:
    """A ``NodeProxy`` without ``__init__`` (which wants a database and the bus registry)."""
    proxy = object.__new__(NodeProxy)
    proxy.bus = bus
    proxy.name = NODE_NAME
    proxy.slow_system = slow_system
    return proxy


class _Node(NodeImpl):
    """Exactly the node surface ``is_dcs_update_available`` reads.

    Subclasses ``NodeImpl`` so the inherited implementation is the one under test, and bypasses its
    ``__init__`` (which wants a config directory and a database) with the fields the method touches.
    """

    def __init__(self, installed: str = "2.9.20.0", latest: str | None = "2.9.20.0",
                 raises: BaseException | None = None):
        self.log = _log()
        self._installed = installed
        self._latest = latest
        self._raises = raises

    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        return "openbeta", self._installed

    async def get_latest_version(self, branch: str) -> str | None:
        if self._raises is not None:
            raise self._raises
        return self._latest


# --------------------------------------------------------------- the contract a node has to honour

async def _stub(self, *args, **kwargs):
    return None


def _node_namespace(skip: frozenset[str] = frozenset()) -> dict:
    """Every abstract member of ``Node`` implemented, except the ones named in ``skip``."""
    namespace: dict = {"__init__": lambda self: None}
    for name in Node.__abstractmethods__ - skip:
        namespace[name] = _stub
    return namespace


def test_the_abc_declares_the_question():
    assert METHOD in Node.__abstractmethods__, \
        "a node that cannot answer it must not be constructible"


def test_a_node_subclass_that_omits_the_method_cannot_be_instantiated():
    # control: with every abstract member implemented the same recipe builds an instance, so the
    # failure below is about the one omitted method and not about the harness.
    complete = type("_CompleteNode", (Node,), _node_namespace())
    complete()  # type: ignore[call-arg]  # __init__ comes from the namespace, Pyright cannot see it

    incomplete = type("_IncompleteNode", (Node,), _node_namespace(frozenset({METHOD})))
    with pytest.raises(TypeError) as excinfo:
        incomplete()  # type: ignore[call-arg]
    assert METHOD in str(excinfo.value)


def test_the_local_implementation_is_an_override_of_the_abc():
    assert getattr(NodeImpl.is_dcs_update_available, "__override__", False), \
        "an implementation that drifts off the ABC must not be shippable (contract 3)"
    assert getattr(NodeProxy.is_dcs_update_available, "__override__", False)


# ------------------------------------------------------------------------- the payload on the wire

@pytest.mark.asyncio
async def test_the_proxy_asks_the_node_by_name_and_returns_its_answer():
    bus = RecordingBus("2.9.21.0")

    assert await remote_proxy(bus).is_dcs_update_available() == "2.9.21.0"
    assert bus.calls == [{"command": "rpc", "object": "Node", "method": METHOD}]
    assert bus.kwargs == [{"node": NODE_NAME, "timeout": 60}]


@pytest.mark.asyncio
async def test_a_slow_system_node_is_given_the_neighbouring_timeout():
    slow = RecordingBus(None)
    await remote_proxy(slow, slow_system=True).is_dcs_update_available()

    neighbour = RecordingBus(None)
    await remote_proxy(neighbour, slow_system=True).get_dcs_branch_and_version()

    assert slow.kwargs[0]["timeout"] == 120
    assert slow.kwargs[0]["timeout"] == neighbour.kwargs[0]["timeout"], \
        "the convention is the neighbour's, not a second one"


# ------------------------------------------------------------------------ the answer that comes back

@pytest.mark.asyncio
async def test_a_node_that_answers_none_yields_none_and_does_not_raise():
    bus = RecordingBus(None)
    assert await remote_proxy(bus).is_dcs_update_available() is None


@pytest.mark.asyncio
async def test_a_newer_version_on_the_node_reaches_the_asker():
    bus = DispatchingBus(_Node(installed="2.9.20.0", latest="2.9.20.1"))
    assert await remote_proxy(bus).is_dcs_update_available() == "2.9.20.1"
    assert bus.calls[0]["method"] == METHOD, "one method name, dispatched without any registration"


@pytest.mark.asyncio
async def test_an_ed_outage_is_answered_with_none_not_an_exception():
    node = _Node(installed="2.9.20.0", raises=aiohttp.ClientError("ED is down"))
    assert await remote_proxy(DispatchingBus(node)).is_dcs_update_available() is None


@pytest.mark.asyncio
async def test_a_failed_call_propagates_exactly_as_the_neighbouring_methods_do():
    """A transport failure is not an answer: the neighbours raise, and so does this one."""
    failure = ConnectionError("node unreachable")

    with pytest.raises(ConnectionError):
        await remote_proxy(RecordingBus(raise_=failure)).get_latest_version("openbeta")
    with pytest.raises(ConnectionError):
        await remote_proxy(RecordingBus(raise_=failure)).is_dcs_update_available()
