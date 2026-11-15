"""The public site's read layer: every public figure binds the site's servers into its statement.

THE ONE ASSERTION THIS SUITE EXISTS FOR. A figure read without the site's server set is a page
that shows every group's pilots. So a recording pool captures the SQL and the parameters of every
statement a public function runs, and:

* each statement carries ``server_name = ANY(%(servers)s::text[])`` and the bound list IS the
  site's server names (the ratchet, over every public function the module exports);
* the statements that legitimately carry no server filter are named, one reason each, in
  ``site_reads.EXEMPT_STATEMENTS`` — a new function either filters or is declared there.

The multi-group case is driven through the REAL resolver (``sites.load_sites`` +
``SiteCatalog.resolve``): a server whose ``managed_by`` names two groups appears in both sites'
parameter lists, and the root site's read names only the untagged server.

NOTHING HERE RAN AGAINST A REAL DATABASE. The pool is a fake, the rows are stubs; the statements
themselves are asserted for shape, not for execution.
"""
from __future__ import annotations

import ast
import inspect
import re
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlparse

from core.utils.helper import ThreadSafeDict
from services.webservice import site_reads, sites

REPO = Path(__file__).resolve().parents[1]

SERVERS = ("S1", "S2")

#: The statements this suite distinguishes, each by a fragment it alone carries.
_COMMUNITY = "AS known_pilots"
_ACTIVITY = "FROM mv_site_activity"
_LEADERBOARD_VIEW = "FROM mv_site_pilot_totals"
_LEADERBOARD_RAW = "FROM statistics s"
_IDENTITY = "SELECT p.ucid, p.name, p.discord_id"
_LINKED_BY_UCID = "ORDER BY p.last_seen DESC NULLS LAST, p.ucid"
_LINKED_BY_DISCORD = "ORDER BY LOWER(COALESCE(p.name"
_PILOT_TOTALS = "AS ejections"
_PILOT_AIRCRAFT = "s.slot AS aircraft"
_PILOT_SERVER = "m.server_name AS server_name,\n       COALESCE(SUM"
_PILOT_RECENT = "m.mission_name"
_PILOT_SEARCH = "ESCAPE"
_SQUADRONS = "WITH site_players AS"
_SQUADRON_POINTS = "campaign_points"
_SQUADRON_RATING = "mu_total"
_TABLE_EXISTS = "to_regclass"
_BOARD = "FROM mv_serverstats"


# --------------------------------------------------------------------------- doubles

class _Cursor:
    """A cursor that records the statement and answers it from the pool's table."""

    def __init__(self, pool):
        self._pool = pool
        self._iterator = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, params))
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
    """A pool that answers by marker and records every call."""

    def __init__(self, answers=None):
        self.answers = list(answers or ())
        self.calls: list[tuple[str, dict]] = []

    def answer(self, statement, params):
        for marker, rows in self.answers:
            if marker in statement:
                return [dict(row) for row in rows]
        return []

    def connection(self):
        return _Conn(self)


@pytest.fixture()
def site():
    return types.SimpleNamespace(name="group1", servers=frozenset(SERVERS))


def _pool(answers=None):
    """A pool whose markers answer the given rows; the keys are this file's marker constants."""
    return _Pool(list((answers or {}).items()))


def _public_async_functions():
    """Every public coroutine in the module's ``__all__`` — the ratchet's enumeration."""
    out = []
    for name in site_reads.__all__:
        value = getattr(site_reads, name, None)
        if inspect.iscoroutinefunction(value) and not name.startswith("_"):
            out.append((name, value))
    return out


def _arguments(name, site, pool):
    """One call per public function, complete enough to run every statement it owns."""
    if name == "pilot_profile":
        return (site, pool, "U1")
    if name == "pilot_search":
        return (site, pool, "pi")
    if name == "my_statistics":
        return (site, pool, 123)
    if name == "server_board":
        return (site, pool, {})
    return (site, pool)


# --------------------------------------------------------------------------- the ratchet

def test_every_public_function_takes_the_site_and_the_pool():
    for name, function in _public_async_functions():
        parameters = list(inspect.signature(function).parameters)
        assert parameters[:2] == ["site", "pool"], (
            f"{name} does not take (site, pool) first: {parameters}")


def test_the_read_layer_does_not_re_derive_membership():
    """Membership is ``sites.py``'s; this module only consumes ``site.servers``."""
    tree = ast.parse(inspect.getsource(site_reads))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "managed_by" not in names | attrs
    assert not names & {"load_servers_config", "managed_by_values", "managed_by_of",
                        "known_managed_by"}


@pytest.mark.asyncio
async def test_every_public_function_binds_the_site_servers_into_its_statement(site):
    for name, function in _public_async_functions():
        pool = _pool({
            _COMMUNITY: [{"known_pilots": 1}],
            _IDENTITY: [{"ucid": "U1", "name": "Pilot", "discord_id": 123}],
            _LINKED_BY_UCID: [{"ucid": "U2", "name": "Alt"}],
            _LINKED_BY_DISCORD: [{"ucid": "U1", "name": "Pilot"}]})
        await function(*_arguments(name, site, pool))
        assert pool.calls, f"{name} ran no statement at all"
        exempt = site_reads.EXEMPT_STATEMENTS.get(name, ())
        for statement, params in pool.calls:
            if site_reads.SERVER_FILTER in statement:
                assert params.get("servers") == sorted(site.servers), (
                    f"{name} bound {params.get('servers')!r}, not the site's servers")
                continue
            assert any(marker in statement for marker, _ in exempt), (
                f"{name} ran a statement without a server filter and without an exemption: "
                f"{statement.strip().splitlines()[0]}")

    # the exemption table is READ by the ratchet — an entry that names no function would be dead
    public = {name for name, _ in _public_async_functions()}
    assert set(site_reads.EXEMPT_STATEMENTS) <= public
    for name, entries in site_reads.EXEMPT_STATEMENTS.items():
        for marker, reason in entries:
            assert marker and reason, f"{name} carries an exemption with no marker or no reason"


