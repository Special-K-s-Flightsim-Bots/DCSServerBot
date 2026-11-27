"""The public site's read layer: every figure a community page shows, scoped to one site.

ONE RULE, APPLIED BY EVERY FUNCTION. Each function takes the resolved
:class:`~services.webservice.sites.SiteContext` and binds the site's server names into the
statement as an array — ``server_name = ANY(%(servers)s::text[])`` — so a figure can never be
read across the groups the cluster holds. The names come from ``site.servers``, which
``sites.py`` resolved from ``servers.yaml``'s ``managed_by`` (root = the untagged servers). Nothing
here re-reads that configuration and nothing accepts a bare server name: a missing site raises
:class:`ValueError` instead of running an installation-wide query.

WHICH TABLE ANSWERS WHICH READ
------------------------------
* LISTS AND LEADERBOARDS -> ``mv_site_pilot_totals``. One row per (server, pilot) carrying the
  metric list's sums, the session count and the takeoff/landing/ejection sums, refreshed by the
  userstats plugin each hour, so a ranked read aggregates the site's servers (~1.6 rows per
  pilot) instead of the per-airframe rows ``mv_statistics`` holds (~4 rows per pilot at three times
  the width). The aggregate carries no mission timestamp, so a leaderboard for a bounded PERIOD is
  the one place that reads ``statistics`` JOIN ``missions``; the column expressions are shared by
  both branches (:data:`COLUMN_SQL`), never written twice.
* THE FAVOURITE AIRFRAME OF EVERY PILOT -> ``mv_site_pilot_aircraft`` (one row per server, pilot and
  slot). The leaderboard's page state is read first and this view then answers the top slot for
  that page's pilots only, so the join is never on the ranking path.
* ONE PILOT'S DETAIL, PER AIRCRAFT, PER MISSION, PER WEEK -> ``statistics`` JOIN ``missions``.
  The materialized view aggregates the mission link away. Each of those statements is bound to the
  UCIDs of ONE pilot (``s.player_ucid = ANY(...)``), so ``idx_statistics_player_ucid`` answers it:
  a profile is an indexed read of one pilot's rows, never a scan of the table. That is why the
  recent-sorties list (the newest :data:`PILOT_RECENT_SESSIONS` sessions) is served from the same
  statement family rather than from an aggregate.
* THE COMMUNITY READOUTS -> three aggregates, none of them the raw join. ``mv_site_activity``
  (per server, per ISO week) carries the closed-session sums the strip adds up and the takeoff sums
  the weekly sorties panel draws; ``mv_site_pilots``
  (one row per pilot per server) carries the two most recent ISO weeks that pilot was active on that
  server; ``mv_site_missions`` (one row per server) counts the missions that have at least one
  session. The per-server, per-week grain answers neither DISTINCT figure: a site-wide pilot count
  is a UNION of the servers' pilot sets, not a sum of them, and a mission can span two ISO weeks, so
  its weekly counts do not add up to the distinct total. Those two key sets are the reason for the
  two smaller views. All three are refreshed by the userstats plugin's hourly view refresh.
* PER AIRFRAME AND PER HOUR OF DAY -> ``mv_site_aircraft`` (one row per server and slot, with the
  playtime and takeoff sums) and ``mv_site_hours`` (one row per server, ISO weekday and UTC hour,
  carrying the flight seconds and the session-hours that fall in that hour). The hourly rows are
  what the panel draws one bar per hour from; the view keeps the plain 24 × 7 grid so the peak hour
  and the busiest weekday are read from the same rows the panel draws. The airframes' SESSION count
  is the one figure those views do not carry, so it is summed from ``mv_statistics``' ``usage``
  (the plugin's own count of ``statistics`` rows at the slot grain).
* THE SERVER BOARD -> ``mv_serverstats`` (all-time per server). WEEKLY ACTIVITY ->
  ``mv_site_activity``. Both are refreshed by the userstats plugin's hourly view refresh, so a page
  read never aggregates the raw statistics join.
* SQUADRON MEMBERSHIP -> the site's pilot set is a scoped DISTINCT over ``mv_site_pilots`` (the same
  join, already reduced to one row per server and pilot), so a squadron's member count never scans
  ``statistics``.
* A PILOT'S PROGRESS -> ``missionstats``, the event stream. A sortie cannot be read from
  ``statistics``: one slot row can hold two takeoff/landing pairs, and a row that never left the
  ramp is not a flight. :func:`pilot_progress` reconstructs the flights with
  ``plugins.missionstats.aar.reconstruct_sorties`` — the function the AAR PDF is built from — so the
  page and the downloadable report count the same thing, and answers ``None`` (no panel) when that
  plugin's table is absent. ``statistics`` still answers every other figure on the page.
* SORTIE STARTS -> ``mv_site_air_starts``, the missionstats plugin's hourly aggregate of the event
  stream at one row per (server, pilot, ISO week). It answers both the site-wide air starts per week
  and the per-pilot sorties figure of the all-time board, so neither page request scans the event
  table. A BOUNDED-period board keeps the raw aggregate over ``missionstats`` (:func:`_starts_cte`),
  because a week grain cannot answer a period that starts mid-week. An install without the view
  falls back to the same raw statement, and an install without ``missionstats`` reports
  ``None`` — the takeoff sum alone, which the page states.

SORTIE, SESSION, FLIGHT — THREE DIFFERENT THINGS, AND ONE WORD FOR EACH. A SORTIE is one flight: it
starts when the aircraft is airborne — a takeoff, or a birth with no airbase behind it (an air start,
:func:`_start_predicate`) — and it ends however it ends: landed, lost, or left unfinished. A SESSION
is one ``statistics`` row: a seat taken until it is left, and one session can hold several flights,
so a session count is NOT a sortie count and never stands in for one. Where the event stream exists a
per-pilot sortie count is the HIGHER of the takeoff sum and the stream's own start count (never
lower: a period the plugin does not cover keeps the ``statistics`` figure), while a site-wide count
ADDS the stream's air starts to the takeoffs, because no flight is in both. Without the plugin a
sortie figure is the takeoff sum, and the page states that instead of implying a flight count it
cannot make.

TRUESKILL IS NOT SITE-SCOPED. ``trueskill`` holds one row per player UCID and carries no server
column, so a rating is a function of the player's global record and is identical on every site.
:func:`squadrons` takes the site for uniformity and uses it to decide WHICH squadrons it reports (a
member must have flown on the site's servers), but the rating value itself is not filtered by it.
:func:`pilot_profile` reads the requested UCID's own rating the same way and reports ``None`` when
the competitive plugin's table is absent (``_table_exists`` — the same probe the squadron rating
uses), so a site without the plugin renders no rating at all instead of an empty one. That is
stated in the docstring rather than expressed as a filter that silently does nothing. The rating's
RECORDED UPDATES come from ``trueskill`` UNION ``trueskill_hist`` (the plugin's own read,
``plugins/competitive/reports.py``, class ``History``), ordered by time, and are downsampled to
:data:`PILOT_RATING_POINTS` points so a player with thousands of updates draws a bounded path.

PAGE CAPS LIVE HERE, NOT IN THE CALLER: a leaderboard page is :data:`LEADERBOARD_PAGE_SIZE` rows,
a pilot search :data:`SEARCH_PAGE_SIZE`. Both take an ``offset`` only.

READ-ONLY: there is no INSERT, UPDATE or DELETE in this module.

The statements that carry no server filter are the ``to_regclass`` probes (a table exists per
DATABASE, not per site) and the competitive plugin's rating reads, which have no server column — they
are declared in :data:`EXEMPT_STATEMENTS` so the ratchet test can name them instead of skipping them.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "SERVER_FILTER",
    "EXEMPT_STATEMENTS",
    "LIVE_READ_TTL",
    "LIVE_STALE_TTL",
    "FOG_TIMEOUT",
    "EXTENSIONS_TIMEOUT",
    "BLOCK_READY",
    "BLOCK_FAILED",
    "BLOCK_EMPTY",
    "LIMIT_FLAGS",
    "SCHEDULE_READY",
    "SCHEDULE_ABSENT",
    "SCHEDULE_FAILED",
    "SCHEDULE_TRIVIAL",
    "live_read",
    "remember_live_read",
    "prefetch_live",
    "forget_live_reads",
    "server_list",
    "server_detail",
    "fleet_live",
    "LEADERBOARD_PAGE_SIZE",
    "SEARCH_PAGE_SIZE",
    "PILOT_RECENT_SESSIONS",
    "PILOT_RATING_POINTS",
    "LEADERBOARD_METRICS",
    "METRIC_LABELS",
    "METRIC_SQL",
    "COLUMN_SQL",
    "LEADERBOARD_COLUMNS",
    "SORT_KEYS",
    "SORT_DIRECTIONS",
    "DEFAULT_SORT_DIRECTION",
    "ACTIVITY_WINDOWS",
    "DEFAULT_ACTIVITY_WINDOW",
    "BOARD_WINDOW_LABEL",
    "AIRCRAFT_ROWS",
    "community_totals",
    "activity_over_time",
    "top_aircraft",
    "hours_of_day",
    "leaderboard",
    "pilot_profile",
    "pilot_search",
    "squadrons",
    "server_board",
    "my_statistics",
    "PROGRESS_BUCKETS",
    "pilot_progress",
    "pilot_standing",
    "my_ucids",
    "STANDING_FLOOR",
    "STANDING_BANDS",
]

#: The one substring every scoped statement carries — the ratchet's assertion, and the shape a new
#: function copies. The alias before it is the statement's own (``m.``, ``ms.``).
SERVER_FILTER = "server_name = ANY(%(servers)s::text[])"

#: A public function's statement that legitimately carries no site filter, with the reason the
#: ratchet reports instead of failing. Keyed by function name, then by a substring of the statement.
EXEMPT_STATEMENTS: dict[str, tuple[tuple[str, str], ...]] = {
    "squadrons": (("to_regclass",
                   "a catalog probe: a table exists per database, not per site"),),
    "pilot_profile": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
        ("FROM trueskill",
         "the competitive plugin's rating has no server column: one rating per pilot, the same on "
         "every site"),
    ),
    "my_statistics": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
        ("FROM trueskill",
         "the competitive plugin's rating has no server column: one rating per pilot, the same on "
         "every site"),
    ),
    "pilot_progress": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
    ),
    "community_totals": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
    ),
    "activity_over_time": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
    ),
    "pilot_standing": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
    ),
    "leaderboard": (
        ("to_regclass", "a catalog probe: a table exists per database, not per site"),
    ),
}

#: Page caps. A caller may ask for a page index, never a page size.
LEADERBOARD_PAGE_SIZE = 50
SEARCH_PAGE_SIZE = 20

#: The number of recent sessions a pilot profile lists (``plugins.mission.actions`` uses the same
#: number for its statistics tab).
PILOT_RECENT_SESSIONS = 10

#: How many recorded rating updates a pilot's trend draws. The series is downsampled to this many
#: points — the first and the newest always kept — so a player with thousands of updates renders a
#: bounded path rather than one vertex per row.
PILOT_RATING_POINTS = 60

#: How many campaigns a pilot's own page lists: the campaigns bound to the site's servers in which
#: the pilot holds credits or a badge, newest first.
PILOT_CAMPAIGN_ROWS = 20

#: The leaderboard metrics, in the order ``plugins/userstats/reports/highscore.json`` shows them,
#: with the display name each ``HighscoreElement`` is titled with.
METRIC_LABELS: dict[str, str] = {
    "playtime": "Longest Playtimes",
    "air_targets": "Air Targets",
    "ships": "Ships",
    "air_defence": "Air Defence",
    "ground_targets": "Ground Targets",
    "kd_ratio": "KD-Ratio",
    "pvp_kd_ratio": "PvP-KD-Ratio",
}
LEADERBOARD_METRICS: tuple[str, ...] = tuple(METRIC_LABELS)

#: The activity windows the page offers, in days; ``None`` is the whole history.
ACTIVITY_WINDOWS: dict[str, int | None] = {"30d": 30, "90d": 90, "1y": 365, "all": None}
DEFAULT_ACTIVITY_WINDOW = "30d"

#: The window the server board reports playtime and sorties over. ``mv_serverstats`` is all-time,
#: so the board's own label says so instead of claiming a trailing window it does not read.
BOARD_WINDOW_LABEL = "all time"

#: How many airframes the "most flown" panel lists.
AIRCRAFT_ROWS = 5

#: The statuses a server's process is UP in (the same vocabulary the dashboard reads).
ONLINE_STATUSES = frozenset({"running", "paused", "loading"})


# --------------------------------------------------------------------------- lanes and figures

#: ``{p}`` is the column prefix of the table the metrics are computed on and ``{play}`` its playtime
#: expression in SECONDS. Defined once so the view branch and the raw branch cannot drift.
METRIC_SQL: dict[str, str] = {
    "playtime": "SUM({play})",
    "air_targets": "SUM({p}kills_planes + {p}kills_helicopters)",
    "ships": "SUM({p}kills_ships)",
    "air_defence": "SUM({p}kills_sams)",
    "ground_targets": "SUM({p}kills_ground)",
    "kd_ratio": ("CASE WHEN SUM({p}deaths_planes + {p}deaths_helicopters + {p}deaths_ships"
                 " + {p}deaths_sams + {p}deaths_ground) = 0 THEN SUM({p}kills)"
                 " ELSE SUM({p}kills::DECIMAL) / SUM(({p}deaths_planes + {p}deaths_helicopters"
                 " + {p}deaths_ships + {p}deaths_sams + {p}deaths_ground)::DECIMAL) END"),
    "pvp_kd_ratio": ("CASE WHEN SUM({p}deaths_pvp) = 0 THEN SUM({p}pvp)"
                     " ELSE SUM({p}pvp::DECIMAL) / SUM({p}deaths_pvp::DECIMAL) END"),
}

#: The count columns a leaderboard row prints beside its metric. ``sorties`` is a FLIGHT count
#: (:data:`_start_predicate`): the recorded takeoffs, raised to the event stream's own start count
#: when the ``missionstats`` plugin is loaded, so a flight that began in the air is counted too.
_COUNT_SQL: dict[str, str] = {
    "sorties": "SUM({p}takeoffs)",
    "landings": "SUM({p}landings)",
    "kills": "SUM({p}kills)",
    "pvp": "SUM({p}pvp)",
    "crashes": "SUM({p}crashes)",
    "ejections": "SUM({p}ejections)",
}

#: Every figure a leaderboard row carries, by its column key: the metrics plus the count columns.
COLUMN_SQL: dict[str, str] = {**METRIC_SQL, **_COUNT_SQL}

#: The columns a row prints, in reading order. ``takeoffs`` is not one of them: it is the base of
#: the ``sorties`` column, so printing both would show the same number twice.
LEADERBOARD_COLUMNS: tuple[str, ...] = ("playtime", "sorties", "landings", "kills",
                                        "pvp", "crashes", "ejections")

#: The sort keys a reader may name in the URL: one per figure plus the pilot's name.
SORT_KEYS: frozenset[str] = frozenset({*COLUMN_SQL, "name"})

#: The direction a column sorts in when the address names none; every other column descends.
DEFAULT_SORT_DIRECTION: dict[str, str] = {"name": "asc"}

#: The directions a reader may name, mapped to the SQL keyword — never interpolated from the query.
SORT_DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}

#: The raw playtime expression: a session still open is counted up to now, as every statistics read
#: in the bot does (``plugins/monitoring/stats_read.py``).
_RAW_PLAY = "EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (NOW() AT TIME ZONE 'utc')) - s.hop_on))"

#: ``(prefix, playtime expression)`` per branch.
_METRIC_VIEW = ("ms.", "ms.playtime")
_METRIC_RAW = ("s.", _RAW_PLAY)


def _start_predicate(prefix: str = "") -> str:
    """The SQL predicate that marks ONE sortie start on the event stream, columns *prefix*-qualified.

    A sortie starts at a takeoff, or at a birth with no airbase behind it — DCS reports a pilot who
    spawned in the air without a place, which is the air start ``plugins.missionstats.aar`` reads.
    A birth ON an airbase is a pilot sitting in the slot, not a flight.
    """
    return (f"({prefix}event = 'S_EVENT_TAKEOFF' OR ({prefix}event = 'S_EVENT_BIRTH' "
            f"AND COALESCE({prefix}place, '') = ''))")


def _start_period_clause(period: str) -> str:
    """The bot's own period clause in its ``missionstats`` form, qualified for a ``missions`` join.

    ``plugins.userstats.filter.MissionStatisticsFilter`` names the bare column ``time``; this
    qualifies it for the statement it is pasted into. Only the lowercase column name matches, so
    ``AT TIME ZONE`` is untouched.
    """
    from plugins.userstats.filter import MissionStatisticsFilter

    return re.sub(r"\btime\b", "mst.time", MissionStatisticsFilter(period).filter(None))  # type: ignore[arg-type]


def _metric(key: str, branch: tuple[str, str], starts: bool = False) -> str:
    """The SQL expression for the figure *key* on *branch*, with the placeholders filled.

    ``sorties`` is a flight count, so it is the takeoff sum RAISED to the event stream's start
    count (:data:`_start_predicate`) when *starts* says the statement joins it: a pilot who spawned
    in the air never took off, and the takeoff sum alone would lose that flight. ``GREATEST`` rather
    than a sum, because the two sources overlap — every takeoff is also an event — and a period the
    event table does not cover must not lower the count the ``statistics`` rows already state.
    """
    prefix, play = branch
    expression = COLUMN_SQL[key].format(p=prefix, play=play)
    if key == "sorties" and starts:
        return f"GREATEST({expression}, COALESCE(MAX(st.air_starts), 0))"
    return expression


# --------------------------------------------------------------------------- helpers

def _server_list(site: Any) -> list[str]:
    """The site's server names as a sorted list, or :class:`ValueError` for anything else.

    THE SCOPE GUARD. A read with no resolved site would compare against no array at all and show
    every group's pilots; refusing here makes that a programming error rather than a quiet leak.
    """
    servers = getattr(site, "servers", None)
    if servers is None or isinstance(servers, (str, bytes)) or not isinstance(
            servers, (frozenset, set, tuple, list)):
        raise ValueError("a resolved site (with its server set) is required for a site read")
    return sorted({str(name) for name in servers if str(name).strip()})


def _params(site: Any, **extra: Any) -> dict[str, Any]:
    """The bound parameters of a scoped statement: the site's servers plus *extra*."""
    return {"servers": _server_list(site), **extra}


