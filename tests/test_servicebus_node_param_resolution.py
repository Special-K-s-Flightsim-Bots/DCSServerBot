"""Pins for the node reference an answering process receives on an RPC.

A node-first ``@proxy`` method names its target node in the payload. The answering process replaces that
name with the node object before the method runs, on the master and on an agent alike; otherwise the
method receives a plain string and every attribute access on it raises.

The pins drive ``ServiceBus.rpc()`` directly — the answering entry — rather than the caller's wire call.
"""
from __future__ import annotations

import logging
import types
from pathlib import Path
from typing import Any

import pytest

try:
    from core import Node
    from services.modmanager.service import Folder, ModManagerService
    from services.servicebus.service import ServiceBus
except ImportError as ex:  # pragma: no cover - depends on the bot's deps being installed
    pytest.skip(f"the bot's core cannot be imported ({ex})", allow_module_level=True)


# --------------------------------------------------------------------------------------------- doubles


class NodeDouble:
    """The node object of the process answering the call: a local node that answers by its own attributes."""

    def __init__(self, name: str, *, master: bool = False):
        self.name = name
        self.master = master
        self.is_remote = False
        self.instances: dict[str, Any] = {}
        self.all_nodes: dict[str, Any] = {name: self}


class AnsweringBus:
    """A bare ``ServiceBus`` carrying only the state ``rpc()`` reads."""

    def __init__(self, *, master: bool, node: NodeDouble):
        self.bus = ServiceBus.__new__(ServiceBus)
        self.bus.node = node
        self.bus.servers = {}
        self.bus.bot = None
        self.bus.log = logging.getLogger("test.servicebus")

    def rpc(self, obj: object, data: dict) -> Any:
        return self.bus.rpc(obj, data)


class LocalBus:
    """A bus double for a service whose target node is the answering node itself: nothing may be sent."""

    def __init__(self):
        self.calls: list[dict] = []

    async def send_to_node_sync(self, message: dict, *, node: str | None = None, timeout=None):
        self.calls.append(message)
        raise AssertionError(f"a local node must be answered in-process, not sent to {node}")


class NodeFirstProbe:
    """A service double with a node-first method that reads an attribute of the node it was given."""

    async def echo_node(self, node: Node) -> Any:
        return node

    async def read_node_is_remote(self, node: Node) -> bool:
        return node.is_remote

    async def read_node_name(self, node: Node) -> str:
        return node.name


def _modmanager_double(folder: Path) -> types.SimpleNamespace:
    """A service double bound to the real ModManager methods that the version read runs through."""
    svc = types.SimpleNamespace()
    svc.log = logging.getLogger("test.modmanager")
    svc.bus = LocalBus()
    svc.get_config = lambda reference=None: {"RootFolder": str(folder), "SavedGames": str(folder), "packages": []}
    svc.parse_filename = ModManagerService.parse_filename
    svc._package_size = ModManagerService._package_size
    svc._package_dir = types.MethodType(ModManagerService._package_dir, svc)
    svc._get_latest_file_version = types.MethodType(ModManagerService._get_latest_file_version, svc)
    svc.get_latest_version = types.MethodType(ModManagerService.get_latest_version, svc)
    svc.get_available_versions = types.MethodType(ModManagerService.get_available_versions, svc)
    svc.list_available_packages = types.MethodType(ModManagerService.list_available_packages, svc)
    svc.list_package_versions = types.MethodType(ModManagerService.list_package_versions.__wrapped__, svc)
    return svc


@pytest.fixture()
def package_folder(tmp_path: Path) -> Path:
    (tmp_path / "TestMod_v1.2.3.zip").write_bytes(b"a")
    (tmp_path / "TestMod_v1.3.0.zip").write_bytes(b"b")
    (tmp_path.parent / "Outside_v9.9.9.zip").write_bytes(b"c")
    return tmp_path


# --------------------------------------------------------------------------------------------- pins


@pytest.mark.asyncio
async def test_agent_resolves_node_name_to_the_node_object():
    bus = AnsweringBus(master=False, node=NodeDouble("REMOTE"))
    data = {"method": "read_node_is_remote", "params": {"node": "REMOTE"}}
    assert await bus.rpc(NodeFirstProbe(), data) is False
    assert await bus.rpc(NodeFirstProbe(), {"method": "read_node_name", "params": {"node": "REMOTE"}}) == "REMOTE"


@pytest.mark.asyncio
async def test_master_resolves_node_name_to_the_node_object():
    bus = AnsweringBus(master=True, node=NodeDouble("LOCAL", master=True))
    data = {"method": "read_node_is_remote", "params": {"node": "LOCAL"}}
    assert await bus.rpc(NodeFirstProbe(), data) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("master", [True, False])
async def test_unknown_node_name_still_resolves_to_none(master: bool):
    """The name that is not in the map answers ``None``, on the master and on an agent alike."""
    bus = AnsweringBus(master=master, node=NodeDouble("LOCAL", master=True))
    data = {"method": "echo_node", "params": {"node": "ELSEWHERE"}}
    assert await bus.rpc(NodeFirstProbe(), data) is None


@pytest.mark.asyncio
async def test_known_node_name_still_resolves_to_the_node_object():
    """The map is not narrowed: a name it holds answers the object itself."""
    node = NodeDouble("LOCAL", master=True)
    bus = AnsweringBus(master=True, node=node)
    data = {"method": "echo_node", "params": {"node": "LOCAL"}}
    assert await bus.rpc(NodeFirstProbe(), data) is node


@pytest.mark.asyncio
async def test_list_package_versions_answers_on_the_answering_node(package_folder: Path):
    bus = AnsweringBus(master=False, node=NodeDouble("REMOTE"))
    svc = _modmanager_double(package_folder)
    data = {
        "method": "list_package_versions",
        "params": {"node": "REMOTE", "folder": "RootFolder", "package_names": ["TestMod"]},
    }
    answer = await bus.rpc(svc, data)
    assert answer["TestMod"]["versions"] == ["1.2.3", "1.3.0"]
    assert answer["TestMod"]["latest"] == "1.3.0"
    assert answer["TestMod"]["downloaded"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../*", "a/b", "a\\b", "", ".."])
async def test_non_leaf_package_name_is_not_globbed(package_folder: Path, name: str):
    svc = _modmanager_double(package_folder)
    assert await svc.get_available_versions(None, Folder.RootFolder, name) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../*", "a/b", "", ".."])
async def test_non_leaf_package_name_is_left_out_of_the_versions_answer(package_folder: Path, name: str):
    svc = _modmanager_double(package_folder)
    answer = await svc.list_package_versions(NodeDouble("REMOTE"), Folder.RootFolder, [name, "TestMod"])
    assert name not in answer
    assert answer["TestMod"]["versions"] == ["1.2.3", "1.3.0"]


@pytest.mark.asyncio
async def test_leaf_package_name_is_still_answered(package_folder: Path):
    svc = _modmanager_double(package_folder)
    answer = await svc.get_available_versions(None, Folder.RootFolder, "TestMod")
    assert answer == ["1.2.3", "1.3.0"]
