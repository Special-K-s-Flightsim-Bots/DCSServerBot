"""The userstats hourly refresh must use the CONCURRENTLY form — one statement per ``execute()``, on
an autocommit connection — and only fall back to the locking form when PostgreSQL refuses.

``REFRESH MATERIALIZED VIEW CONCURRENTLY`` needs an autocommit connection (a transactional one
raises ``FeatureNotSupported`` by design) and refuses a multi-command string. The pool built in
``core/data/impl/nodeimpl.py`` is transactional by default, so the task switches the connection it
takes before the statement and puts it back afterwards. When the concurrent form still fails, the
non-concurrent refresh runs — one statement per call — on a transactional connection, and its
warning fires once per process rather than once per hourly tick.

The plugin owns every view the public site reads, so this file is the one place that pins the set:
``mv_statistics`` first (``mv_serverstats`` is built from it), then the server board's aggregate and
the site's own views.
"""
from __future__ import annotations

import logging

import psycopg.errors
import pytest

from plugins.userstats import commands
from plugins.userstats.commands import UserStatistics

_ORDER = (
    "mv_statistics",
    "mv_serverstats",
    "mv_site_activity",
    "mv_site_pilots",
    "mv_site_pilot_totals",
    "mv_site_pilot_aircraft",
    "mv_site_missions",
    "mv_site_aircraft",
    "mv_site_hours",
)