@pytest.mark.asyncio
async def test_no_public_read_writes(site):
    for name, function in _public_async_functions():
        pool = _pool()
        await function(*_arguments(name, site, pool))
        for statement, _ in pool.calls:
            head = statement.strip().split(maxsplit=1)[0].upper()
            assert head not in {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "DROP"}, (
                f"{name} runs a writing statement")


# --------------------------------------------------------------------------- the guard

@pytest.mark.asyncio
async def test_a_read_without_a_resolved_site_is_refused():
    with pytest.raises(ValueError):
        await site_reads.community_totals(None, _Pool())
    with pytest.raises(ValueError):
        await site_reads.community_totals(types.SimpleNamespace(), _Pool())


@pytest.mark.asyncio
async def test_an_empty_site_still_binds_an_empty_array():
    pool = _pool()
    empty = types.SimpleNamespace(servers=frozenset())
    await site_reads.community_totals(empty, pool)
    statement, params = pool.calls[0]
    assert site_reads.SERVER_FILTER in statement
    assert params["servers"] == []


# --------------------------------------------------------------------------- community totals

@pytest.mark.asyncio
async def test_community_totals_answers_plain_numbers(site):
    pool = _pool({_COMMUNITY: [{
        "known_pilots": 12, "total_playtime": 3600, "sessions": 4, "avg_session": 900,
        "sorties": 3, "missions_flown": 2, "active_this_week": 7, "active_last_week": 5}]})
    totals = await site_reads.community_totals(site, pool)
    assert totals == {
        "known_pilots": 12, "total_playtime": 3600, "avg_session": 900, "sessions": 4,
        "sorties": 3, "missions_flown": 2, "active_this_week": 7, "active_last_week": 5,
        "active_change": 2}


@pytest.mark.asyncio
async def test_community_totals_answers_off_the_hourly_aggregates(site):
    """The six readouts run no aggregate over the raw join: three views, one statement."""
    pool = _pool()
    await site_reads.community_totals(site, pool)

    assert len(pool.calls) == 1, "the strip is one statement, not a query per figure"
    statement, params = pool.calls[0]
    for view in ("mv_site_activity", "mv_site_pilots", "mv_site_missions"):
        assert f"FROM {view}" in statement, view
    assert "FROM statistics" not in statement
    assert "JOIN statistics" not in statement
    assert "JOIN missions" not in statement
    assert params["servers"] == sorted(site.servers)


def test_the_pilot_figures_name_this_week_and_the_one_before():
    """The key view stores the two most recent active weeks; the reads must name the same weeks."""
    assert site_reads._THIS_WEEK == "(DATE_TRUNC('week', NOW() AT TIME ZONE 'utc'))::date"
    assert site_reads._LAST_WEEK == (
        "(DATE_TRUNC('week', NOW() AT TIME ZONE 'utc') - INTERVAL '1 week')::date"), \
        "the previous ISO week is one week before the current one, not one day"
    statement = site_reads._COMMUNITY_SQL
    assert statement.count("last_week =") == 2   # this week, and the previous week's fallback
    assert statement.count("prev_week =") == 1


def test_the_pilot_figures_are_one_pass_over_the_key_view_without_a_distinct_aggregate():
    """A DISTINCT aggregate sorts the whole view per request; the per-pilot row is that sort, once."""
    statement = site_reads._COMMUNITY_SQL
    assert "DISTINCT" not in statement, "no DISTINCT aggregate reads the key view to disk"
    assert "GROUP BY player_ucid" in statement, "the per-pilot subquery is the one collapsing pass"
    assert statement.count("BOOL_OR(") == 2, "a pilot counts as active if ANY of its rows says so"
    assert "COUNT(*) AS known_pilots" in statement, \
        "known_pilots counts the per-pilot rows, so a pilot on three servers counts once"
    assert statement.count("COUNT(*) FILTER") == 2


# --------------------------------------------------------------------------- activity over time

