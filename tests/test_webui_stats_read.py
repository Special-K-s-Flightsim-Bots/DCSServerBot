"""The statistics reads: ONE definition of the numbers, exposed as web actions.

Sections, one per acceptance criterion: DISCOVERY (the two actions are registered by the
``monitoring`` plugin's own load, and are absent when it is not loaded); the SHAPES the two
actions answer with against a stubbed cursor (every figure a number, every label the raw column);
the PERIOD (its vocabulary is the bot's filter, an unknown name falls back to the report's own
default, and the clause the action sends IS the clause the report builds); the READ-LAYER
BOUNDARY (the two report modules no longer carry a statement of their own); and the typed
refusals (a missing pool, a non-console transport).

Nothing here needs a live database, a Discord connection or an HTTP request.
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime
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

#: The statements this suite stubs, by a marker each one carries.
_USAGE = "COUNT(DISTINCT p.discord_id) AS members"
_THEATRES = "SELECT m.mission_theatre,"
_MISSIONS = "regexp_replace(m.mission_name"
_MODULES = "ORDER BY 3 DESC"
_UNIQUE = "WITH players_join AS"
_NODE = "FROM nodestats"
_TOP_THEATRE = "SELECT DISTINCT ON (server_name) server_name, mission_theatre"
_RETENTION = "HAVING MIN(DATE(hop_on))"
_DAY_TIME = "to_char(s.hop_on, 'ID') as weekday"
_FREQUENCY = "make_interval(days =>"
_CLUSTER = "COUNT(DISTINCT s.player_ucid) AS players_distinct"

#: The SQL fragments the two report modules must no longer contain — the pin against a second
#: definition of a number.
INLINE_SQL = ("FROM statistics", "FROM serverstats", "FROM nodestats")

#: The two report modules, relative to the repository root.
REPORT_MODULES = ("plugins/monitoring/serverstats.py", "plugins/admin/reports.py")


@pytest.fixture(autouse=True)
def clean_registry():
    """The ``@action`` registry is PROCESS state — emptied and rebuilt around every test.

    ``discover_actions`` registers through the decorators each actions module runs ON IMPORT, so
    the module is dropped from the module cache too: without that the first test would observe a
    real discovery and every later one an import that registered nothing.
    """
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
        #: ``[(marker, rows)]`` — the first marker found in a statement answers it
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


def context(pool: Pool | None = None, *, transport: str = TRANSPORT_WEB,
            filters: dict | None = None) -> ActionContext:
    """An action context over *pool*, the way the console builds one."""
    node = types.SimpleNamespace(apool=pool,
                                 config={"filter": filters} if filters is not None else {})
    ctx = ActionContext(node=node, bus=types.SimpleNamespace(servers={}))
    ctx._transport = transport
    return ctx


def usage_row(**overrides) -> dict:
    row = {"server_name": "SRS-1", "playtime": Decimal("12"), "players": 4, "members": 3}
    row.update(overrides)
    return row


def theatre_row(**overrides) -> dict:
    row = {"mission_theatre": "Caucasus", "playtime": Decimal("5")}
    row.update(overrides)
    return row


def mission_row(**overrides) -> dict:
    row = {"mission_name": "Op Alpha", "playtime": Decimal("3")}
    row.update(overrides)
    return row


def module_row(**overrides) -> dict:
    row = {"slot": "F-16C_50", "num_usage": 7, "playtime": Decimal("2"), "players": 3}
    row.update(overrides)
    return row


def unique_row(**overrides) -> dict:
    row = {"date": date(2026, 10, 1), "total_players": 5, "new_players": 1}
    row.update(overrides)
    return row


def series_row(**overrides) -> dict:
    row = {"time": datetime(2026, 10, 1, 12, 0), "pool_available": 3, "requests_queued": 0,
           "requests_wait_ms": 0, "dcs_queue": 1, "asyncio_queue": 2}
    row.update(overrides)
    return row


def server_pool() -> Pool:
    """A pool answering the five server reads the action makes."""
    return Pool([
        (_USAGE, [usage_row()]),
        (_THEATRES, [theatre_row()]),
        (_MISSIONS, [mission_row()]),
        (_MODULES, [module_row()]),
        (_UNIQUE, [unique_row()]),
    ])


# ═══════════════════════════════════════════════ 1. discovery


def test_the_two_actions_are_discovered_by_their_tool_names():
    """Loading ``monitoring`` registers both reads under the names the MCP service exposes."""
    tools = discover_actions(["monitoring"])
    assert "get_server_statistics" in tools
    assert "get_node_statistics" in tools


def test_an_unloaded_monitoring_plugin_offers_neither_tool():
    """The registry is filled by the PLUGIN's load: a load that does not name it has neither read."""
    tools = discover_actions([])
    assert "get_server_statistics" not in tools
    assert "get_node_statistics" not in tools
    tools = discover_actions(["mission"])
    assert "get_server_statistics" not in tools
    assert "get_node_statistics" not in tools


