"""The public site's overview page — the site root: hero, live band, and the community board.

EVERY FIGURE COMES FROM ``site_reads``. The page runs each scoped read exactly once and renders what
it returned; it composes and formats, it never queries. Five reads answer the whole page:

* :func:`~services.webservice.site_reads.community_totals` — the hero's hours, the live band's
  ``active this week``, and five of the six readouts;
* :func:`~services.webservice.site_reads.activity_over_time` — the weekly buckets (zero-filled by the
  read), the chart and the four week figures beside it;
* :func:`~services.webservice.site_reads.server_board` — the live band's servers-up and pilots-flying
  figures and the server table;
* :func:`~services.webservice.site_reads.leaderboard` — the top pilots by playtime;
* :func:`~services.webservice.site_reads.squadrons` — the squadrons by campaign points.

THE SIXTH READOUT IS A SESSION COUNT, NOT THE MOCKUP'S RATED COHORT: ``site_reads`` has no
site-scoped pilot rating (``trueskill`` carries no server column and is deliberately not read for a
public page), so the cell states the sessions the installation recorded instead of a figure nothing
returns.

THREE PANELS READ THREE MORE FIGURES, and two of them are bars drawn here as geometry:
``sorties_over_weeks`` and ``hours_bars`` build the SVG of a bar chart from the same rows the
panels' figures are summed from, and ``activity_over_time``'s own buckets answer the weekly sorties
panel — no read was added for it. ``top_aircraft`` is a ranked list, drawn with the sheet's existing
horizontal bars.

PRESENTATION IS COMPOSED HERE, NOT IN THE TEMPLATE: numbers are formatted, the chart's geometry is
computed and the empty states are decided in :func:`view`, so the template only reads and the same
figures can be asserted without parsing HTML. Units and labels stay ENGLISH MSGIDS
(``i18n._``-marked) and are translated where they render.

TWO ADDRESSES ARE BUILT HERE, both from the reads' own rows: a standings row links to its pilot's
profile (``site_pages.player_url`` of the UCID that read returned) and a server row links to the
leaderboard narrowed to that server (``NAV_PATHS["leaderboard"]`` plus the leaderboard's own
``server`` parameter). No page has a second lookup for either.

THE SERVER PICKER NARROWS THE WHOLE PAGE. ``?server=<name>`` names one of the site's own servers and
every read of this page then runs against a site context holding that one name; the request is
otherwise ignored, so an unknown or absent name scopes nothing. The picker is a plain GET form, so
the choice needs no JavaScript, and the scope chosen is written in the page beside the control
(``model["picker"]["scope"]``) as well as selected inside it.

THE TOP THREE RANKS WEAR A MEDAL. ``_standings`` answers the medal class of each podium row and the
template renders the disc through CSS alone — never an emoji, which Windows shows as a box.

THE SQUADRONS SECTION IS CONDITIONAL. It states campaign points, so it applies only when the
CreditSystem plugin is installed (:func:`credits_present`) AND the read returned at least one
squadron row; :func:`view` answers that as ``squadrons_section`` and numbers the sections it does
render (``sections``) from :data:`SECTION_IDS`, so the page reads ``01``, ``02``, … with no gap.

THE SITE'S SECTION SWITCHES GATE THE READS, NOT ONLY THE MARKUP. ``sites.<name>.sections`` names
the sections this site renders (:data:`~services.webservice.sites.SECTION_IDS` is the vocabulary);
:func:`read` runs a read only when its section is on, so a switched-off section costs neither a
band nor a statement. A switch hides a band and NEVER the hero figure: ``community_totals`` and
``server_board`` always run, because the hero's hours and the live band's servers-up and
pilots-flying figures are drawn above the sections and belong to no switch. The printed numbers
stay contiguous either way — :func:`_sections` numbers the bands that render, so the sections below
a switched-off one move up instead of leaving a hole.

THE PAGE IS ANONYMOUS. Its route is registered ``public`` by :mod:`services.webservice.site_pages`;
nothing here reads a session, and a signed-in visitor only gets the shell's chip.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, site_pages, site_reads, sites
from ..site_pages import NAV_PATHS, player_url, render_site_page, resolve_site, site_url

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "ACTIVITY_WINDOW",
    "STANDINGS_ROWS",
    "SECTION_IDS",
    "SHARED_BANDS",
    "CREDITS_PLUGIN",
    "SERVER_PARAM",
    "MEDALS",
    "CHART_WIDTH",
    "CHART_HEIGHT",
    "CHART_VARIANTS",
    "BAR_VARIANTS",
    "HOUR_LABELS",
    "DAY_LABELS",
    "add_route",
    "chart",
    "bars_chart",
    "count",
    "credits_present",
    "hours",
    "clock",
    "scope_context",
    "view",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS`` (its path inside a site is that table's ``""``).
NAV_ID = "overview"

TEMPLATE = "site_overview.html"

#: the query parameter the server picker submits, and the site context narrows to. Empty (or a name
#: this site does not carry) means "all servers".
SERVER_PARAM = "server"

#: the medal class of each podium rank, by position. Rank four and below carry no medal.
MEDALS = {1: "m1", 2: "m2", 3: "m3"}

#: the weekly window the chart reports. One of ``site_reads.ACTIVITY_WINDOWS``.
ACTIVITY_WINDOW = "30d"

#: how many pilots the standings table lists (the read caps its own page at 50).
STANDINGS_ROWS = 8

#: the sections the page numbers, in the order it renders them — the site vocabulary itself, so a
#: switch, a branch point and a printed number cannot disagree. A section that is switched off or
#: does not apply is dropped from this list, so the remaining ones keep a contiguous number.
SECTION_IDS: tuple[str, ...] = sites.SECTION_IDS

#: The section ids that share ONE rendered band. The aircraft and when-we-fly panels stand in one
#: grid row, so they carry one printed number between them: a site that switches one off keeps the
#: other's number, and the page never reads ``04``, ``06`` with a hole at ``05``.
SHARED_BANDS: tuple[tuple[str, ...], ...] = (("aircraft", "when_we_fly"),)

#: the plugin whose campaign points the squadrons section states.
CREDITS_PLUGIN = "creditsystem"

#: the chart's viewBox. The SVG scales with its panel; these are its own units, never pixels.
CHART_WIDTH = 760
CHART_HEIGHT = 250

#: the two geometries the page ships, both built from the same weekly buckets. The desktop viewBox
#: carries at most six week labels and the phone viewBox (400 × 230) at most three, so the axis type
#: stays at reading size instead of shrinking with a scaled-down desktop chart.
CHART_VARIANTS = {
    "wide": {"width": 760, "height": 250, "left": 56, "right": 748, "top": 26, "bottom": 196,
             "labels": 6},
    "narrow": {"width": 400, "height": 230, "left": 42, "right": 392, "top": 30, "bottom": 180,
               "labels": 3},
}
_GRID_LINES = 4

#: The two geometries of a BAR chart — the weekly sorties and the hour-of-day panels. Same split as
#: :data:`CHART_VARIANTS`: the desktop viewBox prints up to six axis labels, the phone viewBox up to
#: three, so the type stays at reading size instead of shrinking with a scaled-down desktop chart.
#: No geometry prints a bar's value; the bars are read as a shape.
BAR_VARIANTS = {
    "wide": {"width": 760, "height": 230, "left": 8, "right": 752, "top": 30, "bottom": 196,
             "labels": 6},
    "narrow": {"width": 400, "height": 200, "left": 4, "right": 396, "top": 26, "bottom": 168,
               "labels": 3},
}

#: A bar takes this share of its slot; the rest is the gap between bars.
_BAR_SHARE = 0.62

#: The hour-of-day panel's axis labels, by variant: indexes into the 24 UTC hours. The first and the
#: last hour are always printed, with the hours between them thinned to what each viewBox carries at
#: reading size — whole hours only, so a label never stands for a range.
HOUR_LABELS: dict[str, tuple[int, ...]] = {
    "wide": (0, 6, 12, 18, 23),
    "narrow": (0, 12, 23),
}

#: The ISO weekday labels, index 0 = Monday (``EXTRACT(ISODOW ...)`` answers 1–7).
DAY_LABELS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# --------------------------------------------------------------------------------- arithmetic

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


def clock(seconds: Any) -> str:
    """A duration as ``H:MM`` (the average-session readout); anything unreadable is ``0:00``."""
    try:
        total = max(int(float(seconds)), 0)
    except (TypeError, ValueError):
        total = 0
    return f"{total // 3600}:{total % 3600 // 60:02d}"


# --------------------------------------------------------------------------------- the chart

def _nice_top(value: float) -> float:
    """The axis maximum at or above *value*: ``_GRID_LINES × {1,2,5} × 10^k``, so every gridline is a
    whole number and no two of them print the same label."""
    if value <= 0:
        return float(_GRID_LINES)
    for magnitude in range(0, 12):
        for unit in (1, 2, 5):
            step = unit * 10 ** magnitude
            if step * _GRID_LINES >= value:
                return float(step * _GRID_LINES)
    return float(value)


def chart(weeks: list[dict], variant: str = "wide") -> dict:
    """The weekly playtime chart as GEOMETRY: gridlines, area and line paths, points, axis labels.

    ``weeks`` is ``activity_over_time()["weeks"]`` — already zero-filled and ordered by the read, so
    one point per bucket and no gap is invented here. A site with no bucket at all answers an empty
    geometry (``points == []``), which the template renders as its empty state rather than a flat line
    across a window that holds nothing.

    *variant* picks one of :data:`CHART_VARIANTS`; both are computed from the same buckets, so the
    two SVGs the page renders carry the same twelve points and the same peak label. The vertical axis
    is HOURS, rounded up to a readable maximum (:func:`_nice_top`); the horizontal labels are the ISO
    week labels the read returned, thinned to the variant's own cap (six, or three on a phone), so a
    52-week window still reads.
    """
    geometry_of = CHART_VARIANTS[variant]
    left, right = geometry_of["left"], geometry_of["right"]
    top, bottom = geometry_of["top"], geometry_of["bottom"]
    buckets = [{"week": str(entry.get("week") or ""),
                "hours": hours(entry.get("playtime")),
                "pilots": entry.get("pilots") or 0} for entry in (weeks or [])]
    geometry: dict = {
        "width": geometry_of["width"], "height": geometry_of["height"],
        "left": left, "right": right, "top": top, "bottom": bottom,
        "gridlines": [], "xlabels": [], "points": [], "area": "", "line": "",
        "peak": None, "top_value": 0, "empty": not buckets, "range": "",
    }
    if not buckets:
        return geometry
    top_value = _nice_top(float(max(bucket["hours"] for bucket in buckets)))
    span_x = right - left
    span_y = bottom - top
    step = span_x / (len(buckets) - 1) if len(buckets) > 1 else 0

    def y_of(value: float) -> float:
        return round(bottom - (value / top_value) * span_y, 1)

    def x_of(index: int) -> float:
        if len(buckets) == 1:
            return round(left + span_x / 2, 1)
        return round(left + index * step, 1)

    for line in range(_GRID_LINES + 1):
        value = top_value * line / _GRID_LINES
        geometry["gridlines"].append({"y": y_of(value), "label": count(round(value))})

    points = [{"x": x_of(index), "y": y_of(bucket["hours"]), "hours": bucket["hours"],
               "week": bucket["week"], "peak": False}
              for index, bucket in enumerate(buckets)]
    peak = max(points, key=lambda point: point["hours"])
    peak["peak"] = True
    geometry["points"] = points
    geometry["peak"] = peak
    geometry["top_value"] = count(round(top_value))

    line = " ".join(f"{'M' if index == 0 else 'L'}{point['x']} {point['y']}"
                    for index, point in enumerate(points))
    geometry["line"] = line
    geometry["area"] = f"{line} L{points[-1]['x']} {bottom} L{points[0]['x']} {bottom} Z"

    # the variant's own cap on labels, spread evenly and always including the first and the last bucket
    wanted = min(geometry_of["labels"], len(buckets))
    indexes = sorted({round(index * (len(buckets) - 1) / (wanted - 1)) if wanted > 1 else 0
                      for index in range(wanted)})
    for index in indexes:
        geometry["xlabels"].append({"x": x_of(index), "text": _week_label(buckets[index]["week"])})
    geometry["range"] = f"{_week_label(buckets[0]['week'])} – {_week_label(buckets[-1]['week'])}"
    return geometry


def _week_label(week: str) -> str:
    """An ISO week label (``2026-W42``) as the axis spells it (``W42``); anything else unchanged."""
    _year, separator, number = str(week).partition("-W")
    return f"W{number}" if separator else str(week)


def bars_chart(entries: list[dict], variant: str = "wide",
               labels: Any = None) -> dict:
    """A bar chart as GEOMETRY: one bar per entry, its value, its axis label.

    ``entries`` is ``[{"label": "W35", "value": 310, "now": False}, …]`` — already ordered and, for
    the weekly panel, already zero-filled by the read, so one bar is drawn per bucket and no bar is
    invented here. A site with no entry at all answers an empty geometry (``bars == []``), which the
    template renders as its empty state rather than a flat row of bars across a window that holds
    nothing.

    *variant* picks one of :data:`BAR_VARIANTS`. Both are computed from the same entries, so the two
    SVGs a panel renders carry the same heights and the same peak; only the viewBox and the number
    of axis labels differ. No bar carries its value. Bar height is the entry's value against the
    tallest entry — the panel's own scale, never an axis the data cannot fill.

    *labels* names the entries that carry an axis label, as indexes into ``entries`` (the hour panel
    passes :data:`HOUR_LABELS`); without it the variant's own cap is spread evenly over the entries,
    first and last included.
    """
    geometry_of = BAR_VARIANTS[variant]
    left, right = geometry_of["left"], geometry_of["right"]
    top, bottom = geometry_of["top"], geometry_of["bottom"]
    geometry: dict = {
        "width": geometry_of["width"], "height": geometry_of["height"],
        "left": left, "right": right, "top": top, "bottom": bottom,
        "bars": [], "xlabels": [], "empty": not entries, "max": 0, "range": "",
    }
    if not entries:
        return geometry
    values = [max(int(entry.get("value") or 0), 0) for entry in entries]
    tallest = max(values)
    span_x = right - left
    span_y = bottom - top
    slot = span_x / len(entries)
    bar_width = slot * _BAR_SHARE
    peak_index = values.index(tallest)
    for index, entry in enumerate(entries):
        height = round(values[index] / tallest * span_y, 1) if tallest else 0.0
        geometry["bars"].append({
            "x": round(left + index * slot + (slot - bar_width) / 2, 1),
            "y": round(bottom - height, 1),
            "w": round(bar_width, 1),
            "h": height,
            "cx": round(left + index * slot + slot / 2, 1),
            "label": str(entry.get("label") or ""),
            "value": values[index],
            "now": bool(entry.get("now")),
            "peak": index == peak_index,
        })
    # the entries that carry an axis label: the caller's own list, or the variant's cap spread evenly
    # and always including the first and the last bar
    if labels is not None:
        wanted_indexes = sorted({int(index) for index in labels if 0 <= int(index) < len(entries)})
    else:
        wanted = min(geometry_of["labels"], len(entries))
        wanted_indexes = sorted({round(index * (len(entries) - 1) / (wanted - 1)) if wanted > 1 else 0
                                 for index in range(wanted)})
    for index in wanted_indexes:
        geometry["xlabels"].append({
            "x": round(left + index * slot + slot / 2, 1),
            "text": str(entries[index].get("label") or ""),
        })
    geometry["max"] = tallest
    geometry["range"] = f"{entries[0].get('label') or ''} – {entries[-1].get('label') or ''}"
    return geometry


def _week_figures(buckets: list[dict]) -> dict:
    """The four figures beside the chart, from the SAME buckets the chart draws (no second read)."""
    values = [hours(entry.get("playtime")) for entry in (buckets or [])]
    if not values:
        return {"combined": 0, "busiest": 0, "busiest_week": "", "average": 0,
                "quietest": 0, "quietest_week": ""}
    busiest = max(values)
    quietest = min(values)
    return {
        "combined": sum(values),
        "busiest": busiest,
        "busiest_week": _week_label(str((buckets[values.index(busiest)] or {}).get("week") or "")),
        "average": round(sum(values) / len(values)),
        "quietest": quietest,
        "quietest_week": _week_label(str((buckets[values.index(quietest)] or {}).get("week") or "")),
    }


# --------------------------------------------------------------------------------- the panels

def _sortie_entries(buckets: list[dict]) -> list[dict]:
    """One bar per week of the SAME buckets the hours chart draws, the current week marked.

    The read zero-fills through the current ISO week, so the last bucket IS the current week; it
    carries ``now`` and the template draws it in the panel's brighter fill. A week at zero is a bar
    of no height, which is the truth of a quiet week.
    """
    entries = [{"label": _week_label(str(entry.get("week") or "")),
                "value": int(entry.get("sorties") or 0), "now": False}
               for entry in (buckets or [])]
    if entries:
        entries[-1]["now"] = True
    return entries


def _sortie_figures(buckets: list[dict]) -> dict:
    """The four figures closing the sorties panel, from the same buckets as its bars.

    ``change`` is the LAST week against the FIRST of the window, in percent, carrying its sign; a
    first week at zero has no percentage to state and answers ``None`` rather than a division.
    """
    values = [int(entry.get("sorties") or 0) for entry in (buckets or [])]
    if not values:
        return {"total": 0, "busiest": 0, "busiest_week": "", "average": 0, "change": None}
    busiest = max(values)
    first = values[0]
    change = round((values[-1] - first) * 100.0 / first) if first else None
    return {
        "total": sum(values),
        "busiest": busiest,
        "busiest_week": _week_label(str((buckets[values.index(busiest)] or {}).get("week") or "")),
        "average": round(sum(values) / len(values)),
        "change": f"{change:+d}" if change is not None else None,
    }


def _aircraft(rows: list[dict]) -> list[dict]:
    """The most-flown airframes as horizontal bars, each scaled against the leader's playtime.

    The order is the read's (playtime, then the slot name); the leader is the first row, so its bar
    is full width. A site whose top airframe has no recorded playtime draws every bar at zero.
    """
    ranked = list(rows or [])
    leader = max((int(row.get("playtime") or 0) for row in ranked), default=0)
    return [{
        "slot": row.get("slot") or "",
        "hours": count(hours(row.get("playtime"))),
        "sorties": count(row.get("sorties")),
        "share": round(int(row.get("playtime") or 0) * 100.0 / leader, 1) if leader else 0,
    } for row in ranked]


def _hour_entries(hours: list[dict]) -> list[dict]:
    """The hour-of-day panel's 24 bars, one per UTC hour of the read's zero-filled day.

    A bar is the flight MINUTES recorded in its hour (the read answers seconds), so the tallest bar
    is the busiest hour and no bar stands for a range of hours.
    """
    values = {int(entry.get("hour") or 0): int(entry.get("seconds") or 0)
              for entry in (hours or [])}
    return [{"label": f"{hour:02d}", "value": round(values.get(hour, 0) / 60)}
            for hour in range(site_reads.HOURS_PER_DAY)]


def _hours_figures(hours: list[dict], weekdays: list[dict]) -> dict:
    """The peak hour and the busiest weekday, read off the same rows the bars are drawn from.

    Both state FLIGHT TIME: the peak is the single UTC hour with the most flight seconds (each bar is
    one hour, so the figure names the tallest bar) and the busiest day is the largest ISO weekday
    total; the first of two equal entries wins, so the figure is stable across requests. A site with
    no flight time answers both as ``""`` and ``empty`` True.
    """
    hourly = [(int(entry.get("hour") or 0), int(entry.get("seconds") or 0)) for entry in (hours or [])]
    daily = [(int(entry.get("dow") or 0), int(entry.get("seconds") or 0)) for entry in (weekdays or [])]
    total = sum(value for _hour, value in hourly)
    peak = max(hourly, key=lambda item: item[1]) if hourly and total else None
    busiest = max(daily, key=lambda item: item[1]) if daily and total else None
    return {
        "total": total,
        "peak_hour": f"{peak[0]:02d}:00" if peak else "",
        "peak_seconds": peak[1] if peak else 0,
        "busiest_day": (i18n._(DAY_LABELS[busiest[0] - 1])
                        if busiest and 1 <= busiest[0] <= len(DAY_LABELS) else ""),
        "busiest_seconds": busiest[1] if busiest else 0,
        "empty": total == 0,
    }


# --------------------------------------------------------------------------------- the addresses

def _player_href(resolved: Any, ucid: Any) -> str:
    """The profile address of a row's pilot, or no link (``""``) for a row without a UCID."""
    return player_url(resolved, ucid) if ucid and resolved is not None else ""