def _number(value: Any) -> int | float:
    """A database figure as a JSON number — ``Decimal`` never escapes this module."""
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stamp(value: Any) -> Any:
    """A date/time column as an ISO string; every other value unchanged."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _plain(rows: list[dict]) -> list[dict]:
    """Rows with their date/time columns rendered as ISO strings."""
    return [{key: _stamp(value) for key, value in row.items()} for row in rows]


def _offset(value: Any) -> int:
    """A page offset as a non-negative int; anything unreadable is the first page."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


async def _fetch_all(conn: Any, statement: str, params: Any) -> list[dict]:
    """Every row of *statement* as a plain dict (no driver row object leaves this module)."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(statement, params)
        return [dict(row) async for row in cursor]


async def _fetch_one(conn: Any, statement: str, params: Any) -> dict:
    """The first row of *statement* as a dict, or ``{}`` when it answered none."""
    rows = await _fetch_all(conn, statement, params)
    return rows[0] if rows else {}


# --------------------------------------------------------------------------- community totals

#: The Monday of the current ISO week as a DATE — the unit ``mv_site_pilots`` stores its weeks in,
#: and of the week before it.
_THIS_WEEK = "(DATE_TRUNC('week', NOW() AT TIME ZONE 'utc'))::date"
_LAST_WEEK = "(DATE_TRUNC('week', NOW() AT TIME ZONE 'utc') - INTERVAL '1 week')::date"

#: The pilot figures collapse the (server, pilot) grain in ONE pass first, so each figure is a plain
#: COUNT over a set of pilots and no DISTINCT aggregate sorts the whole view per request.
_COMMUNITY_SQL = f"""
WITH activity AS (
    SELECT COALESCE(SUM(closed_playtime), 0) AS closed_playtime,
           COALESCE(SUM(closed_sessions), 0) AS closed_sessions,
           COALESCE(SUM(takeoffs), 0) AS takeoffs
      FROM mv_site_activity
     WHERE {SERVER_FILTER}
), pilots AS (
    SELECT COUNT(*) AS known_pilots,
           COUNT(*) FILTER (WHERE active_this_week) AS active_this_week,
           COUNT(*) FILTER (WHERE active_last_week) AS active_last_week
      FROM (SELECT player_ucid,
                   BOOL_OR(last_week = {_THIS_WEEK}) AS active_this_week,
                   BOOL_OR(last_week = {_LAST_WEEK}
                           OR prev_week = {_LAST_WEEK}) AS active_last_week
              FROM mv_site_pilots
             WHERE {SERVER_FILTER}
             GROUP BY player_ucid) per_pilot
), missions AS (
    SELECT COALESCE(SUM(missions), 0) AS missions_flown
      FROM mv_site_missions
     WHERE {SERVER_FILTER}
)
SELECT pilots.known_pilots AS known_pilots,
       COALESCE(ROUND(activity.closed_playtime), 0)::bigint AS total_playtime,
       COALESCE(ROUND(activity.closed_playtime / NULLIF(activity.closed_sessions, 0)), 0)::bigint
           AS avg_session,
       activity.closed_sessions::bigint AS sessions,
       activity.takeoffs::bigint AS sorties,
       missions.missions_flown AS missions_flown,
       pilots.active_this_week AS active_this_week,
       pilots.active_last_week AS active_last_week
  FROM activity, pilots, missions
"""


async def community_totals(site: Any, pool: Any, air_starts: dict[str, int] | None = None) -> dict:
    """The community's headline figures over the site's servers, off the hourly aggregates.

    Definitions, the bot's own (``plugins/monitoring/stats_read.py``): ``known_pilots`` is a
    DISTINCT count of the pilots with a session on the site, ``total_playtime`` and ``avg_session``
    are seconds over the CLOSED sessions, ``sorties`` is every flight the week series counts
    (:func:`activity_over_time`): the recorded takeoffs PLUS the air starts the event stream saw —
    no flight is in both, and a site without that stream answers the takeoffs alone and says so
    (``air_starts`` False). ``missions_flown`` is the distinct missions, and
    ``active_this_week``/``active_last_week`` are DISTINCT pilots in the current and previous ISO
    week. ``active_change`` is the difference in pilots between the two weeks, computed here from
    those two figures.

    ``air_starts`` is :func:`sortie_air_starts`' answer when the caller already has it (the overview
    runs it once for the weekly panel too); ``None`` reads it here.

    WHICH AGGREGATE ANSWERS WHICH FIGURE. ``total_playtime``, ``avg_session``, ``sessions`` and
    ``sorties`` are sums over ``mv_site_activity``; ``known_pilots``, ``active_this_week`` and
    ``active_last_week`` read ``mv_site_pilots`` (one row per pilot per server, carrying the two most
    recent ISO weeks the pilot was active there — the only two weeks the figures ask about);
    ``missions_flown`` reads ``mv_site_missions``. No figure falls back to a direct query: the raw
    ``statistics`` JOIN ``missions`` is not read at all, which is what keeps this off the page's
    critical path.
    """
    async with pool.connection() as conn:
        row = await _fetch_one(conn, _COMMUNITY_SQL, _params(site))
    if air_starts is None:
        air_starts = await sortie_air_starts(site, pool)
    this_week = _number(row.get("active_this_week"))
    last_week = _number(row.get("active_last_week"))
    return {
        "known_pilots": _number(row.get("known_pilots")),
        "total_playtime": _number(row.get("total_playtime")),
        "avg_session": _number(row.get("avg_session")),
        "sessions": _number(row.get("sessions")),
        "sorties": _number(row.get("sorties")) + sum((air_starts or {}).values()),
        "air_starts": air_starts is not None,
        "missions_flown": _number(row.get("missions_flown")),
        "active_this_week": this_week,
        "active_last_week": last_week,
        "active_change": this_week - last_week,
    }


# --------------------------------------------------------------------------- activity over time

_ACTIVITY_SQL = f"""
SELECT TO_CHAR(week_start, 'IYYY-"W"IW') AS week,
       COALESCE(SUM(playtime), 0)::bigint AS playtime,
       COALESCE(SUM(pilots), 0)::bigint AS pilots,
       COALESCE(SUM(takeoffs), 0)::bigint AS sorties
  FROM mv_site_activity
 WHERE {SERVER_FILTER}
   {{window}}
 GROUP BY 1
 ORDER BY 1
"""

#: The site's air starts per ISO week, straight from the event stream (:func:`_start_predicate`).
#: ``mv_site_activity`` carries the takeoffs of the sessions a week STARTED; an air start produces
#: none, so the two are ADDED and no flight is counted twice. The label matches
#: :data:`_ACTIVITY_SQL`'s, so the two series merge as they are.
_AIR_START_WEEKS_SQL = f"""
SELECT TO_CHAR(DATE_TRUNC('week', mst.time), 'IYYY-"W"IW') AS week,
       COUNT(*)::bigint AS air_starts
  FROM missionstats mst
  JOIN missions msm ON msm.id = mst.mission_id
 WHERE msm.{SERVER_FILTER}
   AND {_start_predicate('mst.')}
 GROUP BY 1
"""


#: The same series from ``mv_site_air_starts`` — the missionstats plugin's hourly aggregate, one row
#: per server, pilot and ISO week. ``starts`` is already the stream's own start count
#: (:func:`_start_predicate`), so the read only sums the site's servers. The label matches
#: :data:`_ACTIVITY_SQL`'s, so the two series merge as they are.
_AIR_START_WEEKS_VIEW_SQL = f"""
SELECT TO_CHAR(week_start, 'IYYY-"W"IW') AS week,
       COALESCE(SUM(starts), 0)::bigint AS air_starts
  FROM mv_site_air_starts
 WHERE {SERVER_FILTER}
 GROUP BY 1
"""


async def sortie_air_starts(site: Any, pool: Any) -> dict[str, int] | None:
    """Air starts per ISO week over the site's servers, or ``None`` without the event stream.

    ``mv_site_air_starts`` answers the series when the missionstats plugin's aggregate is there —
    it is that plugin's hourly sum over the event stream, so the page read sums a week grain
    instead of scanning ``missionstats``. Without the view the same figure is aggregated from the
    raw ``missionstats`` JOIN ``missions``. ``None`` is the install-level fact the pages read:
    without ``missionstats`` no air start can be seen, so every site-wide sortie figure is a
    takeoff count and the page says so.
    """
    async with pool.connection() as conn:
        if not await _table_exists(conn, "missionstats"):
            return None
        if await _table_exists(conn, "mv_site_air_starts"):
            rows = await _fetch_all(conn, _AIR_START_WEEKS_VIEW_SQL, _params(site))
        else:
            rows = await _fetch_all(conn, _AIR_START_WEEKS_SQL, _params(site))
    found: dict[str, int] = {}
    for row in rows:
        label = str(row.get("week") or "").strip()
        if label:
            found[label] = found.get(label, 0) + int(_number(row.get("air_starts")))
    return found


def _week_start(value: datetime) -> datetime:
    """The Monday 00:00 UTC of the ISO week *value* falls in."""
    day = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return (day - timedelta(days=day.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _week_of(value: datetime) -> str:
    """The ISO week label (``2026-W42``) of *value*."""
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def _week_start_of_label(label: str) -> datetime | None:
    """The Monday of an ISO week label, or ``None`` for a label that does not parse."""
    try:
        return datetime.strptime(f"{label}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _zero_filled(rows: list[dict], window: str, now: datetime) -> list[dict]:
    """One entry per ISO week of the window, in order, with the weeks no row covers at zero."""
    found: dict[str, dict] = {}
    for row in rows:
        label = str(row.get("week") or "").strip()
        if not label:
            continue
        found[label] = {"week": label,
                        "playtime": _number(row.get("playtime")),
                        "pilots": _number(row.get("pilots")),
                        "sorties": _number(row.get("sorties"))}
    days = ACTIVITY_WINDOWS.get(window)
    if days is None:
        starts = [_week_start_of_label(label) for label in found]
        starts = [start for start in starts if start is not None]
        start = min(starts) if starts else _week_start(now)
    else:
        start = _week_start(now - timedelta(days=days))
    end = _week_start(now)
    weeks: list[dict] = []
    cursor = start
    while cursor <= end:
        label = _week_of(cursor)
        weeks.append(found.get(label, {"week": label, "playtime": 0, "pilots": 0, "sorties": 0}))
        cursor += timedelta(days=7)
    covered = {entry["week"] for entry in weeks}
    weeks.extend(entry for label, entry in found.items() if label not in covered)
    weeks.sort(key=lambda entry: entry["week"])
    return weeks


async def activity_over_time(site: Any, pool: Any, window: str = DEFAULT_ACTIVITY_WINDOW,
                             air_starts: dict[str, int] | None = None) -> dict:
    """Playtime, pilots and sorties per ISO week over the site's servers, zero-filled.

    *window* is one of :data:`ACTIVITY_WINDOWS` (``30d``, ``90d``, ``1y``, ``all``); any other value
    resolves to :data:`DEFAULT_ACTIVITY_WINDOW` and the answer states the window it served. A week
    with no session is present with ``playtime`` ``0``, ``pilots`` ``0`` and ``sorties`` ``0``,
    never missing.

    The buckets come from ``mv_site_activity`` (per server, per ISO week). ``pilots`` is the sum of
    each server's distinct pilots for the week, so a pilot who flew two of the site's servers in one
    week counts twice; the page draws playtime only. ``sorties`` is every flight: the recorded
    takeoffs PLUS the air starts of that week (:func:`sortie_air_starts`), the same measure
    :func:`community_totals` reports for the whole site, so the weekly bars and the headline figure
    cannot disagree. ``air_starts`` says whether the event stream was there to be counted — False
    means the bars are takeoffs alone. *air_starts* is that series when the caller already has it;
    ``None`` reads it here.
    """
    wanted = str(window or "").strip().lower()
    if wanted not in ACTIVITY_WINDOWS:
        wanted = DEFAULT_ACTIVITY_WINDOW
    days = ACTIVITY_WINDOWS[wanted]
    clause = ("AND week_start >= DATE_TRUNC('week', (NOW() AT TIME ZONE 'utc') "
              "- make_interval(days => %(days)s))::date"
              if days is not None else "")
    statement = _ACTIVITY_SQL.format(window=clause)
    params = _params(site)
    if days is not None:
        params["days"] = int(days)
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, statement, params)
    if air_starts is None:
        air_starts = await sortie_air_starts(site, pool)
    weeks = _zero_filled(rows, wanted, datetime.now(timezone.utc))
    for entry in weeks:
        entry["sorties"] += (air_starts or {}).get(entry["week"], 0)
    return {"window": wanted, "weeks": weeks, "air_starts": air_starts is not None}


# --------------------------------------------------------------------------- per airframe

#: One row per (server, slot) in ``mv_site_aircraft``; the site's servers collapse to one row per
#: airframe, which is what the panel ranks. It ranks on PLAYTIME only — the usage figure beside it is
#: a SESSION count, which that view does not carry.
_AIRCRAFT_SQL = f"""
SELECT slot,
       COALESCE(SUM(playtime), 0)::bigint AS playtime
  FROM mv_site_aircraft
 WHERE {SERVER_FILTER}
 GROUP BY slot
 ORDER BY 2 DESC NULLS LAST, 1
 LIMIT %(limit)s