# ═══════════════════════════════════════════════ 2. the shapes


@pytest.mark.asyncio
async def test_server_statistics_answers_the_documented_shape():
    """Every list is present, every figure is a NUMBER and every label is the raw column value."""
    pool = server_pool()
    result = await _actions().get_server_statistics(context(pool, filters={
        "server_name": "!^\\[.*?\\] ", "mission_name": "!^[^_]+_"}))
    assert result.success is True
    data = result.data
    assert set(data) == {"period", "server", "window_days", "unique_users_grain", "usage",
                         "theatres", "missions", "modules", "unique_users", "retention",
                         "day_time", "frequency", "cluster"}
    assert data["period"] == "month"
    assert data["unique_users_grain"] == "day", "a 30-day period is still counted per day"
    assert data["server"] is None
    assert data["usage"] == [{"server_name": "SRS-1", "playtime": 12, "players": 4, "members": 3,
                              "sessions": 0, "missions": 0, "theatre": None, "new_players": 0}]
    assert data["theatres"] == [{"mission_theatre": "Caucasus", "playtime": 5}]
    assert data["missions"] == [{"mission_name": "Op Alpha", "playtime": 3}]
    assert data["modules"] == [{"slot": "F-16C_50", "num_usage": 7, "playtime": 2, "players": 3}]
    assert data["unique_users"] == [{"date": "2026-10-01", "total_players": 5, "new_players": 1}]
    for row in data["usage"]:
        assert isinstance(row["playtime"], int)
    for row in data["modules"]:
        assert isinstance(row["playtime"], int)


@pytest.mark.asyncio
async def test_server_statistics_applies_the_nodes_name_filters_and_the_named_server():
    """The node's own cleanup patterns reach the statement, and a named server is BOUND, not spliced."""
    pool = server_pool()
    await _actions().get_server_statistics(
        context(pool, filters={"server_name": "!^\\[.*?\\] ", "mission_name": "!^[^_]+_"}),
        server="SRS-1")
    usage = pool.statements_matching(_USAGE)
    assert usage, "the usage statement was never run"
    statement, params = usage[0]
    assert "!^\\[.*?\\] " in statement
    assert params["server_name"] == "SRS-1"
    missions = pool.statements_matching(_MISSIONS)
    assert missions and "!^[^_]+_" in missions[0][0]


@pytest.mark.asyncio
async def test_node_statistics_answers_the_documented_series():
    """One series row per minute, every figure a number, the timestamp JSON-safe."""
    pool = Pool([(_NODE, [series_row()])])
    result = await _actions().get_node_statistics(context(pool), "SRS-1", period="Week")
    assert result.success is True
    assert result.data["node"] == "SRS-1"
    assert result.data["period"] == "week"
    assert result.data["series"] == [{"time": "2026-10-01T12:00:00", "pool_available": 3,
                                      "requests_queued": 0, "requests_wait_ms": 0,
                                      "dcs_queue": 1, "asyncio_queue": 2}]
    statement, params = pool.statements_matching(_NODE)[0]
    assert params == {"period": "week", "node": "SRS-1"}


# ═══════════════════════════════════════════════ 3. the period


@pytest.mark.asyncio
async def test_an_unknown_period_falls_back_to_the_reports_own_default():
    """The filter's vocabulary is the only one: a name it does not publish reads as ``all``."""
    pool = server_pool()
    action = _actions()
    assert (await action.get_server_statistics(context(pool), period="month")).data["period"] == "month"
    assert (await action.get_server_statistics(context(pool), period="fortnight")).data["period"] == "all"
    assert (await action.get_server_statistics(context(pool), period="")).data["period"] == "all"


