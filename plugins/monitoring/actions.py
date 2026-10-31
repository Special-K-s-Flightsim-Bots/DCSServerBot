"""The statistics READS as actions — the console's door onto the numbers the reports draw.

Both actions are the ``TRANSPORT_WEB``-gated siblings of ``plugins/mission/actions.py``'s
``get_player_statistics``: they run the statements of :mod:`plugins.monitoring.stats_read` against
the bot's pool and hand back plain rows. The Discord report elements call the SAME functions, so the
console and ``/serverstats`` / ``/server statistics`` cannot disagree about a figure.

Every figure is a NUMBER and every label is the RAW value the database holds (``server_name``,
``mission_theatre``, ``slot``) — formatting is the reader's business, never this layer's.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from core.action_results import ActionResult
from core.actions import TRANSPORT_WEB, action

from . import stats_read

log = logging.getLogger(__name__)

#: The refusal a non-console caller meets: these reads are installation-wide, and a transport that
#: carries no scope (REST, MCP) would read as if it held one.
STATS_CONSOLE_ONLY = "The statistics are only available in the web console."

#: The refusal when this process has no connection pool to read from.
STATS_UNAVAILABLE = "The statistics are not available in this installation."


def _pool(ctx: Any) -> Any:
    """The pool these reads run on: this process node's ``apool``, or ``None``."""
    return getattr(getattr(ctx, "node", None), "apool", None)


def _number(value: Any) -> int | float:
    """A database figure as a JSON number — ``Decimal`` never reaches a caller."""
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)):
        return value
    return int(value)