"""

#: Sessions per airframe: one ``statistics`` row is one session, and ``mv_statistics`` already
#: carries that count as ``usage`` at the (server, pilot, slot, side, tail) grain, so summing it per
#: slot is the number of times that airframe was boarded on the site's servers.
_AIRCRAFT_SESSIONS_SQL = f"""
SELECT slot, COALESCE(SUM(usage), 0)::bigint AS sessions
  FROM mv_statistics
 WHERE {SERVER_FILTER}
 GROUP BY slot
"""


async def top_aircraft(site: Any, pool: Any, limit: int = AIRCRAFT_ROWS) -> dict:
    """The most-flown airframes of the site by PLAYTIME, with their session counts.

    Ranked on playtime (the panel's own scale), ties broken by the slot name, and capped at *limit*
    rows (:data:`AIRCRAFT_ROWS` by default). The ranked rows come from ``mv_site_aircraft`` — one row
    per server and slot — so the read aggregates the site's servers instead of walking
    ``statistics``. ``playtime`` is seconds; ``sessions`` is how many times the airframe was boarded
    (:data:`_AIRCRAFT_SESSIONS_SQL`), which is the module-usage figure the panel states — a sortie
    count would mean nothing here, since one session can hold several flights. A site with no
    recorded session answers no row rather than one at zero.
    """
    try:
        wanted = max(int(limit), 1)
    except (TypeError, ValueError):
        wanted = AIRCRAFT_ROWS
    params = _params(site, limit=wanted)
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _AIRCRAFT_SQL, params)
        sessions = await _fetch_all(conn, _AIRCRAFT_SESSIONS_SQL, _params(site)) if rows else []
    used = {str(row.get("slot") or ""): _number(row.get("sessions")) for row in sessions}
    return {"limit": wanted,
            "rows": [{"slot": row.get("slot") or "",
                      "playtime": _number(row.get("playtime")),
                      "sessions": used.get(str(row.get("slot") or ""), 0)} for row in rows]}


# --------------------------------------------------------------------------- hour of day

#: The flight-time histogram: one row per (server, ISO weekday, UTC hour) in ``mv_site_hours``. The
#: two statements read the same rows from two angles — the panel draws the hours and states the
#: busiest weekday — so the peak hour and the weekday figure cannot come from different populations.
_HOURS_SQL = f"""
SELECT hour, COALESCE(SUM(seconds), 0)::bigint AS seconds,
       COALESCE(SUM(sessions), 0)::bigint AS sessions
  FROM mv_site_hours
 WHERE {SERVER_FILTER}
 GROUP BY hour
"""

_WEEKDAYS_SQL = f"""
SELECT dow, COALESCE(SUM(seconds), 0)::bigint AS seconds,
       COALESCE(SUM(sessions), 0)::bigint AS sessions
  FROM mv_site_hours
 WHERE {SERVER_FILTER}
 GROUP BY dow
"""

#: One bucket per UTC hour and one per ISO weekday; both are zero-filled so a quiet hour is a bar at
#: zero rather than a hole in the axis.
HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7


async def hours_of_day(site: Any, pool: Any) -> dict:
    """Flight time by UTC hour of day and by ISO weekday, zero-filled over the site's servers.

    Both axes come from ``mv_site_hours`` (one row per server, ISO weekday and UTC hour), which
    splits each session's span across the hours it actually covered: ``seconds`` is the flight time
    that fell inside that hour and ``sessions`` how many session spans touched it. A session is never
    booked to the hour it started in. ``hours`` always holds :data:`HOURS_PER_DAY` entries
    (``0``…``23``) and ``weekdays`` :data:`DAYS_PER_WEEK` entries (``1`` = Monday … ``7`` = Sunday),
    each at zero when nothing was recorded, so the page draws a full axis and no caller divides by a
    missing maximum.
    """
    async with pool.connection() as conn:
        hours = {_number(row.get("hour")): (_number(row.get("seconds")), _number(row.get("sessions")))
                 for row in await _fetch_all(conn, _HOURS_SQL, _params(site))}
        weekdays = {_number(row.get("dow")): (_number(row.get("seconds")), _number(row.get("sessions")))
                    for row in await _fetch_all(conn, _WEEKDAYS_SQL, _params(site))}
    return {
        "hours": [{"hour": hour, "seconds": hours.get(hour, (0, 0))[0],
                   "sessions": hours.get(hour, (0, 0))[1]}
                  for hour in range(HOURS_PER_DAY)],
        "weekdays": [{"dow": dow, "seconds": weekdays.get(dow, (0, 0))[0],
                      "sessions": weekdays.get(dow, (0, 0))[1]}
                     for dow in range(1, DAYS_PER_WEEK + 1)],
    }


# --------------------------------------------------------------------------- leaderboard

#: The start count behind a ``sorties`` figure, one row per pilot: every takeoff, and every birth
#: with no airbase behind it (DCS reports a pilot who spawned in the air without a place, so that
#: birth IS the start — the same pair ``plugins.missionstats.aar.reconstruct_sorties`` reads). It is
#: joined only when the ``missionstats`` plugin's table is there; every caller then raises its
#: takeoff sum to it. The aggregate is per pilot, so the ranking path may sort on it as it stands.
_AIR_START_CTE = f"""
starts AS MATERIALIZED (
    SELECT mst.init_id AS ucid, COUNT(*)::bigint AS air_starts
      FROM missionstats mst
      JOIN missions msm ON msm.id = mst.mission_id
     WHERE msm.{SERVER_FILTER}
       AND {{start_predicate}}
       {{start_period}}
     GROUP BY 1
),"""


def _starts_cte(period: str) -> str:
    """The ``starts`` CTE for the board in hand, its period clause resolved for that period.

    An ``all`` board compares no timestamp at all, so the clause is left out rather than pasted as
    ``1 = 1``: the aggregate is the expensive half of the statement, and a predicate it can drop is
    a scan it may prune.
    """
    clause = "" if period == "all" else f"AND {_start_period_clause(period)}"
    return _AIR_START_CTE.format(start_predicate=_start_predicate("mst."), start_period=clause)


def _starts_join(prefix: str) -> str:
    """The join that puts a pilot's start count in scope, *prefix* being the pilot column's own."""
    return f"LEFT JOIN starts st ON st.ucid = {prefix}player_ucid"


#: The ``starts`` CTE for a PERIOD, over ``mv_site_air_starts``: the plugin's per-pilot, per-week sum,
#: collapsed to the pilot. The subquery is aliased ``s`` and its ``week_start`` renamed ``hop_on``,
#: because that is exactly what :func:`_period_clause` writes its predicate against (``s.hop_on``) —
#: the clause is shared with the raw branch, so the alias and the column name are not free choices.
#: The grain is the WEEK: a start
#: inside the week that straddles the period's edge is included, which can only raise a figure the
#: metric then floors against takeoffs.
_AIR_START_VIEW_PERIOD_CTE = """
starts AS MATERIALIZED (
    SELECT s.ucid AS ucid, SUM(s.starts)::bigint AS air_starts
      FROM (SELECT player_ucid AS ucid, starts, week_start AS hop_on
              FROM mv_site_air_starts
             WHERE {server_filter}) s
     WHERE {period}
     GROUP BY 1
),"""


#: Periods whose event scan is bounded to days: there the raw stream is exact AND cheap, and the
#: view's WEEK grain would round "today" onto the week that contains it. Anything longer reads the
#: view — that is the scan that cost seconds per page view.
_AIR_START_RAW_PERIODS = frozenset({"today", "yesterday", "day", "week"})


def _air_start_source(period: str) -> str:
    """``"raw"`` for a short window (exact, bounded), ``"view"`` for everything longer."""
    return "raw" if period in _AIR_START_RAW_PERIODS else "view"


def _air_start_view_cte(period: str) -> str:
    """The ``starts`` CTE over the view for *period*; ``all`` compares no timestamp at all."""
    return _AIR_START_VIEW_PERIOD_CTE.format(server_filter=SERVER_FILTER,
                                             period=_period_clause(period)[0])


#: The same ``starts`` CTE over ``mv_site_air_starts``: the missionstats plugin's per-pilot, per-week
#: sum, collapsed to the pilot. Same alias, same column names, same MATERIALIZED shape, so the
#: expressions :func:`_metric` builds read it unchanged.
_AIR_START_VIEW_CTE = f"""
starts AS MATERIALIZED (
    SELECT player_ucid AS ucid, SUM(starts)::bigint AS air_starts
      FROM mv_site_air_starts
     WHERE {SERVER_FILTER}
     GROUP BY 1
),"""

#: The all-time page, in TWO PHASES. ``page`` ranks the pilots on the two aggregates the order
#: needs (the metric and the sort column) and materializes that page; ``detail`` then sums the wide
#: row over the page's pilots only — ~1.6 rows per pilot through the view's unique index, not the
#: whole view. The favourite airframe is a LATERAL over the same page, so no wide aggregate and no
#: per-row subquery sits on the ranking path.
_LEADERBOARD_VIEW_SQL = f"""
WITH {{starts}}page AS MATERIALIZED (
    SELECT ms.player_ucid AS player_ucid,
           COALESCE(p.name, 'Unknown') AS name,
           {{value}} AS value,
           {{sort_expr}} AS sort_value
      FROM mv_site_pilot_totals ms
      JOIN players p ON p.ucid = ms.player_ucid
      {{starts_join}}
     WHERE ms.{SERVER_FILTER}
     GROUP BY ms.player_ucid, p.name
    HAVING {{value}} > 0
     ORDER BY sort_value {{direction}} NULLS LAST, ms.player_ucid
     LIMIT %(limit)s OFFSET %(offset)s
), detail AS (
    SELECT page.player_ucid AS player_ucid,
           page.name AS name,
           page.value AS value,
           page.sort_value AS sort_value,
           {{detail_aggregates}}
      FROM page
      JOIN mv_site_pilot_totals ms
        ON ms.player_ucid = page.player_ucid
       AND ms.{SERVER_FILTER}
      {{starts_join}}
     GROUP BY page.player_ucid, page.name, page.value, page.sort_value
)
SELECT detail.player_ucid AS ucid,
       detail.name AS name,
       detail.value AS value,
       {{detail_list}},
       air.slot AS aircraft
  FROM detail
  LEFT JOIN LATERAL (
      SELECT a.slot
        FROM mv_site_pilot_aircraft a
       WHERE a.player_ucid = detail.player_ucid
         AND a.{SERVER_FILTER}
       ORDER BY a.playtime DESC, a.slot
       LIMIT 1) air ON TRUE
 ORDER BY detail.sort_value {{direction}} NULLS LAST, detail.player_ucid
"""

#: The bounded period: no view carries a mission timestamp, so both the ranking and the wide row are
#: aggregated from ``statistics`` JOIN ``missions`` over the bot's own period clause, the wide one
#: again for the page's pilots only.
_LEADERBOARD_RAW_SQL = f"""
WITH {{starts}}page AS MATERIALIZED (
    SELECT s.player_ucid AS player_ucid,
           COALESCE(p.name, 'Unknown') AS name,
           {{value}} AS value,
           {{sort_expr}} AS sort_value
      FROM statistics s
      JOIN missions m ON m.id = s.mission_id
      JOIN players p ON p.ucid = s.player_ucid
      {{starts_join}}
     WHERE m.{SERVER_FILTER} AND {{period}}
     GROUP BY s.player_ucid, p.name
    HAVING {{value}} > 0
     ORDER BY sort_value {{direction}} NULLS LAST, s.player_ucid
     LIMIT %(limit)s OFFSET %(offset)s
), detail AS (
    SELECT page.player_ucid AS player_ucid,
           page.name AS name,
           page.value AS value,
           page.sort_value AS sort_value,
           {{detail_aggregates}}
      FROM page
      JOIN statistics s ON s.player_ucid = page.player_ucid
      JOIN missions m ON m.id = s.mission_id
      {{starts_join}}
     WHERE m.{SERVER_FILTER} AND {{period}}
     GROUP BY page.player_ucid, page.name, page.value, page.sort_value
)
SELECT detail.player_ucid AS ucid,
       detail.name AS name,
       detail.value AS value,
       {{detail_list}},
       air.slot AS aircraft
  FROM detail
  LEFT JOIN LATERAL (
      SELECT s.slot
        FROM statistics s
        JOIN missions m ON m.id = s.mission_id
       WHERE s.player_ucid = detail.player_ucid
         AND m.{SERVER_FILTER} AND {{period}}
       GROUP BY s.slot
       ORDER BY SUM({_RAW_PLAY}) DESC, s.slot
       LIMIT 1) air ON TRUE
 ORDER BY detail.sort_value {{direction}} NULLS LAST, detail.player_ucid
"""


def _sort_expression(key: str, branch: tuple[str, str], starts: bool = False) -> str:
    """The expression the page is ORDERED on: the pilot's name, or the column's own aggregate."""
    return "LOWER(p.name)" if key == "name" else _metric(key, branch, starts)


def _detail_list() -> str:
    """One ``detail.<key>`` per column a row prints, in :data:`LEADERBOARD_COLUMNS`' order."""
    return ",\n       ".join(f"detail.{key}" for key in LEADERBOARD_COLUMNS)


def _detail_aggregates(branch: tuple[str, str], starts: bool = False) -> str:
    """One ``expr AS key`` per column a row prints, over the page's pilots only."""
    return ",\n           ".join(f"{_metric(key, branch, starts)} AS {key}"
                                for key in LEADERBOARD_COLUMNS)


def _order(sort: Any, direction: Any, metric: str) -> tuple[str, str]:
    """The sort key and direction this request resolves to, both from a FIXED set.

    An unknown column sorts by the board's own metric (which always is a column); an unknown or
    missing direction takes the column's default (:data:`DEFAULT_SORT_DIRECTION`).
    """
    wanted = str(sort or "").strip().lower()
    key = wanted if wanted in SORT_KEYS else metric
    fallback = DEFAULT_SORT_DIRECTION.get(key, "desc")
    chosen = str(direction or "").strip().lower()
    return key, (chosen if chosen in SORT_DIRECTIONS else fallback)


def _period_clause(period: Any) -> tuple[str, str]:
    """The bot's OWN period clause for *period*, and the name it resolved to.

    ``plugins.userstats.filter.PeriodFilter`` is the one definition of a period vocabulary, so the
    site does not grow a second one. Only the published period names are accepted (the ones
    ``/highscore`` offers); anything else is ``all``.
    """
    from plugins.userstats.filter import PeriodFilter

    known = PeriodFilter.list(None)
    wanted = str(period or "").strip().lower()
    if wanted not in known:
        wanted = "all"
    return PeriodFilter(wanted).filter(None), wanted


async def leaderboard(site: Any, pool: Any, metric: str = "playtime", period: str = "all",
                      offset: Any = 0, sort: Any = None, direction: Any = None,
                      air_starts: bool = True) -> dict:
    """One leaderboard page: the top pilots of *metric* over the site's servers, with every column.

    *metric* is one of :data:`LEADERBOARD_METRICS` (the ``/highscore`` set); an unknown name
    resolves to ``playtime`` and the answer states the metric it served. *period* is the bot's own
    period vocabulary, resolved by :func:`_period_clause`. *sort* and *direction* are the table's
    own: they resolve through :data:`SORT_KEYS` and :data:`SORT_DIRECTIONS` (a column or direction
    the page does not offer falls back to the metric and the column's default) and cannot reach the
    statement unvetted. Every row carries :data:`LEADERBOARD_COLUMNS` plus its favourite airframe;
    ``value`` is the ruled-on metric's figure for the row.

    SORTIES IS A FLIGHT COUNT, not a session count and not a bare takeoff sum: it is the recorded
    takeoffs raised to the event stream's own start count (:func:`_start_predicate`) when the
    ``missionstats`` plugin is loaded, so a pilot who spawned in the air is counted. Without that
    plugin the column is the takeoff sum, and the page no longer prints ``Takeoffs`` beside it —
    the same number twice.

    WHICH RELATION ANSWERS THE START COUNT. The all-time board reads ``mv_site_air_starts``, the
    missionstats plugin's hourly per-pilot, per-week aggregate; the CTE keeps its alias and column
    names, so :func:`_metric` reads it as it stands. A BOUNDED period keeps the raw aggregate over
    ``missionstats`` (:func:`_starts_cte`), because a week-grain view cannot answer a period that
    starts mid-week: its rows would silently widen the window by up to seven days and the sorties
    column would drift from the period the row states.

    ``mv_site_pilot_totals`` answers the all-time read: it holds one row per (server, pilot) with the
    metric list's sums, the session count and the takeoff/landing/ejection sums. The aggregate has
    no mission timestamp, so a bounded period still reads ``statistics`` JOIN ``missions`` — the
    column expressions are the same (:data:`COLUMN_SQL`), only the table differs.
    ``mv_site_pilot_aircraft`` answers the favourite airframe of the all-time page, joined LATERAL
    over the page's pilots only; the bounded period derives the same slot from the raw join, again
    over the page's pilots.

    ``air_starts`` states whether this statement needs the per-pilot start count. ``True`` — the
    default — joins it, so ``sorties`` is a flight count as described above. ``False`` builds no
    ``starts`` CTE at all: no ``LEFT JOIN starts``, no read of ``missionstats`` or
    ``mv_site_air_starts`` and no existence probe for either (a probe is a round trip too), and the
    row's ``sorties`` column answers the recorded takeoff sum. A caller that does not print the
    sortie column must pass ``air_starts=False``, so the page does not aggregate what it discards.

    The answer is capped at :data:`LEADERBOARD_PAGE_SIZE` rows and takes an ``offset``; there is no
    caller-supplied size. Both branches break a tie on ``player_ucid``, so paging is stable under
    every sort.
    """
    wanted = str(metric or "").strip().lower()
    if wanted not in METRIC_LABELS:
        wanted = "playtime"
    order_key, order_dir = _order(sort, direction, wanted)
    clause, resolved_period = _period_clause(period)
    branch = _METRIC_VIEW if resolved_period == "all" else _METRIC_RAW
    params = _params(site, limit=LEADERBOARD_PAGE_SIZE, offset=_offset(offset))
    async with pool.connection() as conn:
        if not air_starts:
            starts = False
            starts_cte = ""
        else:
            starts = await _table_exists(conn, "missionstats")
            if not starts:
                starts_cte = ""
            elif (_air_start_source(resolved_period) == "view"
                  and await _table_exists(conn, "mv_site_air_starts")):
                # the view is the aggregate the plugin already keeps: one small table per page, not
                # a GROUP BY over the whole event stream on every request
                starts_cte = _air_start_view_cte(resolved_period)
            else:
                starts_cte = _starts_cte(resolved_period)
        fields = {"value": _metric(wanted, branch, starts),
                  "sort_expr": _sort_expression(order_key, branch, starts),
                  "detail_aggregates": _detail_aggregates(branch, starts),
                  "detail_list": _detail_list(),
                  "starts": starts_cte,
                  "starts_join": _starts_join(branch[0]) if starts else "",
                  "direction": SORT_DIRECTIONS[order_dir]}
        if resolved_period == "all":
            statement = _LEADERBOARD_VIEW_SQL.format(**fields)
        else:
            statement = _LEADERBOARD_RAW_SQL.format(period=clause, **fields)
        rows = await _fetch_all(conn, statement, params)
    return {
        "metric": wanted,
        "label": METRIC_LABELS[wanted],
        "period": resolved_period,
        "sort": order_key,
        "direction": order_dir,
        "columns": LEADERBOARD_COLUMNS,
        "offset": params["offset"],
        "limit": LEADERBOARD_PAGE_SIZE,
        "rows": [{"ucid": row.get("ucid"), "name": row.get("name"),
                  # the ranked figure is the statement's own `value` alias: a metric that is not
                  # one of the printed columns (ships, air targets, a ratio) has no column of its
                  # own to read, and looking it up by name silently answered 0 for every row.
                  "value": _number(row.get("value")),
                  "aircraft": row.get("aircraft") or "",
                  **{key: _number(row.get(key)) for key in LEADERBOARD_COLUMNS}}
                 for row in rows],
    }


# --------------------------------------------------------------------------- standing

#: The flights a pilot needs in the period before entering the standing's cohort. Without a floor
#: three lucky sorties top a K/D board, and every regular reads worse than they are.
STANDING_FLOOR: int = 10

#: The bands a standing card may name, in the order the card climbs them: ``(key, share, smallest
#: cohort that can have it)``. ``top10`` is an ABSOLUTE place, so a twelve-pilot site has no top 10
#: to reach and the card never names one — a target a pilot cannot reach is worse than no target.
STANDING_BANDS: tuple[tuple[str, float | None, int], ...] = (
    ("half", 0.50, 4),
    ("quarter", 0.25, 8),
    ("top10", None, 30),
)

#: The standing's cohort: every pilot of the site with at least ``%(floor)s`` flights in the period,
#: ranked on K/D (the leaderboard's own definition, so the two pages cannot disagree). ``ranked``
#: numbers the cohort once and ``size`` counts it; the cuts are the VALUE at the place each band
#: ends, so the card can state a distance in the metric's own units. ``ROW_NUMBER`` rather than
#: ``RANK``: a tie must not make two pilots share a place the card then reports as their own.
_STANDING_SQL = """
WITH {starts}cohort AS (
    SELECT {prefix}player_ucid AS ucid,
           {kd} AS value,
           {sorties} AS sorties
      FROM {relation}
      {joins}
     WHERE {where}
     GROUP BY 1
    HAVING {sorties} >= %(floor)s AND {kd} > 0
), ranked AS (
    SELECT ucid, value,
           ROW_NUMBER() OVER (ORDER BY value DESC, ucid) AS place,
           COUNT(*) OVER () AS size
      FROM cohort
)
SELECT (SELECT size FROM ranked LIMIT 1) AS size,
       (SELECT place FROM ranked WHERE ucid = %(ucid)s) AS place,
       (SELECT value FROM ranked WHERE ucid = %(ucid)s) AS value,
       (SELECT sorties FROM cohort WHERE ucid = %(ucid)s) AS sorties,
       (SELECT value FROM ranked
         WHERE place = LEAST(10, (SELECT size FROM ranked LIMIT 1))) AS top10_cut,
       (SELECT value FROM ranked
         WHERE place = CEIL((SELECT size FROM ranked LIMIT 1) * 0.25)) AS quarter_cut,
       (SELECT value FROM ranked
         WHERE place = CEIL((SELECT size FROM ranked LIMIT 1) * 0.50)) AS half_cut
"""


async def pilot_standing(site: Any, pool: Any, ucid: Any, period: Any = "quarter",
                         floor: int = STANDING_FLOOR) -> dict:
    """One pilot's place in the site's K/D cohort for *period*, with the band cuts around them.

    The cohort is every pilot of the site's servers with at least *floor* flights in the period
    (:data:`STANDING_FLOOR`), ranked on the leaderboard's own K/D — kills over deaths by enemy
    action — so the card and the leaderboard cannot state two different K/Ds for one pilot. The
    flights are counted the way the rest of the site counts them (:func:`_metric`'s ``sorties``),
    so a pilot who only ever started in the air is not dropped from the cohort for it.

    The answer carries the numbers only, never a sentence: ``size`` (the cohort), ``place`` and
    ``value`` (the pilot's own, both ``None`` when they are not ranked), ``sorties`` (their flights
    in the period), and ``cuts`` — the metric's value at the last place of each band
    (:data:`STANDING_BANDS`). A band whose cohort is too small to have it answers ``None``, which is
    what lets the card name only a target that exists. ``period`` is the bot's own vocabulary and
    the answer states the one it served; a site with no session in the period answers ``size`` 0.
    """
    clause, resolved = _period_clause(period)
    branch = _METRIC_VIEW if resolved == "all" else _METRIC_RAW
    params = _params(site, floor=max(int(floor), 1), ucid=str(ucid or ""))
    async with pool.connection() as conn:
        starts = await _table_exists(conn, "missionstats")
        if not starts:
            starts_cte = ""
        elif (_air_start_source(resolved) == "view"
              and await _table_exists(conn, "mv_site_air_starts")):
            starts_cte = _air_start_view_cte(resolved)
        else:
            starts_cte = _starts_cte(resolved)
        if resolved == "all":
            # the sorties metric reads st.air_starts, so the join belongs here too
            relation, joins = "mv_site_pilot_totals ms", _starts_join("ms.") if starts else ""
            where = f"ms.{SERVER_FILTER}"
        else:
            relation = "statistics s JOIN missions m ON m.id = s.mission_id"
            joins = _starts_join(branch[0]) if starts else ""
            where = f"m.{SERVER_FILTER} AND {clause}"
        statement = _STANDING_SQL.format(
            starts=starts_cte, prefix=branch[0], kd=_metric("kd_ratio", branch, starts),
            sorties=_metric("sorties", branch, starts), relation=relation, joins=joins,
            where=where)
        row = await _fetch_one(conn, statement, params)
    size = _number(row.get("size"))
    place = _number(row.get("place"))
    cuts = {"top10": row.get("top10_cut"), "quarter": row.get("quarter_cut"),
            "half": row.get("half_cut")}
    return {
        "period": resolved,
        "floor": params["floor"],
        "size": size,
        "place": place or None,
        "value": (None if row.get("value") is None else float(row.get("value"))),
        "sorties": _number(row.get("sorties")),
        "cuts": {key: (None if value is None else float(value)) for key, value in cuts.items()},
        "air_starts": starts,
    }


# --------------------------------------------------------------------------- one pilot

_IDENTITY_SQL = f"""
SELECT p.ucid, p.name, p.discord_id
  FROM players p
 WHERE p.ucid = %(ucid)s
   AND EXISTS (SELECT 1
                 FROM statistics s
                 JOIN missions m ON m.id = s.mission_id
                WHERE s.player_ucid = p.ucid
                  AND m.{SERVER_FILTER})
"""

_LINKED_BY_UCID_SQL = f"""
SELECT p.ucid, p.name
  FROM players p
 WHERE p.discord_id = %(discord_id)s
   AND EXISTS (SELECT 1
                 FROM statistics s
                 JOIN missions m ON m.id = s.mission_id
                WHERE s.player_ucid = p.ucid
                  AND m.{SERVER_FILTER})
 ORDER BY p.last_seen DESC NULLS LAST, p.ucid
"""

_LINKED_BY_DISCORD_SQL = f"""
SELECT p.ucid, p.name
  FROM players p
 WHERE p.discord_id = %(discord_id)s
   AND EXISTS (SELECT 1
                 FROM statistics s
                 JOIN missions m ON m.id = s.mission_id
                WHERE s.player_ucid = p.ucid
                  AND m.{SERVER_FILTER})
 ORDER BY LOWER(COALESCE(p.name, '')), p.ucid
"""

async def my_ucids(site: Any, pool: Any, discord_id: Any) -> list[str]:
    """The UCIDs a Discord id owns on the site's servers, in the profile's own order.

    One read for the callers that need the identity and nothing else — the AAR download names the
    pilot's UCID and never renders a figure, so it must not pay for the whole profile.
    """
    try:
        wanted = int(discord_id)
    except (TypeError, ValueError):
        return []
    if wanted <= 0:
        return []
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _LINKED_BY_DISCORD_SQL, _params(site, discord_id=wanted))
    return [str(row.get("ucid")) for row in rows if row.get("ucid")]