@pytest.mark.asyncio
async def test_activity_zero_fills_every_week_of_the_window(site):
    now = datetime.now(timezone.utc)
    this_week = site_reads._week_of(now)
    start = site_reads._week_start(now - timedelta(days=30))
    span = ((site_reads._week_start(now) - start).days // 7) + 1
    pool = _pool({_ACTIVITY: [{"week": this_week, "playtime": 60, "pilots": 2, "sorties": 7}]})

    result = await site_reads.activity_over_time(site, pool, "30d")

    assert result["window"] == "30d"
    weeks = result["weeks"]
    assert len(weeks) == span
    assert weeks[-1] == {"week": this_week, "playtime": 60, "pilots": 2, "sorties": 7}
    assert all(entry["playtime"] == 0 and entry["pilots"] == 0 and entry["sorties"] == 0
               for entry in weeks[:-1])
    assert [entry["week"] for entry in weeks] == sorted(entry["week"] for entry in weeks)


@pytest.mark.asyncio
async def test_an_unknown_window_is_the_default_and_says_so(site):
    pool = _pool()
    result = await site_reads.activity_over_time(site, pool, "fortnight")
    assert result["window"] == site_reads.DEFAULT_ACTIVITY_WINDOW


# --------------------------------------------------------------------------- leaderboard

#: a row the stub answers the leaderboard read with: every column the table prints, plus the metric
#: and the favourite airframe.
WIDE_ROW = {"ucid": "U1", "name": "Pilot", "value": 36000, "playtime": 36000, "sorties": 12,
            "takeoffs": 11, "landings": 10, "kills": 9, "pvp": 8, "crashes": 7, "ejections": 6,
            "aircraft": "F-16C_50"}


@pytest.mark.asyncio
async def test_the_leaderboard_caps_the_page_at_fifty_and_takes_an_offset(site):
    pool = _pool({_LEADERBOARD_VIEW: [WIDE_ROW]})
    result = await site_reads.leaderboard(site, pool, "playtime", "all", offset=100)
    statement, params = pool.calls[0]
    assert "FROM mv_site_pilot_totals" in statement
    assert "FROM mv_statistics" not in statement
    assert params["limit"] == 50 and params["offset"] == 100
    assert result["limit"] == 50 and result["offset"] == 100
    row = result["rows"][0]
    assert row["ucid"] == "U1" and row["name"] == "Pilot"
    for key in site_reads.LEADERBOARD_COLUMNS:
        assert key in row, f"the row does not carry {key}"
    assert row["aircraft"] == "F-16C_50"
    assert row["value"] == 36000


@pytest.mark.asyncio
async def test_a_metric_without_its_own_column_reads_the_statement_alias(site):
    """The ranked figure comes from the statement's ``value`` alias, whatever the metric is.

    A metric the table does not print as a column (ships, air targets, a ratio) has no key of its
    own in the row, so reading it by name answered 0 for every pilot while the ordering was right.
    """
    pool = _pool({_LEADERBOARD_VIEW: [{"ucid": "U1", "name": "Pilot", "value": 7,
                                       "playtime": 36000, "sorties": 12, "takeoffs": 11,
                                       "landings": 10, "kills": 9, "pvp": 8, "crashes": 7,
                                       "ejections": 6, "aircraft": "F-16C_50"}]})
    result = await site_reads.leaderboard(site, pool, "ships", "all")
    assert result["rows"][0]["value"] == 7


@pytest.mark.asyncio
async def test_every_row_carries_the_whole_wide_set(site):
    """The answer is the table's row: the columns, the metric's figure and the airframe."""
    pool = _pool({_LEADERBOARD_VIEW: [WIDE_ROW]})
    result = await site_reads.leaderboard(site, pool, "air_targets", "all")
    row = result["rows"][0]
    assert set(row) == {"ucid", "name", "value", "aircraft", *site_reads.LEADERBOARD_COLUMNS}
    assert row["sorties"] == 12 and row["takeoffs"] == 11 and row["landings"] == 10
    assert row["kills"] == 9 and row["pvp"] == 8
    assert row["crashes"] == 7 and row["ejections"] == 6


@pytest.mark.asyncio
async def test_the_all_time_leaderboard_groups_the_view_by_the_pilot_alone(site):
    """The players join is outside the aggregate: one group per pilot, not per (pilot, name)."""
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all")
    statement, params = pool.calls[0]
    assert "GROUP BY ms.player_ucid, p.name" in statement
    assert "JOIN players p ON p.ucid = ms.player_ucid" in statement
    assert "GROUP BY 1, 2" not in statement
    assert "AND ms." + site_reads.SERVER_FILTER in statement, \
        "the detail branch lost its scope"
    assert params["servers"] == sorted(site.servers)


@pytest.mark.asyncio
async def test_the_wide_row_is_aggregated_for_the_page_only(site):
    """The ranking aggregates two columns; the eight printed ones are summed over the page."""
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all")
    statement, _ = pool.calls[0]
    assert "WITH page AS MATERIALIZED (" in statement
    assert "JOIN mv_site_pilot_totals ms\n        ON ms.player_ucid = page.player_ucid" in statement
    for key in site_reads.LEADERBOARD_COLUMNS:
        assert f" AS {key}" in statement, f"the detail branch does not sum {key}"


@pytest.mark.asyncio
@pytest.mark.parametrize("metric", site_reads.LEADERBOARD_METRICS)
async def test_every_metric_projects_and_filters_by_the_same_aggregate(site, metric):
    """The metric is the page's value, its default order and its HAVING filter — one expression."""
    pool = _pool()
    await site_reads.leaderboard(site, pool, metric, "all")
    statement, _ = pool.calls[0]
    expression = site_reads._metric(metric, site_reads._METRIC_VIEW)
    expected = 4 if metric in site_reads.LEADERBOARD_COLUMNS else 3
    assert statement.count(expression) == expected, \
        "the metric is its value, its order and its HAVING filter — one expression each"
    assert "ORDER BY sort_value DESC NULLS LAST, ms.player_ucid" in statement
    assert "LIMIT %(limit)s OFFSET %(offset)s" in statement


@pytest.mark.asyncio
@pytest.mark.parametrize("period", ("all", "month"))
async def test_both_branches_order_then_tie_break_on_the_pilot(site, period):
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", period)
    statement, params = pool.calls[0]
    assert "ORDER BY detail.sort_value DESC NULLS LAST, detail.player_ucid" in statement
    assert "LIMIT %(limit)s OFFSET %(offset)s" in statement
    assert params["limit"] == site_reads.LEADERBOARD_PAGE_SIZE


@pytest.mark.asyncio
@pytest.mark.parametrize("column", sorted(site_reads.SORT_KEYS - {"name"}))
async def test_every_sortable_column_orders_by_its_own_aggregate(site, column):
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all", sort=column)
    statement, _ = pool.calls[0]
    expression = site_reads._metric(column, site_reads._METRIC_VIEW)
    assert f"{expression} AS sort_value" in statement
    assert "ORDER BY sort_value DESC NULLS LAST, ms.player_ucid" in statement


@pytest.mark.asyncio
async def test_the_pilot_column_sorts_by_name(site):
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all", sort="name")
    statement, _ = pool.calls[0]
    assert "LOWER(p.name) AS sort_value" in statement
    assert "ORDER BY sort_value ASC NULLS LAST, ms.player_ucid" in statement


@pytest.mark.asyncio
@pytest.mark.parametrize("sort,direction", [("<script>", "desc"), ("nonsense", "sideways"),
                                            ("", ""), (None, None)])
async def test_a_name_the_page_does_not_offer_falls_back_to_the_metric(site, sort, direction):
    """The column and direction are whitelisted: nothing from the query string reaches the SQL."""
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all", sort=sort, direction=direction)
    statement, _ = pool.calls[0]
    assert "<script>" not in statement and "sideways" not in statement.upper()
    assert "ORDER BY sort_value DESC NULLS LAST" in statement


@pytest.mark.asyncio
async def test_the_favourite_airframe_is_joined_after_the_page_is_cut(site):
    """No wide aggregate and no per-pilot lateral sits on the ranking path."""
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "all")
    statement, _ = pool.calls[0]
    assert "LEFT JOIN LATERAL" in statement
    assert "FROM mv_site_pilot_aircraft a" in statement
    assert statement.index("LIMIT %(limit)s OFFSET %(offset)s") < statement.index(
        "FROM mv_site_pilot_aircraft"), "the airframe join runs before the page is cut"