def _stamp(value: Any) -> Any:
    """A date/time column as an ISO string (JSON-safe), every other value unchanged."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _iso_week(value: Any) -> str | None:
    """A date column as its ISO week label (``2026-W42``), or ``None`` when there is no date."""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return None
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return None
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def _name_filters(ctx: Any) -> dict[str, str]:
    """The node's configured name cleanup patterns — ``the bot.filter`` the report interpolates.

    Read off the node's own config, which is where ``ServiceBus.filter`` builds them from. Absent
    keys keep the bus defaults, which match nothing.
    """
    config = getattr(getattr(ctx, "node", None), "config", None) or {}
    configured = config.get("filter") if isinstance(config, dict) else None
    configured = configured if isinstance(configured, dict) else {}
    return {"server_name": configured.get("server_name", "!.*"),
            "mission_name": configured.get("mission_name", "!.*")}


@action
async def get_server_statistics(ctx: Any, period: str = "month",
                                server: str | None = None,
                                window_days: Any = None) -> ActionResult:
    """Playtime, players, theatres, missions, modules and the windowed series for one period.

    *period* accepts exactly the names :meth:`plugins.userstats.filter.PeriodFilter.list` publishes
    (``all``, ``today``, ``yesterday``, ``day``, ``week``, ``month``, ``quarter``, ``halfyear``,
    ``year``); anything else is ``all`` — the value the report's own period filter defaults to. The
    clause and the name come from :func:`plugins.monitoring.stats_read.period_clause`, the bot's one
    definition of a period, so the console has no second vocabulary.

    *server* restricts every read to ONE server; empty reads the whole installation.

    *window_days* sizes ``retention``, ``day_time`` and ``frequency`` only, in the closed range
    ``1..365``; anything else — out of range or not a whole number — falls back to the period's own
    day count, never to an error. It reaches the statements as a bound integer.

    Returns ``{period, server, window_days, unique_users_grain, usage[], theatres[], missions[],
    modules[], unique_users[], retention[], day_time[], frequency[], cluster}``. A ``usage`` row
    carries
    ``server_name``/``playtime``/``players``/``members``/``sessions``/``missions``/``theatre`` (its
    own most-flown theatre, not the scope's ``theatres``); ``unique_users_grain`` is the bucket its
    rows are counted at (``day``, or ``week`` for a range longer than
    :data:`plugins.monitoring.stats_read.UNIQUE_USERS_WEEKLY_AFTER_DAYS`);
    ``retention`` carries ``week``/
    ``new_users``/``retained_users``; ``day_time`` carries ``weekday``/``hour``/``players``;
    ``frequency`` carries ``week``/``sessions``/``players``; ``cluster`` carries ``playtime``/
    ``players_distinct``/``new_players``/``returning_rate``/``avg_session`` where
    ``players_distinct`` is a DISTINCT count over the scope and NOT the sum of the usage rows.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=STATS_CONSOLE_ONLY)
    pool = _pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=STATS_UNAVAILABLE)

    node = getattr(ctx, "node", None)
    clause, resolved = stats_read.period_clause(period, node)
    days = stats_read.window_days(window_days, resolved)
    grain = stats_read.unique_users_grain(resolved)
    server_name = (server or "").strip() or None
    filters = _name_filters(ctx)
    try:
        usage = await stats_read.server_usage(
            pool, server_name=server_name, period_clause=clause,
            server_filter=filters["server_name"])
        top_theatre = await stats_read.server_top_theatres(
            pool, server_name=server_name, period_clause=clause,
            server_filter=filters["server_name"])
        theatres = await stats_read.top_theatres(
            pool, server_name=server_name, period_clause=clause)
        missions = await stats_read.top_missions(
            pool, server_name=server_name, period_clause=clause,
            limit=stats_read.SERVER_STATS_MISSION_LIMIT, mission_filter=filters["mission_name"])
        modules = await stats_read.top_modules(
            pool, server_name=server_name, period_clause=clause,
            limit=stats_read.SERVER_STATS_MODULE_LIMIT)
        unique = await stats_read.unique_users(
            pool, server_name=server_name, period_clause=clause, grain=grain)
        retention = await stats_read.user_retention(
            pool, server_name=server_name, interval=f"{days} days")
        day_time = await stats_read.users_per_day_time(
            pool, server_name=server_name, interval=f"{days} days")
        frequency = await stats_read.weekly_frequency(
            pool, server_name=server_name, window_days=days)
        cluster = await stats_read.server_cluster(
            pool, server_name=server_name, period_clause=clause)
    except Exception as ex:
        log.exception("get_server_statistics failed")
        return ActionResult(success=False, message=f"The statistics could not be read: {ex}")

    theatres_by_server = {row.get("server_name"): row.get("mission_theatre") for row in top_theatre}
    weeks: dict[str, dict] = {}
    for row in retention:
        week = _iso_week(row.get("first_date"))
        if week is None:
            continue
        entry = weeks.setdefault(week, {"week": week, "new_users": 0, "retained_users": 0})
        entry["new_users"] += _number(row.get("new_users"))
        entry["retained_users"] += _number(row.get("retained_users"))
    cluster_row = cluster[0] if cluster else {}

    return ActionResult(success=True, data={
        "period": resolved,
        "server": server_name,
        "window_days": days,
        "unique_users_grain": grain,
        "usage": [{"server_name": row.get("server_name"),
                   "playtime": _number(row.get("playtime")),
                   "players": _number(row.get("players")),
                   "members": _number(row.get("members")),
                   "sessions": _number(row.get("sessions")),
                   "missions": _number(row.get("missions")),
                   "new_players": _number(row.get("new_players")),
                   "theatre": theatres_by_server.get(row.get("server_name"))}
                  for row in usage],
        "theatres": [{"mission_theatre": row.get("mission_theatre"),
                      "playtime": _number(row.get("playtime"))} for row in theatres],
        "missions": [{"mission_name": row.get("mission_name"),
                      "playtime": _number(row.get("playtime"))} for row in missions],
        "modules": [{"slot": row.get("slot"),
                     "num_usage": _number(row.get("num_usage")),
                     "playtime": _number(row.get("playtime")),
                     "players": _number(row.get("players"))} for row in modules],
        "unique_users": [{"date": (_iso_week(row.get("date")) if grain == "week"
                                   else _stamp(row.get("date"))) or _stamp(row.get("date")),
                          "total_players": _number(row.get("total_players")),
                          "new_players": _number(row.get("new_players"))} for row in unique],
        "retention": [weeks[key] for key in sorted(weeks)],
        "day_time": [{"weekday": _number(row.get("weekday")),
                      "hour": _number(row.get("hour")),
                      "players": _number(row.get("players"))} for row in day_time],
        "frequency": [{"week": row.get("week"),
                       "sessions": _number(row.get("sessions")),
                       "players": _number(row.get("players"))} for row in frequency],
        "cluster": {"playtime": _number(cluster_row.get("playtime")),
                    "players_distinct": _number(cluster_row.get("players_distinct")),
                    "new_players": _number(cluster_row.get("new_players")),
                    "returning_rate": _number(cluster_row.get("returning_rate")),
                    "avg_session": _number(cluster_row.get("avg_session"))},
    })