_PILOT_TOTALS_SQL = f"""
SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (NOW() AT TIME ZONE 'utc'))
                                       - s.hop_on))), 0)::bigint AS playtime,
       COUNT(*) AS sessions,
       COALESCE(SUM(s.kills), 0) AS kills,
       COALESCE(SUM(s.pvp), 0) AS pvp,
       COALESCE(SUM(s.deaths), 0) AS deaths,
       COALESCE(SUM(s.teamkills), 0) AS teamkills,
       COALESCE(SUM(s.takeoffs), 0) AS takeoffs,
       COALESCE(SUM(s.landings), 0) AS landings,
       COALESCE(SUM(s.crashes), 0) AS crashes,
       COALESCE(SUM(s.ejections), 0) AS ejections,
       COALESCE(SUM(s.kills_planes), 0) AS kills_planes,
       COALESCE(SUM(s.kills_helicopters), 0) AS kills_helicopters,
       COALESCE(SUM(s.kills_ground), 0) AS kills_ground,
       COALESCE(SUM(s.kills_sams), 0) AS kills_sams,
       COALESCE(SUM(s.deaths_pvp), 0) AS deaths_pvp
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[])
   AND m.{SERVER_FILTER}
"""

_PILOT_BY_AIRCRAFT_SQL = f"""
SELECT s.slot AS aircraft,
       COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (NOW() AT TIME ZONE 'utc'))
                                       - s.hop_on))), 0)::bigint AS playtime,
       COUNT(*) AS sessions
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[])
   AND m.{SERVER_FILTER}
 GROUP BY 1
 ORDER BY 2 DESC NULLS LAST, 1
"""

_PILOT_BY_SERVER_SQL = f"""
SELECT m.server_name AS server_name,
       COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (NOW() AT TIME ZONE 'utc'))
                                       - s.hop_on))), 0)::bigint AS playtime,
       COUNT(*) AS sessions
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[])
   AND m.{SERVER_FILTER}
 GROUP BY 1
 ORDER BY 2 DESC NULLS LAST, 1
"""