_CONCURRENT = tuple(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {view};" for view in _ORDER)
_LOCKING = tuple(f"REFRESH MATERIALIZED VIEW {view};" for view in _ORDER)


# --------------------------------------------------------------------------------------- doubles

class _FakeConnection:
    """Records every execute() with the autocommit setting in force when it ran."""

    def __init__(self, pool: "_FakePool") -> None:
        self.pool = pool
        self.autocommit = False
        self.executed: list[tuple[str, bool]] = []
        self.autocommit_calls: list[bool] = []

    async def set_autocommit(self, value: bool) -> None:
        self.autocommit_calls.append(value)
        self.autocommit = value

    async def execute(self, statement: str) -> None:
        self.executed.append((statement, self.autocommit))
        if self.pool.refuse_concurrently and "CONCURRENTLY" in statement:
            raise psycopg.errors.FeatureNotSupported(
                "REFRESH MATERIALIZED VIEW CONCURRENTLY cannot run inside a transaction block"
            )


class _ConnectionContext:
    def __init__(self, pool: "_FakePool") -> None:
        self.pool = pool
        self.conn = _FakeConnection(pool)

    async def __aenter__(self) -> _FakeConnection:
        self.pool.connections.append(self.conn)
        return self.conn

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _FakePool:
    def __init__(self, *, refuse_concurrently: bool = False) -> None:
        self.refuse_concurrently = refuse_concurrently
        self.connections: list[_FakeConnection] = []

    def connection(self) -> _ConnectionContext:
        return _ConnectionContext(self)


def _plugin(pool: _FakePool) -> UserStatistics:
    plugin = object.__new__(UserStatistics)
    plugin.apool = pool
    plugin.log = logging.getLogger("tests.userstats.refresh")
    return plugin


async def _run(plugin: UserStatistics) -> None:
    """The loop's callback without the scheduler machinery."""
    await UserStatistics.refresh_views.coro(plugin)


def _single_refresh_statements(conn: _FakeConnection) -> list[str]:
    statements = [statement for statement, _ in conn.executed]
    assert all(statement.count("REFRESH") == 1 for statement in statements), \
        f"each execute() must carry one REFRESH statement, got {statements!r}"
    return statements


@pytest.fixture(autouse=True)
def _forget_previous_warning(monkeypatch):
    """The once-per-process flag is module state; each test starts as a fresh process would."""
    monkeypatch.setattr(commands, "_refresh_fallback_warned", False)


# --------------------------------------------------------------------------- the concurrent path

@pytest.mark.asyncio
async def test_the_concurrent_path_issues_one_single_statement_execute_per_view():
    pool = _FakePool()
    await _run(_plugin(pool))

    assert len(pool.connections) == 1, "the happy path takes one pooled connection"
    conn = pool.connections[0]
    assert _single_refresh_statements(conn) == list(_CONCURRENT), \
        "mv_statistics first, then the server board and the aggregates the site reads"
    assert all(autocommit for _, autocommit in conn.executed), \
        "REFRESH ... CONCURRENTLY must run on an autocommit connection"
    assert conn.autocommit_calls == [True, False], \
        "the connection is switched on for the refresh and restored for the pool"


@pytest.mark.asyncio
async def test_the_concurrent_path_does_not_run_a_multi_command_string():
    pool = _FakePool()
    await _run(_plugin(pool))

    conn = pool.connections[0]
    for statement, _ in conn.executed:
        body = statement.strip().rstrip(";")
        assert ";" not in body, f"one execute() must not carry several statements: {statement!r}"
        assert body.startswith("REFRESH MATERIALIZED VIEW CONCURRENTLY ")


# ----------------------------------------------------------------------------- the fallback path

@pytest.mark.asyncio
async def test_the_fallback_issues_one_single_statement_execute_per_view_on_a_transactional_connection():
    pool = _FakePool(refuse_concurrently=True)
    await _run(_plugin(pool))

    assert len(pool.connections) == 2, "the fallback takes a fresh pooled connection"
    first, fallback = pool.connections
    # the concurrent attempt failed on its first statement and never reached the second
    assert _single_refresh_statements(first) == [_CONCURRENT[0]]
    assert _single_refresh_statements(fallback) == list(_LOCKING), \
        "mv_statistics first, then the server board and the aggregates the site reads"
    assert all(not autocommit for _, autocommit in fallback.executed), \
        "the fallback stays on the pool's transactional default"
    assert fallback.autocommit_calls == [], "the fallback never switches the connection"
    assert fallback.autocommit is False
    assert first.autocommit is False, "the failed attempt restored its connection for the pool"


@pytest.mark.asyncio
async def test_the_fallback_warns_once_per_process_across_two_runs(caplog):
    caplog.set_level(logging.WARNING, logger="tests.userstats.refresh")

    await _run(_plugin(_FakePool(refuse_concurrently=True)))
    await _run(_plugin(_FakePool(refuse_concurrently=True)))

    warnings = [record for record in caplog.records if "CONCURRENTLY is unavailable" in record.message]
    assert len(warnings) == 1, f"expected one fallback warning per process, got {len(warnings)}"
    assert warnings[0].levelno == logging.WARNING


# ------------------------------------------------------------------- the start-of-refresh line

def _refreshed_views(conn: _FakeConnection) -> set[str]:
    """The view names the connection's statements refreshed."""
    return {statement.split()[-1].rstrip(";") for statement, _ in conn.executed}


@pytest.mark.asyncio
async def test_the_concurrent_path_logs_one_line_first_naming_the_views_and_the_mode(caplog):
    """A stall is found in the log only if the window is announced before the refresh runs."""
    caplog.set_level(logging.INFO, logger="tests.userstats.refresh")
    pool = _FakePool()

    await _run(_plugin(pool))

    infos = [record for record in caplog.records if record.levelno == logging.INFO]
    assert len(infos) == 1, f"one start line per tick, got {[r.message for r in infos]!r}"
    line = infos[0].getMessage()
    assert "concurrent" in line and "locking" not in line
    assert caplog.records[0] is infos[0], "the line is logged at the start, before any refresh ran"
    for view in _refreshed_views(pool.connections[0]):
        assert view in line, f"the start line does not name {view}"


@pytest.mark.asyncio
async def test_the_fallback_logs_its_own_start_line_with_the_locking_mode(caplog):
    caplog.set_level(logging.INFO, logger="tests.userstats.refresh")
    pool = _FakePool(refuse_concurrently=True)

    await _run(_plugin(pool))

    infos = [record for record in caplog.records if record.levelno == logging.INFO]
    assert len(infos) == 2, f"one line per path taken, got {[r.message for r in infos]!r}"
    assert "concurrent" in infos[0].getMessage()
    locking = infos[1].getMessage()
    assert "locking" in locking and "concurrent" not in locking
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert caplog.records.index(infos[1]) == caplog.records.index(warning) + 1, \
        "the locking line is logged at the start of the fallback, right after the warning"
    for view in _refreshed_views(pool.connections[1]):
        assert view in locking, f"the fallback's start line does not name {view}"