def _server_href(resolved: Any, server_name: Any) -> str:
    """The leaderboard's own address narrowed to ONE server.

    The path is ``site_pages.NAV_PATHS``' leaderboard entry and the parameter is the one that page
    reads (:data:`~services.webservice.pages.site_leaderboard.SERVER_PARAM`), so the server name in
    the table is the leaderboard's ``server`` filter rather than a second, private scope spelling.
    The import sits inside the call: the pages are siblings and load in any order.
    """
    from .site_leaderboard import SERVER_PARAM

    name = str(server_name or "")
    if not name or resolved is None:
        return ""
    return f"{site_url(resolved, NAV_PATHS['leaderboard'])}?{urlencode({SERVER_PARAM: name})}"


def credits_present() -> bool:
    """Whether the CreditSystem plugin is loaded on this installation.

    The list is the bot's own (``PluginManager.plugins``: the node's configured plugins), so the
    page and the plugin loader cannot disagree about what is installed. A process holding no bot
    service — a standalone shell, a test's stub — answers ``False``: it has no plugin list to read.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        service = ServiceRegistry.get(BotService)
        if service is None:
            return False
        names = list(getattr(getattr(service, "bot", None), "plugins", None) or [])
        if not names:
            names = list(getattr(getattr(service, "node", None), "plugins", None) or [])
    except Exception:  # noqa: BLE001 - an unreadable plugin list applies no section, never a 500
        return False
    return any(str(name).lower() == CREDITS_PLUGIN for name in names)


# --------------------------------------------------------------------------------- the view

def _readouts(totals: dict) -> list[dict]:
    """The six community readouts, in the mockup's order, from ``community_totals``.

    ``share`` is a cohort share in percent for the one readout that states a ratio (the pilots active
    this week against every pilot known); ``change`` is that readout's figure against the week before.
    No cell carries a note about how its number is computed.
    """
    known = int(totals.get("known_pilots") or 0)
    active = int(totals.get("active_this_week") or 0)
    share = round(active * 100.0 / known, 1) if known else None
    return [
        {"key": "known_pilots", "value": count(known), "unit": None,
         "label": i18n._("Pilots"), "change": None, "share": None, "tone": ""},
        {"key": "avg_session", "value": clock(totals.get("avg_session")) if totals.get("avg_session") else "0:00",
         "unit": i18n._("h"), "label": i18n._("Average sortie"), "change": None, "share": None, "tone": ""},
        {"key": "sorties", "value": count(totals.get("sorties")), "unit": None,
         "label": i18n._("Sorties flown"), "change": None, "share": None, "tone": ""},
        {"key": "active_this_week", "value": count(active), "unit": None,
         "label": i18n._("Flew this week"), "change": f"{int(totals.get('active_change') or 0):+d}",
         "share": share, "tone": "sage"},
        {"key": "missions_flown", "value": count(totals.get("missions_flown")), "unit": None,
         "label": i18n._("Missions"), "change": None, "share": None, "tone": ""},
        {"key": "sessions", "value": count(totals.get("sessions")), "unit": None,
         "label": i18n._("Sessions recorded"), "change": None, "share": None, "tone": ""},
    ]


def _server_board(board: list[dict], resolved: Any = None) -> list[dict]:
    """The server table's rows: the live state and the all-time figures, per server."""
    return [{
        "server_name": row.get("server_name") or "",
        "href": _server_href(resolved, row.get("server_name")),
        "online": bool(row.get("online")),
        "players_online": count(row.get("players_online")),
        "playtime": count(hours(row.get("playtime"))),
        "sorties": count(row.get("sorties")),
        "window": row.get("window") or site_reads.BOARD_WINDOW_LABEL,
    } for row in (board or [])]