_PILOT_RECENT_SQL = f"""
SELECT s.hop_on, s.hop_off, s.slot, m.server_name, m.mission_name,
       EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (NOW() AT TIME ZONE 'utc')) - s.hop_on))::bigint
           AS playtime
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[])
   AND m.{SERVER_FILTER}
 ORDER BY s.hop_on DESC
 LIMIT %(limit)s
"""

#: A pilot's sortie starts on the event stream — every takeoff and every birth with no airbase
#: behind it (:func:`_start_predicate`). One statement for every UCID the visitor's Discord account
#: owns, and only over the site's servers.
_AIR_START_SQL = f"""
SELECT mst.init_id AS ucid, COUNT(*)::bigint AS air_starts
  FROM missionstats mst
  JOIN missions msm ON msm.id = mst.mission_id
 WHERE msm.{SERVER_FILTER}
   AND mst.init_id = ANY(%(ucids)s::text[])
   AND {_start_predicate('mst.')}
 GROUP BY 1
"""


async def sortie_starts(conn: Any, site: Any, ucids: list[str]) -> dict[str, int] | None:
    """Sortie starts per UCID, or ``None`` when the ``missionstats`` plugin's table is absent.

    ``None`` and an empty dict mean different things and the pages read them that way: ``None`` is
    "this install has no event stream, so sorties can only be the takeoff count", while ``{}`` is
    "the stream is there and none of these pilots started a flight in the air".
    """
    if not await _table_exists(conn, "missionstats"):
        return None
    rows = await _fetch_all(conn, _AIR_START_SQL, _params(site, ucids=ucids))
    return {str(row.get("ucid")): int(_number(row.get("air_starts")))
            for row in rows if row.get("ucid")}


def _totals(row: dict) -> dict:
    """The pilot totals as JSON numbers."""
    return {key: _number(row.get(key)) for key in
            ("playtime", "sessions", "sorties", "kills", "pvp", "deaths", "teamkills",
             "takeoffs", "landings", "crashes", "ejections", "kills_planes",
             "kills_helicopters", "kills_ground", "kills_sams", "deaths_pvp")}


async def _pilot_detail(conn: Any, site: Any, ucids: list[str]) -> dict:
    """Every per-pilot figure over *ucids*, bound to the site's servers.

    ``sorties`` is the FLIGHT count: the takeoff sum, raised to the event stream's start count when
    the ``missionstats`` plugin is loaded (:func:`sortie_starts`), so a flight that began in the air
    is counted. ``air_starts`` states the corrections it carries — ``None`` when the install has no
    event stream, which is how the pages know the figure is a takeoff count alone.
    """
    params = _params(site, ucids=ucids)
    totals = await _fetch_one(conn, _PILOT_TOTALS_SQL, params)
    aircraft = await _fetch_all(conn, _PILOT_BY_AIRCRAFT_SQL, params)
    servers = await _fetch_all(conn, _PILOT_BY_SERVER_SQL, params)
    recent = await _fetch_all(conn, _PILOT_RECENT_SQL,
                              {**params, "limit": PILOT_RECENT_SESSIONS})
    starts = await sortie_starts(conn, site, ucids)
    answer = _totals(totals)
    answer["air_starts"] = None if starts is None else sum(starts.values())
    answer["sorties"] = max(answer["takeoffs"], answer["air_starts"] or 0)
    return {
        "totals": answer,
        "per_aircraft": [{"aircraft": row.get("aircraft"),
                          "playtime": _number(row.get("playtime")),
                          "sessions": _number(row.get("sessions"))} for row in aircraft],
        "per_server": [{"server_name": row.get("server_name"),
                        "playtime": _number(row.get("playtime")),
                        "sessions": _number(row.get("sessions"))} for row in servers],
        "recent": _plain(recent),
    }


def _empty_profile(ucid: str) -> dict:
    """The shape a profile answers with when the pilot has no session on the site."""
    totals = _totals({})
    totals["air_starts"] = None
    return {"ucid": ucid, "name": "", "found": False, "discord_id": -1, "ucids": [],
            "totals": totals, "per_aircraft": [], "per_server": [], "recent": [],
            "rating": None, "campaigns": []}


#: The competitive plugin's latest rating, one row per UCID. It carries no server column, so it is
#: read without the site filter and answers the same value on every site (see the module docstring).
_PILOT_RATING_SQL = """
SELECT skill_mu, skill_sigma, time
  FROM trueskill
 WHERE player_ucid = %(ucid)s
"""

#: The rating's recorded updates, one row per update, oldest first: the plugin's own union
#: (``plugins/competitive/reports.py``, class ``History``) — ``trueskill`` holds the current row and
#: ``trueskill_hist`` the superseded ones, so the two together are the series with no duplicate.
#: Unbounded on purpose: :func:`_downsample` bounds what the page DRAWS, and the row count is what
#: the panel's ``count`` reports.
_PILOT_RATING_SERIES_SQL = """
SELECT skill_mu, skill_sigma, time
  FROM (SELECT skill_mu, skill_sigma, time
          FROM trueskill
         WHERE player_ucid = %(ucid)s
         UNION
        SELECT skill_mu, skill_sigma, time
          FROM trueskill_hist
         WHERE player_ucid = %(ucid)s) recorded
 ORDER BY time ASC
"""


def _downsample(points: list[dict], limit: int = PILOT_RATING_POINTS) -> list[dict]:
    """*points* reduced to at most *limit* entries, the first and the last always kept.

    The kept indexes are evenly spaced over the whole series, so the drawn line is the recorded one
    at its own resolution rather than a stretch of the newest updates.
    """
    total = len(points)
    if total <= limit:
        return list(points)
    stride = (total - 1) / (limit - 1)
    return [points[index] for index in sorted({round(step * stride) for step in range(limit)})]


def _rating_points(rows: list[dict], limit: int = PILOT_RATING_POINTS) -> list[dict]:
    """Recorded updates as ``{"time", "mu", "sigma"}`` points, oldest first, downsampled.

    A row whose time or figures do not parse is not a point the page can place, so it is dropped
    from the series rather than drawn at an invented position.
    """
    points: list[dict] = []
    for row in rows or ():
        stamp = _stamp(row.get("time"))
        mu_value, sigma_value = row.get("skill_mu"), row.get("skill_sigma")
        if mu_value is None or sigma_value is None:
            continue
        try:
            mu, sigma = float(mu_value), float(sigma_value)
        except (TypeError, ValueError):
            continue
        if not isinstance(stamp, str) or not stamp:
            continue
        points.append({"time": stamp, "mu": mu, "sigma": sigma})
    return _downsample(points, limit)


def _conservative(row: dict) -> float | None:
    """The rating the competitive plugin DISPLAYS for *row*: ``μ − 3σ``, or ``None`` when unreadable.

    The same expression the plugin's own command uses (``plugins/competitive/commands.py``), so the
    page and the Discord reply cannot disagree about a pilot's rating.
    """
    try:
        return round(float(row.get("skill_mu")) - 3.0 * float(row.get("skill_sigma")), 2)
    except (TypeError, ValueError):
        return None


async def _pilot_rating(conn: Any, ucid: str) -> dict | None:
    """One pilot's competitive rating and its recorded updates, or ``None`` without the tables.

    ``trueskill`` is the PROBE and the gate: a site whose database has no such table renders no
    rating block at all, rather than an empty one. ``trueskill_hist`` is probed separately, because
    a plugin that predates it still answers the current rating and simply has no recorded earlier
    one — the panel's one-point state rather than an error.

    ``points`` is the plugin's own series, ``trueskill`` UNION ``trueskill_hist``, oldest first and
    downsampled to :data:`PILOT_RATING_POINTS`; ``count`` is how many points this answer carries.
    ``history`` is False when the series holds a single reading, which is what tells the page to
    draw no line at all instead of a flat one.
    """
    if not await _table_exists(conn, "trueskill"):
        return None
    current = await _fetch_one(conn, _PILOT_RATING_SQL, {"ucid": ucid})
    conservative = _conservative(current)
    if conservative is None:
        return None
    if await _table_exists(conn, "trueskill_hist"):
        rows = await _fetch_all(conn, _PILOT_RATING_SERIES_SQL, {"ucid": ucid})
    else:
        rows = [current]
    points = _rating_points(rows)
    return {
        "mu": _number(current.get("skill_mu")),
        "sigma": _number(current.get("skill_sigma")),
        "conservative": conservative,
        "points": points,
        "count": len(points),
        "history": len(points) > 1,
    }


#: The campaign tables the credits and badges panels read. All four must exist: a campaign list
#: with neither credits nor badges has nothing to show, so the panel is absent rather than empty.
_CAMPAIGN_TABLES = ("campaigns", "campaigns_servers", "credits", "players_badges")

#: The pilot's campaigns ON THIS SITE, newest first: the credits held in each and the badge earned.
#: A campaign belongs to servers, not to a site, so the site scope is the ``campaigns_servers``
#: join — the same membership rule every other figure on the page obeys.
_CAMPAIGNS_SQL = f"""
SELECT c.name, c.start, c.stop,
       cr.points,
       b.badge_name, b.badge_url
  FROM campaigns c
  LEFT JOIN (SELECT campaign_id, SUM(points) AS points
               FROM credits
              WHERE player_ucid = %(ucid)s
              GROUP BY campaign_id) cr ON cr.campaign_id = c.id
  LEFT JOIN players_badges b ON b.campaign_id = c.id AND b.player_ucid = %(ucid)s
 WHERE EXISTS (SELECT 1
                 FROM campaigns_servers cs
                WHERE cs.campaign_id = c.id
                  AND {SERVER_FILTER})
   AND (cr.points IS NOT NULL OR b.badge_name IS NOT NULL)
 ORDER BY c.start DESC
 LIMIT %(limit)s
"""


def _running(row: dict) -> bool:
    """Whether a campaign row is the one running now — ``stop`` may be NULL, meaning open-ended."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = row.get("start")
    stop = row.get("stop")
    if not isinstance(start, datetime) or start > now:
        return False
    return not isinstance(stop, datetime) or stop >= now


async def _campaigns(conn: Any, site: Any, ucid: str) -> list[dict]:
    """The credits and badges the pilot holds per campaign, or ``[]`` without the plugin's tables."""
    for table in _CAMPAIGN_TABLES:
        if not await _table_exists(conn, table):
            return []
    rows = await _fetch_all(conn, _CAMPAIGNS_SQL,
                            _params(site, ucid=ucid, limit=PILOT_CAMPAIGN_ROWS))
    return [{"name": row.get("name") or "",
             "points": _number(row.get("points")),
             "badge": row.get("badge_name") or "",
             "badge_url": row.get("badge_url") or "",
             "running": _running(row)} for row in rows]


async def pilot_profile(site: Any, pool: Any, ucid: str) -> dict:
    """ONE pilot by UCID: totals, per aircraft, per server, recent sessions, linked UCIDs, rating.

    The pilot is read ONLY if a session of theirs exists on one of the site's servers, and every
    figure is bound to those servers. The UCIDs of the same Discord link are listed when there are
    several, again only for UCIDs seen on the site — ``players`` itself carries no server column.
    A pilot with no session on the site answers ``found`` False with the same keys as a found one.

    The competitive rating is the ONE figure that is not site-scoped: ``trueskill`` has no server
    column, so it is one pilot's global rating. It is read for the REQUESTED UCID only (indexed
    single-UCID reads, a probe per table) — merging the linked UCIDs would invent a rating no match
    produced.
    """
    wanted = str(ucid or "").strip()
    if not wanted:
        return _empty_profile(wanted)
    async with pool.connection() as conn:
        row = await _fetch_one(conn, _IDENTITY_SQL, _params(site, ucid=wanted))
        if not row:
            return _empty_profile(wanted)
        try:
            discord_id = int(row.get("discord_id"))
        except (TypeError, ValueError):
            discord_id = -1
        identity = [{"ucid": wanted, "name": row.get("name") or ""}]
        linked: list[dict] = []
        if discord_id > 0:
            linked = await _fetch_all(conn, _LINKED_BY_UCID_SQL,
                                      _params(site, discord_id=discord_id))
        ucids = [wanted] + [str(item.get("ucid")) for item in linked
                            if item.get("ucid") and str(item.get("ucid")) != wanted]
        detail = await _pilot_detail(conn, site, ucids)
        rating = await _pilot_rating(conn, wanted)
    return {
        "ucid": wanted,
        "name": row.get("name") or "",
        "found": True,
        "discord_id": discord_id,
        "rating": rating,
        "ucids": [{"ucid": item.get("ucid"), "name": item.get("name") or ""} for item in identity]
                 + [{"ucid": item.get("ucid"), "name": item.get("name") or ""} for item in linked
                    if str(item.get("ucid")) != wanted],
        **detail,
    }


# --------------------------------------------------------------------------- pilot search

#: The match is on ``players``; the site scope is a probe of the per-pilot aggregate, whose unique
#: ``(server_name, player_ucid)`` index answers it — the raw statistics join is not read here.
_PILOT_SEARCH_SQL = f"""
SELECT p.ucid, p.name, p.last_seen
  FROM players p
 WHERE LOWER(p.name) LIKE %(pattern)s ESCAPE '\\'
   AND EXISTS (SELECT 1
                 FROM mv_site_pilot_totals ms
                WHERE ms.player_ucid = p.ucid
                  AND ms.{SERVER_FILTER})
 ORDER BY LOWER(p.name), p.ucid
 LIMIT %(limit)s OFFSET %(offset)s
"""


def _like_escape(value: str) -> str:
    """*value* with the LIKE metacharacters escaped, for a BOUND ``LIKE`` pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def pilot_search(site: Any, pool: Any, query: str, offset: Any = 0) -> dict:
    """Pilots of the site whose NAME CONTAINS *query*, paged and ordered by name.

    The match is a contains term — ``LOWER(p.name) LIKE`` with a bound, lower-cased, ESCAPED pattern
    wrapped in ``%`` and joined by ``ESCAPE`` — the scope is the player's presence on the site's
    servers, the order is the lower-cased name, and the page is capped at
    :data:`SEARCH_PAGE_SIZE` rows. An empty query answers no rows and runs nothing.

    A leading wildcard keeps the planner off ``idx_players_name_lower``. That is deliberate: a
    search box that only finds the beginning of a name reads as a dead box, and ``players`` is small
    on every installation this serves. The index is NOT removed — the prefix form uses it, and a
    fast contains path belongs beside this function as its own change.
    """
    wanted = str(query or "").strip()
    page = {"query": wanted, "offset": _offset(offset), "limit": SEARCH_PAGE_SIZE, "rows": []}
    if not wanted:
        return page
    params = _params(site, pattern=f"%{_like_escape(wanted.lower())}%",
                     limit=SEARCH_PAGE_SIZE, offset=page["offset"])
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _PILOT_SEARCH_SQL, params)
    page["rows"] = [{"ucid": row.get("ucid"), "name": row.get("name"),
                     "last_seen": _stamp(row.get("last_seen"))} for row in rows]
    return page


# --------------------------------------------------------------------------- squadrons

#: The site's pilot set, read off the key view: ``mv_site_pilots`` holds one row per (server, pilot)
#: built from the same join, so the scoped DISTINCT over it is the identical set of pilots.
_SQUADRON_SQL = f"""
WITH site_players AS (
    SELECT DISTINCT player_ucid
      FROM mv_site_pilots
     WHERE {SERVER_FILTER}
)
SELECT sq.id, sq.name, sq.description, sq.image_url,
       COUNT(DISTINCT sp.player_ucid) AS members
  FROM squadrons sq
  JOIN squadron_members sm ON sm.squadron_id = sq.id
  JOIN site_players sp ON sp.player_ucid = sm.player_ucid
 GROUP BY sq.id, sq.name, sq.description, sq.image_url
 ORDER BY LOWER(sq.name), sq.id
