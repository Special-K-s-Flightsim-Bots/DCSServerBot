"""The ONE definition of the statistics numbers: the server reads and the node read.

Every function here answers PLAIN ROWS (dicts) from one statement and formats nothing — no embed
text, no labels, no column renaming. The Discord report elements (``serverstats.py``,
``admin/reports.py``) and the console's actions (``monitoring/actions.py``) both call them, so a
second copy of a statement cannot exist and the two surfaces cannot disagree about a number.

``period_clause`` is the SQL fragment the bot's OWN period filter produced
(``StatisticsFilter.filter``), interpolated into the statement the way the report has always
interpolated it; ``period_clause()`` below is the one place a period NAME is turned into one.
"""
from __future__ import annotations

import logging
import types
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from psycopg.rows import dict_row

log = logging.getLogger(__name__)

#: The row limits the ``serverstats.json`` report declares for its two TOP tables, so the console's
#: default view is the report's default view.
SERVER_STATS_MISSION_LIMIT = 3
SERVER_STATS_MODULE_LIMIT = 10

#: The periods ``/node statistics`` publishes (its command's ``Literal``), lower-cased. Anything
#: else is :data:`NODE_STATS_DEFAULT_PERIOD`, which is that command's own default.
NODE_STATS_PERIODS: tuple[str, ...] = ("hour", "day", "week", "month")
NODE_STATS_DEFAULT_PERIOD = "hour"

#: The interval a read covers when the caller states none — the ``interval`` the report's remaining
#: graph elements are declared with (``serverstats.json``).
DEFAULT_INTERVAL = "1 month"

#: The window (in days) each period name covers, for the series declared on a day count instead
#: of an interval. ``all`` keeps :data:`DEFAULT_WINDOW_DAYS`.
DEFAULT_WINDOW_DAYS = 30

#: The window = the report's own default whenever a period name cannot be expressed as one.
_PERIOD_DAYS: dict[str, int] = {
    "today": 1,
    "yesterday": 1,
    "day": 1,
    "week": 7,
    "month": 30,
    "quarter": 90,
    "halfyear": 182,
    "year": 365,
}

#: The closed range an explicit ``window_days`` argument is accepted in, in days.
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 365

#: Above this many days a per-day series stops being readable as a column chart, so the unique-user
#: series is read at WEEK grain instead — the counts are still distinct per bucket, never a sum of
#: the daily ones.
UNIQUE_USERS_WEEKLY_AFTER_DAYS = 90


def _where(server_name: str | None) -> str:
    """The server restriction a read carries, or an empty string for the whole installation."""
    return "AND m.server_name = %(server_name)s" if server_name else ""


def _limit(limit: int) -> int:
    """A row limit as an integer — the one value these statements interpolate besides the clause.

    A limit reaches a statement as text, so it is forced through ``int`` here rather than trusted:
    a caller-supplied string would otherwise be spliced into the SQL.
    """
    return int(limit)


async def _rows(pool: Any, statement: str, params: dict) -> list[dict]:
    """Every row of *statement* as a plain dict, in the order the statement returns them."""
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(statement, params)
            return [dict(row) async for row in cursor]


def period_clause(period: str | None, node: Any = None) -> tuple[str, str]:
    """The SQL clause for a public period name, and the name it resolved to.

    ``PeriodFilter`` is the bot's one definition of a period, so its vocabulary is the only one
    accepted: a name it does not publish is ``all`` — the value the report's own period filter
    defaults to (``PeriodFilter()`` with no period), i.e. no restriction at all.
    """
    from plugins.userstats.filter import PeriodFilter, StatisticsFilter

    wanted = (period or "").strip().lower()
    if wanted not in PeriodFilter.list(node):
        wanted = "all"
    try:
        # ``detect`` expects a bot: an object carrying the ``node`` the filters read their pools from.
        detected = StatisticsFilter.detect(cast(Any, types.SimpleNamespace(node=node)), wanted)
    except Exception:
        log.warning("statistics: the period filter could not be detected, using '%s'", wanted)
        detected = None
    if type(detected) is not PeriodFilter or detected.period != wanted:
        detected = PeriodFilter(wanted)
    return detected.filter(node), wanted