def _standings(pilots: dict, resolved: Any = None) -> list[dict]:
    """The top pilots by playtime, already ranked by the read, as table rows.

    Each row's address is built from the UCID the SAME read returned for it — the profile link the
    leaderboard prints — and a row without a UCID carries none. The top three answer a ``medal``
    class (``m1``/``m2``/``m3``) and their bare position, which the template renders as a disc; every
    row below answers ``medal == ""`` and keeps the plain two-digit ``rank``.
    """
    rows = []
    for position, row in enumerate((pilots or {}).get("rows") or [], start=1):
        if position > STANDINGS_ROWS:
            break
        ucid = row.get("ucid") or ""
        rows.append({"rank": f"{position:02d}", "position": str(position),
                     "medal": MEDALS.get(position, ""),
                     "name": row.get("name") or "", "ucid": ucid,
                     "href": _player_href(resolved, ucid),
                     "hours": count(hours(row.get("value"))), "hot": position <= 3})
    return rows


def _bands() -> tuple[tuple[str, ...], ...]:
    """The section ids as the bands the page NUMBERS: one entry per rendered row.

    :data:`SECTION_IDS`' order is kept, and the ids of a :data:`SHARED_BANDS` entry answer one band
    between them, so the numbering counts rows rather than ids.
    """
    shared = {key: band for band in SHARED_BANDS for key in band}
    bands: list[tuple[str, ...]] = []
    numbered: set[str] = set()
    for key in SECTION_IDS:
        if key in numbered:
            continue
        band = shared.get(key, (key,))
        bands.append(band)
        numbered.update(band)
    return tuple(bands)


