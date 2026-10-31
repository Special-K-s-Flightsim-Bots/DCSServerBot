"""The Statistics page: the strategic, PERIOD-BASED view at ``/statistics``.

WHAT IT ANSWERS. Period questions about the community — is it growing, do new players stay,
when do they fly, which missions, theatres and modules do they pick. The period is the bot's own
``PeriodFilter`` vocabulary, from one day to the whole history; every figure comes from ONE
read, the ``get_server_statistics`` action (``plugins/monitoring/actions.py``), which is also what
the Discord ``/serverstats`` report draws from, so the page and the report cannot disagree about a
number. Nothing is written here: the route is a GET and the page renders no control that mutates.

WHAT IT DELIBERATELY DOES NOT CARRY. Live figures (players online, a mission's runtime) and node
facts (queue depth, pool health) belong to the dashboard and its Load tab. This page answers
questions about a PERIOD, so a figure that changes per second has no place on it.

READ-ONLY AND INSTALLATION-WIDE, LIKE THE REPORT IT MIRRORS. ``/serverstats`` is Admin-only and
reads the whole installation, so the page's capability is Admin-only and the read applies no scope
of its own. A non-Admin door would need the scoping inside the action, never a filter bolted on
here: a page-level filter over an unscoped payload would render installation-wide figures to a
manager's screen, and a filter that hid rows would still have counted them.

THE SERVER PAGE'S TAB IS THAT OTHER DOOR, and it owes its scope to the page it lives on. The tab
renders this module's own body (:func:`statistics_body`) for ONE server, and the name it passes
comes from the server page, which already resolved it through the scoped source — so the tab applies
no scope itself and cannot be handed a server the caller may not see. It declares no capability
either: it is offered exactly to the viewers who may open that page, and an unnamed request for it
would be the installation-wide read.

DISTINCT IS NOT SUMMABLE. The cluster's unique-player figure is a ``COUNT(DISTINCT …)`` over the
whole scope, while the per-server column counts each player once PER SERVER. The two therefore
disagree on purpose; the page says so in one sentence beside the table, and the KPI's qualifier
carries the word "distinct".

DERIVED FIGURES LIVE HERE, ONCE. Average session, returning rate, sessions per active player per
week and the per-week retention share are each computed by ONE helper below, from the payload's own
numbers — never a second query, and never a second reading of the same series in the template.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.actions import read_action

from .. import i18n, permissions, readmodels
from ..registry import NavItem
from . import dashboard as dashboard_page

__all__ = [
    "STATISTICS_PATH", "STATISTICS_CAPABILITY", "STATISTICS_ROLES", "STATISTICS_TEMPLATE",
    "STATISTICS_STYLE", "TAB_KEY", "NAV_LABEL", "PAGE_TITLE", "PAGE_LEAD", "PERIOD_PARAM",
    "SERVER_PARAM",
    "CHIPS", "DEFAULT_PERIOD", "RETENTION_DAYS", "DISTINCT_NOTE",
    "period_from", "scope_from", "weeks_of", "scope_choices", "kpi_tiles", "daily_rows",
    "retention_rows", "frequency_rows", "day_time_rows", "share_rows", "module_rows",
    "server_rows", "has_figures", "statistics_body", "tab_context", "statistics_context",
    "capabilities", "nav_items", "add_routes",
]

log = logging.getLogger(__name__)

STATISTICS_PATH = "/statistics"
STATISTICS_CAPABILITY = "statistics.view"
#: Admin-only, the same role set ``/serverstats`` requires: the read it renders is
#: installation-wide and carries no scope of its own.
STATISTICS_ROLES: tuple[str, ...] = ("Admin",)
STATISTICS_TEMPLATE = "statistics.html"
STATISTICS_STYLE = "statistics.css"
#: The server page's tab key. Its scope is the server page's own, so this module declares no
#: capability for it: the tab is offered exactly to the viewers who may open that page.
TAB_KEY = "statistics"

NAV_LABEL = i18n._("Statistics")
PAGE_TITLE = i18n._("Statistics")
PAGE_LEAD = i18n._("How the community develops: playtime, new and returning players, the hours they "
                   "fly and the missions they pick. Read-only.")

#: The two query parameters the page reads. Both are GET keys, so every control is a plain link and
#: the page works with JavaScript off.
PERIOD_PARAM = "period"
SERVER_PARAM = "server"

#: The period chips, in render order: the period name the bot's own ``PeriodFilter`` uses and the
#: word the operator sees for it. The names are the action's whole vocabulary, so a chip can never
#: invent a period; an unknown value falls back to :data:`DEFAULT_PERIOD`. The words are durations
#: where a duration is what the period means (``day`` is the last 24 hours, not today's calendar
#: day), and the filter's own calendar names where it does not.
CHIPS: tuple[tuple[str, str], ...] = (
    ("all", i18n._("All time")),
    ("today", i18n._("Today")),
    ("yesterday", i18n._("Yesterday")),
    ("day", i18n._("24 hours")),
    ("week", i18n._("7 days")),
    ("month", i18n._("30 days")),
    ("quarter", i18n._("90 days")),
    ("halfyear", i18n._("6 months")),
    ("year", i18n._("12 months")),
)
DEFAULT_PERIOD = "month"

#: The window the retention, frequency and day-time cards read, in days: twelve weeks. It is passed
#: to the action as ``window_days``, so those three cards state the window the read actually used.
RETENTION_DAYS = 84

#: The one sentence beside the per-server table. The cluster figure is DISTINCT over the whole scope
#: and the table's column adds up to more, which reads as a defect unless the page says it.
DISTINCT_NOTE = i18n._("Distinct is not summable: the unique-player total counts each player once "
                       "across the whole scope, so the per-server column adds up to more than it.")

DISTINCT_TOTAL = i18n._("distinct players")
SUMMED_TOTAL = i18n._("server rows added up")

#: The KPI tiles and the sentences that qualify them.
KPI_PLAYTIME = i18n._("Playtime")
KPI_UNIQUE = i18n._("Unique players")
KPI_NEW = i18n._("New players")
KPI_RETURNING = i18n._("Returning rate")
NOTE_AVERAGE_SESSION = i18n._("average session {value}")
NOTE_DISTINCT_SCOPE = i18n._("distinct across all servers")
NOTE_DISTINCT_SERVER = i18n._("distinct on this server")
NOTE_NEW_IN_PERIOD = i18n._("first seen in this period")
NOTE_RETURNING = i18n._("of the period's players were not new")

#: The cards. The daily card's bucket follows the read's own grain (a long range is counted per
#: week), so its title and meta exist in both shapes and the page prints the one the payload states.
CARD_DAILY = i18n._("Players per day")
CARD_DAILY_META = i18n._("players a day · UTC · returning and new")
CARD_DAILY_WEEK = i18n._("Players per week")
CARD_DAILY_WEEK_META = i18n._("players a week · UTC · returning and new")
CARD_RETENTION = i18n._("Do new players stay?")
CARD_RETENTION_META = i18n._("share of a week's new players that came back · last {weeks} weeks")
CARD_FREQUENCY = i18n._("How often they fly")
CARD_FREQUENCY_META = i18n._("sessions per active player per week · last {weeks} weeks")
CARD_DAY_TIME = i18n._("When they fly")
CARD_DAY_TIME_META = i18n._("players per hour of day · UTC · last {weeks} weeks")
CARD_THEATRES = i18n._("Theatres")
CARD_THEATRES_META = i18n._("share of playtime")
CARD_MISSIONS = i18n._("Top missions")
CARD_MISSIONS_META = i18n._("by playtime")
CARD_MODULES = i18n._("Top modules")
CARD_MODULES_META = i18n._("by playtime")
CARD_SERVERS = i18n._("Servers")
CARD_SERVERS_META = i18n._("the period's totals, one row each")

#: The table's column words.
COL_SERVER = i18n._("Server")
COL_PLAYTIME = i18n._("Playtime")
COL_UNIQUE = i18n._("Unique players")
COL_MISSIONS = i18n._("Missions")
COL_THEATRE = i18n._("Most flown theatre")
COL_MODULE = i18n._("Module")
COL_PLAYERS = i18n._("Players")

LEGEND_RETURNING = i18n._("Returning")
LEGEND_NEW = i18n._("New")
#: One day's split, printed as the bar's accessible name: a two-segment bar carries no readable
#: figure of its own, so the numbers ride in the label a screen reader (and a tooltip) reads.
DAILY_ROW = i18n._("returning {returning} · new {new} · total {total}")
PEAK_HOUR = i18n._("peak {hour}:00 UTC")

SCOPE_LABEL = i18n._("Scope")
SCOPE_ALL = i18n._("All servers")
PERIOD_LABEL = i18n._("Period")

EMPTY_PAGE = i18n._("No statistics are available for this period yet: either the installation has "
                    "no sessions recorded in it, or the statistics read could not be reached.")
EMPTY_SERIES = i18n._("Nothing recorded in this window.")

#: The units the formatted figures carry. A unit is chrome and is translated with the sentence that
#: holds it, never spliced in as an English word.
#: The hour unit is CHROME, not part of a figure: a playtime is formatted as its number here and
#: the template appends this word beside it, so the unit follows the render's language while the
#: digits stay one number the page computed.
UNIT_HOURS = i18n._("h")
NO_VALUE = "—"


def declare() -> None:
    """Declare this page's capability. Idempotent for the same roles and scope rule."""
    permissions.declare_capability(STATISTICS_CAPABILITY, STATISTICS_ROLES)


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers."""
    declare()
    return {STATISTICS_PATH: STATISTICS_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (NavItem(label=NAV_LABEL, url=STATISTICS_PATH, capability=STATISTICS_CAPABILITY),)


def _sentence(msgid: str, **params: Any) -> dict:
    """A sentence record for the template's ``say`` global: the msgid plus its values.

    The record shape is the console's ONE guarded interpolation point, so a catalog line that drops
    a placeholder degrades to the English sentence instead of taking the page down.
    """
    return {"msgid": msgid, "params": params}


# ------------------------------------------------------------------------------- the numbers
#
# The first group reads a payload value as a NUMBER (a JSON number, a Decimal or a string out of a
# double), so a missing column renders as zero instead of raising. The second group is the page's
# DERIVATIONS, each computed once from the payload's own figures.

def _number(value: Any) -> float:
    """*value* as a float, or ``0.0`` when it is absent or not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    """*value* as an int, truncated, or ``0``."""
    return int(_number(value))