@action
async def get_node_statistics(ctx: Any, node: str, period: str = "hour") -> ActionResult:
    """The per-minute queue and pool series of ONE node.

    *node* is the node's NAME, as ``nodestats.node`` stores it. *period* accepts exactly the names
    ``/node statistics`` offers (``hour``, ``day``, ``week``, ``month``, case-insensitively);
    anything else is ``hour``, that command's own default. Both the vocabulary and the fallback are
    :func:`plugins.monitoring.stats_read.node_period`.

    Returns ``{node, period, series[]}`` where every series row carries ``time`` and the five
    figures ``pool_available``, ``requests_queued``, ``requests_wait_ms``, ``dcs_queue`` and
    ``asyncio_queue``.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=STATS_CONSOLE_ONLY)
    wanted = (node or "").strip()
    if not wanted:
        return ActionResult(success=False, message="A node name is required.")
    pool = _pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=STATS_UNAVAILABLE)

    resolved = stats_read.node_period(period)
    try:
        rows = await stats_read.node_stats(pool, node=wanted, period=resolved)
    except Exception as ex:
        log.exception("get_node_statistics failed")
        return ActionResult(success=False, message=f"The node statistics could not be read: {ex}")

    return ActionResult(success=True, data={
        "node": wanted,
        "period": resolved,
        "series": [{"time": _stamp(row.get("time")),
                    "pool_available": _number(row.get("pool_available")),
                    "requests_queued": _number(row.get("requests_queued")),
                    "requests_wait_ms": _number(row.get("requests_wait_ms")),
                    "dcs_queue": _number(row.get("dcs_queue")),
                    "asyncio_queue": _number(row.get("asyncio_queue"))} for row in rows],
    })


def _figure(value: Any) -> int | float | None:
    """A load figure as a JSON number, or ``None`` — a missing bucket is never reported as ``0``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return float(value)


#: The gauges every load series row carries, in payload order.
_LOAD_GAUGES = ("players", "cpu", "mem_ram", "mem_paged", "read", "write", "sent", "recv",
                "fps", "ping")


@action
async def get_load_statistics(ctx: Any, scale: str = "24h", node: str | None = None,
                              server: str | None = None) -> ActionResult:
    """The load series of a fixed-width window, plus the node table's aggregate and sparks.

    *scale* is one of ``1h``, ``6h``, ``24h``, ``7d``, ``30d``; anything else resolves to ``24h``
    and the payload reports the RESOLVED name. *node* restricts the series to one node, *server*
    to one server, neither reads the whole installation — the caller's scope is the page route's
    business, exactly as for :func:`get_server_statistics`.

    Returns ``{scale, window: {start, end, bucket_seconds, points}, series[], nodes[]}``. Every
    figure is a JSON number or ``None`` (a bucket nothing was written in), every time an ISO
    string. No derived figure: the reader takes the last bucket it needs.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=STATS_CONSOLE_ONLY)
    pool = _pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=STATS_UNAVAILABLE)

    try:
        data = await stats_read.load_series(pool, node=node, server_name=server, scale=scale)
    except Exception as ex:
        log.exception("get_load_statistics failed")
        return ActionResult(success=False, message=f"The load statistics could not be read: {ex}")

    return ActionResult(success=True, data={
        "scale": data["scale"],
        "window": {"start": _stamp(data["window"]["start"]),
                   "end": _stamp(data["window"]["end"]),
                   "bucket_seconds": data["window"]["bucket_seconds"],
                   "points": data["window"]["points"]},
        "series": [dict({"time": _stamp(row.get("time"))},
                        **{gauge: _figure(row.get(gauge)) for gauge in _LOAD_GAUGES})
                   for row in data["series"]],
        "nodes": [{"node": row.get("node"),
                   "pool_available": _figure(row.get("pool_available")),
                   "requests_queued": _figure(row.get("requests_queued")),
                   "requests_wait_ms": _figure(row.get("requests_wait_ms")),
                   "dcs_queue": _figure(row.get("dcs_queue")),
                   "asyncio_queue": _figure(row.get("asyncio_queue")),
                   "spark": [_figure(value) for value in row.get("spark") or []]}
                  for row in data["nodes"]],
    })