"""

_SQUADRON_POINTS_SQL = f"""
SELECT sm.squadron_id AS squadron_id,
       COALESCE(SUM(cr.points), 0)::bigint AS campaign_points
  FROM squadron_members sm
  JOIN credits cr ON cr.player_ucid = sm.player_ucid
  JOIN campaigns_servers cs ON cs.campaign_id = cr.campaign_id
 WHERE cs.{SERVER_FILTER}
 GROUP BY 1
"""

_SQUADRON_RATING_SQL = f"""
SELECT sm.squadron_id AS squadron_id,
       COUNT(*) AS members,
       SUM(COALESCE(t.skill_mu, 25.0)) AS mu_total,
       SUM(POWER(COALESCE(t.skill_sigma, 25.0 / 3.0), 2)) AS sigma_sq_total
  FROM squadron_members sm
  LEFT JOIN trueskill t ON t.player_ucid = sm.player_ucid
 WHERE EXISTS (SELECT 1
                 FROM statistics s
                 JOIN missions m ON m.id = s.mission_id
                WHERE s.player_ucid = sm.player_ucid
                  AND m.{SERVER_FILTER})
 GROUP BY 1
"""

_TABLE_EXISTS_SQL = "SELECT to_regclass(%(name)s) IS NOT NULL AS present"


async def _table_exists(conn: Any, name: str) -> bool:
    """Whether a plugin's table exists in this database (its plugin may be unloaded)."""
    row = await _fetch_one(conn, _TABLE_EXISTS_SQL, {"name": name})
    return bool(row.get("present"))


def _combined_rating(row: dict, beta: float) -> float | None:
    """The squadron's combined TrueSkill rating, or ``None`` when it has no rated member.

    The formula is ``plugins.competitive.commands.Competitive.calculate_squadron_rating``: the
    mean ``mu`` and ``sqrt((sum(sigma^2) + n * BETA^2) / n)``, reported as ``mu - 3 * sigma``.
    """
    try:
        members = int(row.get("members") or 0)
    except (TypeError, ValueError):
        members = 0
    if members <= 0:
        return None
    mu = float(row.get("mu_total") or 0) / members
    variance = (float(row.get("sigma_sq_total") or 0) + members * beta * beta) / members
    return round(mu - 3.0 * math.sqrt(variance), 2)


async def squadrons(site: Any, pool: Any) -> list[dict]:
    """The site's squadrons: member count, campaign points, and the combined rating.

    A squadron is on the site when at least one of its members has flown on one of the site's
    servers; ``members`` counts only those members. ``campaign_points`` sums the ``credits`` rows
    whose campaign is joined to the site through ``campaigns_servers``. The combined rating is read
    from ``trueskill`` only while ``plugins.competitive`` is loaded (its table exists) and is NOT
    site-scoped — TrueSkill holds one rating per pilot UCID and has no server column, so every site
    reports the same value for the same squadron. ``rating`` is ``None`` when competitive is not
    loaded or no member is rated, and ``rating_available`` states which of the two it is.
    """
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _SQUADRON_SQL, _params(site))
        points = {int(row["squadron_id"]): _number(row.get("campaign_points"))
                  for row in await _fetch_all(conn, _SQUADRON_POINTS_SQL, _params(site))}
        competitive = await _table_exists(conn, "trueskill")
        ratings: dict[int, float] = {}
        if competitive:
            beta = 25.0 / 6.0
            try:
                from trueskill import BETA
                beta = float(BETA)
            except Exception:  # noqa: BLE001 - a rating is read only where its plugin is loaded
                pass
            for row in await _fetch_all(conn, _SQUADRON_RATING_SQL, _params(site)):
                rating = _combined_rating(row, beta)
                if rating is not None:
                    ratings[int(row["squadron_id"])] = rating
    return [{
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description") or "",
        "image_url": row.get("image_url") or "",
        "members": _number(row.get("members")),
        "campaign_points": points.get(int(row.get("id")), 0),
        "rating": ratings.get(int(row.get("id"))),
        "rating_available": competitive,
    } for row in rows]


# --------------------------------------------------------------------------- server board

#: ``mv_serverstats`` names its session count ``totalSorties`` (the userstats plugin's own word for
#: the ``statistics`` rows behind it). It is read here as ``sessions``: a session is a seat taken, not
#: a flight, and the page must not call it a sortie.
_BOARD_SQL = f"""
SELECT server_name,
       ("totalPlaytime"::bigint * 3600) AS playtime,
       "totalSorties" AS sessions
  FROM mv_serverstats
 WHERE {SERVER_FILTER}
"""


def _live_servers(live: Any) -> dict[str, Any]:
    """The live server objects keyed by name — ``ServiceBus.servers`` unless *live* is given."""
    if live is None:
        from .sites import live_servers as _registry
        live = _registry()
    items = getattr(live, "items", None)
    if callable(items):
        return {str(name): server for name, server in items()}
    return {str(getattr(server, "name", "")): server for server in (live or ())}


def _status_text(server: Any) -> str:
    """A server's status as its lower-cased name, or ``''`` when nothing readable carries one."""
    status = getattr(server, "status", None) if server is not None else None
    name = getattr(status, "name", status)
    return str(name or "").strip().lower()


def _active_players(server: Any) -> int:
    """How many players sit in a slot on *server*, read tolerantly from its in-memory roster."""
    players = getattr(server, "players", None) if server is not None else None
    if not players:
        return 0
    try:
        return sum(1 for player in players.values() if getattr(player, "active", False))
    except Exception:  # noqa: BLE001 - a half-read roster contributes nobody
        return 0


async def server_board(site: Any, pool: Any, live: Any = None) -> list[dict]:
    """One row per site server: up or not, pilots flying now, all-time playtime and sessions.

    The live half is read from the in-process roster (the same objects the dashboard reads), never
    queried: ``online`` is the process being up, ``players_online`` the players sitting in a slot.
    The database half is the all-time aggregate ``mv_serverstats`` (refreshed by the userstats
    plugin's hourly refresh), not the raw statistics join. A server with no recorded session is
    still a row, at zero, and every row carries :data:`BOARD_WINDOW_LABEL` as its window.
    """
    servers = _server_list(site)
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _BOARD_SQL, _params(site))
    figures = {str(row.get("server_name")): row for row in rows}
    registry = _live_servers(live)
    board: list[dict] = []
    for name in servers:
        server = registry.get(name)
        status = _status_text(server)
        figures_row = figures.get(name, {})
        board.append({
            "server_name": name,
            "online": status in ONLINE_STATUSES,
            "status": status,
            "players_online": _active_players(server),
            "playtime": _number(figures_row.get("playtime")),
            "sessions": _number(figures_row.get("sessions")),
            "window": BOARD_WINDOW_LABEL,
        })
    return board


# --------------------------------------------------------------------------- the fleet

#: The live blocks' cache window, in seconds: the same 300 s the bot's own status embed caches its
#: ``getFog`` read for. Weather and extensions are RPCs to the server and the weekly schedule is the
#: scheduler plugin's own config, so one page view must not cost a round trip or a plugin call —
#: :func:`live_read` answers the cached snapshot and refreshes it off the render path.
LIVE_READ_TTL = 300

#: How long a live read that is NOT ready stands. ``down`` and ``failed`` describe a MOMENT: a server
#: starting, stopping or recovering flips them within seconds, and a 300 s window kept telling a
#: visitor a running server was off long after it had started.
LIVE_STALE_TTL = 20

#: How long the extensions RPC may take before this read answers without it. A page render must not
#: wait on a slow node for a decorative list.
EXTENSIONS_TIMEOUT = 5.0

#: How long the ``getFog`` RPC may take. It only refines the visibility figure — the weather itself
#: is already in memory (:func:`_weather_payload`) — so a silent node costs this bound and nothing
#: else: not the state, not the extensions block, not the schedule.
FOG_TIMEOUT = 5.0

#: The states ONE block of a live read can be in, beside the payload's own :data:`LIVE_READY` /
#: :data:`LIVE_DOWN` / :data:`LIVE_FAILED`: ``ready`` carries figures, ``failed`` is a block whose
#: read did not answer (the page says so in one sentence and draws nothing else of it), and
#: ``empty`` is a block the mission or the plugin simply states nothing for.
BLOCK_READY = "ready"
BLOCK_FAILED = "failed"
BLOCK_EMPTY = "empty"

#: The client-integrity switches, in the page's order, as ``(settings key, path, default, in force
#: when TRUE)``. ``path`` is where the value lives — ``settings`` itself or its ``advanced`` block.
#: The defaults are the DCS config schema's own, so a server that states nothing reads like one that
#: states the default.
LIMIT_FLAGS: tuple[tuple[str, str, bool, bool], ...] = (
    ("allow_trial_only_clients", "advanced", False, False),
    ("require_pure_clients", "", True, True),
    ("require_pure_scripts", "", False, True),
    ("require_pure_models", "", True, True),
    ("require_pure_textures", "", True, True),
)

#: the statuses a live read can be in besides ``ready``.
LIVE_PENDING = "pending"
LIVE_DOWN = "down"
LIVE_FAILED = "failed"
LIVE_READY = "ready"


def _text(value: Any) -> str:
    """*value* as a stripped string; anything unreadable is ``''``."""
    return str(value).strip() if value is not None and not isinstance(value, bool) else ""


def _float(value: Any) -> float | None:
    """*value* as a float, or ``None`` — a tolerant read of a DCS-reported number."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _settings(server: Any) -> dict:
    """A server's own settings block as a dict (``{}`` when it carries none)."""
    settings = getattr(server, "settings", None) if server is not None else None
    return settings if isinstance(settings, dict) else {}


def _advanced(server: Any) -> dict:
    """The ``advanced`` block of a server's settings as a dict (``{}`` when it carries none)."""
    block = _settings(server).get("advanced")
    return block if isinstance(block, dict) else {}


def _mission_of(server: Any) -> Any:
    """The server's current mission object, or ``None``."""
    return getattr(server, "current_mission", None) if server is not None else None


def _side_counts(server: Any) -> dict[str, int]:
    """The players sitting in a slot per coalition, keyed ``blue``/``red``."""
    counts = {"blue": 0, "red": 0}
    players = getattr(server, "players", None) if server is not None else None
    if not players:
        return counts
    try:
        for player in players.values():
            if not getattr(player, "active", False):
                continue
            side = str(getattr(getattr(player, "side", None), "name", "") or "").strip().lower()
            if side in counts:
                counts[side] += 1
    except Exception:  # noqa: BLE001 - a half-read roster contributes nobody
        return {"blue": 0, "red": 0}
    return counts


def _slots(server: Any) -> tuple[dict, dict]:
    """``(blue, red)`` as ``{"current", "total"}`` — the live count against the mission's own seats.

    ``total`` is the mission's ``num_slots_blue`` / ``num_slots_red``; a mission that reports no
    count answers ``0`` and the page prints no figure rather than a zero seat row.
    """
    mission = _mission_of(server)
    counts = _side_counts(server)
    blue_total = _number(getattr(mission, "num_slots_blue", 0)) if mission is not None else 0
    red_total = _number(getattr(mission, "num_slots_red", 0)) if mission is not None else 0
    return ({"current": counts["blue"], "total": int(blue_total or 0)},
            {"current": counts["red"], "total": int(red_total or 0)})


def _max_players(server: Any) -> int | None:
    """The seats a real player can take (``settings.maxPlayers`` less the observer slot), or None."""
    configured = _number(_settings(server).get("maxPlayers"))
    if not configured:
        return None
    from core.utils.helper import player_slots
    return player_slots(int(configured))


def _restart_seconds(server: Any) -> int | None:
    """Seconds until the scheduled restart, or ``None`` — the embed's own rule.

    Only a server with a ``restart_time`` and NOT in maintenance reports a countdown; a restart in
    the past answers ``None`` rather than a negative figure.
    """
    if server is None or bool(getattr(server, "maintenance", False)):
        return None
    restart_time = getattr(server, "restart_time", None)
    if not isinstance(restart_time, datetime):
        return None
    return max(int((restart_time - datetime.now(timezone.utc)).total_seconds()), 0)


def _address(server: Any) -> str:
    """``public_ip:port`` for a server, or ``''`` when the node reports no address.

    The port is the DCS port players join on (``settings.port``, 10308 when unstated) — the value
    the Discord status embed prints. ``Server.port`` is NOT it: that field carries the server
    process's own bot port, the internal RPC port the bot talks to the process on.
    """
    node = getattr(server, "node", None) if server is not None else None
    ip = _text(getattr(node, "public_ip", None))
    port = _number(_settings(server).get("port", 10308)) if server is not None else 0
    return f"{ip}:{int(port)}" if ip and port else ""


def _mission_clock(mission: Any) -> dict[str, str]:
    """The in-mission date and time as ``{"date", "time"}``, or two empty strings.

    ``Mission.date`` is the mission's own calendar date and ``start_time`` the clock at its start;
    adding the elapsed ``mission_time`` gives the moment the mission has reached. ``date`` arrives
    as a ``datetime`` from one DCS build and as an ISO-ish string from another, so both shapes are
    rendered (a formatted date, or the raw value beside the elapsed clock).
    """
    if mission is None:
        return {"date": "", "time": ""}
    elapsed = int(_number(getattr(mission, "mission_time", 0)) or 0)
    start = int(_number(getattr(mission, "start_time", 0)) or 0)
    date = getattr(mission, "date", None)
    if isinstance(date, datetime):
        moment = datetime.fromtimestamp(date.timestamp() + start + elapsed)
        return {"date": f"{moment:%d %b %Y}", "time": f"{moment:%H:%M:%S}"}
    if not _text(date):
        return {"date": "", "time": ""}
    return {"date": _text(date),
            "time": str(timedelta(seconds=max(start + elapsed, 0))).zfill(8)}


def _limits(server: Any) -> list[dict]:
    """Every client-integrity switch with whether it is IN FORCE, in :data:`LIMIT_FLAGS`' order."""
    settings = _settings(server)
    advanced = _advanced(server)
    rows: list[dict] = []
    for key, path, default, true_is_in_force in LIMIT_FLAGS:
        source = advanced if path == "advanced" else settings
        value = bool(source.get(key, default))
        rows.append({"key": key, "enabled": value if true_is_in_force else not value})
    return rows


def _bot_version(server: Any) -> str:
    """The bot's own version as the server's node reports it (``sub_version`` appended), or ``''``."""
    node = getattr(server, "node", None) if server is not None else None
    version = _text(getattr(node, "bot_version", None))
    sub = getattr(node, "sub_version", None)
    if version and isinstance(sub, str) and sub.strip() and sub.strip() != "0":
        return f"{version}.{sub.strip()}"
    return version


