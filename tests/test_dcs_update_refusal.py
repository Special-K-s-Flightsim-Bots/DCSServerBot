"""A refused update must never announce itself as installed.

``federated_update`` answers with a negative code when the installer was never started: the shared installation is
already being updated by another cluster, or servers on it were still up. ``dcs_update`` turns a code into an
operator-facing message, and reading those two as success posts "DCS World updated to version X" plus an audit entry
for an update that did not run. The positive control below keeps the fix honest: a real ``0`` still announces.
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


class _Bus:
    def __init__(self):
        self.sent: list = []
        self.servers: dict = {}

    async def send_to_node(self, payload):
        self.sent.append(payload)

    def methods(self):
        return [payload['method'] for payload in self.sent]


class _Registry:
    bus = None

    @classmethod
    def get(cls, _service):
        return cls.bus


@pytest.fixture
def bus(monkeypatch):
    fake = _Bus()
    _Registry.bus = fake
    monkeypatch.setattr(nodeimpl, 'ServiceRegistry', _Registry)
    return fake


def node(rc: int) -> types.SimpleNamespace:
    async def federated_update(_resource_id, **_kwargs):
        return rc

    return types.SimpleNamespace(
        log=_Log(), locals={}, name='node-1', node=types.SimpleNamespace(name='node-1'),
        resource_of_installation=lambda: RESOURCE, federated_update=federated_update)


@pytest.mark.asyncio
@pytest.mark.parametrize('rc', [UPDATE_STOOD_BY, UPDATE_SERVERS_UP])
async def test_a_refused_update_is_reported_and_not_announced(bus, rc):
    subject = node(rc)
    assert await NodeImpl.dcs_update(subject, version='2.9.0') == rc
    assert bus.methods() == ['alert']            # the operator is told; nobody is told it was installed
    message = bus.sent[0]['params']['message']
    assert 'did not run' in message
    assert subject.log.messages('error')


@pytest.mark.asyncio
async def test_an_update_that_ran_still_announces(bus):
    """The control: the fix must not silence a real update."""
    subject = node(0)
    assert await NodeImpl.dcs_update(subject, version='2.9.0') == 0
    assert bus.methods() == ['audit']
