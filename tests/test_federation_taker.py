"""The taker: who runs the update, what stops it, and what is left behind when it does not.

Four things are pinned here, and the last two matter most because they are the failure paths.

* **The lock decides.** A node that does not get it has powered off nothing, so it must not touch anything — no
  step-down, no update, no restore. Two nodes seeing the update in the same second is exactly this case.
* **A residue refuses the update and names it.** The installer must never run under somebody's live server, and
  "the update did not happen" with no reason is the failure this whole slice exists to prevent.
* **The window is released even when the update raises** — otherwise every cluster stays dark until the holder's
  heartbeat goes stale.
* **This node's own servers come back through the same restore a follower uses**, after the window is gone.
"""
from __future__ import annotations

import types

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl
from core.data.node import UPDATE_SERVERS_UP, UPDATE_STOOD_BY

RESOURCE = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'


class _Log:
    def __init__(self):
        self.lines: list = []

    def _record(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._record('info', msg, *args)

    def error(self, msg, *args):
        self._record('error', msg, *args)

    def messages(self, level=None):
        return [msg for lvl, msg in self.lines if level is None or lvl == level]


class _Windows:
    def __init__(self, granted=True):
        self.granted = granted
        self.taken: list = []
        self.released: list = []

    async def take(self, resource_id, action, scope):
        self.taken.append((resource_id, action, scope))
        return self.granted

    async def release(self, resource_id):
        self.released.append(resource_id)


class _Registry:
    def __init__(self, members=()):
        self.members_rows = list(members)

    async def members(self, resource_id):
        return list(self.members_rows)


class _Protocol:
    """The protocol's surface, recording the sequence the taker drives."""

    calls: list = []
    granted = True
    residue = 0
    members: list = []
    raises: Exception | None = None

    def __init__(self, node, **_kwargs):
        self.node = node
        self.log = node.log
        self.windows = _Windows(granted=_Protocol.granted)
        self.registry = _Registry(members=_Protocol.members)

    async def step_down(self, resource_id, scope='server', warn_times=None):
        _Protocol.calls.append(('step_down', resource_id, scope, warn_times))

    async def wait_for_clear(self, resource_id, timeout):
        _Protocol.calls.append(('wait_for_clear', resource_id))
        if _Protocol.raises:
            raise _Protocol.raises
        return _Protocol.residue

    async def restore(self, resource_id):
        _Protocol.calls.append(('restore', resource_id))


class _Node:
    def __init__(self, resources=None, update_rc=0, update_raises=None):
        self.resources = {RESOURCE: 'server'} if resources is None else resources
        self.log = _Log()
        self.update_calls: list = []
        self.update_rc = update_rc
        self.update_raises = update_raises

    async def update(self, **kwargs):
        self.update_calls.append(kwargs)
        if self.update_raises:
            raise self.update_raises
        return self.update_rc


@pytest.fixture
def taker(monkeypatch):
    _Protocol.calls = []
    _Protocol.granted = True
    _Protocol.residue = 0
    _Protocol.members = []
    _Protocol.raises = None
    monkeypatch.setattr(nodeimpl, 'ResourceWindowProtocol', _Protocol)
    return _Protocol


def calls():
    return list(_Protocol.calls)


# ------------------------------------------------------------------------------- the lock decides

@pytest.mark.asyncio
async def test_a_node_that_does_not_get_the_lock_touches_nothing(taker):
    taker.granted = False
    subject = _Node()
    assert await NodeImpl.federated_update(subject, RESOURCE) == UPDATE_STOOD_BY
    assert calls() == []                       # no step-down, no wait, no restore
    assert subject.update_calls == []          # and the installer never runs
    assert any('standing by' in msg for msg in subject.log.messages('info'))


@pytest.mark.asyncio
async def test_the_lock_is_taken_for_an_update_on_this_installation(taker):
    subject = _Node()
    await NodeImpl.federated_update(subject, RESOURCE)
    assert subject.update_calls == [{'warn_times': [300, 120, 60], 'version': None, 'branch': None}]


# ------------------------------------------------------------------------------ the sequence itself

@pytest.mark.asyncio
async def test_the_taker_stands_its_own_servers_down_before_it_updates(taker):
    subject = _Node()
    await NodeImpl.federated_update(subject, RESOURCE)
    assert calls() == [('step_down', RESOURCE, 'server', [300, 120, 60]),   # the update's own warn times
                       ('wait_for_clear', RESOURCE),
                       ('restore', RESOURCE)]
    assert subject.update_calls, 'the update runs between the step-down and the restore'


# ------------------------------------------------------------------------------------ the residue

@pytest.mark.asyncio
async def test_a_residue_refuses_the_update_and_names_who_is_still_up(taker):
    taker.residue = 3
    taker.members = [(42, 'node-1', 2, True), (99, 'other-cluster', 1, True), (7, 'gone', 5, False)]
    subject = _Node()
    assert await NodeImpl.federated_update(subject, RESOURCE) == UPDATE_SERVERS_UP
    assert subject.update_calls == [], 'the installer must not run under a live server'
    errors = ' '.join(subject.log.messages('error'))
    assert 'node-1' in errors and 'other-cluster' in errors     # the offenders are named
    assert 'gone' not in errors                                 # a dead node is not one of them
    assert ('restore', RESOURCE) in calls()                     # and the window is given back anyway


# ------------------------------------------------------------------------- the window always comes back

@pytest.mark.asyncio
async def test_the_window_is_released_and_this_nodes_servers_restored_when_the_update_raises(taker):
    subject = _Node(update_raises=RuntimeError('dcs_updater blew up'))
    with pytest.raises(RuntimeError):
        await NodeImpl.federated_update(subject, RESOURCE)
    assert calls()[-1] == ('restore', RESOURCE)


# ------------------------------------------------------------------------------- no resource at all

def test_a_node_without_a_registered_installation_has_no_resource():
    """Then dcs_update keeps the pre-federation behaviour — there is nothing to coordinate."""
    assert NodeImpl.resource_of_installation(_Node(resources={})) is None
    assert NodeImpl.resource_of_installation(_Node()) == RESOURCE