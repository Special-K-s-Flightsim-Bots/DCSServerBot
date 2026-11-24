"""The registry's cluster-database half: what it declares, what it reads, and what it refuses to guess.

The pool is doubled here, and a doubled pool validates neither SQL syntax nor column names — there is no Postgres on
this machine. What it *can* pin is the contract: which rows are written with which values, that the registry leaves
the schema alone (sql/cluster.sql and migrate_3_19 own it), that the scope comes from the resource *type*, and that
staleness is the cluster's own heartbeat setting rather than a second number. The SQL itself still has to survive its
first run against a real server.

The double follows the rules that make a psycopg double faithful: ``conn.execute()`` returns a cursor that has already
been driven, rows are tuples (the default row factory), the cursor is an async context manager, and ``__aiter__``
stops instead of looping.
"""
from __future__ import annotations

import logging
import re

from pathlib import Path

from typing import Any

import pytest

from core.data.resources import ResourceRegistry, scope_of
from core.utils.resources import ResourceIdentity

REPO = Path(__file__).resolve().parents[1]
CLUSTER_SQL = REPO / 'sql' / 'cluster.sql'
MIGRATE_PY = REPO / 'migrate.py'
FEDERATION_TABLES = ('resources', 'resource_members', 'resource_dependents', 'resource_window', 'resource_ack')


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.index = 0
        self.rowcount = 0
        self.queries: list[tuple[str, object]] = []

    async def execute(self, query, params=None):
        self.queries.append((str(query), params))
        self.rowcount += 1
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index >= len(self.rows):
            raise StopAsyncIteration
        row = self.rows[self.index]
        self.index += 1
        return row

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return list(self.rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Conn:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    async def execute(self, query, params=None):
 # construct-and-await: a cursor that was never driven answers every read with "no such record"
        return await self._cursor.execute(query, params)

    def cursor(self, *_args, **_kwargs):
        return self._cursor


class _ConnectionCM:
    def __init__(self, cursor: _Cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return _Conn(self._cursor)

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, rows=()):
        self.cursor = _Cursor(rows)

    def connection(self):
        return _ConnectionCM(self.cursor)

    @property
    def queries(self) -> list[str]:
        return [query for query, _params in self.cursor.queries]

    @property
    def calls(self) -> list[tuple[str, object]]:
        return self.cursor.queries


class _Node:
    def __init__(self, cpool=None, guild_id: int = 42, name: str = 'node-1', locals_: dict | None = None):
        self.cpool = cpool
        self.guild_id = guild_id
        self.name = name
        self.locals = locals_ or {}
        self.log = logging.getLogger('test')


IDENTITY = ResourceIdentity.of('C:\\DCS World', 'dcs_installation', machine='MACHINE-A',
                               case_insensitive=True, resolve_links=False)


def registry(locals_: dict | None = None):
    """A registry wired to a faithful pool double."""
    pool = _Pool()
    node = _Node(cpool=pool, locals_=locals_)
    return ResourceRegistry(node), pool, node  # type: ignore[arg-type]  # a double, not a NodeImpl


def registry_without_pool():
    return ResourceRegistry(_Node())  # type: ignore[arg-type]  # a double, not a NodeImpl


def statements(pool: _Pool, needle: str) -> list[tuple[str, Any]]:
    return [(query, params) for query, params in pool.calls if needle in query]


# ------------------------------------------------------------------ registration

@pytest.mark.asyncio
async def test_register_declares_the_resource_membership_and_its_dependents():
    reg, pool, _node = registry()
    await reg.register(IDENTITY, dependents=[('server', 'SRS-1'), ('server', 'SRS-2')], servers_up=2)

    resource = statements(pool, 'INSERT INTO resources')
    assert len(resource) == 1
    _query, params = resource[0]
    assert params == (IDENTITY.id, 'dcs_installation', 'server', 42, IDENTITY.path)

    member = statements(pool, 'INSERT INTO resource_members')
    assert len(member) == 1
    assert member[0][1] == (IDENTITY.id, 42, 'node-1', 2)

    dependents = statements(pool, 'INSERT INTO resource_dependents')
    assert [params[-2:] for _query, params in dependents] == [('server', 'SRS-1'), ('server', 'SRS-2')]


@pytest.mark.asyncio
async def test_register_can_be_repeated_without_duplicating_anything():
    """It runs at every start, so every write has to be an upsert or a deliberate no-op."""
    reg, pool, _node = registry()
    await reg.register(IDENTITY, dependents=[('server', 'SRS-1')])
    await reg.register(IDENTITY, dependents=[('server', 'SRS-1')])

    assert 'ON CONFLICT (id) DO NOTHING' in statements(pool, 'INSERT INTO resources')[0][0]
    assert 'DO UPDATE' in statements(pool, 'INSERT INTO resource_members')[0][0]
    assert 'DO NOTHING' in statements(pool, 'INSERT INTO resource_dependents')[0][0]


@pytest.mark.asyncio
async def test_a_dependent_this_node_no_longer_runs_is_pruned():
    """The set changes legitimately - a server removed from nodes.yaml - and a leftover row names a server this
    node does not have, which another cluster reads as "it will step that down"."""
    reg, pool, _node = registry()
    pool.cursor.rows = [('server', 'GONE'), ('server', 'SRS-1')]     # what the table holds now
    await reg.register(IDENTITY, dependents=[('server', 'SRS-1')])

    deletes = statements(pool, 'DELETE FROM resource_dependents')
    assert [params[-1] for _query, params in deletes] == ['GONE']
    assert all(params[:3] == (IDENTITY.id, 42, 'node-1') for _query, params in deletes), \
        "a prune must never touch another node's rows"


@pytest.mark.asyncio
async def test_the_first_declarer_keeps_the_resource_row():
    """The owner column is reporting only, so a second cluster must not overwrite it."""
    reg, pool, _node = registry()
    await reg.register(IDENTITY)
    assert 'DO NOTHING' in statements(pool, 'INSERT INTO resources')[0][0]


@pytest.mark.asyncio
async def test_registration_without_a_cluster_pool_is_a_wiring_error():
    reg = registry_without_pool()
    with pytest.raises(RuntimeError):
        await reg.register(IDENTITY)


# ------------------------------------------------------------------ whose job the schema is

@pytest.mark.asyncio
async def test_registration_does_not_create_any_table():
    """The tables come from sql/cluster.sql and migrate_3_19. A registry that created its own would give one install
    two schemas with two histories — and would hide a missing migration behind a working registration."""
    reg, pool, _node = registry()
    await reg.register(IDENTITY)
    await reg.set_servers_up(IDENTITY.id, 1)
    await reg.members(IDENTITY.id)
    assert not [query for query in pool.queries if 'CREATE TABLE' in query.upper()]


def columns(sql_text: str) -> dict[str, set[tuple[str, str]]]:
    """``{table: {(column, type)}}`` for the federation tables, from any text that creates them."""
    found = {}
    for table in FEDERATION_TABLES:
        match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)^\s*\)\s*(?:;|\"\"\")", sql_text, re.S | re.M)
        assert match, f"{table} is not created here"
        found[table] = {(m.group(1), m.group(2)) for m in
                        re.finditer(r"^\s*(\w+)\s+(TEXT|BIGINT|INT|TIMESTAMP|BOOLEAN|SERIAL)\b",
                                    match.group(1), re.M)}
    return found