def _live_key(server: Any) -> str:
    """The name a live read is cached under (the server's own name)."""
    return _text(getattr(server, "name", None)) if server is not None else ""


def _plain_version(value: Any) -> str:
    """An extension's version, or ``''`` for the spellings that mean "there is no version"."""
    text = _text(value)
    return "" if text.lower() in ("n/a", "n.a.", "na", "-", "none") else text


def _weather_payload(server: Any, fog: Any, answer: Any = None) -> dict | None:
    """The mission's weather as raw figures, or ``None`` when it reports no weather block.

    The live ``getFog`` reading (when the server answered one) is the visibility, because DYNAMIC
    weather moves it after the mission starts; the mission's own figure is the fallback.

    ``answer`` is the game server's own ``getWeatherInfo`` reply, used only when the mission object
    in THIS process carries no weather: a page served from a node that holds a copied mission gets
    the mission's name and map but not its weather block, and the block would otherwise read as
    "the mission reports no weather" while the status embed shows it.
    """
    mission = _mission_of(server)
    weather = getattr(mission, "weather", None) if mission is not None else None
    clouds = getattr(mission, "clouds", None) if mission is not None else None
    weather = weather if isinstance(weather, dict) else {}
    clouds = clouds if isinstance(clouds, dict) else {}
    if answer is not None:
        remote_weather = answer.get("weather")
        remote_clouds = answer.get("clouds")
        if not weather and isinstance(remote_weather, dict):
            weather = remote_weather
        if not clouds and isinstance(remote_clouds, dict):
            clouds = remote_clouds
    if not weather and not clouds:
        return None
    raw_wind = weather.get("wind")
    wind = raw_wind if isinstance(raw_wind, dict) else {}

    def level(key: str) -> dict | None:
        branch = wind.get(key)
        if not isinstance(branch, dict):
            return None
        direction = _float(branch.get("dir"))
        speed = _float(branch.get("speed"))
        if direction is None or speed is None:
            return None
        return {"dir": direction, "speed": speed}

    raw_visibility = weather.get("visibility")
    visibility = (_float(raw_visibility.get("distance"))
                  if isinstance(raw_visibility, dict) else None)
    live_visibility = False
    if isinstance(fog, dict) and fog.get("visibility") is not None:
        measured = _float(fog.get("visibility"))
        if measured is not None:
            visibility, live_visibility = measured, True
    raw_preset = clouds.get("preset")
    preset = raw_preset if isinstance(raw_preset, dict) else {}
    raw_season = weather.get("season")
    temperature = (_float(raw_season.get("temperature"))
                   if isinstance(raw_season, dict) else None)
    density = clouds.get("density")
    return {
        "temperature": temperature,
        "qnh_mmhg": _float(weather.get("qnh")),
        "visibility": visibility,
        "live_visibility": live_visibility,
        "clouds": {"preset": _text(preset.get("readableName")).split("/")[-1].strip(),
                   "base": _float(clouds.get("base")),
                   "density": int(_number(density)) if density is not None else None,
                   "thickness": _float(clouds.get("thickness"))},
        "wind": {"ground": level("atGround"), "low": level("at2000"), "high": level("at8000")},
    }


def _extensions_payload(entries: Any) -> list[dict]:
    """The public extension render as plain dicts: name, version, value lines, withheld count."""
    rows: list[dict] = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        if not name:
            continue
        rows.append({"name": name, "version": _plain_version(entry.get("version")),
                     "value": _text(entry.get("value")),
                     "withheld": int(_number(entry.get("withheld")) or 0)})
    return rows


# --------------------------------------------------------------------------- the weekly schedule

#: The scheduler block's three states inside a live read. ``ready`` carries the week, ``absent`` is
#: the case no page may draw at all (no Scheduler plugin, no ``schedule``, or the trivial always-on
#: week), and ``failed`` is a config this read could not take apart — the page states that in one
#: sentence and still draws no grid.
SCHEDULE_READY = "ready"
SCHEDULE_ABSENT = "absent"
SCHEDULE_FAILED = "failed"

#: the one week that means "no schedule": a server that always runs.
SCHEDULE_TRIVIAL = {"00-24": "YYYYYYY"}

#: the letters a day cell may carry: ``Y`` runs, ``N`` off, ``P`` shuts down with no players.
SCHEDULE_LETTERS = "YNP"

#: the day count of one period's pattern.
SCHEDULE_DAYS = 7


def _scheduler(server: Any) -> Any:
    """The connected bot's ``Scheduler`` cog — the plugin's own per-server config — or ``None``.

    Read through the service registry (``BotService.bot.cogs['Scheduler']``), never from a second
    parse of ``scheduler.yaml``. A process with no bus, no bot or no plugin answers ``None``.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        bot = getattr(ServiceRegistry.get(BotService), "bot", None)
        cogs = getattr(bot, "cogs", None)
        return cogs.get("Scheduler") if cogs is not None else None
    except Exception:  # noqa: BLE001 - an absent bot or plugin is "no schedule", not an error
        return None


def _schedule_rows(schedule: dict) -> list[dict]:
    """Every readable period of a ``schedule`` map as ``{"period", "days"}``, in file order.

    A period is ``"HH-HH"`` and its value exactly seven letters of :data:`SCHEDULE_LETTERS`; the
    block's own ``timezone`` key is not a period and every entry this read cannot take apart is
    dropped rather than guessed into a state.
    """
    rows: list[dict] = []
    for period, pattern in schedule.items():
        name = _text(period)
        if not name or name == "timezone":
            continue
        days = _text(pattern).upper()
        if len(days) != SCHEDULE_DAYS or any(day not in SCHEDULE_LETTERS for day in days):
            continue
        rows.append({"period": name, "days": days})
    return rows


def _schedule_payload(server: Any) -> dict:
    """The server's weekly schedule as the live read carries it — ``state``, ``rows``, ``timezone``.

    The plugin is asked HERE, inside the cached read, so a page render never reaches it. The
    timezone is the config's own; the ``schedule`` block may state it instead, so that key is read
    as a fallback.
    """
    empty = {"state": SCHEDULE_ABSENT, "rows": [], "timezone": ""}
    scheduler = _scheduler(server)
    if scheduler is None:
        return empty
    try:
        config = scheduler.get_config(server)
    except Exception as ex:  # noqa: BLE001 - a plugin that will not answer is one sentence
        _warn(_live_key(server), "schedule", ex)
        return {**empty, "state": SCHEDULE_FAILED}
    if not isinstance(config, dict):
        return empty
    schedule = config.get("schedule")
    if not isinstance(schedule, dict) or not schedule or schedule == SCHEDULE_TRIVIAL:
        return empty
    rows = _schedule_rows(schedule)
    if not rows:
        return {**empty, "state": SCHEDULE_FAILED}
    timezone = _text(config.get("timezone")) or _text(schedule.get("timezone"))
    return {"state": SCHEDULE_READY, "rows": rows, "timezone": timezone}


#: one cached live reading per server name: ``(monotonic stamp, payload)``.
_live_cache: dict[str, tuple[float, dict]] = {}
#: the refresh in flight per server name, so a second page view joins it instead of firing another.
_live_refreshing: dict[str, Any] = {}


def _warn(name: str, block: str, ex: BaseException) -> None:
    """One warning line for a live block that did not answer: the server, the block, the failure type.

    Never the payload and never ``exc_info``: a DCS reply carries mission data this line must not
    copy into the log.
    """
    log.warning("%s: the %s read did not answer (%s)", name or "?", block, type(ex).__name__)


async def _bounded(call: Any, timeout: float, name: str, block: str) -> Any:
    """Await ``call()`` for at most *timeout* seconds; ``None`` when it hung or refused.

    A block's failure is one warning line and NOTHING else: the caller keeps whatever it already
    holds and states the block as failed, so a silent RPC never pins the rest of the payload.
    """
    try:
        return await asyncio.wait_for(call(), timeout)
    except Exception as ex:  # noqa: BLE001 - a hung or refusing RPC is one sentence, not an error
        _warn(name, block, ex)
        return None


def _payload(state: str, weather: Any = None, extensions: Any = None,
             schedule: Any = None, weather_state: str | None = None,
             extensions_state: str | None = None) -> dict:
    """A live read's answer: its state, the three blocks, and each block's own state.

    ``weather_state`` / ``extensions_state`` are :data:`BLOCK_READY`, :data:`BLOCK_FAILED` or
    :data:`BLOCK_EMPTY`, and default from what the block carries: a block with figures is ready, one
    without is empty.
    """
    entries = list(extensions or ())
    return {
        "state": state,
        "weather": weather,
        "weather_state": weather_state or (BLOCK_READY if weather is not None else BLOCK_EMPTY),
        "extensions": entries,
        "extensions_state": extensions_state or (BLOCK_READY if entries else BLOCK_EMPTY),
        "schedule": schedule or {"state": SCHEDULE_ABSENT, "rows": [], "timezone": ""},
    }


async def _read_live(server: Any) -> dict:
    """Read the server's weather, public extensions and weekly schedule — the ONE live read.

    The weather is built from the mission object FIRST, so its figures are cached whether or not the
    node answers. The two RPCs — ``getFog`` (a refinement of the visibility figure only) and the
    public extensions render — are each bounded (:data:`FOG_TIMEOUT`, :data:`EXTENSIONS_TIMEOUT`)
    and asked together, so one silent node costs the longer bound once instead of a reply timeout.
    """
    name = _live_key(server)
    # the schedule is the plugin's per-server config, in process: it is read whether the server is
    # up or not, so a stopped server still states the week it will run on
    schedule = _schedule_payload(server)
    if _status_text(server) not in ONLINE_STATUSES:
        return _payload(LIVE_DOWN, schedule=schedule)
    try:
        weather = _weather_payload(server, None)
        weather_state = BLOCK_READY if weather is not None else BLOCK_EMPTY
    except Exception as ex:  # noqa: BLE001 - a half-read weather block states nothing
        _warn(name, "weather", ex)
        weather, weather_state = None, BLOCK_FAILED
    fog, entries = await asyncio.gather(
        _bounded(lambda: server.send_to_dcs_sync({"command": "getFog"}),
                 FOG_TIMEOUT, name, "weather"),
        _bounded(lambda: server.render_extensions(public=True),
                 EXTENSIONS_TIMEOUT, name, "extensions"))
    if weather_state != BLOCK_READY:
        # THE MISSION OBJECT DOES NOT CARRY THE WEATHER IN EVERY PROCESS. The status embed is drawn
        # where the mission data arrives; a page served by another node's copy can hold the mission
        # (name, map, slots) with an empty ``weather`` block, and the block then reads as "the
        # mission reports no weather" while the embed shows it. The game server is the source of
        # record, so one bounded ``getWeatherInfo`` asks for exactly the two dicts the block needs.
        answer = await _bounded(lambda: server.send_to_dcs_sync({"command": "getWeatherInfo"}),
                                FOG_TIMEOUT, name, "weather")
        if isinstance(answer, dict) and (answer.get("weather") or answer.get("clouds")):
            try:
                weather = _weather_payload(server, fog, answer=answer)
                weather_state = BLOCK_READY if weather is not None else BLOCK_EMPTY
            except Exception as ex:  # noqa: BLE001 - the fallback is one attempt, not a retry loop
                _warn(name, "weather", ex)
    elif isinstance(fog, dict) and weather_state == BLOCK_READY:
        try:
            weather = _weather_payload(server, fog) or weather
        except Exception:  # noqa: BLE001 - the mission's own figures already answer the block
            pass
    if entries is None:
        extensions, extensions_state = [], BLOCK_FAILED
    else:
        extensions, extensions_state = _extensions_payload(entries), BLOCK_READY
    return _payload(LIVE_READY, weather, extensions, schedule,
                    weather_state=weather_state, extensions_state=extensions_state)


def _refresh_live(server: Any, name: str) -> None:
    """Schedule one background refresh of *server*'s live read; a running one is reused."""
    running = _live_refreshing.get(name)
    if running is not None and not running.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - a page render always runs inside a loop
        return

    async def run() -> None:
        try:
            answer = await _read_live(server)
        except Exception as ex:  # noqa: BLE001 - a read that blew up is a cached failure, not a pin
            _warn(name, "live", ex)
            answer = _payload(LIVE_FAILED)
        finally:
            _live_refreshing.pop(name, None)
        _live_cache[name] = (time.monotonic(), answer)

    task = loop.create_task(run())
    _live_refreshing[name] = task


def live_read(server: Any) -> dict:
    """The cached live read for *server*, refreshing it in the background when it is stale.

    NEVER AWAITS: a page view answers the snapshot at hand and schedules the next one, so the two
    RPCs behind this block (``getFog`` and the public extensions render) never sit on the render
    path. EVERY outcome is cached — ready, down and failed — so ``pending`` is the state of the very
    first view of a server this process has not read yet, and never again after that: the page says
    so in one sentence instead of drawing empty instruments.

    A SNAPSHOT THAT CONTRADICTS THE ROSTER IS NOT SERVED. A read taken while the server was off says
    ``down``; once the roster says the server is up, that answer is stale the moment it is asked for,
    whatever its age. The read is refreshed at once and the caller is told the read is in flight —
    saying "the server is not running" about a running server is worse than one view without
    figures. A ready snapshot is dropped the same way when the server stops.
    """
    name = _live_key(server)
    if not name:
        return _payload(LIVE_PENDING)
    now = time.monotonic()
    entry = _live_cache.get(name)
    if entry is None:
        _refresh_live(server, name)
        return _payload(LIVE_PENDING)
    payload = dict(entry[1])
    online = _status_text(server) in ONLINE_STATUSES
    reads_online = payload.get("state") != LIVE_DOWN
    # THE MEMORY HALF IS RECOMPOSED ON EVERY CALL. The weather is the mission's own dict — no RPC,
    # no statement — so a snapshot cached while the mission was still loading (state ``ready`` with
    # an empty weather block) must not hide the weather for its whole window. Only an EMPTY block is
    # filled: a payload already carrying figures, live visibility included, is left alone.
    if payload.get("state") == LIVE_READY and not payload.get("weather"):
        try:
            fresh = _weather_payload(server, None)
        except Exception as ex:  # noqa: BLE001 - a half-read mission keeps the cached answer
            _warn(name, "weather", ex)
            fresh = None
        if fresh:
            payload["weather"] = fresh
            payload["weather_state"] = BLOCK_READY
    window = LIVE_READ_TTL if payload.get("state") == LIVE_READY else LIVE_STALE_TTL
    if not payload.get("weather"):
        # an empty weather block is re-read like a non-ready answer: the mission may simply not have
        # published its figures yet
        window = min(window, LIVE_STALE_TTL)
    if now - entry[0] >= window or online != reads_online:
        _refresh_live(server, name)
    if online != reads_online:
        # the schedule is the plugin's config and states itself whether the server runs, so it
        # survives the wait this answer imposes on the live blocks
        return _payload(LIVE_PENDING, schedule=payload.get("schedule"))
    return payload