@pytest.mark.asyncio
async def test_an_unreadable_offset_is_the_first_page(site):
    pool = _pool()
    result = await site_reads.leaderboard(site, pool, "playtime", "all", offset="not-a-number")
    assert result["offset"] == 0


@pytest.mark.asyncio
async def test_an_unknown_metric_is_playtime_and_says_so(site):
    pool = _pool()
    result = await site_reads.leaderboard(site, pool, "nonsense")
    assert result["metric"] == "playtime"


@pytest.mark.asyncio
async def test_a_bounded_period_reads_the_raw_table_with_the_bots_clause(site):
    pool = _pool()
    result = await site_reads.leaderboard(site, pool, "kd_ratio", "month")
    statement, params = pool.calls[0]
    assert "FROM statistics s" in statement
    assert "interval '1 month'" in statement
    assert site_reads.SERVER_FILTER in statement
    assert params["servers"] == sorted(site.servers)
    assert result["period"] == "month"


@pytest.mark.asyncio
async def test_the_bounded_period_sums_the_wide_row_from_the_raw_join(site):
    pool = _pool()
    await site_reads.leaderboard(site, pool, "playtime", "month", sort="takeoffs")
    statement, _ = pool.calls[0]
    assert "SUM(s.takeoffs) AS takeoffs" in statement
    assert "COUNT(*) AS sorties" in statement, "sorties is the statistics-row count"
    assert "JOIN statistics s ON s.player_ucid = page.player_ucid" in statement


def test_the_two_removed_metrics_are_gone():
    for key in ("most_efficient_killers", "most_wasteful_pilots"):
        assert key not in site_reads.METRIC_LABELS
        assert key not in site_reads.COLUMN_SQL
        assert key not in site_reads.LEADERBOARD_METRICS


# --------------------------------------------------------------------------- pilot profile

@pytest.mark.asyncio
async def test_a_pilot_profile_orders_every_figure_by_the_site_and_lists_linked_ucids(site):
    pool = _pool({_IDENTITY: [{"ucid": "U1", "name": "Pilot", "discord_id": 123}],
                    _LINKED_BY_UCID: [{"ucid": "U2", "name": "Alt"}],
                    _PILOT_TOTALS: [{"playtime": 10, "sessions": 1}],
                    _PILOT_AIRCRAFT: [{"aircraft": "F-16C_50", "playtime": 10, "sessions": 1}],
                    _PILOT_SERVER: [{"server_name": "S1", "playtime": 10, "sessions": 1}],
                    _PILOT_RECENT: [{"hop_on": datetime(2026, 11, 8, tzinfo=timezone.utc),
                                     "server_name": "S1"}]})
    result = await site_reads.pilot_profile(site, pool, "U1")

    assert result["found"] is True
    assert [entry["ucid"] for entry in result["ucids"]] == ["U1", "U2"]
    assert result["totals"]["playtime"] == 10
    assert result["per_aircraft"][0]["aircraft"] == "F-16C_50"
    assert result["per_server"][0]["server_name"] == "S1"
    assert result["recent"][0]["hop_on"] == "2026-11-08T00:00:00+00:00"

    totals_call = next(params for statement, params in pool.calls if _PILOT_TOTALS in statement)
    assert totals_call["ucids"] == ["U1", "U2"]
    assert totals_call["servers"] == sorted(site.servers)