def _sections(enabled: Any) -> dict[str, str]:
    """The printed number of every section: ``01``, ``02``, … for the ones that render, ``""`` else.

    *enabled* is the site's own enabled ids (:data:`~services.webservice.sites.SECTION_IDS` when a
    site names none) and this is the ONE mapping from that vocabulary to the page's model. Every id
    of the vocabulary answers a key, so the template can read ``model.sections.<id>`` for all seven
    without a lookup that might miss; a switched-off id answers ``""``, which is falsy and therefore
    the switch itself. The ids are numbered in :data:`SECTION_IDS`' order, so a switched-off section
    leaves no hole, and the two ids of a shared band answer the same number.
    """
    wanted = set(SECTION_IDS if enabled is None else enabled)
    numbers: dict[str, str] = {key: "" for key in SECTION_IDS}
    printed = 0
    for band in _bands():
        present = [key for key in band if key in wanted]
        if not present:
            continue
        printed += 1
        for key in present:
            numbers[key] = f"{printed:02d}"
    return numbers


def _squadrons(squadrons: list[dict]) -> list[dict]:
    """The site's squadrons by campaign points, with each bar scaled against the leader's points."""
    ranked = sorted((row for row in (squadrons or [])),
                    key=lambda row: (-int(row.get("campaign_points") or 0),
                                     str(row.get("name") or "").lower()))
    leader = int(ranked[0].get("campaign_points") or 0) if ranked else 0
    rows = []
    for row in ranked:
        points = int(row.get("campaign_points") or 0)
        rating = row.get("rating")
        rows.append({
            "name": row.get("name") or "",
            "points": count(points),
            "share": round(points * 100.0 / leader, 1) if leader else 0,
            "members": count(row.get("members")),
            "rating": f"{float(rating):.2f}" if isinstance(rating, (int, float)) else None,
            "rating_available": bool(row.get("rating_available")),
        })
    return rows