_ADDED_COLUMN = re.compile(
    r"ALTER TABLE\s+(\w+)\s+ADD COLUMN IF NOT EXISTS\s+(\w+)\s+(TEXT|BIGINT|INT|TIMESTAMP|BOOLEAN|SERIAL)\b")


def added_columns(sql_text: str) -> dict[str, set[tuple[str, str]]]:
    """``columns()`` plus every column a later ``ALTER TABLE … ADD COLUMN`` gives a federation table."""
    found = columns(sql_text)
    for table, column, column_type in _ADDED_COLUMN.findall(sql_text):
        if table in found:
            found[table].add((column, column_type))
    return found


def test_the_migration_creates_exactly_what_the_cluster_schema_creates():
    """A fresh install and an upgraded one must end up with the same federation tables.

    ``migrate_3_19`` is a deliberate frozen copy of the cluster schema, and a column added to one and not
    the other would only show up as a failure on somebody's upgrade. The frozen copy may legitimately lag:
    a column the schema gained later is carried by its own ``ALTER TABLE … ADD COLUMN`` in a later
    migration, and that statement is part of the upgrade path, so it counts as well.
    """
    assert columns(CLUSTER_SQL.read_text(encoding='utf-8')) == added_columns(MIGRATE_PY.read_text(encoding='utf-8'))