@pytest.mark.asyncio
async def test_a_pilot_with_no_session_on_the_site_is_not_found(site):
    pool = _pool()
    result = await site_reads.pilot_profile(site, pool, "U1")
    assert result["found"] is False
    assert result["ucids"] == []


# --------------------------------------------------------------------------- the rating series

_SERIES = "FROM trueskill_hist"
_RATING_ROW = "FROM trueskill\n WHERE"


def _rating_rows(count: int) -> list[dict]:
    """*count* recorded updates, one per day from 2025-01-01, μ creeping up."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [{"skill_mu": round(20.0 + index * 0.01, 2), "skill_sigma": 6.0,
             "time": start + timedelta(days=index)} for index in range(count)]


def _profile_pool(rows: list[dict]) -> _Pool:
    """A pool that answers the identity, the table probe, the current row and the series."""
    return _pool({
        _TABLE_EXISTS: [{"present": True}],
        _IDENTITY: [{"ucid": "U1", "name": "Pilot", "discord_id": None}],
        _SERIES: list(rows),
        _RATING_ROW: [rows[-1]] if rows else [],
    })


@pytest.mark.asyncio
async def test_the_rating_series_is_downsampled_to_sixty_points_keeping_the_ends(site):
    rows = _rating_rows(500)
    result = await site_reads.pilot_profile(site, _profile_pool(rows), "U1")
    rating = result["rating"]

    assert len(rating["points"]) == site_reads.PILOT_RATING_POINTS == 60
    assert rating["count"] == len(rating["points"]), "the count is what this answer carries"
    assert rating["points"][0] == {"time": "2025-01-01T00:00:00+00:00", "mu": 20.0,
                                   "sigma": 6.0}
    assert rating["points"][-1] == {"time": "2026-05-15T00:00:00+00:00", "mu": 24.99,
                                    "sigma": 6.0}
    times = [point["time"] for point in rating["points"]]
    assert times == sorted(times), "the drawn points stay in time order"


@pytest.mark.asyncio
async def test_a_series_under_the_cap_is_drawn_whole(site):
    rows = _rating_rows(7)
    result = await site_reads.pilot_profile(site, _profile_pool(rows), "U1")
    rating = result["rating"]
    assert rating["count"] == 7 and len(rating["points"]) == 7
    assert [point["mu"] for point in rating["points"]] == [row["skill_mu"] for row in rows]


@pytest.mark.asyncio
async def test_history_is_false_exactly_when_one_row_exists(site):
    single = await site_reads.pilot_profile(site, _profile_pool(_rating_rows(1)), "U1")
    assert single["rating"]["history"] is False
    assert single["rating"]["count"] == 1

    pair = await site_reads.pilot_profile(site, _profile_pool(_rating_rows(2)), "U1")
    assert pair["rating"]["history"] is True
    assert pair["rating"]["count"] == 2


@pytest.mark.asyncio
async def test_a_plugin_without_the_history_table_answers_one_point(site):
    """``trueskill_hist`` is probed separately: its absence is a one-point panel, not an error."""
    class _NoHistoryPool(_Pool):
        """Answers each table probe on its own name, so ``trueskill_hist`` is absent."""

        def answer(self, statement, params):
            if "to_regclass" in statement:
                return [{"present": (params or {}).get("name") == "trueskill"}]
            return super().answer(statement, params)

    pool = _NoHistoryPool([(_IDENTITY, [{"ucid": "U1", "name": "Pilot", "discord_id": None}]),
                           (_RATING_ROW, [{"skill_mu": 43.7, "skill_sigma": 4.1,
                                           "time": datetime(2026, 11, 1, 20, 0,
                                                            tzinfo=timezone.utc)}])])
    result = await site_reads.pilot_profile(site, pool, "U1")
    rating = result["rating"]
    assert rating["history"] is False and rating["count"] == 1
    assert rating["conservative"] == 31.4
    assert not any(_SERIES in statement for statement, _params in pool.calls)


@pytest.mark.asyncio
async def test_my_statistics_is_the_union_of_the_discord_links_ucids(site):
    pool = _pool({_LINKED_BY_DISCORD: [{"ucid": "U1", "name": "One"},
                                         {"ucid": "U2", "name": "Two"}],
                    _PILOT_TOTALS: [{"playtime": 20, "sessions": 2}]})
    result = await site_reads.my_statistics(site, pool, 123)

    assert result["found"] is True
    assert [entry["ucid"] for entry in result["ucids"]] == ["U1", "U2"]
    totals_call = next(params for statement, params in pool.calls if _PILOT_TOTALS in statement)
    assert totals_call["ucids"] == ["U1", "U2"]
    assert totals_call["servers"] == sorted(site.servers)


@pytest.mark.asyncio
async def test_a_viewer_with_no_linked_ucid_is_not_found(site):
    pool = _pool()
    result = await site_reads.my_statistics(site, pool, 123)
    assert result["found"] is False


# --------------------------------------------------------------------------- pilot search

@pytest.mark.asyncio
async def test_the_search_caps_the_page_at_twenty_and_escapes_the_term(site):
    pool = _pool({_PILOT_SEARCH: [{"ucid": "U1", "name": "Pilot"}]})
    result = await site_reads.pilot_search(site, pool, "pi%", offset=40)
    statement, params = pool.calls[0]
    assert "ORDER BY LOWER(p.name)" in statement
    assert params["pattern"] == "%pi\\%%"
    assert params["limit"] == 20 and params["offset"] == 40
    assert result["limit"] == 20 and result["offset"] == 40


@pytest.mark.asyncio
async def test_an_empty_search_runs_no_statement(site):
    pool = _pool()
    result = await site_reads.pilot_search(site, pool, "   ")
    assert result["rows"] == [] and pool.calls == []


@pytest.mark.asyncio
async def test_the_search_filters_on_the_lower_cased_name_the_index_covers(site):
    """A btree on ``LOWER(name)`` serves only predicates on that same expression, never ``ILIKE``."""
    pool = _pool({_PILOT_SEARCH: [{"ucid": "U1", "name": "Pilot"}]})
    await site_reads.pilot_search(site, pool, "pi")
    statement, _ = pool.calls[0]
    assert "LOWER(p.name) LIKE" in statement
    assert "ILIKE" not in statement.upper()
    assert "ESCAPE '\\'" in statement
    assert "ORDER BY LOWER(p.name), p.ucid" in statement
    assert "LIMIT %(limit)s OFFSET %(offset)s" in statement


@pytest.mark.asyncio
async def test_the_bound_pattern_is_lower_case_and_neutralises_every_metacharacter(site):
    pool = _pool()
    await site_reads.pilot_search(site, pool, "  Pi%_Ne  ")
    _, params = pool.calls[0]
    pattern = params["pattern"]
    assert pattern == "%pi\\%\\_ne%"
    assert pattern.islower()
    assert pattern.startswith("%") and pattern.endswith("%")


@pytest.mark.asyncio
async def test_the_wildcards_are_appended_after_the_escaping(site):
    """Wrapping after the escaping is what keeps a typed ``%`` literal while the match is contains."""
    pool = _pool()
    await site_reads.pilot_search(site, pool, "Pi%")
    _, params = pool.calls[0]
    pattern = params["pattern"]
    assert pattern == "%pi\\%%"
    assert pattern.startswith("%") and pattern.endswith("%")
    assert pattern.count("\\%") == 1, "the typed % must stay escaped; the wrapping ones must not"


@pytest.mark.asyncio
async def test_a_middle_substring_is_the_bound_pattern(site):
    """The reported defect: the pattern was anchored to the start, so a middle term found nothing."""
    pool = _pool()
    await site_reads.pilot_search(site, pool, "ot O")
    _, params = pool.calls[0]
    assert params["pattern"] == "%ot o%"


@pytest.mark.asyncio
async def test_the_lowered_query_keeps_a_backslash_escaped_exactly_once(site):
    pool = _pool()
    await site_reads.pilot_search(site, pool, "Pi\\%")
    _, params = pool.calls[0]
    assert params["pattern"] == "%pi\\\\\\%%"
    assert params["pattern"].count("\\") == 3


def test_the_escape_helper_adds_exactly_one_backslash_per_metacharacter():
    assert site_reads._like_escape("a%b_c\\d") == "a\\%b\\_c\\\\d"


# --------------------------------------------------------------------------- squadrons

def test_the_squadron_pilot_set_is_read_off_the_pilot_key_view():
    """The site's pilot set is the same join ``mv_site_pilots`` already reduced; no raw scan."""
    statement = site_reads._SQUADRON_SQL
    assert "FROM mv_site_pilots" in statement
    assert "FROM statistics" not in statement
    assert "JOIN missions" not in statement
    assert site_reads.SERVER_FILTER in statement
    assert "SELECT DISTINCT player_ucid" in statement
    assert "COUNT(DISTINCT sp.player_ucid) AS members" in statement
    assert "GROUP BY sq.id, sq.name, sq.description, sq.image_url" in statement