def scope_context(resolved: Any, requested: Any) -> tuple[Any, str]:
    """*resolved* narrowed to the server *requested*, and the scope that names it.

    ``requested`` is the picker's own query value. A name this site does not carry — and the empty
    value the "All servers" option submits — leaves the site whole and answers ``""``, so a mistyped
    or hand-edited address shows the site's own board rather than an empty page. The narrowed
    context is a copy: the request's own membership is never mutated.
    """
    name = str(requested or "").strip()
    servers = getattr(resolved, "servers", None) or frozenset()
    if not name or name not in servers:
        return resolved, ""
    return replace(resolved, servers=frozenset({name})), name


def _picker(resolved: Any, scope: str, servers: Any) -> dict:
    """The server picker's model: its address, the scope in force, and one option per server.

    The options come from the SITE's servers (not from the narrowed context the reads answer), so
    choosing one server keeps every other choice in the list. "All servers" is the empty value: it
    submits no server and clears the scope.
    """
    names = [str(name) for name in (servers or ()) if str(name).strip()]
    options = [{"value": "", "label": i18n._("All servers"), "selected": not scope}]
    options += [{"value": name, "label": name, "selected": name == scope} for name in names]
    return {
        "param": SERVER_PARAM,
        "action": site_url(resolved) if resolved is not None else "",
        "scope": scope or i18n._("All servers"),
        "on": bool(scope),
        "options": options,
    }