@pytest.mark.asyncio
async def test_the_action_and_the_report_build_the_same_period_clause():
    """One name, one clause: the statement carries exactly what the report's filter composes."""
    from plugins.userstats.filter import PeriodFilter

    pool = server_pool()
    await _actions().get_server_statistics(context(pool), period="month")
    expected = PeriodFilter("month").filter(None)
    statement, _ = pool.statements_matching(_USAGE)[0]
    assert expected in statement
    assert expected == _stats_read().period_clause("month")[0]


@pytest.mark.asyncio
async def test_an_unknown_node_period_falls_back_to_the_commands_default():
    """``/node statistics`` publishes hour/day/week/month; anything else is its own default."""
    pool = Pool([(_NODE, [])])
    result = await _actions().get_node_statistics(context(pool), "SRS-1", period="fortnight")
    assert result.data["period"] == "hour"


# ═══════════════════════════════════════════════ 4. the read-layer boundary


def assert_no_inline_sql(path):
    """*path* states no statement of its own — the read layer is the only home of a number."""
    text = path.read_text(encoding="utf-8")
    found = [fragment for fragment in INLINE_SQL if fragment in text]
    assert not found, f"{path} still carries inline SQL: {found}"


def test_the_report_modules_no_longer_carry_their_own_statements():
    """A second copy of a statement is how two surfaces start disagreeing about a figure."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for relative in REPORT_MODULES:
        assert_no_inline_sql(root / relative)


# ═══════════════════════════════════════════════ 5. the typed refusals


@pytest.mark.asyncio
async def test_a_missing_pool_is_a_typed_refusal_never_an_exception():
    """No pool answers a sentence, not a traceback — for BOTH reads."""
    action = _actions()
    server = await action.get_server_statistics(context(None), period="month")
    assert server.success is False and server.message
    node = await action.get_node_statistics(context(None), "SRS-1")
    assert node.success is False and node.message


@pytest.mark.asyncio
async def test_a_non_console_transport_is_refused():
    """These reads are installation-wide, so a transport carrying no scope is refused."""
    action = _actions()
    pool = server_pool()
    server = await action.get_server_statistics(context(pool, transport=TRANSPORT_SERVICE))
    assert server.success is False
    node = await action.get_node_statistics(context(Pool([(_NODE, [])]),
                                                    transport=TRANSPORT_SERVICE), "SRS-1")
    assert node.success is False
    assert pool.statements == [], "a refused read must not reach the database"


@pytest.mark.asyncio
async def test_an_empty_node_name_is_a_typed_refusal():
    result = await _actions().get_node_statistics(context(Pool([(_NODE, [])])), "  ")
    assert result.success is False


# ═══════════════════════════════════════════════ 6. the windowed figures


def retention_row(**overrides) -> dict:
    row = {"first_date": date(2026, 10, 1), "new_users": 3, "retained_users": 1}
    row.update(overrides)
    return row


def day_time_row(**overrides) -> dict:
    row = {"weekday": "3", "hour": "14", "players": 2}
    row.update(overrides)
    return row


def frequency_row(**overrides) -> dict:
    row = {"week": "2026-W39", "sessions": 4, "players": 2}
    row.update(overrides)
    return row


def cluster_row(**overrides) -> dict:
    row = {"sessions": 13, "playtime": Decimal("21"), "players_distinct": 5,
           "new_players": 4, "returning_rate": Decimal("0.2000"),
           "avg_session": Decimal("1.62")}
    row.update(overrides)
    return row


def full_pool() -> Pool:
    """A pool answering every read the extended action makes.

    The two ``usage`` rows carry 4 and 3 distinct players — a naive sum of 7 — while the cluster
    read answers 5, so the two figures can never be mistaken for one another.
    """
    return Pool([
        (_USAGE, [usage_row(sessions=9, missions=3, new_players=2),
                  usage_row(server_name="SRS-2", playtime=Decimal("9"), players=3, members=2,
                            sessions=4, missions=2, new_players=5)]),
        (_TOP_THEATRE, [{"server_name": "SRS-1", "mission_theatre": "Caucasus"},
                        {"server_name": "SRS-2", "mission_theatre": "Syria"}]),
        (_THEATRES, [theatre_row()]),
        (_MISSIONS, [mission_row()]),
        (_MODULES, [module_row()]),
        (_UNIQUE, [unique_row()]),
        (_RETENTION, [retention_row(),
                      retention_row(first_date=date(2026, 10, 2), new_users=2, retained_users=0),
                      retention_row(first_date=date(2026, 9, 22), new_users=1, retained_users=1)]),
        (_DAY_TIME, [day_time_row(), day_time_row(weekday="6", hour="20", players=4)]),
        (_FREQUENCY, [frequency_row(), frequency_row(week="2026-W40", sessions=9, players=5)]),
        (_CLUSTER, [cluster_row()]),
    ])


@pytest.mark.asyncio
async def test_every_windowed_figure_is_present_and_carries_its_value():
    """The key set is asserted whole: a series dropped from the mapping fails here."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}))
    assert result.success is True
    data = result.data
    assert set(data) == {"period", "server", "window_days", "unique_users_grain", "usage",
                         "theatres", "missions", "modules", "unique_users", "retention",
                         "day_time", "frequency", "cluster"}
    assert data["window_days"] == 30
    assert data["usage"] == [
        {"server_name": "SRS-1", "playtime": 12, "players": 4, "members": 3, "sessions": 9,
         "missions": 3, "new_players": 2, "theatre": "Caucasus"},
        {"server_name": "SRS-2", "playtime": 9, "players": 3, "members": 2, "sessions": 4,
         "missions": 2, "new_players": 5, "theatre": "Syria"},
    ]
    assert data["retention"] == [
        {"week": "2026-W39", "new_users": 1, "retained_users": 1},
        {"week": "2026-W40", "new_users": 5, "retained_users": 1},
    ]
    assert data["day_time"] == [
        {"weekday": 3, "hour": 14, "players": 2},
        {"weekday": 6, "hour": 20, "players": 4},
    ]
    assert data["frequency"] == [
        {"week": "2026-W39", "sessions": 4, "players": 2},
        {"week": "2026-W40", "sessions": 9, "players": 5},
    ]
    assert data["cluster"] == {"playtime": 21, "players_distinct": 5, "new_players": 4,
                               "returning_rate": 0.2, "avg_session": 1.62}


