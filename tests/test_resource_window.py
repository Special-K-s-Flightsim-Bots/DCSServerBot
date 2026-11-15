"""Taking and releasing a shared resource: who gets it, what the row says, and what a crash leaves behind.

The cluster pool is doubled, and so is the dedicated connection ``take()`` opens — neither validates SQL. What these
tests pin is the protocol's state machine: the lock is tried on a *dedicated* cluster connection before any row is
written, a node that does not get the lock writes nothing at all, the window row names its holder, a previous window's
acks are cleared so a new window cannot look already answered, and a release removes the row before dropping the
session that holds the lock.
"""
from __future__ import annotations

import asyncio
import logging

from typing import Any

import pytest

from core.data import resource_window
from core.data.resource_window import OpenWindow, ResourceWindows, lock_key

RESOURCE_ID = 'a3f1c2d4e5b60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00'


class _Cursor:
    def __init__(self, rows=(), rowcount: int = 1):
        self.rows = list(rows)
        self.index = 0
        self.rowcount = rowcount

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.index]
        self.index += 1
        return row


class _FakeConnection:
    """Stands in for the dedicated connection ``take()`` opens, and records everything asked of it."""

    instances: list['_FakeConnection'] = []
    fail_on: str | None = None
    #: a needle of a statement this connection answers with "no row was affected" — what a renewal sees once its
    #: window has been released or replaced.
    zero_rowcount_on: str | None = None

    def __init__(self, granted: bool = True):
        self.granted = granted
        self.calls: list[tuple[str, Any]] = []
        self.closed = False
        _FakeConnection.instances.append(self)

    async def execute(self, query, params=None):
        self.calls.append((str(query), params))
        if _FakeConnection.fail_on and _FakeConnection.fail_on in str(query):
            raise RuntimeError(f'{_FakeConnection.fail_on} failed')
        if 'try_advisory_lock' in str(query):
            return _Cursor([(self.granted,)])
        if _FakeConnection.zero_rowcount_on and _FakeConnection.zero_rowcount_on in str(query):
            return _Cursor(rowcount=0)
        return _Cursor()

    async def close(self):
        self.closed = True

    @property
    def queries(self) -> list[str]:
        return [query for query, _params in self.calls]

    def statements(self, needle: str) -> list[tuple[str, Any]]:
        return [(query, params) for query, params in self.calls if needle in query]


class _FakeAsyncConnection:
    """The module global ``windows.AsyncConnection`` is patched to this, so no psycopg call happens."""

    granted = True
    url: str | None = None
    kwargs: dict = {}

    @staticmethod
    async def connect(url, **kwargs):
        _FakeAsyncConnection.url = url
        _FakeAsyncConnection.kwargs = kwargs
        return _FakeConnection(granted=_FakeAsyncConnection.granted)