def _count(value: Any) -> str:
    """A count with thousands separators."""
    return f"{_integer(value):,}"


def _hours(value: Any) -> str:
    """A playtime in hours, thousands separated, WITHOUT its unit (see :data:`UNIT_HOURS`)."""
    return _count(value)


def _duration(hours: Any) -> str:
    """An average session in hours as ``h:mm`` — the shape a session length is read in."""
    total_minutes = round(_number(hours) * 60)
    return f"{total_minutes // 60}:{total_minutes % 60:02d}"


def _percent(fraction: Any) -> str:
    """A rate the payload stores as a fraction (``0.38``) as the percentage a reader expects."""
    return f"{round(_number(fraction) * 100)} %"


def _width(value: Any, ceiling: float) -> float:
    """*value* as a bar width against *ceiling*, one decimal, clamped into 0..100."""
    if ceiling <= 0:
        return 0.0
    return round(min(100.0, max(0.0, _number(value) / ceiling * 100.0)), 1)


def _rows(value: Any) -> list[Mapping]:
    """The mapping rows of a series, ignoring anything that is not a mapping."""
    return [row for row in (value or ()) if isinstance(row, Mapping)]


def _text(value: Any) -> str:
    """A payload label as print-ready text (the read models' own reader)."""
    return readmodels.text(value)


