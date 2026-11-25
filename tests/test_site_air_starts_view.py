"""The precomputed air-start aggregate: which relation answers which sortie figure.

``mv_site_air_starts`` (one row per server, pilot and ISO week) is the missionstats plugin's hourly
sum over the event stream. Two public reads consume it — the site-wide air starts per ISO week and
the per-pilot ``sorties`` figure of the all-time board — so neither page request scans
``missionstats``. These pins drive the shipped reads through a recording pool and assert the
STATEMENT SHAPE and the RELATION each figure comes from; nothing here is timed and nothing ran
against a real database.

The migration half is pinned as text equality: the view's WHERE clause is the predicate
``site_reads._start_predicate('mst.')`` returns, copied into the two ``.sql`` files because a
``.sql`` file cannot call it. A drifted copy is a view that counts a different event set from the
read that falls back to the raw table.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from services.webservice import site_reads

REPO = Path(__file__).resolve().parents[1]
SERVERS = ("S1", "S2")

MISSIONSTATS = "missionstats"
VIEW = "mv_site_air_starts"

_COMMUNITY = "AS known_pilots"
_TABLE_EXISTS = "to_regclass"
_RAW_WEEKS = "FROM missionstats mst"
_ALL_TIME = "FROM mv_site_pilot_totals"
_BOUNDED = "FROM statistics s"


# --------------------------------------------------------------------------- doubles

class _Cursor:
    """A cursor that records the statement and answers it from the pool."""

    def __init__(self, pool):
        self._pool = pool
        self._iterator = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, dict(params or {})))
        self._iterator = iter(self._pool.answer(statement, params))

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
    def __init__(self, pool):
        self._pool = pool

    def cursor(self, *args, **kwargs):
        return _Cursor(self._pool)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pool:
    """A pool holding a set of RELATIONS; every other statement answers by marker."""

    def __init__(self, relations=(), answers=None):
        self.relations = set(relations)
        self.answers = list(answers or ())
        self.calls: list[tuple[str, dict]] = []

    def answer(self, statement, params):
        params = dict(params or {})
        if _TABLE_EXISTS in statement:
            return [{"present": params.get("name") in self.relations}]
        for marker, rows in self.answers:
            if marker in statement:
                return [dict(row) for row in rows]
        return []

    def connection(self):
        return _Conn(self)

    @property
    def statements(self):
        return [statement for statement, _ in self.calls]

    def data_statements(self):
        """The statements that read a relation (the existence probes aside)."""
        return [statement for statement in self.statements if _TABLE_EXISTS not in statement]


class _RefreshConn:
    def __init__(self, pool):
        self._pool = pool

    async def set_autocommit(self, value):
        self._pool.autocommit.append(value)

    async def execute(self, statement):
        self._pool.statements.append(statement)
        if self._pool.fail_concurrent and "CONCURRENTLY" in statement:
            from psycopg.errors import FeatureNotSupported

            raise FeatureNotSupported("cannot run inside a transaction block")


class _RefreshContext:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return _RefreshConn(self._pool)

    async def __aexit__(self, *exc):
        return False


class _RefreshPool:
    def __init__(self, fail_concurrent=False):
        self.statements: list[str] = []
        self.autocommit: list[bool] = []
        self.fail_concurrent = fail_concurrent

    def connection(self):
        return _RefreshContext(self)


class _Log:
    def __init__(self):
        self.debugs: list[tuple] = []
        self.warnings: list[tuple] = []

    def debug(self, *args):
        self.debugs.append(args)

    def warning(self, *args):
        self.warnings.append(args)


class _Plugin:
    """The shipped task's ``self``: a log recorder and a pool that records statements."""

    def __init__(self, fail_concurrent=False):
        self.log = _Log()
        self.apool = _RefreshPool(fail_concurrent)


@pytest.fixture()
def site():
    return types.SimpleNamespace(name="group1", servers=frozenset(SERVERS))


def _pool(relations, answers=None):
    return _Pool(relations, list((answers or {}).items()))


def _missionstats_module():
    """The plugin's commands module, imported on demand.

    ``plugins/missionstats/commands.py`` imports ``openpyxl`` for its Excel export, which a test
    interpreter does not carry; the module is stubbed for the import only, as the mission plugin's
    pins do.
    """
    import sys

    for name in ("openpyxl", "openpyxl.utils"):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.get_column_letter = lambda index: "A"
            sys.modules[name] = module
    sys.modules["openpyxl"].utils = sys.modules["openpyxl.utils"]
    from plugins.missionstats import commands as module

    return module


