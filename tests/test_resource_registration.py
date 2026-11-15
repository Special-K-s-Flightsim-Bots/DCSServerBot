"""Which servers a node files under a resource - and which it must not.

A registry row is read by *another* cluster to decide who has to stand down, so a dependent this node does
not run is a lie the other cluster acts on. The tempting source, ``utils.findDCSInstances()``, scans the
machine's Saved Games folder and hands back every DCS instance on the PC - including one another cluster
configures - which files a foreign cluster's servers under this node, stamped with this node's guild_id.

The node's own configured instances are the only honest source. The installation belongs to the node
(``DCS.installation``, one per node), so every instance of this node runs on it and nobody else's does -
no per-instance installation exists to filter on.
"""
from __future__ import annotations

import types

import pytest

from core.data.impl import nodeimpl

INSTALL = '//nas/dcs'          # a network path needs no machine id, so the identity is derivable anywhere


class _Log:
    def __init__(self):
        self.lines: list[str] = []

    def info(self, msg, *args):
        self.lines.append(msg % args if args else msg)

    def error(self, msg, *args):
        self.lines.append(msg % args if args else msg)


class _Registry:
    """Records what the node declared."""

    calls: list = []

    def __init__(self, node=None):
        self.node = node

    async def register(self, identity, *, dependents=(), servers_up=0):
        _Registry.calls.append((identity.id, tuple(dependents), servers_up))


class _Node:
    def __init__(self, instances, installation=INSTALL):
        self.installation = installation
        self.instances = {name: types.SimpleNamespace(name=name) for name in instances}
        self.resources = {}
        self.log = _Log()


@pytest.fixture
def registry(monkeypatch):
    _Registry.calls = []
    monkeypatch.setattr(nodeimpl, 'ResourceRegistry', _Registry)
 # The machine-wide scan: if registration ever goes back to it, these servers appear as dependents.
    monkeypatch.setattr(nodeimpl.utils, 'findDCSInstances',
                        lambda *a, **kw: {'srv1': 'WW2', 'srv2': 'Modern', 'srv3': 'SomeoneElsesServer'})
    return _Registry


@pytest.mark.asyncio
async def test_a_node_declares_only_its_own_servers(registry):
    node = _Node(['WW2', 'Modern'])
    await nodeimpl.NodeImpl.register_resources(node)
    (_resource_id, dependents, servers_up), = registry.calls
    assert dependents == (('server', 'Modern'), ('server', 'WW2'))
    assert ('server', 'SomeoneElsesServer') not in dependents, \
        "a machine-wide scan must not name this node's dependents"
    assert any('2 server(s) depend on it' in line for line in node.log.lines)


@pytest.mark.asyncio
async def test_the_resource_is_remembered_so_this_node_can_follow_it(registry):
    node = _Node(['WW2'])
    await nodeimpl.NodeImpl.register_resources(node)
    assert list(node.resources) == [registry.calls[0][0]]


@pytest.mark.asyncio
async def test_a_node_without_an_installation_declares_nothing(registry):
    node = _Node(['WW2'], installation=None)
    await nodeimpl.NodeImpl.register_resources(node)
    assert registry.calls == []
    assert node.resources == {}


@pytest.mark.asyncio
async def test_a_failure_to_register_is_reported_and_never_fatal(registry, monkeypatch):
    """Registration coordinates; it is not a precondition for running servers."""
    async def boom(self, identity, **kwargs):
        raise RuntimeError('no database today')

    monkeypatch.setattr(_Registry, 'register', boom)
    node = _Node(['WW2'])
    await nodeimpl.NodeImpl.register_resources(node)          # must not raise
    assert any('no database today' in line for line in node.log.lines)