"""The overview's standings read asks for no per-pilot start count.

``site_reads.leaderboard`` fills a ``sorties`` column by joining a per-pilot start aggregate. The
overview's standings table prints the pilot and its playtime and no start count, so that read passes
``air_starts=False``: no ``starts`` CTE, no ``LEFT JOIN starts``, no read of ``missionstats`` or
``mv_site_air_starts`` and no existence probe for either. The rankings page keeps the default and
still joins the aggregate — the control that the switch changes the STATEMENT, not the read.

These pins drive the shipped page reads through a recording pool and assert the STATEMENT SHAPE and
the relations it names; nothing here is timed and nothing ran against a real database.

The overview's hero sorties figure is a SEPARATE read (:func:`site_reads.sortie_air_starts`, which
answers the site-wide air starts per ISO week) and it does read ``mv_site_air_starts``; it is not the
standings read and its figure is unchanged. The standings statements are therefore attributed by
slicing the recorded calls around the ``leaderboard`` call.
"""
from __future__ import annotations

import types

import pytest

from services.webservice import site_reads
from services.webservice.pages import site_leaderboard, site_overview

SERVERS = ("S1", "S2")

MISSIONSTATS = "missionstats"
VIEW = "mv_site_air_starts"

_PROBE = "to_regclass"
_STARTS_CTE = "starts AS MATERIALIZED"
_STARTS_JOIN = "LEFT JOIN starts st"
_VIEW_STARTS = "FROM mv_site_air_starts"
_VIEW_BOARD = "FROM mv_site_pilot_totals"
_COMMUNITY = "AS known_pilots"
_BOARD = "FROM mv_serverstats"

#: One standings row: the takeoff sum is 7, the start aggregate would have raised it to 9.
ROWS = [{"ucid": "U1", "name": "Pilot One", "value": 3600, "playtime": 3600, "sorties": 7,
         "landings": 6, "kills": 2, "pvp": 1, "crashes": 0, "ejections": 0,
         "aircraft": "F-16C_50"}]


# --------------------------------------------------------------------------- doubles

class _Cursor:
    """A cursor that records the statement and answers it from the pool."""

    def __init__(self, pool):
        self._pool = pool
        self._rows = iter(())

    async def execute(self, statement, params=None):
        self._pool.calls.append((statement, dict(params or {})))
        self._rows = iter(self._pool.answer(statement, params))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._rows)
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
    """A pool holding a set of RELATIONS; every statement is recorded and answered by marker."""

    def __init__(self, relations=(MISSIONSTATS, VIEW)):
        self.relations = set(relations)
        self.calls: list[tuple[str, dict]] = []

    def answer(self, statement, params):
        params = dict(params or {})
        if _PROBE in statement:
            return [{"present": params.get("name") in self.relations}]
        if _VIEW_BOARD in statement:
            return [dict(row) for row in ROWS]
        if _COMMUNITY in statement:
            return [{"known_pilots": 2, "total_playtime": 7200, "avg_session": 3600,
                     "sessions": 2, "sorties": 5, "missions_flown": 1, "active_this_week": 2,
                     "active_last_week": 1}]
        if _BOARD in statement:
            return [{"server_name": name, "playtime": 3600, "sorties": 5} for name in SERVERS]
        if _VIEW_STARTS in statement:
            return [{"week": "2026-W44", "air_starts": 3}]
        return []

    def connection(self):
        return _Conn(self)

    @property
    def statements(self) -> list[str]:
        return [statement for statement, _ in self.calls]


@pytest.fixture()
def site():
    return types.SimpleNamespace(name="group1", servers=frozenset(SERVERS))


def _probed(statements: list[str], calls: list[tuple[str, dict]], name: str) -> int:
    """How many existence probes for *name* the recorded calls carry."""
    return sum(1 for statement, params in calls
               if _PROBE in statement and params.get("name") == name)


# --------------------------------------------------------------------------- the overview

