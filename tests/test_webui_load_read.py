"""The load series read: the fixed-width bucket table, the payload the console gets, and the scope.

Sections, one per acceptance criterion: the SCALE TABLE (every published window's bucket width and
bucket count, and the fallback for a name the table does not carry); the PAYLOAD (its key set, its
numbers, its missing buckets); the NODE TABLE (an offline node and its all-``None`` spark); the
SCOPE (cluster, node and server reach the statement as bound parameters); and the typed refusals.

Nothing here needs a live database, a Discord connection or an HTTP request: the statements are
answered by a recording pool.
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from decimal import Decimal

import pytest

import core.actions as actions_module
from core.actions import (
    TRANSPORT_SERVICE,
    TRANSPORT_WEB,
    ActionContext,
    action_registry,
    discover_actions,
)

#: The three statements this read runs, by a marker each one carries and no sibling does.
_SERIES = "AVG(pm.users) AS players"
_NODES = "LEFT JOIN win w ON w.node = r.node"
_SPARK = "CROSS JOIN roster r"

#: The published window table: scale → (bucket seconds, bucket count).
EXPECTED_SCALES = {"1h": (60, 60), "6h": (300, 72), "24h": (300, 288),
                   "7d": (3600, 168), "30d": (3600, 720)}


@pytest.fixture(autouse=True)
def clean_registry():
    """The ``@action`` registry is PROCESS state — emptied and rebuilt around every test."""
    saved = dict(action_registry())
    actions_module._ACTIONS.clear()
    sys.modules.pop("plugins.monitoring.actions", None)
    try:
        yield actions_module._ACTIONS
    finally:
        actions_module._ACTIONS.clear()
        actions_module._ACTIONS.update(saved)
        sys.modules.pop("plugins.monitoring.actions", None)


def _actions():
    """The ``monitoring`` plugin's action module, imported inside a test — never at collection."""
    return importlib.import_module("plugins.monitoring.actions")


def _stats_read():
    return importlib.import_module("plugins.monitoring.stats_read")