@pytest.mark.asyncio
async def test_the_cluster_player_figure_is_a_distinct_count_not_a_sum_of_rows():
    """The usage rows add up to 7 distinct players; the cluster answers 5 — the COUNT(DISTINCT)."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}))
    data = result.data
    naive_sum = sum(row["players"] for row in data["usage"])
    assert naive_sum == 7
    assert data["cluster"]["players_distinct"] == 5
    assert data["cluster"]["players_distinct"] != naive_sum
    statement, _ = pool.statements_matching(_CLUSTER)[0]
    assert "COUNT(DISTINCT s.player_ucid) AS players_distinct" in statement


@pytest.mark.asyncio
@pytest.mark.parametrize("given, expected", [
    (84, 84),
    ("84", 84),
    (1, 1),
    (365, 365),
    (0, 30),
    (-1, 30),
    (366, 30),
])
async def test_window_days_outside_the_closed_range_falls_back_to_the_period_default(given, expected):
    """Out of range is never an error: the period's own day count answers, and the statement
    carries it as a bound integer rather than as SQL text."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}),
                                                    period="month", window_days=given)
    assert result.success is True
    assert result.data["window_days"] == expected
    retention_statement, retention_params = pool.statements_matching(_RETENTION)[0]
    assert retention_params["server_name"] is None
    assert f"INTERVAL '{expected} days'" in retention_statement
    day_time_statement, _ = pool.statements_matching(_DAY_TIME)[0]
    assert f"interval '{expected} days'" in day_time_statement
    frequency_statement, frequency_params = pool.statements_matching(_FREQUENCY)[0]
    assert frequency_params["window_days"] == expected
    assert "%(window_days)s" in frequency_statement
    assert "INTERVAL '" not in frequency_statement


@pytest.mark.asyncio
async def test_the_mean_session_is_rounded_as_a_numeric():
    """Postgres has no ``round(double precision, int)``.

    ``EXTRACT(EPOCH …)`` is double precision on an older server (numeric only from 14 on), so the
    two-argument ``ROUND`` around the mean session needs its own cast. Only the statement TEXT can
    catch this: the cursor double never executes it, so every shape test above stays green while the
    real query raises ``UndefinedFunction``.
    """
    pool = Pool()
    await _stats_read().server_cluster(pool, period_clause="1 = 1")
    statement = pool.statements_matching("AS avg_session")[0][0]
    assert "(COALESCE(SUM(s.seconds), 0)" in statement
    assert "::numeric, 2) AS avg_session" in statement, statement