class _PoolCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries: list[tuple[str, Any]] = []

    async def execute(self, query, params=None):
        self.queries.append((str(query), params))
        return _Cursor(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _PoolConnection:
    def __init__(self, cursor: _PoolCursor):
        self._cursor = cursor

    async def execute(self, query, params=None):
        return await self._cursor.execute(query, params)


class _Pool:
    def __init__(self, rows=()):
        self.cursor = _PoolCursor(rows)

    def connection(self):
        cursor = self.cursor

        class _CM:
            async def __aenter__(self):
                return _PoolConnection(cursor)

            async def __aexit__(self, *exc):
                return False

        return _CM()

    @property
    def queries(self) -> list[str]:
        return [query for query, _params in self.cursor.queries]

    def statements(self, needle: str) -> list[tuple[str, Any]]:
        return [(query, params) for query, params in self.cursor.queries if needle in query]


class _Node:
    def __init__(self, cpool=None, guild_id: int = 42, name: str = 'node-1', locals_: dict | None = None):
        self.cpool = cpool
        self.guild_id = guild_id
        self.name = name
        self.locals = locals_ or {}
        self.log = logging.getLogger('test')

    def get_database_urls(self):
        return 'postgres://cluster-db', 'postgres://data-db'


@pytest.fixture
def fake_connection(monkeypatch):
    """Patch the module's connection factory, and reset the recorder between tests."""
    _FakeConnection.instances = []
    _FakeConnection.fail_on = None
    _FakeConnection.zero_rowcount_on = None
    _FakeAsyncConnection.granted = True
    _FakeAsyncConnection.url = None
    _FakeAsyncConnection.kwargs = {}
    monkeypatch.setattr(resource_window, 'AsyncConnection', _FakeAsyncConnection)
    return _FakeAsyncConnection


def windows_for(locals_: dict | None = None, rows=()):
    pool = _Pool(rows)
    return ResourceWindows(_Node(cpool=pool, locals_=locals_)), pool  # type: ignore[arg-type]  # a double


# ------------------------------------------------------------------ the lock key

def test_the_lock_key_is_derived_from_the_resource_id():
    key = lock_key(RESOURCE_ID)
    assert key == lock_key(RESOURCE_ID)
    assert key != lock_key('00' + RESOURCE_ID[2:])
    assert -(2 ** 63) <= key < 2 ** 63


def test_every_resource_gets_its_own_lock_key():
 # the key reads the FIRST bytes of the id, so the ids have to differ there to prove anything
    keys = {lock_key(f'{n:016x}' + '0' * 48) for n in range(200)}
    assert len(keys) == 200


# ------------------------------------------------------------------ taking

@pytest.mark.asyncio
async def test_taking_uses_a_dedicated_cluster_connection_and_writes_the_row(fake_connection):
    win, _pool = windows_for()
    assert await win.take(RESOURCE_ID, 'update', 'server') is True

    assert fake_connection.url == 'postgres://cluster-db'      # the CLUSTER db, not the data db
    assert fake_connection.kwargs.get('autocommit') is True    # the lock needs its own session, not a transaction
    conn = _FakeConnection.instances[0]
    assert conn.statements('try_advisory_lock')[0][1] == (lock_key(RESOURCE_ID),)
    query, params = conn.statements('INSERT INTO resource_window')[0]
    assert 'ON CONFLICT (resource_id) DO UPDATE' in query
    assert params == (RESOURCE_ID, 42, 'node-1', 'update', 'server')
    assert conn.closed is False
    assert win.holds(RESOURCE_ID) is True


@pytest.mark.asyncio
async def test_a_new_window_clears_the_previous_windows_acks(fake_connection):
    """An ack says "I stepped down for THIS window"; left behind, it would make the next one look already answered."""
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    conn = _FakeConnection.instances[0]
    assert conn.statements('DELETE FROM resource_ack')[0][1] == (RESOURCE_ID,)
    assert conn.queries.index(next(q for q in conn.queries if 'DELETE FROM resource_ack' in q)) < \
        conn.queries.index(next(q for q in conn.queries if 'INSERT INTO resource_window' in q))


@pytest.mark.asyncio
async def test_a_node_that_does_not_get_the_lock_writes_nothing(fake_connection):
    """The follower powered off nothing, so there is nothing to undo and nothing to record."""
    fake_connection.granted = False
    win, _pool = windows_for()
    assert await win.take(RESOURCE_ID, 'update', 'server') is False
    conn = _FakeConnection.instances[0]
    assert not conn.statements('INSERT INTO resource_window')
    assert not conn.statements('DELETE FROM resource_ack')
    assert conn.closed is True          # and it does not hold a session open for nothing
    assert win.holds(RESOURCE_ID) is False


@pytest.mark.asyncio
async def test_taking_what_this_node_already_holds_is_a_no_op(fake_connection):
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    assert await win.take(RESOURCE_ID, 'update', 'server') is True
    assert len(_FakeConnection.instances) == 1


@pytest.mark.asyncio
async def test_a_failed_row_write_releases_the_lock_it_just_took(fake_connection):
    """A grant with no window row behind it would block every other cluster with nothing to show for it."""
    win, _pool = windows_for()
    _FakeConnection.fail_on = 'INSERT INTO resource_window'
    with pytest.raises(RuntimeError):
        await win.take(RESOURCE_ID, 'update', 'server')
    conn = _FakeConnection.instances[0]
    assert conn.statements('try_advisory_lock')          # the lock WAS granted before the write failed
    assert conn.closed is True
    assert win.holds(RESOURCE_ID) is False


# ------------------------------------------------------------------ releasing

@pytest.mark.asyncio
async def test_releasing_deletes_the_row_and_then_drops_the_session(fake_connection):
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    conn = _FakeConnection.instances[0]
    await win.release(RESOURCE_ID)
    query, params = conn.statements('DELETE FROM resource_window')[0]
    assert 'holder_guild = %s AND holder_node = %s' in query     # never somebody else's window
    assert params == (RESOURCE_ID, 42, 'node-1')
    assert conn.closed is True                                    # the lock dies with the session
    assert win.holds(RESOURCE_ID) is False


@pytest.mark.asyncio
async def test_releasing_a_window_this_node_never_held_does_nothing(fake_connection):
    win, _pool = windows_for()
    await win.release(RESOURCE_ID)
    assert _FakeConnection.instances == []


# ------------------------------------------------------------------ reading the window

@pytest.mark.asyncio
async def test_the_window_read_carries_the_holder_and_whether_it_is_alive():
    """Presence alone is not enough: a taker that died leaves its row behind, so followers need liveness too."""
    win, pool = windows_for(rows=[(42, 'node-1', 'update', 'server', True)])
    window = await win.state(RESOURCE_ID)
    assert window == OpenWindow(42, 'node-1', 'update', 'server', True)
    query = pool.statements('FROM resource_window')[0][0]
    # liveness is the holder's OWN lease stamp, never the node heartbeat: the holder's next session writes that
    # heartbeat again, so a row abandoned by a killed process would read live for ever.
    assert 'w.refreshed >' in query and 'coalesce(' in query
    assert 'nodes' not in query


@pytest.mark.asyncio
async def test_no_window_reads_as_none():
    win, _pool = windows_for(rows=[])
    assert await win.state(RESOURCE_ID) is None


@pytest.mark.asyncio
async def test_the_window_read_reuses_the_clusters_heartbeat_rule():
    win, pool = windows_for(locals_={'cluster': {'heartbeat': 7}}, rows=[(42, 'node-1', 'update', 'server', True)])
    await win.state(RESOURCE_ID)
    assert '7 seconds' in pool.statements('FROM resource_window')[0][0]


@pytest.mark.asyncio
async def test_acking_this_node_is_an_upsert_of_its_own_row(fake_connection):
    win, pool = windows_for()
    await win.ack(RESOURCE_ID)
    query, params = pool.statements('INSERT INTO resource_ack')[0]
    assert 'ON CONFLICT (resource_id, guild_id, node) DO UPDATE' in query
    assert params == (RESOURCE_ID, 42, 'node-1')


@pytest.mark.asyncio
async def test_reading_the_acks_names_the_members_that_answered(fake_connection):
    win, pool = windows_for(rows=[(1, 'node-a'), (2, 'node-b')])
    assert await win.acks(RESOURCE_ID) == [(1, 'node-a'), (2, 'node-b')]


# ------------------------------------------------------------------ the holder's lease

@pytest.mark.asyncio
async def test_taking_stamps_the_lease_in_the_row(fake_connection):
    """A row with no lease of its own can only be aged by the holder's node heartbeat — which its own NEXT session
    keeps writing, so an abandoned window would never look abandoned."""
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    query, _params = _FakeConnection.instances[0].statements('INSERT INTO resource_window')[0]
    assert 'refreshed' in query
    await win.release(RESOURCE_ID)


@pytest.mark.asyncio
async def test_the_taker_renews_its_lease_while_it_holds_the_window(fake_connection, monkeypatch):
    monkeypatch.setattr(resource_window, 'POLL_INTERVAL', 0)
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    conn = _FakeConnection.instances[0]
    for _ in range(5):
        await asyncio.sleep(0)
    renewals = conn.statements('SET refreshed =')
    assert renewals, 'the holder has to keep its own lease fresh'
    query, params = renewals[0]
    assert 'holder_guild = %s AND holder_node = %s' in query     # never somebody else's window
    assert params == (RESOURCE_ID, 42, 'node-1')
    await win.release(RESOURCE_ID)


@pytest.mark.asyncio
async def test_the_renewal_stops_when_the_window_is_no_longer_this_nodes(fake_connection, monkeypatch):
    """A released or replaced row is not ours any more: the renewal affects nothing and ends, rather than keeping
    somebody else's window alive."""
    monkeypatch.setattr(resource_window, 'POLL_INTERVAL', 0)
    _FakeConnection.zero_rowcount_on = 'SET refreshed ='
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    await asyncio.wait_for(win._renewals[RESOURCE_ID], timeout=1.0)   # it returns by itself
    await win.release(RESOURCE_ID)


@pytest.mark.asyncio
async def test_releasing_stops_the_renewal(fake_connection, monkeypatch):
    monkeypatch.setattr(resource_window, 'POLL_INTERVAL', 0)
    win, _pool = windows_for()
    await win.take(RESOURCE_ID, 'update', 'server')
    conn = _FakeConnection.instances[0]
    await win.release(RESOURCE_ID)
    seen = len(conn.statements('SET refreshed ='))
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(conn.statements('SET refreshed =')) == seen


# ------------------------------------------------------------------ the row a killed process leaves

@pytest.mark.asyncio
async def test_release_own_deletes_only_this_nodes_windows_and_names_them(fake_connection):
    """Boot and shutdown clear the residue a previous session left: by holder, and they report what they cleared."""
    win, pool = windows_for(rows=[(RESOURCE_ID, 'update')])
    assert await win.release_own() == [(RESOURCE_ID, 'update')]
    query, params = pool.statements('DELETE FROM resource_window')[0]
    assert 'holder_guild = %s AND holder_node = %s' in query
    assert 'RETURNING resource_id, action' in query
    assert params == (42, 'node-1')