def test_the_notify_function_and_its_trigger_are_named_the_same_in_both():
    cluster = CLUSTER_SQL.read_text(encoding='utf-8')
    migration = MIGRATE_PY.read_text(encoding='utf-8')
    for name in ('federation_window_notify', 'resource_window_trigger', 'federation_window'):
        assert name in cluster and name in migration


# ------------------------------------------------------------------ what the protocol reads

@pytest.mark.asyncio
async def test_in_service_count_is_the_sql_sum_over_live_members():
    reg, pool, _node = registry()
    pool.cursor.rows = [(3,)]
    assert await reg.in_service_count(IDENTITY.id) == 3
    query = statements(pool, 'sum(')[0][0]
    assert 'resource_members' in query and 'last_seen >' in query


@pytest.mark.asyncio
async def test_staleness_is_the_clusters_own_heartbeat_setting():
    """Not a second number: the same value get_ready_nodes() applies to peers, from the node's config, default 30."""
    reg, pool, _node = registry(locals_={'cluster': {'heartbeat': 7}})
    pool.cursor.rows = [(0,)]
    await reg.in_service_count(IDENTITY.id)
    assert '7 seconds' in statements(pool, 'sum(')[0][0]

    default, pool2, _node2 = registry()
    pool2.cursor.rows = [(0,)]
    await default.in_service_count(IDENTITY.id)
    assert '30 seconds' in statements(pool2, 'sum(')[0][0]


@pytest.mark.asyncio
async def test_a_member_whose_node_row_is_gone_reads_as_not_live():
    """A node unregistered while still in the table must not break the reading the protocol depends on."""
    reg, pool, _node = registry()
    pool.cursor.rows = [(1, 'node-1', 2, True), (2, 'gone', 5, False)]
    rows = await reg.members(IDENTITY.id)
    assert rows == [(1, 'node-1', 2, True), (2, 'gone', 5, False)]
 # the coalesce is what turns a missing node row into FALSE instead of NULL; asserted on the SQL, since a doubled
 # pool cannot execute it
    left_join = [q for q in pool.queries if 'LEFT JOIN nodes' in q]
    assert left_join and 'coalesce(' in left_join[0]


@pytest.mark.asyncio
async def test_servers_up_is_published_for_this_nodes_row_only():
    reg, pool, _node = registry()
    await reg.set_servers_up(IDENTITY.id, 0)
    query, params = statements(pool, 'UPDATE resource_members')[0]
    assert 'guild_id = %s AND node = %s' in query
    assert params == (0, IDENTITY.id, 42, 'node-1')


# ------------------------------------------------------------------ scope declared by type

def test_the_scope_comes_from_the_resource_type():
    assert scope_of('dcs_installation') == 'server'


def test_an_untyped_resource_refuses_instead_of_defaulting():
    """A resource with no declared scope must never reach a maintenance window."""
    with pytest.raises(ValueError):
        scope_of('something_new')