class _Cursor:
    """A cursor that answers ONE statement from the pool's table and records it."""

    def __init__(self, pool):
        self._pool = pool
        self._rows: list[dict] = []
        self._iterator = iter(())

    async def execute(self, statement, params=None):
        self._rows = self._pool.answer(statement, params)
        self._pool.statements.append((statement, params))
        self._iterator = iter(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


class _Conn:
    """A connection handing out recording cursors; usable as ``async with pool.connection()``."""

    def __init__(self, pool):
        self._pool = pool

    def cursor(self, *args, **kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Pool:
    """An ``apool`` double: per-statement answers, and every statement it was asked to run."""

    def __init__(self, answers=None):
        self.answers: list[tuple[str, object]] = list(answers or [])
        self.statements: list[tuple[str, dict]] = []

    def connection(self):
        return _Conn(self)

    def answer(self, statement: str, params) -> list[dict]:
        for marker, rows in self.answers:
            if marker in statement:
                given = rows(params) if callable(rows) else rows
                return [dict(row) for row in given]
        return []

    def statements_matching(self, marker: str) -> list[tuple[str, dict]]:
        return [(statement, params) for statement, params in self.statements if marker in statement]


def context(pool: Pool | None = None, *, transport: str = TRANSPORT_WEB) -> ActionContext:
    """An action context over *pool*, the way the console builds one."""
    node = types.SimpleNamespace(apool=pool, config={})
    ctx = ActionContext(node=node, bus=types.SimpleNamespace(servers={}))
    ctx._transport = transport
    return ctx


def series_row(**overrides) -> dict:
    row = {"time": datetime(2026, 10, 31, 5, 20), "players": 7, "cpu": Decimal("12.5"),
           "mem_ram": Decimal("512.0"), "mem_paged": Decimal("64.0"), "read": Decimal("10.5"),
           "write": Decimal("20.5"), "sent": Decimal("30.5"), "recv": Decimal("40.5"),
           "fps": Decimal("60.0"), "ping": Decimal("42.0")}
    row.update(overrides)
    return row


def node_row(**overrides) -> dict:
    row = {"node": "n1", "pool_available": 4, "requests_queued": 0, "requests_wait_ms": 0,
           "dcs_queue": 1, "asyncio_queue": 2}
    row.update(overrides)
    return row


def spark_row(**overrides) -> dict:
    row = {"time": datetime(2026, 10, 31, 5, 20), "node": "n1", "users": 3}
    row.update(overrides)
    return row


# ═══════════════════════════════════════════════ 1. the scale table


def test_the_load_scales_table_carries_the_five_published_windows():
    """The width and the count are a module-level table — never a caller string spliced into SQL."""
    assert _stats_read().LOAD_SCALES == EXPECTED_SCALES
    assert _stats_read().LOAD_DEFAULT_SCALE == "24h"


@pytest.mark.parametrize("scale, expected", sorted(EXPECTED_SCALES.items()))
@pytest.mark.asyncio
async def test_every_published_scale_resolves_its_bucket_width_and_count(scale, expected):
    """Each window is fixed width: the payload states the resolved scale, its width and its count."""
    seconds, points = expected
    result = await _actions().get_load_statistics(context(Pool()), scale=scale)
    assert result.success is True
    data = result.data
    assert data["scale"] == scale
    assert data["window"]["bucket_seconds"] == seconds
    assert data["window"]["points"] == points
    start = datetime.fromisoformat(data["window"]["start"])
    end = datetime.fromisoformat(data["window"]["end"])
    assert (end - start).total_seconds() == seconds * (points - 1)


@pytest.mark.asyncio
async def test_an_unpublished_scale_resolves_to_the_24h_window():
    """``13h`` is not a window: the caller gets the default back, in the payload itself."""
    result = await _actions().get_load_statistics(context(Pool()), scale="13h")
    assert result.success is True
    assert result.data["scale"] == "24h"
    assert result.data["window"] == {**result.data["window"],
                                     "bucket_seconds": 300, "points": 288}
    assert (await _actions().get_load_statistics(context(Pool()), scale="")).data["scale"] == "24h"


# ═══════════════════════════════════════════════ 2. the payload


def full_pool() -> Pool:
    """A pool answering the three load statements with one populated row each."""
    return Pool([
        (_SERIES, [series_row()]),
        (_NODES, [node_row()]),
        (_SPARK, [spark_row(users=3), spark_row(users=None), spark_row(users=5)]),
    ])


@pytest.mark.asyncio
async def test_the_payload_carries_the_documented_key_set_and_json_safe_values():
    """Every figure is a JSON number, every time an ISO string, and each row carries every gauge."""
    result = await _actions().get_load_statistics(context(full_pool()))
    assert result.success is True
    data = result.data
    assert set(data) == {"scale", "window", "series", "nodes"}
    assert set(data["window"]) == {"start", "end", "bucket_seconds", "points"}
    assert len(data["series"]) == 1
    assert set(data["series"][0]) == {"time", "players", "cpu", "mem_ram", "mem_paged", "read",
                                      "write", "sent", "recv", "fps", "ping"}
    assert data["series"][0] == {"time": "2026-10-31T05:20:00", "players": 7, "cpu": 12.5,
                                 "mem_ram": 512.0, "mem_paged": 64.0, "read": 10.5,
                                 "write": 20.5, "sent": 30.5, "recv": 40.5,
                                 "fps": 60.0, "ping": 42.0}
    assert set(data["nodes"][0]) == {"node", "pool_available", "requests_queued",
                                     "requests_wait_ms", "dcs_queue", "asyncio_queue", "spark"}
    assert data["nodes"][0]["spark"] == [3, None, 5]
    assert isinstance(data["window"]["start"], str)
    assert isinstance(data["window"]["end"], str)


@pytest.mark.asyncio
async def test_a_bucket_with_no_rows_stays_none_and_never_reads_as_zero():
    """An unwritten bucket is a gap, not an idle server: every figure survives as ``None``."""
    empty = series_row(time=datetime(2026, 10, 31, 5, 25), players=None, cpu=None, mem_ram=None,
                       mem_paged=None, read=None, write=None, sent=None, recv=None, fps=None,
                       ping=None)
    pool = Pool([(_SERIES, [series_row(), empty])])
    result = await _actions().get_load_statistics(context(pool))
    assert result.success is True
    gap = result.data["series"][1]
    for gauge in ("players", "cpu", "mem_ram", "mem_paged", "read", "write", "sent", "recv",
                  "fps", "ping"):
        assert gap[gauge] is None, f"{gauge} was reported as {gap[gauge]!r} instead of None"
    assert gap["time"] == "2026-10-31T05:25:00"


@pytest.mark.asyncio
async def test_an_offline_node_reports_nones_and_an_all_none_spark():
    """A node known to the roster but silent in the window is offline, never zeroed."""
    offline = node_row(node="n2", pool_available=None, requests_queued=None,
                       requests_wait_ms=None, dcs_queue=None, asyncio_queue=None)
    pool = Pool([
        (_NODES, [node_row(), offline]),
        (_SPARK, [spark_row(users=None, node="n2"), spark_row(users=None, node="n2")]),
    ])
    result = await _actions().get_load_statistics(context(pool))
    assert result.success is True
    rows = {row["node"]: row for row in result.data["nodes"]}
    assert rows["n2"]["pool_available"] is None
    assert rows["n2"]["requests_queued"] is None
    assert rows["n2"]["requests_wait_ms"] is None
    assert rows["n2"]["dcs_queue"] is None
    assert rows["n2"]["asyncio_queue"] is None
    assert rows["n2"]["spark"] == [None, None]
    assert rows["n1"]["pool_available"] == 4


# ═══════════════════════════════════════════════ 3. the scope


@pytest.mark.asyncio
async def test_a_cluster_call_binds_no_scope_at_all():
    """Neither name given: the statements carry both parameters as ``None`` and no row filter."""
    pool = Pool()
    await _actions().get_load_statistics(context(pool))
    for marker in (_SERIES, _NODES, _SPARK):
        statement, params = pool.statements_matching(marker)[0]
        assert params["node"] is None
        assert params["server_name"] is None
        assert "node = %(node)s" not in statement
        assert "server_name = %(server_name)s" not in statement


@pytest.mark.asyncio
async def test_a_node_call_binds_the_node_and_a_server_call_binds_the_server():
    """The scope reaches the statement as a bound parameter, never spliced into the SQL text."""
    pool = Pool()
    await _actions().get_load_statistics(context(pool), node="n1")
    for marker in (_SERIES, _NODES, _SPARK):
        statement, params = pool.statements_matching(marker)[0]
        assert params["node"] == "n1"
        assert "node = %(node)s" in statement

    pool = Pool()
    await _actions().get_load_statistics(context(pool), server="SRS-1")
    for marker in (_SERIES, _SPARK):
        statement, params = pool.statements_matching(marker)[0]
        assert params["node"] is None
        assert params["server_name"] == "SRS-1"
        assert "server_name = %(server_name)s" in statement


@pytest.mark.asyncio
async def test_a_server_name_never_reaches_the_sql_text():
    """A name with SQL punctuation in it is a parameter value, not a fragment."""
    hostile = "SRS'); DROP TABLE serverstats; --"
    pool = Pool()
    await _actions().get_load_statistics(context(pool), server=hostile)
    statement, params = pool.statements_matching(_SERIES)[0]
    assert hostile not in statement
    assert params["server_name"] == hostile


# ═══════════════════════════════════════════════ 4. the typed refusals


@pytest.mark.asyncio
async def test_a_non_console_transport_is_refused_before_any_read():
    """The read is console-only, exactly like its two siblings: no pool contact on refusal."""
    pool = Pool()
    result = await _actions().get_load_statistics(context(pool, transport=TRANSPORT_SERVICE))
    assert result.success is False
    assert result.message == _actions().STATS_CONSOLE_ONLY
    assert pool.statements == [], "a refused read must not reach the database"


@pytest.mark.asyncio
async def test_a_missing_pool_is_the_same_typed_refusal_as_the_statistics_read():
    """No pool answers the sentence, not a traceback."""
    result = await _actions().get_load_statistics(context(None))
    assert result.success is False
    assert result.message == _actions().STATS_UNAVAILABLE


def test_the_action_is_discovered_under_the_name_the_console_uses():
    """Loading ``monitoring`` registers the read beside the other two."""
    assert "get_load_statistics" not in discover_actions([])
    assert "get_load_statistics" in discover_actions(["monitoring"])


@pytest.mark.asyncio
async def test_a_node_scope_never_leaves_an_and_where_a_where_belongs():
    """A scope fragment appended after a bare ``FROM`` is a SyntaxError, not a restriction.

    The recording pool never parses a statement, so ``FROM nodestats AND node = %(node)s`` reads as
    a green test and reaches Postgres as an error. Only the TEXT can catch it: no generated
    statement may put an ``AND`` directly after a table in a FROM clause.
    """
    import re

    pool = Pool()
    await _actions().get_load_statistics(context(pool), node="n1")
    assert pool.statements, "the read ran no statement"
    for statement, _params in pool.statements:
        text = " ".join(statement.split())
        assert not re.search(r"\bFROM\s+\w+\s+AND\b", text), text

    pool = Pool()
    await _actions().get_load_statistics(context(pool), server="SRS-1")
    for statement, _params in pool.statements:
        text = " ".join(statement.split())
        assert not re.search(r"\bFROM\s+\w+\s+AND\b", text), text