def period_days(period: str) -> int:
    """The day count a resolved period name covers, for the reads declared on a window.

    ``all`` states no interval of its own, so it keeps :data:`DEFAULT_WINDOW_DAYS`.
    """
    return _PERIOD_DAYS.get((period or "").strip().lower(), DEFAULT_WINDOW_DAYS)


def unique_users_grain(period: str | None) -> str:
    """The bucket :func:`unique_users` is read at for *period*: ``week`` once a range is long.

    ``all`` is the whole history and is bucketed by week for that reason alone; every other name
    answers by its own day count, so the switch is :data:`UNIQUE_USERS_WEEKLY_AFTER_DAYS` and
    nothing else.
    """
    if (period or "").strip().lower() == "all":
        return "week"
    return "week" if period_days(period or "") > UNIQUE_USERS_WEEKLY_AFTER_DAYS else "day"


def window_days(value: Any, period: str) -> int:
    """The window a caller asked for, or the period's own day count when it asked for nothing valid.

    A value outside :data:`MIN_WINDOW_DAYS` .. :data:`MAX_WINDOW_DAYS`, or one that is not a whole
    number, is never an error: it falls back to :func:`period_days`. The day count reaches a
    statement as a bound integer, never as text.
    """
    try:
        days = float(str(value).strip())
    except (TypeError, ValueError):
        return period_days(period)
    if not days.is_integer() or not MIN_WINDOW_DAYS <= days <= MAX_WINDOW_DAYS:
        return period_days(period)
    return int(days)


def node_period(period: str | None) -> str:
    """The canonical node-stats period name for *period*; anything unknown is the command's default."""
    wanted = (period or "").strip().lower()
    return wanted if wanted in NODE_STATS_PERIODS else NODE_STATS_DEFAULT_PERIOD


# ── Server statistics ───────────────────────────────────────────────────────