# --------------------------------------------------------------------------- the per-week series

@pytest.mark.asyncio
async def test_the_weekly_series_reads_the_view_and_never_the_event_table(site):
    pool = _pool({MISSIONSTATS, VIEW},
                 {VIEW: [{"week": "2026-W44", "air_starts": 4}]})
    series = await site_reads.sortie_air_starts(site, pool)
    assert series == {"2026-W44": 4}
    assert any(VIEW in statement for statement in pool.statements)
    for statement in pool.data_statements():
        assert f"FROM {MISSIONSTATS}" not in statement, (
            "the page still aggregates the event table while the view answers the figure")


@pytest.mark.asyncio
async def test_the_weekly_series_falls_back_to_the_event_table_without_the_view(site):
    pool = _pool({MISSIONSTATS},
                 {_RAW_WEEKS: [{"week": "2026-W44", "air_starts": 5}]})
    series = await site_reads.sortie_air_starts(site, pool)
    assert series == {"2026-W44": 5}
    raw = [statement for statement in pool.statements if _RAW_WEEKS in statement]
    assert len(raw) == 1, "the fallback is the raw aggregate"
    assert site_reads._start_predicate("mst.") in raw[0], (
        "the fallback counts a different event set from the view")


@pytest.mark.asyncio
async def test_without_the_event_stream_the_series_is_none(site):
    pool = _pool(set())
    assert await site_reads.sortie_air_starts(site, pool) is None


@pytest.mark.asyncio
async def test_without_the_event_stream_the_page_sorties_are_takeoffs(site):
    pool = _pool(set(), {_COMMUNITY: [{"known_pilots": 1, "total_playtime": 60, "sessions": 2,
                                       "avg_session": 30, "sorties": 10, "missions_flown": 1,
                                       "active_this_week": 1, "active_last_week": 1}]})
    totals = await site_reads.community_totals(site, pool)
    assert totals["sorties"] == 10
    assert totals["air_starts"] is False, (
        "False is how the page knows it may only claim a takeoff count")


# --------------------------------------------------------------------------- the all-time board

@pytest.mark.asyncio
async def test_the_all_time_board_reads_the_view_and_never_the_event_table(site):
    pool = _pool({MISSIONSTATS, VIEW})
    await site_reads.leaderboard(site, pool, "playtime", "all")
    statement = pool.data_statements()[-1]
    assert _ALL_TIME in statement
    assert "starts AS MATERIALIZED" in statement
    assert f"FROM {VIEW}" in statement
    assert f"FROM {MISSIONSTATS}" not in statement, (
        "the all-time board still aggregates the event table while the view answers the figure")
    assert "SUM(starts)::bigint AS air_starts" in statement
    assert "LEFT JOIN starts st ON st.ucid = ms.player_ucid" in statement


@pytest.mark.asyncio
async def test_the_all_time_board_falls_back_to_the_event_table_without_the_view(site):
    pool = _pool({MISSIONSTATS})
    await site_reads.leaderboard(site, pool, "playtime", "all")
    statement = pool.data_statements()[-1]
    assert f"FROM {MISSIONSTATS}" in statement
    assert f"FROM {VIEW}" not in statement
    assert site_reads._start_predicate("mst.") in statement
    assert "COUNT(*)::bigint AS air_starts" in statement


@pytest.mark.asyncio
async def test_a_bounded_period_still_aggregates_the_event_table(site):
    """A week grain cannot answer a period that starts mid-week, so the period board keeps the raw
    aggregate: its sorties column must not widen the window it states."""
    pool = _pool({MISSIONSTATS, VIEW})
    await site_reads.leaderboard(site, pool, "playtime", "quarter")
    statement = pool.data_statements()[-1]
    assert _BOUNDED in statement
    assert f"FROM {MISSIONSTATS}" in statement
    assert f"FROM {VIEW}" not in statement
    assert site_reads._start_predicate("mst.") in statement


# --------------------------------------------------------------------------- the view's definition