@pytest.mark.asyncio
async def test_squadrons_without_competitive_carry_no_rating(site):
    pool = _pool({_SQUADRONS: [{"id": 1, "name": "VFW", "description": "d",
                                  "image_url": "", "members": 3}],
                    _SQUADRON_POINTS: [{"squadron_id": 1, "campaign_points": 120}]})
    rows = await site_reads.squadrons(site, pool)
    assert rows == [{"id": 1, "name": "VFW", "description": "d", "image_url": "",
                     "members": 3, "campaign_points": 120, "rating": None,
                     "rating_available": False}]


@pytest.mark.asyncio
async def test_squadrons_with_competitive_report_the_combined_rating(site):
    beta = 25.0 / 6.0
    mu, sigma = 30.0, 5.0
    expected = round(mu - 3.0 * (((sigma ** 2 + beta ** 2) / 1) ** 0.5), 2)
    pool = _pool({_SQUADRONS: [{"id": 1, "name": "VFW", "description": "",
                                  "image_url": "", "members": 1}],
                    _TABLE_EXISTS: [{"present": True}],
                    _SQUADRON_RATING: [{"squadron_id": 1, "members": 1,
                                        "mu_total": mu, "sigma_sq_total": sigma ** 2}]})
    rows = await site_reads.squadrons(site, pool)
    assert rows[0]["rating_available"] is True
    assert rows[0]["rating"] == expected


# --------------------------------------------------------------------------- server board

@pytest.mark.asyncio
async def test_the_board_marks_online_state_and_counts_the_players_in_a_slot(site):
    live = {"S1": types.SimpleNamespace(
        name="S1", status=types.SimpleNamespace(name="Running"),
        players={"a": types.SimpleNamespace(active=True),
                 "b": types.SimpleNamespace(active=False)})}
    pool = _pool({_BOARD: [{"server_name": "S1", "playtime": 600, "sorties": 2}]})

    board = await site_reads.server_board(site, pool, live)

    assert [row["server_name"] for row in board] == sorted(SERVERS)
    first = board[0]
    assert first["online"] is True and first["players_online"] == 1
    assert first["playtime"] == 600 and first["sorties"] == 2
    assert first["window"] == site_reads.BOARD_WINDOW_LABEL
    assert board[1]["online"] is False and board[1]["players_online"] == 0