async def server_usage(pool: Any, *, server_name: str | None = None, period_clause: str = "1 = 1",
                       server_filter: str = "!.*") -> list[dict]:
    """Playtime, players, Discord members, sessions, missions and new players per server.

    One row is one session, so ``sessions`` is a row count. ``new_players`` counts the players
    whose FIRST session of the installation falls inside the period — the meaning
    :func:`unique_users` gives the word, not every player seen in the window.
    """
    sql = f"""
        WITH first_visit AS (
            SELECT s.player_ucid
            FROM (
                SELECT s.player_ucid, MIN(s.hop_on) AS hop_on
                FROM statistics s, missions m
                WHERE m.id = s.mission_id
                {_where(server_name)}
                GROUP BY s.player_ucid
            ) s
            WHERE {period_clause}
        )
        SELECT trim(regexp_replace(m.server_name, '{server_filter}', '', 'g')) AS server_name,
               ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime,
               COUNT(DISTINCT s.player_ucid) AS players,
               COUNT(DISTINCT p.discord_id) AS members,
               COUNT(*) AS sessions,
               COUNT(DISTINCT m.id) AS missions,
               COUNT(DISTINCT fv.player_ucid) AS new_players
        FROM missions m
        JOIN statistics s ON m.id = s.mission_id
        JOIN players p ON s.player_ucid = p.ucid
        LEFT JOIN first_visit fv ON fv.player_ucid = s.player_ucid
        WHERE s.hop_off IS NOT NULL
        {_where(server_name)}
        AND {period_clause}
        GROUP BY 1
        ORDER BY 2 DESC
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def server_top_theatres(pool: Any, *, server_name: str | None = None,
                              period_clause: str = "1 = 1",
                              server_filter: str = "!.*") -> list[dict]:
    """The theatre of most playtime for EACH server — one row per server."""
    sql = f"""
        WITH per_theatre AS (
            SELECT trim(regexp_replace(m.server_name, '{server_filter}', '', 'g')) AS server_name,
                   m.mission_theatre,
                   COALESCE(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))), 0) AS playtime
            FROM missions m
            JOIN statistics s ON m.id = s.mission_id
            WHERE s.hop_off IS NOT NULL
            {_where(server_name)}
            AND {period_clause}
            GROUP BY 1, 2
        )
        SELECT DISTINCT ON (server_name) server_name, mission_theatre
        FROM per_theatre
        ORDER BY server_name, playtime DESC, mission_theatre
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def server_cluster(pool: Any, *, server_name: str | None = None,
                         period_clause: str = "1 = 1") -> list[dict]:
    """One row for the whole scope: sessions, playtime, new players, rates and means.

    ``players_distinct`` is a ``COUNT(DISTINCT player_ucid)`` over the whole scope, never the sum
    of the per-server distinct counts. ``returning_rate`` is the share of those players that were
    not new in the period; ``avg_session`` is the mean session length in hours.
    """
    sql = f"""
        WITH first_visit AS (
            SELECT s.player_ucid
            FROM (
                SELECT s.player_ucid, MIN(s.hop_on) AS hop_on
                FROM statistics s
                JOIN missions m ON m.id = s.mission_id
                {_where(server_name)}
                GROUP BY s.player_ucid
            ) s
            WHERE {period_clause}
        ),
        sessions AS (
            SELECT s.player_ucid,
                   EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)) AS seconds
            FROM statistics s
            JOIN missions m ON m.id = s.mission_id
            WHERE s.hop_off IS NOT NULL
            {_where(server_name)}
            AND {period_clause}
        )
        SELECT COUNT(*) AS sessions,
               COALESCE(ROUND(SUM(s.seconds) / 3600), 0) AS playtime,
               COUNT(DISTINCT s.player_ucid) AS players_distinct,
               COUNT(DISTINCT fv.player_ucid) AS new_players,
               ROUND((COUNT(DISTINCT s.player_ucid) - COUNT(DISTINCT fv.player_ucid))::numeric
                     / NULLIF(COUNT(DISTINCT s.player_ucid), 0), 4) AS returning_rate,
               ROUND((COALESCE(SUM(s.seconds), 0) / NULLIF(COUNT(*), 0) / 3600)::numeric, 2) AS avg_session
        FROM sessions s
        LEFT JOIN first_visit fv ON fv.player_ucid = s.player_ucid
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def weekly_frequency(pool: Any, *, server_name: str | None = None,
                           window_days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    """Per ISO week of the last *window_days*: session count and distinct players."""
    sql = f"""
        SELECT to_char(date_trunc('week', s.hop_on), 'IYYY-"W"IW') AS week,
               COUNT(*) AS sessions,
               COUNT(DISTINCT s.player_ucid) AS players
        FROM missions m
        JOIN statistics s ON m.id = s.mission_id
        WHERE s.hop_off IS NOT NULL
        {_where(server_name)}
        AND s.hop_on > (now() AT TIME ZONE 'utc') - make_interval(days => %(window_days)s)
        GROUP BY 1
        ORDER BY 1
    """
    return await _rows(pool, sql, {"server_name": server_name, "window_days": int(window_days)})


async def top_theatres(pool: Any, *, server_name: str | None = None,
                       period_clause: str = "1 = 1") -> list[dict]:
    """Playtime per mission theatre, longest first."""
    sql = f"""
        SELECT m.mission_theatre,
               COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS playtime
        FROM missions m, statistics s
        WHERE m.id = s.mission_id
        {_where(server_name)}
        AND {period_clause}
        GROUP BY 1
        ORDER BY 2 DESC
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def top_missions(pool: Any, *, server_name: str | None = None, period_clause: str = "1 = 1",
                       limit: int = SERVER_STATS_MISSION_LIMIT,
                       mission_filter: str = "!.*") -> list[dict]:
    """The *limit* most played missions."""
    sql = f"""
        SELECT trim(regexp_replace(m.mission_name, '{mission_filter}', ' ', 'g')) AS mission_name,
               ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime
        FROM missions m, statistics s
        WHERE m.id = s.mission_id
        AND s.hop_off IS NOT NULL
        {_where(server_name)}
        AND {period_clause}
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT {_limit(limit)}
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def top_modules(pool: Any, *, server_name: str | None = None, period_clause: str = "1 = 1",
                      limit: int = SERVER_STATS_MODULE_LIMIT) -> list[dict]:
    """The *limit* most used modules: playtime, distinct players and use count per slot."""
    sql = f"""
        SELECT s.slot, COUNT(s.slot) AS num_usage,
               COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600),0) AS playtime,
               COUNT(DISTINCT s.player_ucid) AS players
        FROM missions m, statistics s
        WHERE m.id = s.mission_id
        {_where(server_name)}
        AND {period_clause}
        GROUP BY s.slot
        ORDER BY 3 DESC
        LIMIT {_limit(limit)}
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def unique_users(pool: Any, *, server_name: str | None = None,
                       period_clause: str = "1 = 1", grain: str = "day") -> list[dict]:
    """Per bucket of the period: all distinct players seen, and those seen for the first time.

    The series comes from the READ's OWN rows (its first session to its last), not from an interval
    the caller guessed: the period is stated by *period_clause* — the bot's ``PeriodFilter`` output,
    the same clause every other read here takes — so the calendar windows (``today``, ``yesterday``)
    are served exactly, and a period expressed as a span rather than an interval cannot silently fall
    back to a default window.

    *grain* is ``day`` or ``week`` and is the bucket the counts are taken at, never an aggregation
    of the finer one: a player flying three days of one week counts ONCE in that week, which is what
    ``COUNT(DISTINCT …)`` per bucket gives and what summing daily counts cannot. An unknown grain
    reads ``day``. A bucket inside the span with no session keeps its row (a zero), because the
    series is generated across the span; buckets before the first or after the last belong to none.
    """
    grain = grain if grain in ("day", "week") else "day"
    step, width = ("1 week", "7 days") if grain == "week" else ("1 day", "1 day")
    sql = f"""
        WITH players_join AS (
            SELECT
                player_ucid,
                MIN(DATE(hop_on)) AS join_date
            FROM statistics
            GROUP BY player_ucid
        ),
        scoped AS (
            SELECT s.player_ucid,
                   DATE(s.hop_on) AS first_day,
                   DATE(s.hop_off) AS last_day
            FROM statistics s
            INNER JOIN missions m ON s.mission_id = m.id {_where(server_name)}
            AND {period_clause}
        ),
        date_series AS (
            SELECT generate_series(date_trunc('{grain}', MIN(first_day)),
                                   date_trunc('{grain}', MAX(last_day)),
                                   INTERVAL '{step}')::DATE AS date
            FROM scoped
        )
        SELECT
            ds.date AS date,
            COALESCE(COUNT(DISTINCT s.player_ucid), 0) AS total_players,
            COALESCE(COUNT(DISTINCT
                CASE WHEN pj.join_date >= ds.date
                      AND pj.join_date < ds.date + INTERVAL '{width}'
                     THEN s.player_ucid ELSE NULL END), 0) AS new_players
        FROM
            date_series ds
            LEFT JOIN scoped s ON s.first_day < ds.date + INTERVAL '{width}'
                              AND s.last_day >= ds.date
            LEFT JOIN players_join pj ON s.player_ucid = pj.player_ucid
        GROUP BY ds.date
        ORDER BY ds.date
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def users_per_day_time(pool: Any, *, server_name: str | None = None,
                             interval: str | None = DEFAULT_INTERVAL) -> list[dict]:
    """Distinct players per weekday (1-7) and hour (00-23)."""
    interval = interval or DEFAULT_INTERVAL
    sql = f"""
        SELECT to_char(s.hop_on, 'ID') as weekday, to_char(h.time, 'HH24') AS hour,
               COUNT(DISTINCT s.player_ucid) AS players
        FROM statistics s, missions m, generate_series(current_date, current_date + 1, INTERVAL '1 hour') h
        WHERE date_part('hour', h.time) BETWEEN date_part('hour', s.hop_on) AND date_part('hour', s.hop_off)
        AND s.hop_on > (NOW() at time zone 'UTC') - interval '{interval}'
        AND s.mission_id = m.id
        {_where(server_name)}
        GROUP BY 1, 2
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def user_retention(pool: Any, *, server_name: str | None = None,
                         interval: str | None = DEFAULT_INTERVAL) -> list[dict]:
    """Per day: players whose first visit was that day, and how many of them came back."""
    interval = interval or DEFAULT_INTERVAL
    sql = f"""
        WITH date_series AS (
            SELECT
                generate_series(DATE(NOW()) - INTERVAL '{interval}', DATE(NOW()), INTERVAL '1 day') AS date
        ),
        first_visit AS (
            SELECT
                player_ucid,
                MIN(DATE(hop_on)) AS first_date
            FROM statistics s
            JOIN missions m ON s.mission_id = m.id {_where(server_name)}
            GROUP BY player_ucid
            HAVING MIN(DATE(hop_on)) >= DATE(NOW()) - INTERVAL '{interval}'
        ),
        activity AS (
            SELECT
                fv.player_ucid,
                fv.first_date,
                DATE(hop_on) AS activity_date
            FROM first_visit fv
            JOIN statistics s ON fv.player_ucid = s.player_ucid
            JOIN missions m ON s.mission_id = m.id {_where(server_name)}
        )
        SELECT
            ds.date AS first_date,
            COALESCE(COUNT(DISTINCT fv.player_ucid), 0) AS new_users,
            COALESCE(COUNT(DISTINCT CASE WHEN a.activity_date > fv.first_date THEN a.player_ucid ELSE NULL END), 0) AS retained_users
        FROM date_series ds
        LEFT JOIN first_visit fv ON ds.date = fv.first_date
        LEFT JOIN activity a ON fv.player_ucid = a.player_ucid
        GROUP BY ds.date
        ORDER BY ds.date
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def user_engagement(pool: Any, *, server_name: str | None = None,
                          interval: str | None = DEFAULT_INTERVAL) -> list[dict]:
    """Per number of days present: new and returning players among the last *interval*."""
    interval = interval or DEFAULT_INTERVAL
    sql = f"""
        WITH first_seen AS (
            SELECT
                s.player_ucid,
                MIN(s.hop_on) AS first_seen
            FROM
                statistics s
            GROUP BY s.player_ucid
        ),
        player_days AS (
            SELECT
                s.player_ucid,
                COUNT(DISTINCT DATE(s.hop_on)) AS days_present,
                CASE WHEN f.first_seen >= (NOW() AT TIME ZONE 'UTC') - INTERVAL '{interval}' THEN true ELSE false END AS is_new
            FROM
                statistics s
                LEFT JOIN missions m ON s.mission_id = m.id
                LEFT JOIN first_seen f ON s.player_ucid = f.player_ucid
            WHERE s.hop_on >= NOW() - INTERVAL '{interval}'
            {_where(server_name)}
            GROUP BY s.player_ucid, f.first_seen
        ),
        retention AS (
            SELECT
                days_present,
                COUNT(player_ucid) FILTER (WHERE is_new) AS new_player_count,
                COUNT(player_ucid) FILTER (WHERE NOT is_new) AS returning_player_count
            FROM player_days
            GROUP BY days_present
        ),
        date_series AS (
            SELECT generate_series(1, (
                SELECT DATE_PART('day', (NOW() AT TIME ZONE 'UTC') - ((NOW() AT TIME ZONE 'UTC') - INTERVAL '{interval}'))::INTEGER)
            ) AS date
        )
        SELECT
            ds.date AS days_present,
            COALESCE(r.new_player_count, 0) AS new_player_count,
            COALESCE(r.returning_player_count, 0) AS returning_player_count
        FROM
            date_series ds
            LEFT JOIN retention r ON r.days_present = ds.date
        ORDER BY ds.date
     """
    return await _rows(pool, sql, {"server_name": server_name})


async def users_per_mission_time(pool: Any, *, server_name: str | None = None,
                                 interval: str | None = DEFAULT_INTERVAL) -> list[dict]:
    """Average users per hour of mission time."""
    interval = interval or DEFAULT_INTERVAL
    where_clause = "AND server_name = %(server_name)s" if server_name else ""
    inner_sql = f"""
        SELECT mission_time / 3600 AS time, users
        FROM serverstats
        WHERE mission_time IS NOT NULL
        AND time > (now() at time zone 'UTC') - interval '{interval}'
        {where_clause}
    """
    sql = f"""
        SELECT time::INTEGER, avg(users)::DECIMAL AS users FROM (
            {inner_sql}
        ) s
        GROUP BY 1 ORDER BY 1
    """
    return await _rows(pool, sql, {"server_name": server_name})


async def server_load(pool: Any, *, node: str | None = None, period_clause: str = "1 = 1",
                      server_name: str | None = None) -> list[dict]:
    """Per minute: users, CPU, memory, disk, network, FPS and ping — aggregated for a node."""
    inner_sql = f"""
        SELECT date_trunc('minute', time) AS time, AVG(users) AS users, AVG(cpu) AS cpu,
               AVG(CASE WHEN mem_total-mem_ram < 0 THEN 0 ELSE mem_total-mem_ram END)/(1024*1024) AS mem_paged,
               AVG(mem_ram)/(1024*1024) AS mem_ram,
               SUM(read_bytes)/1024 AS read,
               SUM(write_bytes)/1024 AS write,
               ROUND(AVG(bytes_sent)) AS sent,
               ROUND(AVG(bytes_recv)) AS recv,
               ROUND(AVG(fps), 2) AS fps,
               ROUND(AVG(ping), 2) AS ping
        FROM serverstats
        WHERE {period_clause}
        and time < (now() at time zone 'UTC')
    """
    if node:
        inner_sql += "AND node = %(node)s"
    if server_name:
        sql = f"""
            {inner_sql}
            AND server_name = %(server_name)s
            GROUP BY 1 ORDER BY 1
        """
    else:
        sql = f"""
            SELECT time, SUM(users) AS users, SUM(cpu) AS cpu, SUM(mem_paged) AS mem_paged, SUM(mem_ram) AS mem_ram,
                         SUM(read) AS read, SUM(write) AS write, ROUND(AVG(sent)) AS sent, ROUND(AVG(recv)) AS recv,
                         ROUND(AVG(fps), 2) AS fps, ROUND(AVG(ping), 2) AS ping
            FROM (
                {inner_sql}
                GROUP BY 1, server_name
            ) x
            GROUP BY 1 ORDER BY 1
        """
    return await _rows(pool, sql, {"node": node, "server_name": server_name})


# ── Node statistics ────────────────────────────────────────────────────────


async def node_stats(pool: Any, *, node: str,
                     period: str = NODE_STATS_DEFAULT_PERIOD) -> list[dict]:
    """Per minute: pool availability, queued requests, wait time and both queues, for one node."""
    sql = """
        SELECT date_trunc('minute', time) AS time, pool_available, requests_queued, requests_wait_ms,
               dcs_queue, asyncio_queue
        FROM nodestats
        WHERE time > ((NOW() AT TIME ZONE 'UTC') - ('1 ' || %(period)s)::interval)
        AND node = %(node)s
        ORDER BY 1
    """
    return await _rows(pool, sql, {"period": node_period(period), "node": node})


# ── Load series ────────────────────────────────────────────────────────────

#: The load series' fixed-width windows: scale name → (bucket width in seconds, bucket count).
#: The right edge is NOW; the left edge is ``(points - 1)`` buckets before it.
LOAD_SCALES: dict[str, tuple[int, int]] = {
    "1h": (60, 60),
    "6h": (300, 72),
    "24h": (300, 288),
    "7d": (3600, 168),
    "30d": (3600, 720),
}

#: The window when the caller names no published scale.
LOAD_DEFAULT_SCALE = "24h"

#: The sparkline length on the node table: one value per minute for the last hour.
LOAD_SPARK_POINTS = 60


def load_scale(scale: Any) -> tuple[str, int, int]:
    """The published scale *scale* resolves to, its bucket width in seconds and its bucket count.

    An unpublished name is :data:`LOAD_DEFAULT_SCALE`, never an error and never SQL: the three
    values come from :data:`LOAD_SCALES`, so a caller string only ever selects a row of that table.
    """
    wanted = str(scale or "").strip().lower()
    if wanted not in LOAD_SCALES:
        wanted = LOAD_DEFAULT_SCALE
    seconds, points = LOAD_SCALES[wanted]
    return wanted, seconds, points


def _utc_floor(seconds: int, *, at: float | None = None) -> datetime:
    """The last bucket edge at or before *at* (default: now), in UTC, as a naive datetime."""
    moment = datetime.now(timezone.utc) if at is None else datetime.fromtimestamp(at, timezone.utc)
    moment = moment.replace(microsecond=0, tzinfo=None)
    offset = int(moment.replace(tzinfo=timezone.utc).timestamp()) % seconds
    return moment - timedelta(seconds=offset)


def _load_scope(node: str | None, server_name: str | None) -> str:
    """The row restriction the load reads carry, as bound parameters, or nothing for the cluster."""
    clause = ""
    if node:
        clause += "\n        AND node = %(node)s"
    if server_name:
        clause += "\n        AND server_name = %(server_name)s"
    return clause


def _bucket(value: str) -> str:
    """The SQL expression flooring a ``serverstats.time`` column onto :data:`LOAD_SCALES` edges."""
    return (f"(to_timestamp(floor(extract(epoch FROM {value}) / %(bucket_seconds)s)"
            f" * %(bucket_seconds)s) AT TIME ZONE 'utc')")


async def load_series(pool: Any, *, node: str | None = None, server_name: str | None = None,
                      scale: Any = LOAD_DEFAULT_SCALE) -> dict:
    """The load series of a window: one row per bucket, the node table's aggregate and its sparks.

    The window is fixed width with its right edge on the current bucket; ``scale`` only selects a
    row of :data:`LOAD_SCALES`, so the bucket width and count never come from a caller string.

    Scope is *node* (one node), *server_name* (one server) or neither (the whole installation).
    Players are additive across servers: a bucket's figure is the mean of the per-minute sums.
    Every other column is the mean of its rows. A bucket with no rows carries ``None`` figures —
    never ``0``, which would read as an idle server rather than a missing write.

    ``nodes`` is the node roster of the ``nodestats`` table, each with its mean figures over the
    same window and ``spark``: its players per minute over the last
    :data:`LOAD_SPARK_POINTS` minutes, ``None`` where nothing was written.
    """
    resolved, seconds, points = load_scale(scale)
    end = _utc_floor(seconds)
    start = end - timedelta(seconds=seconds * (points - 1))
    params = {"node": (node or "").strip() or None,
              "server_name": (server_name or "").strip() or None,
              "bucket_seconds": seconds, "points": points,
              "start": start, "end": end}
    scope = _load_scope(params["node"], params["server_name"])
    node_filter = "\n        AND node = %(node)s" if params["node"] else ""

    series_sql = f"""
        WITH grid AS (
            SELECT generate_series(%(start)s::timestamp, %(end)s::timestamp,
                                   make_interval(secs => %(bucket_seconds)s)) AS bucket
        ),
        per_minute AS (
            SELECT {_bucket('time')} AS bucket,
                   time,
                   SUM(users) AS users,
                   AVG(cpu) AS cpu,
                   AVG(mem_ram) / 1048576 AS mem_ram,
                   AVG(GREATEST(mem_total - mem_ram, 0)) / 1048576 AS mem_paged,
                   AVG(read_bytes) / 1024 AS read,
                   AVG(write_bytes) / 1024 AS write,
                   AVG(bytes_sent) AS sent,
                   AVG(bytes_recv) AS recv,
                   AVG(fps) AS fps,
                   AVG(ping) AS ping
            FROM serverstats
            WHERE time >= %(start)s
            AND time < %(end)s + make_interval(secs => %(bucket_seconds)s)
            {scope}
            GROUP BY 1, 2
        )
        SELECT g.bucket AS time,
               AVG(pm.users) AS players,
               AVG(pm.cpu) AS cpu,
               AVG(pm.mem_ram) AS mem_ram,
               AVG(pm.mem_paged) AS mem_paged,
               AVG(pm.read) AS read,
               AVG(pm.write) AS write,
               AVG(pm.sent) AS sent,
               AVG(pm.recv) AS recv,
               AVG(pm.fps) AS fps,
               AVG(pm.ping) AS ping
        FROM grid g
        LEFT JOIN per_minute pm ON pm.bucket = g.bucket
        GROUP BY g.bucket
        ORDER BY g.bucket
    """
    nodes_sql = f"""
        WITH roster AS (
            SELECT DISTINCT node FROM nodestats
            {node_filter}
        ),
        win AS (
            SELECT node,
                   ROUND(AVG(pool_available)) AS pool_available,
                   ROUND(AVG(requests_queued)) AS requests_queued,
                   ROUND(AVG(requests_wait_ms)) AS requests_wait_ms,
                   ROUND(AVG(dcs_queue)) AS dcs_queue,
                   ROUND(AVG(asyncio_queue)) AS asyncio_queue
            FROM nodestats
            WHERE time >= %(start)s
            AND time < %(end)s + make_interval(secs => %(bucket_seconds)s)
            {node_filter}
            GROUP BY node
        )
        SELECT r.node AS node, w.pool_available, w.requests_queued, w.requests_wait_ms,
               w.dcs_queue, w.asyncio_queue
        FROM roster r
        LEFT JOIN win w ON w.node = r.node
        ORDER BY r.node
    """
    now_bucket = _bucket("(now() AT TIME ZONE 'utc')")
    spark_sql = f"""
        WITH roster AS (
            SELECT DISTINCT node FROM nodestats
            {node_filter}
        ),
        last_minute AS (
            SELECT {now_bucket} AS m
        ),
        minutes AS (
            SELECT generate_series(lm.m - make_interval(secs => %(spark_span)s), lm.m,
                                   make_interval(secs => 60)) AS m
            FROM last_minute lm
        ),
        per_minute AS (
            SELECT {_bucket('time')} AS m, node, SUM(users) AS users
            FROM serverstats
            WHERE time >= (SELECT m FROM last_minute) - make_interval(secs => %(spark_span)s)
            AND time < (now() AT TIME ZONE 'utc')
            {scope}
            GROUP BY 1, 2
        )
        SELECT mi.m AS time, r.node AS node, pm.users AS users
        FROM minutes mi
        CROSS JOIN roster r
        LEFT JOIN per_minute pm ON pm.m = mi.m AND pm.node = r.node
        ORDER BY r.node, mi.m
    """
    spark_seconds = 60
    spark_params = dict(params, bucket_seconds=spark_seconds,
                        spark_span=spark_seconds * (LOAD_SPARK_POINTS - 1))

    series = await _rows(pool, series_sql, params)
    nodes = await _rows(pool, nodes_sql, params)
    sparks = await _rows(pool, spark_sql, spark_params)

    spark_by_node: dict[Any, list] = {}
    for row in sparks:
        spark_by_node.setdefault(row.get("node"), []).append(row.get("users"))

    return {
        "scale": resolved,
        "window": {"start": start, "end": end,
                   "bucket_seconds": seconds, "points": points},
        "series": [{"time": row.get("time"),
                    "players": row.get("players"),
                    "cpu": row.get("cpu"),
                    "mem_ram": row.get("mem_ram"),
                    "mem_paged": row.get("mem_paged"),
                    "read": row.get("read"),
                    "write": row.get("write"),
                    "sent": row.get("sent"),
                    "recv": row.get("recv"),
                    "fps": row.get("fps"),
                    "ping": row.get("ping")} for row in series],
        "nodes": [{"node": row.get("node"),
                   "pool_available": row.get("pool_available"),
                   "requests_queued": row.get("requests_queued"),
                   "requests_wait_ms": row.get("requests_wait_ms"),
                   "dcs_queue": row.get("dcs_queue"),
                   "asyncio_queue": row.get("asyncio_queue"),
                   "spark": spark_by_node.get(row.get("node"), [])} for row in nodes],
    }