def view(totals: dict, activity: dict, board: list[dict], pilots: dict,
         squadrons: list[dict], *, aircraft: dict | None = None,
         hours_of_day: dict | None = None, resolved: Any = None, credits: bool = False,
         sections: Any = None, scope: str = "", servers: Any = ()) -> dict:
    """The page's whole model from the seven reads' own answers — pure, so a test can pin it.

    *resolved* is the request's site context, used to build the standings' and the server table's
    addresses and the picker's own address; without it every row renders its name as plain text.
    *credits* states whether the CreditSystem plugin is installed on this installation
    (:func:`credits_present`). *sections* is the site's own enabled section ids and decides which
    bands the model numbers (``sections``, the only switch the template reads); without it every
    section counts as on. *scope* is the server the picker narrowed the page to (``""`` for all of
    them) and *servers* the site's whole server set, which is what the picker offers.

    *aircraft* is :func:`~services.webservice.site_reads.top_aircraft`'s answer and *hours_of_day*
    :func:`~services.webservice.site_reads.hours_of_day`'s; both default to nothing, so a caller
    that names neither draws the two panels' empty states instead of failing.
    """
    aircraft_rows = list((aircraft or {}).get("rows") or [])
    weeks = list((activity or {}).get("weeks") or [])
    server_rows = _server_board(board, resolved)
    up = sum(1 for row in (board or []) if row.get("online"))
    flying = sum(int(row.get("players_online") or 0) for row in (board or []))
    standings = _standings(pilots, resolved)
    squadron_rows = _squadrons(squadrons)
    sortie_entries = _sortie_entries(weeks)
    hour_entries = _hour_entries((hours_of_day or {}).get("hours") or [])
    hour_figures = _hours_figures((hours_of_day or {}).get("hours") or [],
                                  (hours_of_day or {}).get("weekdays") or [])
    # the squadrons section states campaign points, which only exist with the plugin; with no
    # squadron row there is nothing to state at all. Either way the section is not rendered.
    enabled = list(SECTION_IDS if sections is None else sections)
    show_squadrons = bool(credits and squadron_rows and "squadrons" in enabled)
    rendered = [key for key in enabled if key != "squadrons" or show_squadrons]
    return {
        "hero": {
            "hours": count(hours(totals.get("total_playtime"))),
            "server_count": count(len(server_rows)),
            "known_pilots": count(totals.get("known_pilots")),
        },
        # the live band: what is running right now, and one figure that says how big the community is
        "live": {
            "pilots_flying": count(flying),
            "servers_up": count(up),
            "servers_total": count(len(server_rows)),
            "active_this_week": count(totals.get("active_this_week")),
        },
        "picker": _picker(resolved, scope, servers),
        "readouts": _readouts(totals),
        "activity": {
            "window": (activity or {}).get("window") or ACTIVITY_WINDOW,
            "weeks": len(weeks),
            "chart": chart(weeks, "wide"),
            "chart_narrow": chart(weeks, "narrow"),
            "figures": _week_figures(weeks),
        },
        # the sorties panel: the SAME weekly buckets as the hours chart, so the two panels of the
        # season always describe one window
        "sorties": {
            "chart": bars_chart(sortie_entries, "wide"),
            "chart_narrow": bars_chart(sortie_entries, "narrow"),
            "figures": _sortie_figures(weeks),
            "empty": not any(entry["value"] for entry in sortie_entries),
        },
        "aircraft": {
            "rows": _aircraft(aircraft_rows),
            "empty": not aircraft_rows,
        },
        "hours": {
            "chart": bars_chart(hour_entries, "wide", HOUR_LABELS["wide"]),
            "chart_narrow": bars_chart(hour_entries, "narrow", HOUR_LABELS["narrow"]),
            "figures": hour_figures,
            "empty": hour_figures["empty"],
        },
        "standings": {
            "rows": standings,
            "shown": count(len(standings)),
            "known": count(totals.get("known_pilots")),
        },
        "squadrons": squadron_rows,
        "squadrons_section": show_squadrons,
        "sections": _sections(rendered),
        "servers": server_rows,
        "window_label": (server_rows[0]["window"] if server_rows
                         else site_reads.BOARD_WINDOW_LABEL),
    }