@pytest.mark.asyncio
async def test_the_board_reads_the_all_time_view_not_the_raw_join(site):
    """The page needs playtime and sorties; the all-time aggregate answers both without a join."""
    pool = _pool()
    await site_reads.server_board(site, pool, {})
    statement, params = pool.calls[0]
    assert "FROM mv_serverstats" in statement
    assert "JOIN statistics" not in statement
    assert "make_interval" not in statement
    assert params["servers"] == sorted(site.servers)


@pytest.mark.asyncio
async def test_activity_reads_the_weekly_view_not_the_raw_join(site):
    """The buckets are pre-aggregated per server and ISO week; the read only sums them."""
    pool = _pool()
    await site_reads.activity_over_time(site, pool, "30d")
    statement, params = pool.calls[0]
    assert "FROM mv_site_activity" in statement
    assert "JOIN statistics" not in statement
    assert "week_start >=" in statement
    assert params["days"] == 30
    assert params["servers"] == sorted(site.servers)


# --------------------------------------------------------------------------- multi-group, real resolver

def _server(name, managed_by=None):
    return types.SimpleNamespace(
        name=name, locals={} if managed_by is None else {"managed_by": managed_by})


def _catalog(tmp_path):
    (tmp_path / "servers.yaml").write_text(
        "Shared:\n  port: 10308\n  managed_by: [Alpha, Beta]\n"
        "RootOnly:\n  port: 10309\n"
        "AlphaOnly:\n  port: 10310\n  managed_by: [Alpha]\n", encoding="utf-8")
    config = {"sites": {
        "root": {"slug": ""},
        "alpha": {"slug": "alpha", "group_role": "Alpha"},
        "beta": {"slug": "beta", "group_role": "Beta"},
    }}
    return sites.load_sites(config, config_dir=tmp_path)


def _registry():
    return ThreadSafeDict({
        "Shared": _server("Shared", ["Alpha", "Beta"]),
        "RootOnly": _server("RootOnly"),
        "AlphaOnly": _server("AlphaOnly", ["Alpha"]),
    })


@pytest.mark.asyncio
async def test_a_server_named_by_two_groups_is_read_by_both_sites(tmp_path):
    catalog = _catalog(tmp_path)
    registry = _registry()
    alpha = catalog.resolve("alpha", servers=registry)
    beta = catalog.resolve("beta", servers=registry)

    assert alpha.servers == frozenset({"Shared", "AlphaOnly"})
    assert beta.servers == frozenset({"Shared"})

    alpha_pool, beta_pool = _pool(), _pool()
    await site_reads.community_totals(alpha, alpha_pool)
    await site_reads.community_totals(beta, beta_pool)

    assert "Shared" in alpha_pool.calls[0][1]["servers"]
    assert beta_pool.calls[0][1]["servers"] == ["Shared"]


@pytest.mark.asyncio
async def test_the_root_site_reads_only_the_untagged_servers(tmp_path):
    catalog = _catalog(tmp_path)
    root = catalog.resolve("", servers=_registry())

    assert root.servers == frozenset({"RootOnly"})
    pool = _pool()
    await site_reads.community_totals(root, pool)
    assert pool.calls[0][1]["servers"] == ["RootOnly"]


# --------------------------------------------------------------------------- per airframe

_AIRCRAFT = "FROM mv_site_aircraft"
_HOURS = "GROUP BY hour"
_WEEKDAYS = "GROUP BY dow"


@pytest.mark.asyncio
async def test_the_aircraft_read_ranks_playtime_and_caps_the_list_at_five(site):
    pool = _pool({_AIRCRAFT: [{"slot": "F/A-18C Hornet", "playtime": 7200, "sorties": 9},
                              {"slot": "F-16C Viper", "playtime": 3600, "sorties": 4}]})

    result = await site_reads.top_aircraft(site, pool)

    statement, params = pool.calls[0]
    assert "FROM mv_site_aircraft" in statement
    assert site_reads.SERVER_FILTER in statement
    assert params["servers"] == sorted(site.servers)
    assert params["limit"] == site_reads.AIRCRAFT_ROWS == 5
    assert "ORDER BY 2 DESC" in statement, "the panel's scale is playtime, not sorties"
    assert [row["slot"] for row in result["rows"]] == ["F/A-18C Hornet", "F-16C Viper"]
    assert result["rows"][0] == {"slot": "F/A-18C Hornet", "playtime": 7200, "sorties": 9}
    assert result["limit"] == 5


@pytest.mark.asyncio
async def test_the_aircraft_read_binds_a_caller_limit_and_refuses_a_nonsense_one(site):
    pool = _pool()
    assert (await site_reads.top_aircraft(site, pool, 3))["limit"] == 3
    assert pool.calls[-1][1]["limit"] == 3
    assert (await site_reads.top_aircraft(site, pool, 0))["limit"] == 1
    assert (await site_reads.top_aircraft(site, pool, "many"))["limit"] == site_reads.AIRCRAFT_ROWS


@pytest.mark.asyncio
async def test_an_empty_aircraft_read_answers_no_row_not_one_at_zero(site):
    result = await site_reads.top_aircraft(site, _pool())
    assert result["rows"] == []


