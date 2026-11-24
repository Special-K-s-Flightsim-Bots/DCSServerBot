"""The node's follower loop: which resources it follows, and what one broken resource costs the others.

The loop itself is a thin shell around ``follow_resources``, which is exercised unbound against a double — the point
being that every registered resource is followed with its own scope, and that a resource this node cannot read does
not take the others down with it. A node that silently stops following will not step down for the next window either,
so the failure has to be both non-fatal and visible.
"""
from __future__ import annotations

import types

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl

RESOURCE_A = 'aa' * 32
RESOURCE_B = 'bb' * 32


class _Log:
    def __init__(self):
        self.errors: list = []

    def exception(self, msg, *args, **kwargs):
        self.errors.append(msg if not args else msg % args)


class _Follow:
    """The protocol's surface, recording what the node asked it to follow."""

    calls: list = []
    fail_on: str | None = None

    def __init__(self, node, **_kwargs):
        self.node = node

    async def follow(self, resource_id, scope='server'):
        _Follow.calls.append((resource_id, scope))
        if resource_id == _Follow.fail_on:
            raise RuntimeError('this resource cannot be read')
        return 'idle'


@pytest.fixture
def follower(monkeypatch):
    _Follow.calls = []
    _Follow.fail_on = None
    monkeypatch.setattr(nodeimpl, 'ResourceWindowProtocol', _Follow)
    return _Follow


def node(resources: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(resources=resources, log=_Log())


@pytest.mark.asyncio
async def test_the_loop_follows_every_registered_resource_with_its_scope(follower):
    subject = node({RESOURCE_A: 'server', RESOURCE_B: 'server'})
    actions = await NodeImpl.follow_resources(subject)
    assert sorted(follower.calls) == [(RESOURCE_A, 'server'), (RESOURCE_B, 'server')]
    assert actions == {RESOURCE_A: 'idle', RESOURCE_B: 'idle'}


@pytest.mark.asyncio
async def test_one_unreadable_resource_does_not_stop_the_others(follower):
    follower.fail_on = RESOURCE_A
    subject = node({RESOURCE_A: 'server', RESOURCE_B: 'server'})
    actions = await NodeImpl.follow_resources(subject)
    assert actions == {RESOURCE_A: 'failed', RESOURCE_B: 'idle'}
    assert len(follower.calls) == 2
    assert subject.log.errors, 'a resource that could not be followed must not fail silently'


@pytest.mark.asyncio
async def test_a_node_without_resources_follows_nothing(follower):
    subject = node({})
    assert await NodeImpl.follow_resources(subject) == {}
    assert follower.calls == []