def prefetch_live(servers: Any = None) -> int:
    """Schedule a live read for every ONLINE server whose cached answer is stale or absent.

    A page already does this for the one server it shows; a fleet view warms all of them, so the
    first visit to a server's page is not the one that pays the read. Never raises: a server that
    cannot be read caches that outcome like any other. Returns how many reads it scheduled.
    """
    registry = _live_servers(servers)
    scheduled = 0
    for server in list(registry.values()):
        name = _live_key(server)
        if not name or _status_text(server) not in ONLINE_STATUSES:
            continue
        entry = _live_cache.get(name)
        if entry is not None and time.monotonic() - entry[0] < LIVE_READ_TTL:
            continue
        try:
            live_read(server)
        except Exception as ex:  # noqa: BLE001 - a prefix is an optimisation, never a failure
            _warn(name, "live", ex)
            continue
        scheduled += 1
    return scheduled


def remember_live_read(name: str, payload: dict) -> None:
    """File *payload* as the fresh live read of *name* (the suite's own entry point)."""
    _live_cache[str(name)] = (time.monotonic(), dict(payload))


def forget_live_reads() -> None:
    """Empty the live-read cache (the suite's own entry point; a live process never needs it)."""
    _live_cache.clear()


def _published_password(server: Any) -> str:
    """The join password the PUBLIC SITE may print, or ``""``.

    Two conditions, both required: the server's ``servers.yaml`` entry carries
    ``show_web_passwords: true`` (absent means OFF), and the server actually holds a non-blank
    password. The Discord embed's own ``show_passwords`` is deliberately NOT read here — the two
    settings are independent, so one being on never publishes the other's value.
    """
    locals_ = getattr(server, "locals", None)
    if not isinstance(locals_, dict) or not locals_.get("show_web_passwords", False):
        return ""
    password = _settings(server).get("password")
    if not isinstance(password, str) or not password.strip():
        return ""
    return password


def _live_half(name: str, server: Any) -> dict:
    """The roster half of one fleet row: state, players, mission, options, address and clocks.

    Read from the process's own server object and NOTHING else — no statement, no RPC — which is
    what lets a page poll it as often as it likes. The historical half (``playtime``/``sorties``)
    comes from the database and belongs to :func:`server_list`.
    """
    mission = _mission_of(server)
    blue, red = _slots(server)
    status = _status_text(server)
    locals_ = getattr(server, "locals", None)
    return {
        "name": name,
        "status": status,
        "online": status in ONLINE_STATUSES,
        "locked": bool(_settings(server).get("password")),
        "password": _published_password(server),
        "players": _active_players(server),
        "max_players": _max_players(server),
        "mission": _text(getattr(mission, "name", None)) if mission is not None else "",
        "map": _text(getattr(mission, "map", None)) if mission is not None else "",
        "runtime": int(_number(getattr(mission, "mission_time", 0)) or 0) if mission is not None
        else None,
        "restart_in": _restart_seconds(server),
        "blue": blue,
        "red": red,
        "coalitions": bool(isinstance(locals_, dict) and locals_.get("coalitions")),
        "address": _address(server),
    }


def _server_row(name: str, server: Any, figures: dict) -> dict:
    """One fleet row: the live half from the roster, the historical half from ``mv_serverstats``."""
    return {**_live_half(name, server),
            "playtime": _number(figures.get("playtime")),
            "sessions": _number(figures.get("sessions")),
            "window": BOARD_WINDOW_LABEL}


def fleet_live(site: Any, live: Any = None) -> list[dict]:
    """The live half of the site's fleet rows, one per server, in the page's own order.

    No database and no RPC: the state, the players in slots, the mission, the options and the two
    clocks come from the process registry the dashboard reads. This is what the fleet page polls —
    every figure here changes within seconds, and none of it costs a query, so a visitor's browser
    may ask for it as often as the page likes.
    """
    registry = _live_servers(live)
    fleet = [_live_half(name, registry.get(name)) for name in _server_list(site)]
    fleet.sort(key=lambda row: (0 if row["online"] else 1, row["name"].lower()))
    return fleet


async def server_list(site: Any, pool: Any, live: Any = None) -> list[dict]:
    """One row per server of the site, up first and then by name — the fleet the page draws.

    The live half is the in-process roster the dashboard reads (state, players, mission, map,
    runtime, restart-in, lock); the historical half is ``mv_serverstats``' all-time
    playtime and session count, read in the SAME statement the site filter binds. A server with no
    recorded session is still a row, at zero.
    """
    names = _server_list(site)
    async with pool.connection() as conn:
        rows = await _fetch_all(conn, _BOARD_SQL, _params(site))
    figures = {str(row.get("server_name")): row for row in rows}
    registry = _live_servers(live)
    fleet = [_server_row(name, registry.get(name), figures.get(name, {})) for name in names]
    fleet.sort(key=lambda row: (0 if row["online"] else 1, row["name"].lower()))
    return fleet


async def server_detail(site: Any, pool: Any, live: Any = None, name: str = "") -> dict:
    """One server's inventory, or ``{"found": False}``.

    Built on :func:`server_list`, so the page's list and its detail can never disagree about a live
    figure, and a name that is not one of THIS site's servers answers not-found before anything is
    read — a group's server is never handed to another site's page.
    """
    wanted = str(name or "").strip()
    fleet = await server_list(site, pool, live)
    row = next((entry for entry in fleet if entry["name"] == wanted), None)
    if row is None:
        return {"found": False, "name": wanted}
    server = _live_servers(live).get(wanted)
    return {
        "found": True,
        "name": wanted,
        "server": row,
        "address": row["address"],
        "mission_clock": _mission_clock(_mission_of(server)),
        "limits": _limits(server),
        "dcs_version": _text(getattr(getattr(server, "node", None), "dcs_version", "")),
        "bot_version": _bot_version(server),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- my statistics

async def my_statistics(site: Any, pool: Any, discord_id: Any, period: str = "quarter") -> dict:
    """The viewer's own figures: every UCID linked to *discord_id*, scoped to the site.

    The UCID union is read from ``players`` (a link lives per player, not per server), but only for
    UCIDs seen on the site's servers; every figure is then the pilot profile's own set, bound to
    those servers, plus the panels the public profile does not carry: the competitive rating with
    its recorded history, the pilot's campaigns (credits and badge), the pilot's place in the site's
    K/D cohort (:func:`pilot_standing`) and — when the ``missionstats`` plugin is loaded — the
    flights behind :func:`pilot_progress` for *period*. *period* is the bot's
    own period vocabulary; an unknown name resolves to ``all``. No linked UCID on the site answers
    ``found`` False with the profile's keys, and a site without the events table answers
    ``"progress": None`` instead of failing.
    """
    try:
        wanted = int(discord_id)
    except (TypeError, ValueError):
        wanted = -1
    empty = _empty_profile("")
    empty.update({"discord_id": wanted, "progress": None, "standing": None})
    if wanted <= 0:
        return empty
    async with pool.connection() as conn:
        linked = await _fetch_all(conn, _LINKED_BY_DISCORD_SQL,
                                  _params(site, discord_id=wanted))
        ucids = [str(row.get("ucid")) for row in linked if row.get("ucid")]
        if not ucids:
            return empty
        detail = await _pilot_detail(conn, site, ucids)
        rating = await _pilot_rating(conn, ucids[0])
        campaigns = await _campaigns(conn, site, ucids[0])
    progress = await pilot_progress(site, pool, ucids[0], period)
    standing = await pilot_standing(site, pool, ucids[0], period)
    return {
        "ucid": ucids[0],
        "name": "",
        "found": True,
        "discord_id": wanted,
        "rating": rating,
        "campaigns": campaigns,
        "progress": progress,
        "standing": standing,
        "ucids": [{"ucid": row.get("ucid"), "name": row.get("name") or ""} for row in linked],
        **detail,
    }


# --------------------------------------------------------------------------- pilot progress

#: The most buckets a progress panel draws, counting back from the newest one. A bar per week of a
#: whole year is 52 slivers on a phone, so the two longest periods switch to a monthly grain rather
#: than drawing narrower bars.
PROGRESS_BUCKETS = 26

#: The periods the progress panel buckets by MONTH. Every other period buckets by ISO week.
PROGRESS_MONTHLY_PERIODS = frozenset({"year", "all"})


def _mission_period_clause(period: Any) -> tuple[str, str]:
    """The period clause for a ``missionstats`` statement, and the name it resolved to.

    ``plugins.userstats.filter.MissionStatisticsFilter`` is the event table's own variant of the one
    period vocabulary (``/aar`` takes it as its period filter), so the site does not grow a second
    one. Its predicate names the bare column ``time``; this qualifies it, because the statement also
    joins ``missions``. Only the lowercase column name matches, so ``AT TIME ZONE`` is untouched.
    """
    from plugins.userstats.filter import MissionStatisticsFilter, PeriodFilter

    known = PeriodFilter.list(None)  # type: ignore[arg-type]
    wanted = str(period or "").strip().lower()
    if wanted not in known:
        wanted = "all"
    clause = MissionStatisticsFilter(wanted).filter(None)  # type: ignore[arg-type]
    return re.sub(r"\btime\b", "ms.time", clause), wanted


#: One pilot's own sortie events in the period, in the order the reconstruction needs them
#: (``ms.id``, not ``time``: two events can share a timestamp, and the reconstruction is a state
#: machine over the stream). The kill query is ``plugins.missionstats.reports.AAR._collect``'s,
#: filter for filter, so the page and the PDF count the same kills.
_PROGRESS_SORTIE_SQL = f"""
    SELECT ms.mission_id AS mission_id, ms.event AS event, ms.init_type AS init_type,
           ms.init_side AS init_side, ms.place AS place, ms.time AS time,
           m.mission_name AS mission_name, m.mission_theatre AS mission_theatre,
           m.server_name AS server_name
      FROM missionstats ms
      JOIN missions m ON m.id = ms.mission_id
     WHERE ms.init_id = %(ucid)s
       AND ms.event = ANY(%(events)s::text[])
       AND m.{SERVER_FILTER} AND {{period}}
     ORDER BY ms.id
"""

_PROGRESS_KILLS_SQL = f"""
    SELECT ms.time AS time
      FROM missionstats ms
      JOIN missions m ON m.id = ms.mission_id
     WHERE ms.init_id = %(ucid)s
       AND ms.init_id IS DISTINCT FROM ms.target_id
       AND ms.event = 'S_EVENT_KILL'
       AND ms.weapon != ms.init_type
       AND ms.target_cat IS NOT NULL
       AND ms.target_side != '0'
       AND m.{SERVER_FILTER} AND {{period}}
     ORDER BY ms.time
"""


def _bucket_of(moment: Any, monthly: bool) -> date | None:
    """The bucket a moment falls in: its ISO week's Monday, or the first of its month."""
    if not isinstance(moment, datetime):
        return None
    day = moment.date()
    return date(day.year, day.month, 1) if monthly else day - timedelta(days=day.weekday())


def _next_bucket(start: date, monthly: bool) -> date:
    """The bucket after *start*, on the same grain."""
    if not monthly:
        return start + timedelta(days=7)
    return date(start.year + start.month // 12, start.month % 12 + 1, 1)


def _bucket_starts(first: date, last: date, monthly: bool) -> list[date]:
    """Every bucket from *first* to *last* inclusive, oldest first, capped to the newest window.

    The empty buckets are kept on purpose: a week nobody flew is a gap in the chart, and dropping it
    would draw a steady line across a break in the record.
    """
    starts, cursor = [], first
    while cursor <= last:
        starts.append(cursor)
        cursor = _next_bucket(cursor, monthly)
    return starts[-PROGRESS_BUCKETS:]


def _rate(value: Any, total: Any) -> float | None:
    """*value* per *total* to two decimals; ``None`` when *total* is zero (no rate exists)."""
    try:
        whole = float(total or 0)
    except (TypeError, ValueError):
        return None
    if whole <= 0:
        return None
    return round(float(value or 0) / whole, 2)


async def pilot_progress(site: Any, pool: Any, ucid: Any, period: str = "quarter") -> dict | None:
    """One pilot's flights over time, reconstructed from the events of the site's servers.

    A sortie is a FLIGHT, so it cannot come from ``statistics``, where one row is a boarding of a
    slot: that row can hold two takeoff/landing pairs, and a pilot who sits on the ramp and leaves
    is a row with no flight in it. :func:`plugins.missionstats.aar.reconstruct_sorties` rebuilds
    them from the event stream — the same function the downloadable AAR PDF is built from — and
    calls each one ``RTB`` (came back), a loss (``LOSS_OUTCOMES``) or neither (left the slot, never
    finished). The three groups are disjoint and always add up to the sortie count.

    ``period`` is the bot's own vocabulary, resolved by :func:`_mission_period_clause`; the buckets
    are ISO weeks for ``month``/``quarter``/``halfyear`` and months for ``year``/``all``, capped to
    the newest :data:`PROGRESS_BUCKETS`.

    Answers ``None`` when the ``missionstats`` plugin's table is absent — the page then renders no
    progress panel at all, the way it renders no campaigns panel without the credit system.

    ``kd`` is kills per LOST sortie and is ``None`` when nothing was lost; ``return_rate`` is
    returned per sortie and ``None`` when no sortie was flown. Both are rates on purpose: a total
    only ever grows, so it says nothing about progress.
    """
    from plugins.missionstats import aar

    wanted = str(ucid or "").strip()
    if not wanted:
        return None
    clause, resolved = _mission_period_clause(period)
    monthly = resolved in PROGRESS_MONTHLY_PERIODS
    params = _params(site, ucid=wanted, events=list(aar.SORTIE_EVENTS))
    async with pool.connection() as conn:
        if not await _table_exists(conn, "missionstats"):
            return None
        events = await _fetch_all(conn, _PROGRESS_SORTIE_SQL.format(period=clause), params)
        kills = await _fetch_all(conn, _PROGRESS_KILLS_SQL.format(period=clause), params)

    sorties, aborted = aar.reconstruct_sorties(events)
    returned = [sortie for sortie in sorties if sortie.recovered]
    lost = [sortie for sortie in sorties if sortie.lost]
    moments: list[datetime] = []
    for sortie in sorties:
        if isinstance(sortie.start, datetime):
            moments.append(sortie.start)
    for row in kills:
        if isinstance(row.get("time"), datetime):
            moments.append(row["time"])
    first = _bucket_of(min(moments), monthly) if moments else None
    last = _bucket_of(max(moments), monthly) if moments else None
    buckets = _bucket_starts(first, last, monthly) if first and last else []
    rows = {start: {"start": start.isoformat(), "sorties": 0, "returned": 0, "lost": 0, "kills": 0}
            for start in buckets}
    for sortie in sorties:
        where = _bucket_of(sortie.start, monthly)
        if where is None:
            continue
        bucket = rows.get(where)
        if bucket is None:
            continue
        bucket["sorties"] += 1
        bucket["returned"] += int(sortie.recovered)
        bucket["lost"] += int(sortie.lost)
    for row in kills:
        where = _bucket_of(row.get("time"), monthly)
        if where is None:
            continue
        bucket = rows.get(where)
        if bucket is not None:
            bucket["kills"] += 1
    return {
        "period": resolved,
        "grain": "month" if monthly else "week",
        "buckets": list(rows.values()),
        "sorties": len(sorties),
        "returned": len(returned),
        "lost": len(lost),
        "unfinished": len(sorties) - len(returned) - len(lost),
        "aborted": aborted,
        "kills": len(kills),
        "kd": _rate(len(kills), len(lost)),
        "return_rate": _rate(len(returned), len(sorties)),
    }