# --------------------------------------------------------------------------- hour of day

@pytest.mark.asyncio
async def test_the_hours_read_zero_fills_all_24_hours_and_all_seven_weekdays(site):
    pool = _pool({_HOURS: [{"hour": 3, "seconds": 420, "sessions": 2},
                           {"hour": 20, "seconds": 2400, "sessions": 9}],
                  _WEEKDAYS: [{"dow": 2, "seconds": 660, "sessions": 3},
                              {"dow": 6, "seconds": 2160, "sessions": 12}]})

    result = await site_reads.hours_of_day(site, pool)

    assert len(result["hours"]) == site_reads.HOURS_PER_DAY == 24
    assert [entry["hour"] for entry in result["hours"]] == list(range(24))
    assert len(result["weekdays"]) == site_reads.DAYS_PER_WEEK == 7
    assert [entry["dow"] for entry in result["weekdays"]] == [1, 2, 3, 4, 5, 6, 7]
    # the metric is flight time and session-hours, never a takeoff count
    assert result["hours"][3] == {"hour": 3, "seconds": 420, "sessions": 2}
    assert result["hours"][20] == {"hour": 20, "seconds": 2400, "sessions": 9}
    assert result["weekdays"][1] == {"dow": 2, "seconds": 660, "sessions": 3}
    assert result["weekdays"][5] == {"dow": 6, "seconds": 2160, "sessions": 12}
    # every hour the view did not answer is present at zero, never missing
    assert sum(1 for entry in result["hours"] if entry["seconds"] == 0) == 22
    assert result["hours"][0] == {"hour": 0, "seconds": 0, "sessions": 0}
    assert result["weekdays"][0] == {"dow": 1, "seconds": 0, "sessions": 0}
    assert all("sorties" not in entry for entry in result["hours"] + result["weekdays"])


@pytest.mark.asyncio
async def test_both_hour_statements_are_scoped_to_the_site(site):
    pool = _pool()
    await site_reads.hours_of_day(site, pool)

    assert len(pool.calls) == 2, "one statement for the hours, one for the weekdays"
    for statement, params in pool.calls:
        assert "FROM mv_site_hours" in statement
        assert site_reads.SERVER_FILTER in statement
        assert "seconds" in statement and "sessions" in statement
        assert "sorties" not in statement
        assert params["servers"] == sorted(site.servers)


def test_the_hour_view_splits_a_session_across_the_hours_it_spanned():
    """The histogram is flight time per hour actually flown, never a session booked to its start.

    The statement walks a ``generate_series`` of the session's hours and clamps each to the span, so a
    pilot who hops on at 19:00 and flies until 23:00 fills four hours instead of spiking one.
    """
    for path in (REPO / "plugins" / "userstats" / "db" / "tables.sql",
                 REPO / "plugins" / "userstats" / "db" / "update_v3.14.sql"):
        text = path.read_text(encoding="utf-8")
        view = next(statement for statement in sqlparse.split(text, encoding="utf-8")
                    if "mv_site_hours AS" in statement)
        assert "generate_series" in view, path
        assert "LEAST(" in view and "GREATEST(" in view, path
        assert "EXTRACT(EPOCH FROM SUM(" in view, path
        assert "AS seconds" in view and "AS sessions" in view, path
        assert "GROUP BY" in view, path
        # the session-start attribution this replaced
        assert "EXTRACT(HOUR FROM s.hop_on)" not in view, path
        assert "EXTRACT(ISODOW FROM s.hop_on)" not in view, path
        assert "SUM(s.takeoffs)" not in view, path


def test_the_migration_chain_top_carries_the_new_hour_view():
    """A file without the version bump never runs, and the bump without the file steps over it.

    The chain's TOP hop is the one an install at the previous release runs, so that is the file that
    must carry the re-create: a hop below it would be stepped over by an install whose version is
    already higher.
    """
    version = (REPO / "plugins" / "userstats" / "version.py").read_text(encoding="utf-8")
    declared = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)"', version)
    assert declared, "plugins/userstats/version.py carries no __version__"
    hops = sorted((int(match.group(1)), int(match.group(2)))
                  for match in (REPO / "plugins" / "userstats" / "db").glob("update_v*.*.sql")
                  for match in [re.search(r"update_v(\d+)\.(\d+)\.sql$", match.name)] if match)
    assert hops, "plugins/userstats/db carries no migration at all"
    top = hops[-1]
    assert (int(declared.group(1)), int(declared.group(2))) > top, (
        f"the declared version {declared.group(0)} does not run the top hop {top}")
    migration = REPO / "plugins" / "userstats" / "db" / f"update_v{top[0]}.{top[1]}.sql"
    text = migration.read_text(encoding="utf-8")
    assert "CREATE MATERIALIZED VIEW IF NOT EXISTS mv_site_hours AS" in text
    assert "generate_series" in text, "the hop would build the session-start histogram"
    fresh = (REPO / "plugins" / "userstats" / "db" / "tables.sql").read_text(encoding="utf-8")
    assert "generate_series" in fresh, "a fresh install would build the session-start histogram"


@pytest.mark.asyncio
async def test_an_empty_hour_read_answers_a_full_axis_at_zero(site):
    result = await site_reads.hours_of_day(site, _pool())
    assert len(result["hours"]) == 24 and len(result["weekdays"]) == 7
    assert all(entry["seconds"] == 0 and entry["sessions"] == 0
               for entry in result["hours"] + result["weekdays"])