# --------------------------------------------------------------------------------- the read

def _pool() -> Any:
    """The database pool this process already holds — the page seam's one accessor."""
    return site_pages.pool()


async def read(resolved: Any, pool: Any, sections: Any = None) -> dict:
    """Run the reads the site's enabled *sections* need — once each — and return their answers.

    *sections* is the site's own enabled ids (``resolved.sections``); without it every section
    counts as on, which is what a site that names no ``sections:`` map renders.

    TWO READS ALWAYS RUN, whatever the switches say: ``community_totals`` answers the hero's hours,
    the live band and the standings' denominator, and ``server_board`` answers the live band's
    servers-up and pilots-flying figures. Both bands stand above the sections and belong to no
    switch — a switch hides a band, never the hero figure. Every other read runs ONLY when its
    section is on, so a switched-off section costs no statement at all and not merely no markup.
    """
    enabled = set(SECTION_IDS if sections is None else sections)
    totals = await site_reads.community_totals(resolved, pool)
    activity = (await site_reads.activity_over_time(resolved, pool, ACTIVITY_WINDOW)
                if "season" in enabled else {})
    board = await site_reads.server_board(resolved, pool)
    pilots = (await site_reads.leaderboard(resolved, pool, metric="playtime", period="all")
              if "standings" in enabled else {})
    squadrons = await site_reads.squadrons(resolved, pool) if "squadrons" in enabled else []
    aircraft = await site_reads.top_aircraft(resolved, pool) if "aircraft" in enabled else {}
    hours = await site_reads.hours_of_day(resolved, pool) if "when_we_fly" in enabled else {}
    return {"totals": totals, "activity": activity, "board": board, "pilots": pilots,
            "squadrons": squadrons, "aircraft": aircraft, "hours_of_day": hours}


