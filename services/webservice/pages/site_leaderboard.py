"""The public site's leaderboard page — one metric, one server scope, one period, one page of rows.

EVERY FIGURE IS ``site_reads.leaderboard``'S ANSWER. The page runs that read exactly once per
request and renders what it returned: the ranked rows, the rank numbers, the page window and the
paging links. It composes and formats; it never queries and it never re-ranks.

THE TABLE IS ONE COLUMN PER FIGURE AND EVERY FIGURE COLUMN SORTS. The metric still picks the
board's title and its DEFAULT order (:data:`site_reads.DEFAULT_SORT_DIRECTION`), while ``sort`` and
``direction`` in the address re-order by any column the table prints. The column names travel
through :data:`site_reads.SORT_KEYS`; this page only builds the links, so a name the read does not
know is a link the read answers with the metric's own order.

THE RANKED COLUMN CARRIES THE METRIC'S FIGURE, never a fixed one. A metric the table already prints
as a column (:data:`COLUMN_METRICS` — playtime) is not printed twice: that column is marked
``ranked``. Every other metric (Ships, Ground targets, either ratio) adds one column of its own,
right after the pilot, headed by :data:`METRIC_MSGIDS`' msgid and carrying each row's ``value`` from
the read's answer — the figure the read ranked the row on. A ratio metric prints two decimals
(:func:`figure`), never a count. The order is not the identity: sorting by another column re-orders
the rows and leaves the ranked header and its figures as the metric's.

THE CONTROLS ARE THE READ'S PARAMETERS, EACH OF THEM IN THE URL. ``metric`` and ``period`` travel
straight to the read, ``server`` narrows the site context the read is bound to
(:func:`scoped_context`), and the current sort follows every control except ``metric``: a new metric
is a new default order, so its link drops the explicit sort while the server and period links keep
it. Paging keeps it too, so page two is the same ordering as page one.

THE PERIOD KEYS ARE THE CARD'S (``all``/``30``/``90``/``365``), THE WINDOWS ARE THE BOT'S.
``site_reads._period_clause`` accepts only ``plugins.userstats.filter.PeriodFilter``'s own names, so
each key resolves to the nearest interval that vocabulary carries (:data:`PERIODS`): 30 to
``month``, 90 to ``quarter``, 365 to ``year``. A day-exact window would be a new period vocabulary
inside the read, which this page may not add — the page states the windows it serves instead.

PAGING IS THE READ'S CAP, NOT A CALLER-SUPPLIED SIZE: the read answers
``site_reads.LEADERBOARD_PAGE_SIZE`` rows per offset, so a page is ``(page - 1) * size`` and the
next link exists when a full page came back.

PRESENTATION IS COMPOSED IN :func:`view`, so every figure can be asserted without parsing HTML. The
metric names are ``site_reads.METRIC_LABELS``' own English msgids, marked here for extraction and
translated where they render.

THE SITE OFFERS A SUBSET OF THE BOARDS, AND OPENS ON ONE OF ITS OWN. ``sites.<name>.metrics`` is
the list of metric ids the site offers (:func:`offered_metrics`); the metric control renders those
and nothing else, so it never advertises a board the site does not rank. A request for a board the
site does not offer — a stale ``?metric=`` link, a hand-edited address — opens the site's OWN
opening metric instead of falling back to a hard-coded ``playtime``: the character's first choice
among the offered metrics (:data:`CHARACTER_METRICS`), and the first offered metric when the
character's own choices are switched off. A site that disables playtime therefore never opens on an
empty board.

The page is ANONYMOUS: its route is registered ``public`` by :mod:`services.webservice.site_pages`,
and nothing here reads a session or a capability.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, site_reads, sites
from ..readmodels.views import PAGE_PARAM, normalise_page
from ..site_pages import NAV_PATHS, player_url, pool, render_site_page, resolve_site, site_url

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "METRIC_PARAM",
    "SERVER_PARAM",
    "PERIOD_PARAM",
    "SORT_PARAM",
    "DIRECTION_PARAM",
    "SERVER_ALL",
    "METRIC_MSGIDS",
    "CHARACTER_METRICS",
    "COLUMNS",
    "COLUMN_METRICS",
    "MEDALS",
    "PERIODS",
    "PERIOD_DAYS",
    "RATIO_METRICS",
    "count",
    "hours",
    "figure",
    "offered_metrics",
    "opening_metric",
    "metric_options",
    "period_options",
    "server_options",
    "scoped_context",
    "column_defs",
    "columns",
    "view",
    "add_route",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS``.
NAV_ID = "leaderboard"

TEMPLATE = "site_leaderboard.html"

#: the controls' query parameters. ``sort`` and ``direction`` are the table's own.
METRIC_PARAM = "metric"
SERVER_PARAM = "server"
PERIOD_PARAM = "period"
SORT_PARAM = "sort"
DIRECTION_PARAM = "direction"

#: ``?server=``'s value meaning "every server of this site".
SERVER_ALL = "all"

#: the medal class of each podium rank, by position. Rank four and below carry no medal.
MEDALS = {1: "m1", 2: "m2", 3: "m3"}

#: the metric names the page offers, in ``site_reads.METRIC_LABELS``' order — the ``/highscore``
#: set — each with the English msgid it renders. Marked here because ``METRIC_LABELS``' values are
#: plain strings the extractor cannot see; a test pins this table against that one.
METRIC_MSGIDS: dict[str, str] = {
    "playtime": i18n._("Longest Playtimes"),
    "air_targets": i18n._("Air Targets"),
    "ships": i18n._("Ships"),
    "air_defence": i18n._("Air Defence"),
    "ground_targets": i18n._("Ground Targets"),
    "kd_ratio": i18n._("KD-Ratio"),
    "pvp_kd_ratio": i18n._("PvP-KD-Ratio"),
}

#: the table's columns, in the mockup's order: ``(msgid, sort key, right-aligned, hidden on small
#: screens)``. ``sort key`` is the read's own whitelist entry, ``None`` for the columns that carry a
#: label rather than a figure (the rank and the favourite airframe).
COLUMNS: tuple[tuple[str, str | None, bool, bool], ...] = (
    ("#", None, False, False),
    (i18n._("Pilot"), "name", False, False),
    (i18n._("Playtime"), "playtime", True, False),
    (i18n._("Sorties"), "sorties", True, True),
    (i18n._("Takeoffs"), "takeoffs", True, True),
    (i18n._("Landings"), "landings", True, True),
    (i18n._("Kills"), "kills", True, False),
    (i18n._("PvP"), "pvp", True, True),
    (i18n._("Crashes"), "crashes", True, True),
    (i18n._("Ejections"), "ejections", True, True),
    (i18n._("Most flown"), None, False, True),
)

#: the period keys the control offers -> the ``PeriodFilter`` name the read resolves them to.
PERIODS: dict[str, str] = {"all": "all", "30": "month", "90": "quarter", "365": "year"}

#: the period keys -> the day count the control's label states; ``None`` is the whole history.
PERIOD_DAYS: dict[str, int | None] = {"all": None, "30": 30, "90": 90, "365": 365}

#: the metrics whose figure is a ratio (printed to two decimals) rather than a count or a duration.
RATIO_METRICS: frozenset[str] = frozenset({"kd_ratio", "pvp_kd_ratio"})

#: The metric each character opens its board on, in preference order. The first of these that the
#: site's ``metrics:`` offers is the board's opening metric; a character whose own choices are all
#: switched off falls through to the site's first offered metric, so the two settings compose
#: rather than compete.
CHARACTER_METRICS: dict[str, tuple[str, ...]] = {
    "pvp": ("pvp_kd_ratio", "kd_ratio"),
    "pve": ("air_targets", "ground_targets"),
    "pvpve": ("playtime",),
}

#: the columns a row already prints as a figure (:data:`site_reads.LEADERBOARD_COLUMNS`). A metric
#: among them needs no column of its own: its column carries the ranked figure.
COLUMN_METRICS: frozenset[str] = frozenset(site_reads.LEADERBOARD_COLUMNS)


def column_defs(metric: str) -> tuple[tuple[str, str | None, bool, bool], ...]:
    """The table's columns for *metric*: :data:`COLUMNS`, headed by the ranked figure when it has none.

    A metric that is not one of :data:`COLUMN_METRICS` (Ships, Ground targets, either ratio) gets one
    column of its own, right after the pilot, labelled with :data:`METRIC_MSGIDS`' msgid — the same
    label the panel heading and the metric control render.
    """
    label = METRIC_MSGIDS.get(str(metric or "").strip().lower())
    if label is None or metric in COLUMN_METRICS:
        return COLUMNS
    return COLUMNS[:2] + ((label, metric, True, False),) + COLUMNS[2:]


# --------------------------------------------------------------------------------- formatting

def count(value: Any) -> str:
    """A whole figure with thousands separators (``1,482``); anything unreadable is ``0``."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def hours(seconds: Any) -> int:
    """Whole hours in *seconds*, floored; anything unreadable is ``0``."""
    try:
        return max(int(float(seconds) // 3600), 0)
    except (TypeError, ValueError):
        return 0


def figure(metric: str, value: Any) -> str:
    """One figure for *metric*: hours for playtime, two decimals for a ratio, else a count."""
    if metric == "playtime":
        return count(hours(value))
    if metric in RATIO_METRICS:
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return "0.00"
    return count(value)


# --------------------------------------------------------------------------------- addresses

def _sort_query(metric: str, sort: str | None, direction: str | None) -> dict[str, str]:
    """The sort parameters an address needs, or none when the metric's own default order applies.

    A column sorted in its default direction while the board's metric is that same column is the
    read's own default, so the address stays the short one; every other order is carried.
    """
    key = str(sort or "").strip().lower()
    if not key:
        return {}
    direction = str(direction or "").strip().lower() or site_reads.DEFAULT_SORT_DIRECTION.get(
        key, "desc")
    if key == metric and direction == site_reads.DEFAULT_SORT_DIRECTION.get(key, "desc"):
        return {}
    return {SORT_PARAM: key, DIRECTION_PARAM: direction}


def _href(resolved: Any, *, metric: str, period: str, server: str | None, page: int = 1,
          sort: str | None = None, direction: str | None = None) -> str:
    """This page's address with one combination of the controls, the order, and *page* when not 1."""
    query: dict[str, Any] = {"metric": metric, "period": period, "server": server or SERVER_ALL}
    query.update(_sort_query(metric, sort, direction))
    if page > 1:
        query[PAGE_PARAM] = page
    return f"{site_url(resolved, site_pages_path())}?{urlencode(query)}"


def site_pages_path() -> str:
    """The path (inside a site) of this page — ``site_pages.NAV_PATHS``' entry, never a literal."""
    return NAV_PATHS[NAV_ID]


def offered_metrics(resolved: Any) -> tuple[str, ...]:
    """The metric ids this site offers, in the order its ``metrics:`` block names them.

    ``metrics:`` is a list over the metric vocabulary, so its order is the operator's and its first
    entry is the site's first offered metric. A site that names none offers every metric in
    :data:`METRIC_MSGIDS`' order (``sites.DEFAULT_METRICS``); a block that carries no id this page
    can rank falls back to the same set, so the control is never empty.
    """
    declared = [str(key).strip().lower() for key in (getattr(resolved, "metrics", ()) or ())]
    offered = tuple(key for key in declared if key in METRIC_MSGIDS)
    return offered or tuple(METRIC_MSGIDS)


def opening_metric(resolved: Any) -> str:
    """The metric the site's board opens on: the character's first choice among its offered metrics.

    The character names a preference list (:data:`CHARACTER_METRICS`); the first id on it that the
    site offers wins, and a site that offers none of them opens on its own first offered metric —
    never on a board it does not rank.
    """
    offered = offered_metrics(resolved)
    character = str(getattr(resolved, "character", "") or "").strip().lower()
    for key in CHARACTER_METRICS.get(character) or CHARACTER_METRICS[sites.DEFAULT_CHARACTER]:
        if key in offered:
            return key
    return offered[0]


def metric_options(resolved: Any, chosen: str, period: str, server: str | None) -> list[dict]:
    """One entry per OFFERED metric, the chosen one marked. A metric change drops the sort."""
    return [{"key": key, "label": METRIC_MSGIDS[key], "on": key == chosen,
             "href": _href(resolved, metric=key, period=period, server=server)}
            for key in offered_metrics(resolved)]


def period_options(resolved: Any, chosen: str, metric: str, server: str | None,
                   sort: str | None = None, direction: str | None = None) -> list[dict]:
    """One entry per offered period, the chosen one marked, the current order kept."""
    return [{"key": key, "days": PERIOD_DAYS[key], "on": key == chosen,
             "href": _href(resolved, metric=metric, period=key, server=server, sort=sort,
                           direction=direction)}
            for key in PERIODS]


def server_options(resolved: Any, chosen: str | None, metric: str, period: str,
                   sort: str | None = None, direction: str | None = None) -> list[dict]:
    """``All servers`` first, then the site's own servers in name order, the chosen one marked."""
    entries = [{"key": SERVER_ALL, "label": i18n._("All servers"), "on": chosen is None,
                "href": _href(resolved, metric=metric, period=period, server=None, sort=sort,
                              direction=direction)}]
    for name in sorted(getattr(resolved, "servers", ()) or ()):
        entries.append({"key": str(name), "label": str(name), "on": chosen == str(name),
                        "href": _href(resolved, metric=metric, period=period, server=str(name),
                                      sort=sort, direction=direction)})
    return entries


def scoped_context(resolved: Any, server: str | None) -> Any:
    """The site context bound to ONE of its servers, or *resolved* itself for the whole site.

    ``server`` must already be one of the site's own names (:func:`_server`); the context is the
    same :class:`~services.webservice.sites.SiteContext` with its ``servers`` narrowed, so every
    field the shell renders from (language, branding, prefix) stays the request's.
    """
    if server is None:
        return resolved
    return replace(resolved, servers=frozenset({server}))


# --------------------------------------------------------------------------------- the table

def columns(resolved: Any, metric: str, period: str, server: str | None, sort: str,
            direction: str) -> list[dict]:
    """The table's headers, in :func:`column_defs`' order, each carrying its own sort state.

    The header of the metric's own column is the ranked one (``ranked``), whether that column comes
    from :data:`COLUMNS` or was added for a metric the table does not otherwise print; it wears the
    sheet's active-sort look (``sorted-*``) so the ranked figure reads as the board's figure. Only
    the column the rows are actually ordered by carries ``aria-sort``, so the treatment never claims
    an order the read did not apply. A sortable header points at the other direction when it is the
    active column and at its own default direction otherwise, so one click orders and the next
    reverses.
    """
    headers: list[dict] = []
    for label, key, right, hide_sm in column_defs(metric):
        header = {"label": label, "key": key, "right": right, "hide_sm": hide_sm,
                  "sortable": key is not None, "active": key is not None and key == sort,
                  "ranked": key is not None and key == metric,
                  "direction": "", "aria_sort": None, "href": None}
        if key is not None:
            default = site_reads.DEFAULT_SORT_DIRECTION.get(key, "desc")
            target = ("desc" if direction == "asc" else "asc") if key == sort else default
            header.update({"direction": direction if key == sort else target,
                           "href": _href(resolved, metric=metric, period=period, server=server,
                                         sort=key, direction=target)})
            if key == sort:
                header["aria_sort"] = "ascending" if direction == "asc" else "descending"
        header["classes"] = " ".join(part for part in (
            "r" if right else "",
            "hide-sm" if hide_sm else "",
            "sortable" if header["sortable"] else "",
            f"sorted-{header['direction']}" if header["active"] or header["ranked"] else "",
            "ranked" if header["ranked"] else "") if part)
        headers.append(header)
    return headers


def _cell(key: str, row: dict) -> str:
    """One row's formatted figure for *key* (the metric's own formatter, so hours stay hours)."""
    return figure(key, row.get(key))


# --------------------------------------------------------------------------------- the view

def view(resolved: Any, metric: str, period: str, server: str | None, page: int,
         answer: dict, unavailable: bool = False) -> dict:
    """The page's whole model from the read's own answer — pure, so a test can pin it.

    THE RANKED METRIC IS THE READ'S ANSWER, NOT THE ADDRESS: the read states the metric it served,
    so the panel heading, the ranked column's header and every row's ranked figure come from one
    place (:func:`column_defs`, :data:`METRIC_MSGIDS`, ``answer['rows'][*]['value']``) and cannot
    disagree. The order (``sort``) is separate from that identity: a column sort re-orders the rows
    and never changes what the page says it is ranking.
    """
    rows = list((answer or {}).get("rows") or [])
    offset = int((answer or {}).get("offset") or 0)
    size = int((answer or {}).get("limit") or site_reads.LEADERBOARD_PAGE_SIZE)
    sort = str((answer or {}).get("sort") or metric)
    direction = str((answer or {}).get("direction") or
                    site_reads.DEFAULT_SORT_DIRECTION.get(sort, "desc"))
    served = str((answer or {}).get("metric") or "").strip().lower()
    metric = served if served in METRIC_MSGIDS else (
        metric if metric in offered_metrics(resolved) else opening_metric(resolved))
    rendered = []
    for index, row in enumerate(rows, start=1):
        position = offset + index
        rendered.append({
            "rank": f"{position:02d}",
            "position": str(position),
            "medal": MEDALS.get(position, ""),
            "name": row.get("name") or "",
            "href": _player_href(resolved, row.get("ucid")),
            "metric_value": figure(metric, row.get("value")),
            "figures": {key: _cell(key, row) for key in site_reads.LEADERBOARD_COLUMNS},
            "aircraft": row.get("aircraft") or "",
            "hot": position <= 3,
        })
    return {
        "metric": metric,
        "metric_label": METRIC_MSGIDS[metric],
        "metric_column": metric not in COLUMN_METRICS,
        "period": period,
        "period_days": PERIOD_DAYS.get(period),
        "server": server,
        "sort": sort,
        "direction": direction,
        "columns": columns(resolved, metric, period, server, sort, direction),
        "page": page,
        "size": size,
        "rows": rendered,
        "first": offset + 1 if rendered else 0,
        "last": offset + len(rendered),
        "has_prev": page > 1,
        "has_next": len(rendered) == size,
        "prev_href": _href(resolved, metric=metric, period=period, server=server, page=page - 1,
                           sort=sort, direction=direction)
                     if page > 1 else None,
        "next_href": _href(resolved, metric=metric, period=period, server=server, page=page + 1,
                           sort=sort, direction=direction)
                     if len(rendered) == size else None,
        "first_href": _href(resolved, metric=metric, period=period, server=server, sort=sort,
                            direction=direction),
        "metric_options": metric_options(resolved, metric, period, server),
        "period_options": period_options(resolved, period, metric, server, sort, direction),
        "server_options": server_options(resolved, server, metric, period, sort, direction),
        "empty": not rendered,
        "past_end": not rendered and page > 1,
        "unavailable": bool(unavailable),
    }


def _player_href(resolved: Any, ucid: Any) -> str:
    """The profile address of a row's pilot, or no link (``""``) for a row without a UCID."""
    return player_url(resolved, ucid) if ucid else ""


# --------------------------------------------------------------------------------- the read

def _metric(value: Any, resolved: Any) -> str:
    """The requested metric when this site offers it, else the site's own opening metric.

    A ``?metric=`` naming a board the site does not offer — a stale link, a hand-edited address, a
    typo — opens the board the site DOES offer (:func:`opening_metric`) rather than a hard-coded
    one, so a site that disables playtime never opens on an empty board.
    """
    wanted = str(value or "").strip().lower()
    return wanted if wanted in offered_metrics(resolved) else opening_metric(resolved)


def _period(value: Any) -> str:
    """The requested period key, or ``all`` for anything this page does not offer."""
    wanted = str(value or "").strip().lower()
    return wanted if wanted in PERIODS else "all"


def _server(value: Any, resolved: Any) -> str | None:
    """The requested server when it is one of THIS site's, else ``None`` (the whole site)."""
    wanted = str(value or "").strip()
    names = {str(name) for name in (getattr(resolved, "servers", ()) or ())}
    return wanted if wanted in names else None


async def read(resolved: Any, metric: str, period: str, server: str | None, page: int,
               sort: str | None, direction: str | None, db: Any) -> dict:
    """One page of the board — the single scoped read of this page."""
    offset = max(page - 1, 0) * site_reads.LEADERBOARD_PAGE_SIZE
    return await site_reads.leaderboard(scoped_context(resolved, server), db,
                                        metric=metric, period=PERIODS[period], offset=offset,
                                        sort=sort, direction=direction)


# --------------------------------------------------------------------------------- the route

def add_route(router: APIRouter, site: Any) -> str:
    """Serve this page at *site*'s leaderboard address and return the path it was registered at."""
    path = site_url(site, site_pages_path())

    async def site_leaderboard(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        query = request.query_params
        metric = _metric(query.get(METRIC_PARAM), resolved)
        period = _period(query.get(PERIOD_PARAM))
        server = _server(query.get(SERVER_PARAM), resolved)
        page = normalise_page(query.get(PAGE_PARAM))
        sort = query.get(SORT_PARAM)
        direction = query.get(DIRECTION_PARAM)
        db = pool()
        if db is None:
            model = view(resolved, metric, period, server, page, {}, unavailable=True)
        else:
            model = view(resolved, metric, period, server, page,
                         await read(resolved, metric, period, server, page, sort, direction, db))
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                model=model)

    router.add_api_route(path, site_leaderboard, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-leaderboard-{site.key}")
    return path
