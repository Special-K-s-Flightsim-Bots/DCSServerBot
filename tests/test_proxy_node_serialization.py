"""The payload a ``@proxy`` call sends: reference objects travel as NAMES.

``Node`` is not a ``DataObject``, and a live node is not JSON-serialisable — the RPC message is
stored in a JSONB column, so a node parameter left as an object raised
``TypeError: Object of type NodeProxy is not JSON serializable`` in ``PubSub._process_write``: the
message was dropped and the caller waited out its RPC timeout. Every reference object is therefore
sent as its name, which the answering process resolves back into a node (``ServiceBus.rpc``), and
both callers — the ``@proxy`` decorator and the dynamic ``ServiceProxy`` — send the same payload.
"""
from __future__ import annotations

import asyncio
import json
from enum import Enum

from core import Node
from core.data.impl.nodeimpl import NodeImpl
from core.services.base import proxy, ServiceProxy


def a_node(name: str = "AGENT-1") -> Node:
    """A live ``Node`` instance, built without a config: the decorator reads name/is_remote only."""
    node = object.__new__(NodeImpl)
    node.name = name
    node.is_remote = True
    return node


class Bus:
    """Records the RPC the decorator would send, and serialises it the way the store does."""

    def __init__(self):
        self.payloads: list[dict] = []

    async def send_to_node_sync(self, message: dict, *, node=None, timeout=None):
        json.dumps(message["params"])       # what the JSONB column does at insert time
        self.payloads.append(message)
        return "ok"


class Mode(Enum):
    RANDOM = "random"


class MusicService:
    """A service with a node-first method, as the music service has four of."""

    bus = Bus()

    @proxy
    async def read_library(self, node: Node, refresh: bool = False, mode: Mode | None = None,
                           extra: list | None = None):
        return "local"


def call(**kwargs):
    service = MusicService()
    asyncio.run(service.read_library(**kwargs))
    return service.bus.payloads[-1]["params"]


def test_a_node_parameter_travels_as_its_name():
    params = call(node=a_node())
    assert params["node"] == "AGENT-1"
    assert isinstance(params["node"], str)


def test_a_node_nested_in_a_list_is_dereferenced():
    params = call(node=a_node(), extra=[a_node("AGENT-2"), "plain"])
    assert params["extra"] == ["AGENT-2", "plain"]


def test_an_enum_parameter_travels_as_its_value():
    assert call(node=a_node(), mode=Mode.RANDOM)["mode"] == "random"


def test_the_local_call_still_receives_the_node_object():
    """Dereferencing is a WIRE rule: the local path must keep the object it was given."""
    passed = []

    class LocalService:
        bus = Bus()

        @proxy
        async def read_library(self, node: Node):
            passed.append(node)
            return "local"

    local = object.__new__(NodeImpl)
    local.name = "MASTER"
    local.is_remote = False
    asyncio.run(LocalService().read_library(local))
    assert passed == [local]


def test_the_dynamic_service_proxy_sends_the_same_payload():
    """One serialiser for both callers: the dynamic proxy answers what the decorator sends."""
    proxy_caller = ServiceProxy(MusicService)
    assert proxy_caller._build_params("read_library", (a_node(),), {})["node"] == "AGENT-1"