def _empty(scope: str = "", servers: Any = (), sections: Any = None) -> dict:
    """The model of a site whose database is not reachable yet: every figure at zero, no rows.

    The site's *sections* still apply, so an unreachable database does not bring back a band the
    operator switched off.
    """
    return view({}, {}, [], {}, [], sections=sections, scope=scope, servers=servers)


# --------------------------------------------------------------------------------- the route

def add_route(router: APIRouter, site: Any) -> str:
    """Serve this page at *site*'s root and return the address it was registered at."""
    path = site_url(site)

    async def site_overview(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        # the picker's choice scopes EVERY read of this page: the reads answer the site context, and
        # the narrowed copy holds one name while the picker still offers them all.
        offered = tuple(sorted(getattr(resolved, "servers", None) or ()))
        resolved, scope = scope_context(resolved, request.query_params.get(SERVER_PARAM))
        pool = _pool()
        model = (_empty(scope, offered, resolved.sections) if pool is None
                 else view(**await read(resolved, pool, resolved.sections), resolved=resolved,
                           credits=credits_present(), sections=resolved.sections, scope=scope,
                           servers=offered))
        return render_site_page(request, site, TEMPLATE, active=NAV_ID,
                                resolved=resolved, model=model, unavailable=pool is None)

    router.add_api_route(path, site_overview, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-overview-{site.key}")
    return path