def period_from(value: Any) -> str:
    """The period a request names, or :data:`DEFAULT_PERIOD` for anything this page does not offer."""
    wanted = str(value or "").strip().lower()
    return wanted if wanted in {name for name, _label in CHIPS} else DEFAULT_PERIOD


def scope_from(value: Any) -> str | None:
    """The server a request scopes to, or ``None`` for the whole installation."""
    wanted = str(value or "").strip()
    return wanted or None


def weeks_of(window_days: int) -> int:
    """The whole weeks in *window_days* — the one reading of the three windowed cards' window."""
    return max(1, window_days // 7)


# ------------------------------------------------------------------------------- the views
#
# One builder per block of the page. Each returns plain data: the template prints it and computes
# nothing of its own.

def _query(*, period: str, server: str | None) -> dict[str, str]:
    """The page's query string as a mapping, omitting a scope the page is not under."""
    query = {PERIOD_PARAM: period}
    if server:
        query[SERVER_PARAM] = server
    return query


def _here(period: str, server: str | None) -> str:
    """The path plus query for one (period, scope) pair."""
    return f"{STATISTICS_PATH}?{urlencode(_query(period=period, server=server))}"


def _chips(period: str, href) -> list[dict]:
    """The period chips as links, built through *href* so each surface says where a chip goes."""
    return [{"label": label, "active": name == period, "url": href(name)}
            for name, label in CHIPS]


def scope_choices(request: Request, period: str, server: str | None) -> list[dict]:
    """The scope links: the whole installation first, then one per server in the caller's view.

    The NAMES come from the console's own source (``dashboard.request_source``, already scoped), so
    the list is the cluster this caller may see. Names only — no figure on this page is read from
    the live source.
    """
    choices = [{"label": SCOPE_ALL, "active": server is None, "url": _here(period, None)}]
    names: list[str] = []
    for entry in getattr(dashboard_page.request_source(request), "servers", ()) or ():
        name = _text(readmodels.safe(lambda entry=entry: getattr(entry, "name", None)))
        if name and name not in names:
            names.append(name)
    return choices + [{"label": name, "active": name == server, "url": _here(period, name)}
                      for name in sorted(names)]


def kpi_tiles(data: Mapping, server: str | None) -> list[dict]:
    """The four KPI figures, each carrying the sentence that qualifies it."""
    cluster = data.get("cluster")
    cluster = cluster if isinstance(cluster, Mapping) else {}
    qualifier = NOTE_DISTINCT_SERVER if server else NOTE_DISTINCT_SCOPE
    return [
        {"label": KPI_PLAYTIME, "value": _hours(cluster.get("playtime")), "unit": UNIT_HOURS,
         "note": _sentence(NOTE_AVERAGE_SESSION, value=_duration(cluster.get("avg_session")))},
        {"label": KPI_UNIQUE, "value": _count(cluster.get("players_distinct")), "unit": "",
         "note": _sentence(qualifier)},
        {"label": KPI_NEW, "value": _count(cluster.get("new_players")), "unit": "",
         "note": _sentence(NOTE_NEW_IN_PERIOD)},
        {"label": KPI_RETURNING, "value": _percent(cluster.get("returning_rate")), "unit": "",
         "note": _sentence(NOTE_RETURNING)},
    ]


def daily_rows(unique_users: Any) -> dict:
    """Players per day as stacked returning+new rows, each row's two shares of its own total.

    ``unique_users`` carries ``total_players`` and ``new_players`` per day; the returning share is
    the difference, which is what makes a row's two segments add up to the total it is scaled to.
    """
    entries = _rows(unique_users)
    ceiling = max((_integer(row.get("total_players")) for row in entries), default=0)
    rows = []
    for row in entries:
        total = _integer(row.get("total_players"))
        new = min(total, _integer(row.get("new_players")))
        returning = max(0, total - new)
        rows.append({"label": _text(row.get("date")), "total": _count(total), "new": _count(new),
                     "returning": _count(returning),
                     "title": _sentence(DAILY_ROW, returning=_count(returning), new=_count(new),
                                        total=_count(total)),
                     "new_width": _width(new, total), "returning_width": _width(returning, total)})
    return {"rows": rows, "ceiling": _count(ceiling)}


def retention_rows(retention: Any) -> list[dict]:
    """Per week: the share of that week's new players that came back later.

    A week with no new players has no share — it is skipped rather than drawn as ``0 %``, which
    would read as "nobody came back".
    """
    rows = []
    for row in _rows(retention):
        new = _integer(row.get("new_users"))
        if new <= 0:
            continue
        share = _number(row.get("retained_users")) / new * 100.0
        rows.append({"label": _text(row.get("week")), "share": f"{round(share)} %",
                     "width": _width(share, 100.0)})
    return rows


def frequency_rows(frequency: Any) -> list[dict]:
    """Per week: sessions divided by the players who flew in it — sessions per active player."""
    entries = [row for row in _rows(frequency) if _integer(row.get("players")) > 0]
    values = [(row, _number(row.get("sessions")) / _integer(row.get("players"))) for row in entries]
    ceiling = max((value for _row, value in values), default=0.0)
    return [{"label": _text(row.get("week")), "value": f"{value:.1f}", "unit": "",
             "width": _width(value, ceiling)} for row, value in values]


def day_time_rows(day_time: Any) -> dict:
    """The 24-hour profile: players per hour of day, summed over the weekdays in the window.

    ``day_time`` is one row per (weekday, hour) with a DISTINCT player count, so summing the weekdays
    is a count of player-hours — the card says so rather than presenting it as a headcount.
    """
    totals: dict[int, int] = {}
    for row in _rows(day_time):
        hour = _integer(row.get("hour"))
        if 0 <= hour <= 23:
            totals[hour] = totals.get(hour, 0) + _integer(row.get("players"))
    ceiling = max(totals.values(), default=0)
    rows = [{"label": f"{hour:02d}", "players": _count(totals.get(hour, 0)),
             "width": _width(totals.get(hour, 0), ceiling)} for hour in range(24)]
    peak = max(totals, key=lambda hour: totals[hour]) if totals else None
    return {"rows": rows, "peak": f"{peak:02d}" if peak is not None else None}


def share_rows(rows: Any, label_key: str) -> list[dict]:
    """A ``{label, playtime}`` series as bars scaled against its own largest value."""
    entries = _rows(rows)
    ceiling = max((_number(row.get("playtime")) for row in entries), default=0.0)
    return [{"label": _text(row.get(label_key)), "value": _hours(row.get("playtime")),
             "unit": UNIT_HOURS, "width": _width(row.get("playtime"), ceiling)}
            for row in entries]


def module_rows(modules: Any) -> list[dict]:
    """The flown modules: the slot's own name, its players and its playtime."""
    entries = _rows(modules)
    ceiling = max((_number(row.get("playtime")) for row in entries), default=0.0)
    return [{"label": _text(row.get("slot")), "players": _count(row.get("players")),
             "value": _hours(row.get("playtime")), "unit": UNIT_HOURS,
             "width": _width(row.get("playtime"), ceiling)} for row in entries]


def server_rows(usage: Any) -> dict:
    """The per-server comparison table plus the summed column the distinct-vs-sum sentence reads.

    ``players`` is DISTINCT per server here, while the KPI's figure is DISTINCT over the whole
    scope, so ``summed`` is deliberately larger — see :data:`DISTINCT_NOTE`.
    """
    entries = _rows(usage)
    ceiling = max((_number(row.get("playtime")) for row in entries), default=0.0)
    rows = [{"name": _text(row.get("server_name")), "playtime": _hours(row.get("playtime")),
             "unit": UNIT_HOURS, "width": _width(row.get("playtime"), ceiling),
             "players": _count(row.get("players")), "new_players": _count(row.get("new_players")),
             "missions": _count(row.get("missions")),
             "theatre": _text(row.get("theatre")) or NO_VALUE} for row in entries]
    summed = sum(_integer(row.get("players")) for row in entries)
    return {"rows": rows, "summed": _count(summed)}


def has_figures(data: Mapping | None) -> bool:
    """Whether the payload carries any figure this page can show."""
    if not data:
        return False
    for key in ("usage", "theatres", "missions", "modules", "unique_users", "retention",
                "day_time", "frequency"):
        if _rows(data.get(key)):
            return True
    cluster = data.get("cluster")
    return bool(isinstance(cluster, Mapping) and _number(cluster.get("playtime")))


# ------------------------------------------------------------------------------- the page

def _body_shell(period: str, href, grain: str = "day") -> dict:
    """The body's fixed half: the chips, every label and card word, and the empty series.

    Present on BOTH surfaces (the page and the server page's tab), so a block that has no data
    renders its own sentence instead of the template meeting an undefined name. *grain* is the
    bucket the unique-player series is counted at, and it is the only thing that changes a word
    here: a week-bucketed series is not a per-day card.
    """
    weeks = weeks_of(RETENTION_DAYS)
    daily_card = ({"title": CARD_DAILY_WEEK, "meta": _sentence(CARD_DAILY_WEEK_META)}
                  if grain == "week" else
                  {"title": CARD_DAILY, "meta": _sentence(CARD_DAILY_META)})
    return {
        "period": period,
        "chips": _chips(period, href),
        "labels": {
            "scope": SCOPE_LABEL, "period": PERIOD_LABEL, "distinct_note": DISTINCT_NOTE,
            "distinct_total": DISTINCT_TOTAL, "summed_total": SUMMED_TOTAL,
            "legend_returning": LEGEND_RETURNING, "legend_new": LEGEND_NEW,
            "col_server": COL_SERVER, "col_playtime": COL_PLAYTIME, "col_unique": COL_UNIQUE,
            "col_new": KPI_NEW, "col_missions": COL_MISSIONS, "col_theatre": COL_THEATRE,
            "col_module": COL_MODULE,
            "col_players": COL_PLAYERS, "empty_series": EMPTY_SERIES,
        },
        "cards": {
            "daily": daily_card,
            "retention": {"title": CARD_RETENTION,
                          "meta": _sentence(CARD_RETENTION_META, weeks=weeks)},
            "frequency": {"title": CARD_FREQUENCY,
                          "meta": _sentence(CARD_FREQUENCY_META, weeks=weeks)},
            "day_time": {"title": CARD_DAY_TIME,
                         "meta": _sentence(CARD_DAY_TIME_META, weeks=weeks)},
            "theatres": {"title": CARD_THEATRES, "meta": _sentence(CARD_THEATRES_META)},
            "missions": {"title": CARD_MISSIONS, "meta": _sentence(CARD_MISSIONS_META)},
            "modules": {"title": CARD_MODULES, "meta": _sentence(CARD_MODULES_META)},
            "servers": {"title": CARD_SERVERS, "meta": _sentence(CARD_SERVERS_META)},
        },
        "empty": EMPTY_PAGE,
        "kpis": [],
        "daily": {"rows": [], "ceiling": _count(0)},
        "retention": [],
        "frequency": [],
        "day_time": {"rows": [], "peak": None},
        "peak": None,
        "theatres": [],
        "missions": [],
        "modules": [],
        "servers": {"rows": [], "summed": _count(0), "distinct": _count(0)},
    }


async def _read(request: Request, *, server: str | None, period: str) -> Mapping | None:
    """The one read behind both surfaces, or ``None`` when it carried nothing to show."""
    from . import actions as actions_page  # local: ``pages.actions`` reaches this module's path

    result = await read_action("get_server_statistics", actions_page.context_for(request),
                               period=period, server=server, window_days=RETENTION_DAYS)
    data = getattr(result, "data", None)
    if not (getattr(result, "success", False) and isinstance(data, Mapping) and has_figures(data)):
        return None
    return data


async def statistics_body(request: Request, *, server: str | None, period: str, href) -> dict:
    """The figures both surfaces render, for *server* (``None``: the whole installation).

    The page and the server page's Statistics tab differ only in their chrome and in where their
    period chips point, so they take this ONE body: a card or a derivation added here reaches both,
    and neither can print a number the other does not.
    """
    data = await _read(request, server=server, period=period)
    grain = "week" if isinstance(data, Mapping) and data.get("unique_users_grain") == "week" else "day"
    context = _body_shell(period, href, grain)
    if data is None:
        return context
    cluster = data.get("cluster")
    cluster = cluster if isinstance(cluster, Mapping) else {}
    servers = server_rows(data.get("usage"))
    servers["distinct"] = _count(cluster.get("players_distinct"))
    day_time = day_time_rows(data.get("day_time"))
    context.update({
        "kpis": kpi_tiles(data, server),
        "daily": daily_rows(data.get("unique_users")),
        "retention": retention_rows(data.get("retention")),
        "frequency": frequency_rows(data.get("frequency")),
        "day_time": day_time,
        "peak": _sentence(PEAK_HOUR, hour=day_time["peak"]) if day_time["peak"] else None,
        "theatres": share_rows(data.get("theatres"), "mission_theatre"),
        "missions": share_rows(data.get("missions"), "mission_name"),
        "modules": module_rows(data.get("modules")),
        "servers": servers,
    })
    return context


async def tab_context(request: Request, server_name: str) -> dict:
    """The same body, narrowed to ONE server — the server page's Statistics tab.

    *server_name* is the name the server page already resolved through the scoped source, so this
    applies no scope of its own and cannot be handed a server the caller may not see.
    """
    period = period_from(request.query_params.get(PERIOD_PARAM))
    base = readmodels.server_url(server_name, tab=TAB_KEY)
    return await statistics_body(
        request, server=server_name, period=period,
        href=lambda name: f"{base}&{urlencode({PERIOD_PARAM: name})}")


async def statistics_context(request: Request) -> dict:
    """Everything the Statistics template reads, in one value."""
    period = period_from(request.query_params.get(PERIOD_PARAM))
    server = scope_from(request.query_params.get(SERVER_PARAM))
    registry = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    context = {
        "title": PAGE_TITLE,
        "page_title": PAGE_TITLE,
        "lead": PAGE_LEAD,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, PAGE_TITLE),
        "nav_groups": dashboard_page.nav_groups(registry, permissions.role_names_for(request),
                                                current=STATISTICS_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "style": STATISTICS_STYLE,
        "server": server,
        "scope": scope_choices(request, period, server),
    }
    context.update(await statistics_body(request, server=server, period=period,
                                        href=lambda name: _here(name, server)))
    return context


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Statistics route to the shell's own router and return it."""
    declare()

    @router.get(STATISTICS_PATH, response_class=HTMLResponse, name="page-statistics")
    async def _statistics_page(request: Request):
        # no-store: the figures move with every session the installation records, so a browser must
        # not serve an older render from its cache
        return HTMLResponse(i18n.render(request, STATISTICS_TEMPLATE,
                                        **await statistics_context(request)),
                            headers={"Cache-Control": "no-store"})

    return router