@pytest.mark.asyncio
async def test_the_overview_standings_read_never_asks_for_a_start_count(site, monkeypatch):
    """The page's standings statement joins no start aggregate and probes neither relation."""
    pool = _Pool()
    slices: list[list[tuple[str, dict]]] = []
    original = site_reads.leaderboard

    async def spy(*args, **kwargs):
        start = len(pool.calls)
        try:
            return await original(*args, **kwargs)
        finally:
            slices.append(pool.calls[start:])

    monkeypatch.setattr(site_reads, "leaderboard", spy)
    answer = await site_overview.read(site, pool, sections=("standings",))

    assert len(slices) == 1, "the page runs its standings read exactly once"
    calls = slices[0]
    statements = [statement for statement, _ in calls]
    assert len(statements) == 1, f"the standings read issued {len(statements)} statements"
    statement = statements[0]
    assert _VIEW_BOARD in statement, "the standings read did not read the pilot totals view"
    assert _STARTS_JOIN not in statement
    assert _STARTS_CTE not in statement
    assert _VIEW_STARTS not in statement
    assert MISSIONSTATS not in statement
    assert _probed(statements, calls, MISSIONSTATS) == 0
    assert _probed(statements, calls, VIEW) == 0
    # the page as a whole joins no start aggregate either
    assert not any(_STARTS_JOIN in text for text in pool.statements)
    assert answer["pilots"]["rows"], "the standings read answered no row"


@pytest.mark.asyncio
async def test_the_overview_standings_rows_still_carry_pilot_playtime_and_sorties(site):
    """``air_starts=False`` answers usable rows: name, playtime and the takeoff sum."""
    pool = _Pool()
    answer = await site_overview.read(site, pool, sections=("standings",))
    rows = answer["pilots"]["rows"]
    assert rows and rows[0]["name"] == "Pilot One"
    assert rows[0]["playtime"] == 3600
    assert rows[0]["sorties"] == 7, "the row's sorties is the recorded takeoff sum"
    assert rows[0]["aircraft"] == "F-16C_50"
    statement = next(text for text in pool.statements if _VIEW_BOARD in text)
    assert "GREATEST" not in statement
    assert "SUM(ms.takeoffs) AS sorties" in statement


# --------------------------------------------------------------------------- the control

@pytest.mark.asyncio
async def test_the_rankings_page_still_joins_the_start_aggregate(site):
    """The same stub, the default keyword: the board joins the aggregate and probes both relations."""
    pool = _Pool()
    await site_leaderboard.read(site, "playtime", "all", None, 1, None, None, pool)

    statement = next(text for text in pool.statements if _VIEW_BOARD in text)
    assert _STARTS_CTE in statement
    assert "LEFT JOIN starts st ON st.ucid = ms.player_ucid" in statement
    assert _VIEW_STARTS in statement
    assert "GREATEST(SUM(ms.takeoffs), COALESCE(MAX(st.air_starts), 0)) AS sorties" in statement
    assert _probed(pool.statements, pool.calls, MISSIONSTATS) == 1
    assert _probed(pool.statements, pool.calls, VIEW) == 1


@pytest.mark.asyncio
async def test_a_direct_leaderboard_call_without_the_keyword_still_joins(site):
    """No keyword: the aggregate is in the statement, exactly as before the switch existed."""
    pool = _Pool()
    answer = await site_reads.leaderboard(site, pool, metric="playtime", period="all")
    statement = pool.statements[-1]
    assert _STARTS_CTE in statement
    assert _STARTS_JOIN in statement
    assert _VIEW_STARTS in statement
    assert answer["rows"][0]["sorties"] == 7


@pytest.mark.asyncio
async def test_a_bounded_board_without_air_starts_reads_no_event_table(site):
    """``air_starts=False`` on a bounded period: no raw CTE either, and no probe."""
    pool = _Pool()
    await site_reads.leaderboard(site, pool, metric="playtime", period="quarter",
                                 air_starts=False)
    statement = pool.statements[-1]
    assert _STARTS_CTE not in statement
    assert _STARTS_JOIN not in statement
    assert MISSIONSTATS not in statement
    assert _probed(pool.statements, pool.calls, MISSIONSTATS) == 0
