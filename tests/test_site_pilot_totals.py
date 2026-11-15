"""The per-pilot aggregate: its grain, its playtime, and the columns the leaderboard's metrics sum.

``mv_site_pilot_totals`` exists so the all-time leaderboard aggregates one row per (server, pilot)
instead of the per-airframe rows ``mv_statistics`` carries. Two things keep that honest and are pinned
here: the view must carry EVERY column :data:`site_reads.METRIC_SQL` sums (a metric whose column is
missing fails at request time, on a page, not at import), and its playtime must be the sum of the
rounded per-grain values the leaderboard showed before, or every pilot's figure moves by a second.

NOTHING HERE RUNS AGAINST A DATABASE. The view definition is read from the shipped SQL and the metric
list from the module; the shapes are asserted, not the results.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import sqlparse

from services.webservice import site_reads

REPO = Path(__file__).resolve().parents[1]
DB_DIR = REPO / "plugins" / "userstats" / "db"
VIEW = "mv_site_pilot_totals"
HOPS = ("update_v3.14.sql", "tables.sql")


def _view_body(filename: str) -> str:
    """The CREATE MATERIALIZED VIEW statement for :data:`VIEW`, comments dropped, space collapsed."""
    text = (DB_DIR / filename).read_text(encoding="utf-8")
    for statement in sqlparse.split(text, encoding="utf-8"):
        if VIEW in statement and "CREATE MATERIALIZED VIEW" in statement.upper():
            lines = [line for line in statement.splitlines()
                     if not line.strip().startswith("--")]
            return re.sub(r"\s+", " ", " ".join(lines)).strip()
    raise AssertionError(f"{filename} creates no {VIEW}")


@pytest.fixture(params=HOPS)
def body(request) -> str:
    return _view_body(request.param)


def _metric_columns() -> set[str]:
    """Every ``ms.`` column the metrics sum, across the leaderboard's whole metric list."""
    columns: set[str] = set()
    for metric in site_reads.LEADERBOARD_METRICS:
        expression = site_reads._metric(metric, site_reads._METRIC_VIEW)
        columns.update(re.findall(r"\bms\.(\w+)", expression))
    return columns


def test_every_metric_column_is_carried_by_the_view(body):
    missing = sorted(column for column in _metric_columns()
                     if not re.search(rf"\b{re.escape(column)}\b", body))
    assert not missing, f"{VIEW} does not carry {missing}, so those metrics would fail at request time"


def test_the_view_grain_is_one_row_per_server_and_pilot(body):
    """The inner aggregate reproduces mv_statistics' grain; the outer one rolls it up per pilot."""
    assert re.sub(r"\s+", " ", body).count("GROUP BY 1, 2, 3, 4, 5") == 1, \
        "the inner aggregate must keep mv_statistics' (pilot, server, slot, side, tail) grain"
    assert body.endswith("GROUP BY 1, 2;"), "the view must roll the grain up to (server, pilot)"
    assert "s.slot" in body and "s.side" in body and "s.tail_no" in body


def test_the_view_playtime_stays_the_sum_of_the_rounded_grains(body):
    """mv_statistics rounds each grain; a pilot's total sums those values, not one raw sum."""
    assert "ROUND(SUM(EXTRACT(EPOCH FROM" in body
    assert body.count("SUM(g.playtime)") == 1


def test_the_unique_index_is_the_pair_concurrently_refreshes_on():
    for filename in HOPS:
        statements = [re.sub(r"\s+", " ", s).strip()
                      for s in sqlparse.split((DB_DIR / filename).read_text(encoding="utf-8"))]
        wanted = (f"CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_pilot_totals ON {VIEW} "
                  f"(server_name, player_ucid);")
        assert wanted in statements, f"{filename} carries no unique index for {VIEW}"


def test_the_wide_columns_are_carried_and_summed_by_the_view(body):
    """The leaderboard prints these; a column missing from the view fails on a page, not at import."""
    for column in ("sorties", "takeoffs", "landings", "ejections"):
        assert f"AS {column}" in body, f"{VIEW} does not carry {column}"
        assert f"SUM(g.{column})" in body, f"{VIEW} does not roll {column} up per pilot"
    assert "COUNT(*) AS sorties" in body, "sorties is the statistics-row count at the inner grain"


def test_the_favourite_airframe_view_is_one_row_per_server_pilot_and_slot():
    text = (DB_DIR / "update_v3.14.sql").read_text(encoding="utf-8")
    body = next(statement for statement in sqlparse.split(text, encoding="utf-8")
                if "mv_site_pilot_aircraft" in statement
                and "CREATE MATERIALIZED VIEW" in statement.upper())
    flat = re.sub(r"\s+", " ", body)
    assert "SELECT m.server_name, s.player_ucid, s.slot" in flat
    assert "GROUP BY 1, 2, 3" in flat
    assert ("CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_site_pilot_aircraft ON "
            "mv_site_pilot_aircraft (server_name, player_ucid, slot);") in \
        [re.sub(r"\s+", " ", s).strip() for s in sqlparse.split(text, encoding="utf-8")]


def test_the_all_time_leaderboard_reads_the_aggregate():
    statement = site_reads._LEADERBOARD_VIEW_SQL.format(
        value=site_reads._metric("playtime", site_reads._METRIC_VIEW),
        sort_expr=site_reads._sort_expression("playtime", site_reads._METRIC_VIEW),
        detail_aggregates=site_reads._detail_aggregates(site_reads._METRIC_VIEW),
        detail_list=site_reads._detail_list(),
        direction="DESC")
    assert f"FROM {VIEW}" in statement
    assert "FROM mv_statistics" not in statement
    assert "GROUP BY ms.player_ucid, p.name" in statement
    assert site_reads.SERVER_FILTER in statement


def test_a_bounded_period_still_reads_the_mission_join():
    """The aggregate has no mission timestamp, so only the all-time page can use it."""
    assert "JOIN missions m ON m.id = s.mission_id" in site_reads._LEADERBOARD_RAW_SQL
    assert "FROM statistics s" in site_reads._LEADERBOARD_RAW_SQL


def test_the_docstring_names_the_branch_the_aggregate_cannot_answer():
    doc = site_reads.leaderboard.__doc__ or ""
    assert VIEW in doc
    assert "no mission timestamp" in doc