def _mv_statement(path: Path) -> str:
    """The ``CREATE MATERIALIZED VIEW ... mv_site_air_starts`` statement of a ``.sql`` file."""
    import sqlparse

    for statement in sqlparse.split(path.read_text(encoding="utf-8")):
        if "CREATE MATERIALIZED VIEW" in statement and VIEW in statement:
            return statement.strip()
    raise AssertionError(f"{path} carries no CREATE MATERIALIZED VIEW {VIEW}")


def test_the_view_counts_exactly_the_predicate_the_read_falls_back_to():
    predicate = site_reads._start_predicate("mst.")
    for name in ("tables.sql", "update_v3.7.sql"):
        statement = _mv_statement(REPO / "plugins" / "missionstats" / "db" / name)
        assert predicate in statement, f"{name} counts a different event set from the read"


def test_both_files_ship_the_same_view_definition():
    """A fresh install runs ``tables.sql``, an existing one the update hop: one definition only."""
    root = REPO / "plugins" / "missionstats" / "db"
    normalise = lambda text: re.sub(r"\s+", " ", text).strip()
    assert normalise(_mv_statement(root / "tables.sql")) == \
        normalise(_mv_statement(root / "update_v3.7.sql"))


def test_the_view_carries_the_unique_index_the_concurrent_refresh_needs():
    root = REPO / "plugins" / "missionstats" / "db"
    for name in ("tables.sql", "update_v3.7.sql"):
        text = (root / name).read_text(encoding="utf-8")
        assert "UNIQUE INDEX IF NOT EXISTS idx_mv_site_air_starts_key" in text, name
        assert f"ON {VIEW} (server_name, player_ucid, week_start)" in text, name
        assert f"INDEX IF NOT EXISTS idx_mv_site_air_starts_ucid ON {VIEW} (player_ucid)" in text, name


def test_the_migration_is_the_hop_the_next_boot_runs():
    """The file is named for the version an install is upgrading FROM, the bump for where it goes."""
    root = REPO / "plugins" / "missionstats"
    version = re.search(r'__version__ = "([0-9.]+)"',
                        (root / "version.py").read_text(encoding="utf-8")).group(1)
    hops = sorted(path.name for path in (root / "db").glob("update_v*.sql"))
    revisions = sorted((name[len("update_v"):-len(".sql")] for name in hops),
                       key=lambda value: tuple(int(part) for part in value.split(".")))
    newest = f"{revisions[-1][:revisions[-1].rfind('.')]}.{int(revisions[-1].split('.')[-1]) + 1}"
    assert newest == version, "the newest hop must be the one the declared version upgrades FROM"


# --------------------------------------------------------------------------- the hourly refresh

def test_the_hourly_task_refreshes_the_view_concurrently():
    module = _missionstats_module()
    loop = module.MissionStatistics.refresh_air_starts
    from discord.ext import tasks

    assert isinstance(loop, tasks.Loop), "the refresh is a discord.py task, not a coroutine"
    assert loop.hours == 1, "the aggregate is rebuilt hourly"
    plugin = _Plugin()
    import asyncio

    asyncio.run(loop.coro(plugin))
    assert len(plugin.apool.statements) == 1, "one statement, so CONCURRENTLY stays legal"
    assert plugin.apool.statements[0] == (
        f"REFRESH MATERIALIZED VIEW CONCURRENTLY {VIEW};")
    assert plugin.apool.autocommit == [True, False], (
        "the concurrent refresh needs an autocommit connection and must hand it back")


def test_the_refresh_falls_back_to_the_locking_statement_once_per_process(monkeypatch):
    module = _missionstats_module()
    monkeypatch.setattr(module, "_air_starts_refresh_fallback_warned", False)
    plugin = _Plugin(fail_concurrent=True)
    import asyncio

    asyncio.run(module.MissionStatistics.refresh_air_starts.coro(plugin))
    assert plugin.apool.statements[-1] == f"REFRESH MATERIALIZED VIEW {VIEW};"
    assert len(plugin.log.warnings) == 1, "the fallback warning is logged once per process"

    asyncio.run(module.MissionStatistics.refresh_air_starts.coro(plugin))
    assert len(plugin.log.warnings) == 1, "a second tick must not log the warning again"
    assert plugin.apool.statements[-1] == f"REFRESH MATERIALIZED VIEW {VIEW};"
