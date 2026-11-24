"""The window row a killed process leaves behind: who clears it, when, and when it must NOT be cleared.

A window's advisory lock dies with the session that took it, but its ROW does not: it is deleted only by the process
that wrote it. So a bot that is closed mid-update leaves a row behind that its own next session reads as a live
window — the installation then refuses to start anything on it. These tests pin the two moments that row is cleared
(boot, and a clean shutdown) and the one moment it must be left alone (an installer still running, where the window
is the only thing keeping the other clusters' servers down).
"""
from __future__ import annotations

import types

import pytest

from core.data.impl import nodeimpl
from core.data.impl.nodeimpl import NodeImpl

RESOURCE = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'


class _Log:
    def __init__(self):
        self.lines: list = []

    def _record(self, level, msg, *args):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args):
        self._record('info', msg, *args)

    def warning(self, msg, *args):
        self._record('warning', msg, *args)

    def error(self, msg, *args):
        self._record('error', msg, *args)

    def messages(self, level=None):
        return [msg for lvl, msg in self.lines if level is None or lvl == level]


class _Windows:
    """The protocol's state surface, recording what the node asked it to drop."""

    released: list = []
    calls: list = []
    raise_on_release = False

    def __init__(self, node, **_kwargs):
        self.node = node

    async def release_own(self):
        _Windows.calls.append('release_own')
        if _Windows.raise_on_release:
            raise RuntimeError('the cluster database is unreachable')
        return list(_Windows.released)


@pytest.fixture
def windows(monkeypatch):
    _Windows.released = [(RESOURCE, 'update')]
    _Windows.calls = []
    _Windows.raise_on_release = False
    monkeypatch.setattr(nodeimpl, 'ResourceWindows', _Windows)
    return _Windows


def node(**fields) -> types.SimpleNamespace:
    base = {'log': _Log(), 'installer_running': False}
    base.update(fields)
    return types.SimpleNamespace(**base)


# ------------------------------------------------------------------ boot

@pytest.mark.asyncio
async def test_boot_drops_the_row_a_previous_session_left_and_says_so(windows):
    subject = node()
    await NodeImpl.release_own_windows(subject)
    assert windows.calls == ['release_own']
    warnings = ' '.join(subject.log.messages('warning'))
    assert RESOURCE in warnings and 'update' in warnings       # which window, and what it was for
    assert 'previous session' in warnings                      # and that it was residue, not a live window


@pytest.mark.asyncio
async def test_a_boot_that_cannot_read_the_cluster_database_still_starts(windows):
    """Cleaning residue is a repair, not a precondition: a database hiccup must not keep the node down."""
    windows.raise_on_release = True
    subject = node()
    await NodeImpl.release_own_windows(subject)
    assert subject.log.messages('warning')                     # reported, never raised


@pytest.mark.asyncio
async def test_the_boot_clears_residue_before_it_registers_the_resource(windows, monkeypatch):
    """The hook has to be wired where the node can still act on its own rows, and the order is part of the rule."""
    order: list[str] = []

    class _Recorder(_Windows):
        async def release_own(self):
            order.append('release_own')
            return []

    monkeypatch.setattr(nodeimpl, 'ResourceWindows', _Recorder)
    monkeypatch.setattr(nodeimpl.utils, 'safe_start', lambda *a, **k: order.append('federation_loop'))

    class _NodeDouble:
        locals: dict = {}
        all_nodes: dict = {}
        log = _Log()
        _master = True
        _claimed_master = None
        installer_running = False
        federation_loop = object()
        # the SHIPPED method, as a plain method of the double: the boot hook is what is under test
        release_own_windows = NodeImpl.release_own_windows

        def is_federation(self) -> bool:
            return False

        def commit_claimed_master(self):
            return None

        async def init_db(self):
            order.append('init_db')

        async def update_db(self):
            order.append('update_db')

        async def init_instances(self):
            order.append('init_instances')

        async def register_resources(self):
            order.append('register_resources')

    await NodeImpl._post_init(_NodeDouble())
    assert order.index('release_own') < order.index('register_resources')
    assert order[-1] == 'federation_loop'


# ------------------------------------------------------------------ a clean shutdown

@pytest.mark.asyncio
async def test_a_clean_shutdown_gives_the_window_back(windows):
    subject = node()
    await NodeImpl.release_windows(subject)
    assert windows.calls == ['release_own']
    assert RESOURCE in ' '.join(subject.log.messages('info'))


@pytest.mark.asyncio
async def test_a_shutdown_never_gives_back_a_window_while_an_installer_runs(windows):
    """The window is what keeps the other clusters' servers down for the install. Dropping it there is what
    corrupts an installation, so it is left to expire instead."""
    subject = node(installer_running=True)
    await NodeImpl.release_windows(subject)
    assert windows.calls == []
    assert 'expires' in ' '.join(subject.log.messages('warning'))


@pytest.mark.asyncio
async def test_a_shutdown_that_cannot_reach_the_database_does_not_fail_the_exit(windows):
    windows.raise_on_release = True
    subject = node()
    await NodeImpl.release_windows(subject)
    assert subject.log.messages('warning')