@pytest.mark.asyncio
async def test_a_non_numeric_window_falls_back_to_the_period_default():
    """A word where a day count belongs is a fallback, never a refusal and never spliced SQL."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}), window_days="many")
    assert result.success is True
    assert result.data["window_days"] == 30


@pytest.mark.asyncio
async def test_the_existing_keys_keep_their_values_for_a_caller_passing_nothing_new():
    """The chat reports' path: the five original lists still answer with the figures they did."""
    pool = server_pool()
    result = await _actions().get_server_statistics(context(pool, filters={
        "server_name": "!^\\[.*?\\] ", "mission_name": "!^[^_]+_"}))
    data = result.data
    assert data["period"] == "month"
    assert data["server"] is None
    assert data["usage"][0]["server_name"] == "SRS-1"
    assert data["usage"][0]["playtime"] == 12
    assert data["usage"][0]["players"] == 4
    assert data["usage"][0]["members"] == 3
    assert data["theatres"] == [{"mission_theatre": "Caucasus", "playtime": 5}]
    assert data["missions"] == [{"mission_name": "Op Alpha", "playtime": 3}]
    assert data["modules"] == [{"slot": "F-16C_50", "num_usage": 7, "playtime": 2, "players": 3}]
    assert data["unique_users"] == [{"date": "2026-10-01", "total_players": 5, "new_players": 1}]



# ------------------------------------------------------- the series follows the period, not a guess

@pytest.mark.asyncio
async def test_the_player_series_is_the_periods_own_clause_not_a_guessed_interval():
    """The chart's window is the filter's WHERE clause — the same one every other read takes.

    `today` and `yesterday` are CALENDAR days the filter states as a clause; an interval computed
    here could only approximate them, and its fallback would draw a month under a one-day heading.
    """
    pool = Pool()
    await _stats_read().unique_users(pool, period_clause="s.hop_on >  INTERVAL '1 week'")
    statement = pool.statements_matching("WITH players_join AS")[0][0]
    assert "s.hop_on >  INTERVAL '1 week'" in statement, statement
    assert "DATE(NOW())" not in statement, "the series is still anchored on a relative interval"


@pytest.mark.asyncio
async def test_a_week_grain_counts_distinct_players_per_week():
    """Week grain is a COUNT(DISTINCT …) per bucket, never a sum of the daily counts."""
    pool = Pool()
    await _stats_read().unique_users(pool, grain="week")
    statement = pool.statements_matching("WITH players_join AS")[0][0]
    assert "date_trunc('week', MIN(first_day))" in statement, statement
    assert "INTERVAL '7 days'" in statement, statement
    assert "COUNT(DISTINCT s.player_ucid)" in statement, statement


def test_the_grain_follows_the_periods_length():
    """The switch is the period's own day count; ``all`` is the whole history and is weekly."""
    stats_read = _stats_read()
    assert stats_read.unique_users_grain("today") == "day"
    assert stats_read.unique_users_grain("month") == "day"
    assert stats_read.unique_users_grain("quarter") == "day"       # exactly at the limit
    assert stats_read.unique_users_grain("halfyear") == "week"
    assert stats_read.unique_users_grain("year") == "week"
    assert stats_read.unique_users_grain("all") == "week"
    assert stats_read.unique_users_grain(None) == "day"
    assert stats_read.unique_users_grain("nonsense") == "day"


@pytest.mark.asyncio
async def test_a_long_period_reads_the_series_by_week_and_says_so():
    """The payload carries the bucket, so the page cannot label a weekly series per day."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}), period="year")
    assert result.success is True
    assert result.data["period"] == "year"
    assert result.data["unique_users_grain"] == "week"
    statement = pool.statements_matching("WITH players_join AS")[0][0]
    assert "date_trunc('week'" in statement, statement


@pytest.mark.asyncio
async def test_a_short_period_still_reads_the_series_by_day():
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}), period="today")
    assert result.data["unique_users_grain"] == "day"
    statement = pool.statements_matching("WITH players_join AS")[0][0]
    assert "date_trunc('day'" in statement, statement


@pytest.mark.asyncio
async def test_a_weekly_series_is_labelled_as_a_week_not_a_bare_date():
    """A week bucket prints the week's label, the same shape the retention card uses."""
    pool = full_pool()
    result = await _actions().get_server_statistics(context(pool, filters={}), period="year")
    labels = [row["date"] for row in result.data["unique_users"]]
    assert labels, "the fixture answered no series"
    for label in labels:
        assert "-W" in label and len(label) == 8, f"a week bucket was labelled {label!r}"
