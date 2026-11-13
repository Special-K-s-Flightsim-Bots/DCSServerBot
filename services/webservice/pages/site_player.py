"""The public site's pilot profile — one pilot by UCID: the strip, the combat record, flight
discipline, airframes, servers, recent sorties, the competitive rating and the linked UCIDs.

EVERY FIGURE IS ``site_reads.pilot_profile``'S ANSWER, run once per request: the totals, the
per-aircraft hours, the per-server hours, the newest sessions and the UCIDs the same Discord link
owns, plus the competitive plugin's rating when its tables exist. The page composes and formats; it
never queries.

THE SITE'S CHARACTER DECIDES WHICH FIGURES LEAD, never which are shown. ``sites.<name>.character``
(``pvp``, ``pve``, ``pvpve``, default ``pvpve``) is a declared value: :data:`READOUT_ORDER` fixes
the strip's cell order and :data:`COMBAT_LAYOUT` fixes which combat panel leads and how wide each
one is. Every figure every character renders is the same five readouts and the same two panels, so
the page states the character in one line (:data:`CHARACTER_LINES`) and leads with the numbers that
follow from it. The ONE figure a character withholds is the air-kills cell's ``N in PvP`` note: a
``pve`` site does not print it, because a community that never flies against another player would
print a zero there — a statistic that states nothing.

WHAT IT MAY RENDER, AND NOTHING ELSE. Pilot name, the figures derived from the statistics tables,
the competitive rating and the links a profile carries. It never renders a Discord id, an e-mail, an
address, a ban or watch state, or moderation history. The read answers ``discord_id`` and the shell
never prints it: the page uses it only to decide whether a Discord account is linked at all.

A SESSION IS A ROW, NOT A LOG LINE. The recent list shows a sortie's DATE (no clock time), the
airframe, the mission's name, the server and the duration — the same fields the site's own session
tables carry, and none of the per-session detail a public page may not show.

AN UNKNOWN UCID IS A 404, NOT AN EMPTY PAGE: a pilot with no session on this site's servers renders
:data:`TEMPLATE` with the not-found copy and the response carries ``404``, so a shared link to a
pilot who is not here answers the way a wrong address does.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, site_reads, sites
from ..site_pages import (NAV_PATHS, PLAYER_PATH, SEARCH_PATH, player_url, pool, render_site_page,
                          resolve_site, site_url)

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "CHARACTER_LINES",
    "READOUT_ORDER",
    "COMBAT_LAYOUT",
    "count",
    "hours",
    "ratio",
    "per_sortie",
    "sentence",
    "airframes",
    "servers",
    "linked",
    "last_flown",
    "most_flown",
    "readouts",
    "discipline",
    "combat",
    "recent",
    "rating_chart",
    "rating_single",
    "rating_view",
    "view",
    "add_route",
]

log = logging.getLogger(__name__)

#: the nav id this page belongs to: a pilot's profile is reached from the ``players`` page, which
#: the menu links, so the Pilots entry stays marked while a profile is open.
NAV_ID = "players"

TEMPLATE = "site_player.html"

#: The one line each character states, so a visitor knows why the page leads with those figures.
CHARACTER_LINES: dict[str, str] = {
    "pvp": i18n._("PvP community"),
    "pve": i18n._("PvE community"),
    "pvpve": i18n._("PvP and PvE community"),
}

#: The strip's cell order per character: the SAME five figures, the leading ones first. A ``pve``
#: site leads with what its pilots FLEW (sorties, then hours), never with a kill count.
READOUT_ORDER: dict[str, tuple[str, ...]] = {
    "pvp": ("pvp", "kd", "air_kills", "sorties", "playtime"),
    "pve": ("sorties", "playtime", "air_kills", "pvp", "kd"),
    "pvpve": ("playtime", "sorties", "air_kills", "pvp", "kd"),
}

#: The combat panels per character — ``(panel id, column span)`` in reading order. ``pvpve`` puts
#: the two halves side by side; a single-character site leads with its own half, wider.
COMBAT_LAYOUT: dict[str, tuple[tuple[str, int], ...]] = {
    "pvp": (("pvp", 7), ("targets", 5)),
    "pve": (("targets", 7), ("pvp", 5)),
    "pvpve": (("pvp", 6), ("targets", 6)),
}


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


def ratio(kills: Any, deaths: Any) -> str:
    """Kills per death to two decimals; a pilot with no death reports their kills (the bot's rule)."""
    try:
        killed = int(kills or 0)
        lost = int(deaths or 0)
    except (TypeError, ValueError):
        return "0"
    if lost <= 0:
        return count(killed)
    return f"{killed / lost:.2f}"


def per_sortie(value: Any, sorties: Any) -> str:
    """*value* per recorded sortie to two decimals; no sortie is ``0`` (never a division)."""
    try:
        whole = int(sorties or 0)
        return f"{int(value or 0) / whole:.2f}" if whole > 0 else "0"
    except (TypeError, ValueError):
        return "0"


def sentence(msgid: str, count_text: str) -> dict:
    """A sentence RECORD for the shell's guarded ``say``: the msgid and its already-formatted figure.

    The figure is formatted here (thousands separators) so the catalog's line interpolates a string
    and never a raw number whose grouping would follow one language's rules on every other.
    """
    return {"msgid": msgid, "params": {"count": count_text}}


def _record(msgid: str, **params: Any) -> dict:
    """A sentence RECORD carrying named parameters, interpolated by the shell's ``say``."""
    return {"msgid": msgid, "params": dict(params)}


def _share(value: Any, total: Any) -> float:
    """*value* as a percentage of *total*, one decimal; ``total`` of zero is ``0.0``."""
    try:
        whole = float(total or 0)
        return round(float(value or 0) * 100.0 / whole, 1) if whole else 0.0
    except (TypeError, ValueError):
        return 0.0


def airframes(rows: list[dict], total_playtime: Any) -> list[dict]:
    """The per-aircraft rows as bars: the airframe, its hours, its sessions and its share."""
    return [{"name": row.get("aircraft") or i18n._("Unknown"),
             "hours": count(hours(row.get("playtime"))),
             "sessions": count(row.get("sessions")),
             "share": _share(row.get("playtime"), total_playtime)}
            for row in (rows or [])]


def servers(rows: list[dict]) -> list[dict]:
    """The per-server rows: the server's name, the pilot's hours and sessions on it."""
    return [{"name": row.get("server_name") or "",
             "hours": count(hours(row.get("playtime"))),
             "sessions": count(row.get("sessions"))}
            for row in (rows or [])]


def most_flown(rows: list[dict]) -> str:
    """The airframe the pilot has flown longest, or ``""`` when the read answered none.

    The read orders the airframe rows by playtime, so the FIRST row is the answer; an explicit
    maximum keeps the figure right even if that order ever changes.
    """
    best, best_seconds = "", -1.0
    for row in (rows or []):
        try:
            seconds = float(row.get("playtime") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds > best_seconds:
            best, best_seconds = str(row.get("aircraft") or ""), seconds
    return best


def linked(rows: list[dict], resolved: Any, ucid: str) -> list[dict]:
    """Every OTHER UCID of the same Discord link, as a link to that UCID's own profile.

    The primary UCID is the page's own and is not repeated here; a pilot with one UCID answers an
    empty list, so the section renders nothing.
    """
    entries: list[dict] = []
    for row in (rows or []):
        other = str(row.get("ucid") or "")
        if not other or other == str(ucid):
            continue
        entries.append({"name": row.get("name") or "", "href": player_url(resolved, other)})
    return entries


def last_flown(recent: list[dict]) -> str:
    """The date of the most recent session (``2026-11-06``), or ``""`` when there is none.

    Takes the DATE part only: a clock time is the session-level detail a public page does not show.
    """
    for row in (recent or []):
        stamp = str(row.get("hop_on") or "")
        if stamp:
            return stamp.split("T", 1)[0]
    return ""


def recent(rows: list[dict]) -> list[dict]:
    """The newest sessions as table rows: the date, airframe, mission, server and duration.

    A duration is split into whole hours and the remaining minutes so the template renders the
    units (``h`` is a msgid) instead of the page composing a translated string.
    """
    out: list[dict] = []
    for row in (rows or []):
        try:
            seconds = max(int(float(row.get("playtime") or 0)), 0)
        except (TypeError, ValueError):
            seconds = 0
        stamp = str(row.get("hop_on") or "")
        out.append({
            "date": stamp.split("T", 1)[0],
            "aircraft": row.get("slot") or i18n._("Unknown"),
            "mission": row.get("mission_name") or "",
            "server": row.get("server_name") or "",
            "hours": str(seconds // 3600),
            "minutes": f"{seconds % 3600 // 60:02d}",
        })
    return out


# --------------------------------------------------------------------------------- the figures

def _character(resolved: Any) -> str:
    """The character the request's site declared, defaulting when it carries none."""
    declared = str(getattr(resolved, "character", "") or "").strip().lower()
    return declared if declared in COMBAT_LAYOUT else sites.DEFAULT_CHARACTER


def _headlines(totals: dict, character: str = sites.DEFAULT_CHARACTER) -> dict[str, dict]:
    """The strip's five figures by key — one definition, whatever order they are shown in.

    The air-kills cell states how many of those kills a player flew (``N in PvP``) on every
    character except ``pve``: a community that declares it never flies against another player would
    print a zero there, so that cell states its figure alone (:data:`READOUT_ORDER` still shows it).
    """
    air = (int(totals.get("kills_planes") or 0) + int(totals.get("kills_helicopters") or 0))
    pvp_note = (None if str(character or "").strip().lower() == "pve"
                else sentence(i18n._("{count} in PvP"), count(totals.get("pvp"))))
    return {
        "playtime": {"key": "playtime", "value": count(hours(totals.get("playtime"))),
                     "unit": i18n._("h"), "label": i18n._("Playtime"),
                     "note": i18n._("on this site's servers")},
        "sorties": {"key": "sorties", "value": count(totals.get("takeoffs")), "unit": None,
                    "label": i18n._("Sorties"), "note": i18n._("recorded takeoffs")},
        "air_kills": {"key": "air_kills", "value": count(air), "unit": None,
                      "label": i18n._("Air kills"), "note": pvp_note},
        "pvp": {"key": "pvp", "value": count(totals.get("pvp")), "unit": None,
                "label": i18n._("PvP kills"),
                "note": sentence(i18n._("{count} per sortie"),
                                 per_sortie(totals.get("pvp"), totals.get("takeoffs")))},
        "kd": {"key": "kd", "value": ratio(totals.get("kills"), totals.get("deaths")),
               "unit": None, "label": i18n._("K/D"),
               "note": sentence(i18n._("%(count)s deaths"), count(totals.get("deaths")))},
    }


def readouts(totals: dict, character: str = sites.DEFAULT_CHARACTER) -> list[dict]:
    """The strip's five figures, ordered so the DECLARED character's figures lead.

    The set never changes — every character shows playtime, sorties, air kills, PvP kills and the
    K/D ratio — so an operator choosing ``pvp`` changes emphasis, not the record. Only the
    air-kills cell's ``N in PvP`` note follows the character (:func:`_headlines`).
    """
    figures = _headlines(totals, character)
    order = READOUT_ORDER.get(str(character or "").strip().lower(),
                              READOUT_ORDER[sites.DEFAULT_CHARACTER])
    return [figures[key] for key in order]


def discipline(totals: dict) -> dict:
    """The flight-discipline rows, each as a rate of the pilot's own sorties, and the balance.

    ``sorties`` is the recorded takeoff count — the same denominator the community page calls a
    sortie — so a rate answers "how often does one of their sorties end this way". The balance pair
    is the takeoff and landing count, which is what makes a mismatch readable at a glance.
    """
    sorties = totals.get("takeoffs")
    rows = [{"key": key, "label": label, "value": count(totals.get(key)),
             "rate": _share(totals.get(key), sorties)}
            for key, label in (("takeoffs", i18n._("Takeoffs")),
                               ("landings", i18n._("Landings")),
                               ("crashes", i18n._("Crashes")),
                               ("ejections", i18n._("Ejections")))]
    return {"rows": rows,
            "sorties": count(sorties),
            "takeoffs": count(totals.get("takeoffs")),
            "landings": count(totals.get("landings"))}


def _pvp_panel(totals: dict) -> dict:
    """The player-versus-player panel: PvP kills, the PvP ratio and the deaths behind it."""
    return {"id": "pvp", "title": i18n._("PvP record"), "figures": [
        {"label": i18n._("PvP kills"), "value": count(totals.get("pvp")),
         "note": sentence(i18n._("{count} PvP deaths"), count(totals.get("deaths_pvp")))},
        {"label": i18n._("PvP K/D"), "value": ratio(totals.get("pvp"), totals.get("deaths_pvp")),
         "note": sentence(i18n._("{count} total kills"), count(totals.get("kills")))},
    ]}


def _targets_panel(totals: dict) -> dict:
    """The panel for what the pilot destroyed that was not flown by a player."""
    return {"id": "targets", "title": i18n._("Targets hit"), "figures": [
        {"label": i18n._("Air Targets"),
         "value": count((int(totals.get("kills_planes") or 0)
                         + int(totals.get("kills_helicopters") or 0))), "note": None},
        {"label": i18n._("Ground Targets"), "value": count(totals.get("kills_ground")),
         "note": None},
        {"label": i18n._("Air Defence"), "value": count(totals.get("kills_sams")), "note": None},
    ]}


def combat(totals: dict, character: str = sites.DEFAULT_CHARACTER) -> list[dict]:
    """The two combat panels, in the order and at the width the DECLARED character leads with.

    ``pvp`` leads with the player-versus-player panel, ``pve`` with the targets panel, and
    ``pvpve`` shows both side by side — the layout the site's own mockup draws.
    """
    panels = {"pvp": _pvp_panel(totals), "targets": _targets_panel(totals)}
    layout = COMBAT_LAYOUT.get(str(character or "").strip().lower(),
                               COMBAT_LAYOUT[sites.DEFAULT_CHARACTER])
    return [{**panels[panel_id], "span": span} for panel_id, span in layout]


# --------------------------------------------------------------------------------- the rating chart

#: The plot's own box. Geometry is written in percent of it (``viewBox 0 0 100 100`` with
#: ``preserveAspectRatio="none"``), so one path fits the 425 x 104 plot at desktop width and the
#: 320 x 92 one at 430 px; every stroked element carries ``vector-effect: non-scaling-stroke``, so
#: the μ line stays 2 CSS px whatever the box is stretched to.
PLOT_BOX = 100.0

#: A span at or above this many days labels the x axis with month and year instead of day and month.
LONG_SPAN_DAYS = 300

#: The x axis keeps a mid date only inside this band of the plot, so the two end labels never crowd
#: it out.
MID_BAND = (22.0, 78.0)

#: A change smaller than this is reported as flat: below it the reading is noise, not a move.
FLAT_CHANGE = 0.05

#: The three date shapes, one msgid per month. The month is part of the pattern, never a name glued
#: into a template, because a language that declines it writes "май 2025" and "4 мая" differently —
#: and because the page composes dates from the site's own catalog, never through the process
#: locale, so a German page prints German month names whatever the host is set to.
MONTH_YEAR: tuple[str, ...] = (
    i18n._("January {year}"), i18n._("February {year}"), i18n._("March {year}"),
    i18n._("April {year}"), i18n._("May {year}"), i18n._("June {year}"),
    i18n._("July {year}"), i18n._("August {year}"), i18n._("September {year}"),
    i18n._("October {year}"), i18n._("November {year}"), i18n._("December {year}"))

DAY_MONTH: tuple[str, ...] = (
    i18n._("{day} January"), i18n._("{day} February"), i18n._("{day} March"),
    i18n._("{day} April"), i18n._("{day} May"), i18n._("{day} June"),
    i18n._("{day} July"), i18n._("{day} August"), i18n._("{day} September"),
    i18n._("{day} October"), i18n._("{day} November"), i18n._("{day} December"))

FULL_DATE: tuple[str, ...] = (
    i18n._("{day} January {year}"), i18n._("{day} February {year}"),
    i18n._("{day} March {year}"), i18n._("{day} April {year}"),
    i18n._("{day} May {year}"), i18n._("{day} June {year}"),
    i18n._("{day} July {year}"), i18n._("{day} August {year}"),
    i18n._("{day} September {year}"), i18n._("{day} October {year}"),
    i18n._("{day} November {year}"), i18n._("{day} December {year}"))

RATING_HEAD = i18n._("Rating {mu}, confidence plus or minus {sigma}.")
RATING_UP_SINCE = i18n._("Up {delta} points since {date}.")
RATING_DOWN_SINCE = i18n._("Down {delta} points since {date}.")
RATING_SINCE = i18n._("Unchanged since {date}.")
RATING_UP_ACROSS = i18n._("Up {delta} points across this span.")
RATING_DOWN_ACROSS = i18n._("Down {delta} points across this span.")
RATING_FLAT = i18n._("Flat across this span.")
RATING_BAND = i18n._("μ is the expected rating; the band is ±σ, the confidence around it.")
RATING_ONCE = i18n._("Rating {mu}, confidence plus or minus {sigma}, recorded once on {date}. "
                     "No earlier values exist, so there is no trend to draw.")
RATING_ONCE_CAPTION = i18n._("Recorded once — too few for a trend. The bar is ±σ, the confidence "
                             "the plugin holds in this rating.")
RATING_EMPTY = i18n._("No rating update has been recorded yet. The figure above is what the plugin "
                      "holds now.")
RATING_VALUE = i18n._("{mu} μ")
RATING_UPDATES = i18n._("{mu} μ · {count} rating updates")


def _moment(value: Any) -> datetime | None:
    """*value* as a UTC datetime, or ``None`` for a stamp that does not parse."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _recorded(points: list[dict]) -> list[tuple[datetime, float, float]]:
    """The read's points as ``(moment, mu, sigma)``, oldest first; unreadable points are dropped.

    A point without a parsable time or figures cannot be placed on either axis, so it is left out
    rather than drawn at an invented position.
    """
    plotted: list[tuple[datetime, float, float]] = []
    for point in (points or ()):
        moment = _moment(point.get("time"))
        mu_value, sigma_value = point.get("mu"), point.get("sigma")
        if mu_value is None or sigma_value is None:
            continue
        try:
            mu, sigma = float(mu_value), float(sigma_value)
        except (TypeError, ValueError):
            continue
        if moment is not None:
            plotted.append((moment, mu, sigma))
    plotted.sort(key=lambda item: item[0])
    return plotted


def _nice_step(value: float) -> float:
    """The smallest step of ``{1, 2, 2.5, 5} x 10^k`` that is at least *value*."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** int(f"{value:e}".split("e")[1])
    for unit in (1.0, 2.0, 2.5, 5.0, 10.0):
        if unit * magnitude >= value:
            return unit * magnitude
    return 10.0 * magnitude


def y_ticks(low: float, high: float) -> tuple[list[float], float, float]:
    """The three tick values covering ``[low, high]``, and the domain they span.

    Two steps must cover the data, so the step is half the reach rounded up to a nice number. The
    window is then slid onto a whole-number mark (a half, quarter, fifth or tenth of a step) until
    it sits outside the data on both sides. The pad is tried 6%, then 3%, then 0 of the span before
    the step widens, so a history that nearly fills two steps still reads at full height instead of
    being squeezed into the middle of the plot.
    """
    span = (high - low) or 1.0
    for pad_share in (0.06, 0.03, 0.0):
        lo, hi = low - span * pad_share, high + span * pad_share
        step = _nice_step((hi - lo) / 2)
        divisors = sorted((2, 4, 5, 10),
                          key=lambda divisor: (0 if float(step / divisor).is_integer() else 1,
                                               divisor))
        for divisor in divisors:
            mark = step / divisor
            bottom = math.floor(lo / mark) * mark
            if bottom + 2 * step >= hi - 1e-9:
                return [bottom, bottom + step, bottom + 2 * step], bottom, bottom + 2 * step
            lifted = math.ceil((hi - 2 * step) / mark) * mark
            if lifted <= lo + 1e-9:
                return ([lifted, lifted + step, lifted + 2 * step], lifted, lifted + 2 * step)
    bottom = low - span * 0.06
    return [bottom, bottom + span, bottom + 2 * span], bottom, bottom + 2 * span


def tick_text(value: float) -> str:
    """A tick as a figure: never ``-0``, never a trailing zero."""
    return f"{round(value, 1) + 0.0:g}"


def date_label(stamp: datetime, position: float, place: str,
               long_span: bool = False) -> dict:
    """One x-axis label: where it sits, where it is anchored, and the msgid it is composed from.

    The pattern carries the month (:data:`MONTH_YEAR` / :data:`DAY_MONTH`), so the template renders
    the name and the order the request's language wants; ``day`` and ``year`` are plain figures.
    """
    return {
        "at": f"{position:.2f}",
        "place": place,
        "pattern": (MONTH_YEAR if long_span else DAY_MONTH)[stamp.month - 1],
        "day": str(stamp.day),
        "year": str(stamp.year),
    }


def full_date(stamp: datetime, place: str = "mid") -> dict:
    """The full date under a single reading's bar, anchored where *place* says."""
    label = date_label(stamp, PLOT_BOX / 2, place)
    label["pattern"] = FULL_DATE[stamp.month - 1]
    return label


def _move(delta: float, since: bool) -> dict:
    """The sentence a change of *delta* points is reported in, and the msgid behind it."""
    if abs(delta) < FLAT_CHANGE:
        return {"pattern": RATING_SINCE if since else RATING_FLAT, "delta": "0.0"}
    if since:
        pattern = RATING_UP_SINCE if delta > 0 else RATING_DOWN_SINCE
    else:
        pattern = RATING_UP_ACROSS if delta > 0 else RATING_DOWN_ACROSS
    return {"pattern": pattern, "delta": f"{abs(delta):.1f}"}


def rating_chart(points: list[dict]) -> dict | None:
    """The band, the μ polyline, the ticks, the dates and the sentences of a multi-point history.

    ``None`` for fewer than two recorded updates: one reading is the panel's one-point state
    (:func:`rating_single`) and no reading is its empty state, so a single row never draws a line
    that pretends to be a trend.

    x is the point's position in time across the recorded span; y is flipped so 100 is the bottom of
    the box. The band is ONE closed path — the upper edge (μ + σ) oldest to newest, then the lower
    edge (μ − σ) newest back to oldest — so it is one fill and one hairline, not a stack of areas.
    """
    plotted = _recorded(points)
    if len(plotted) < 2:
        return None
    first, last = plotted[0][0], plotted[-1][0]
    span = (last - first).total_seconds() or 1.0
    positions = [(moment - first).total_seconds() / span * PLOT_BOX
                 for moment, _mu, _sigma in plotted]
    ticks, bottom, top = y_ticks(min(mu - sigma for _moment_, mu, sigma in plotted),
                                 max(mu + sigma for _moment_, mu, sigma in plotted))

    def at(value: float) -> float:
        return (top - value) / (top - bottom) * PLOT_BOX

    upper = [(x, at(mu + sigma)) for x, (_m, mu, sigma) in zip(positions, plotted)]
    lower = [(x, at(mu - sigma)) for x, (_m, mu, sigma) in zip(positions, plotted)]
    line = [(x, at(mu)) for x, (_m, mu, _s) in zip(positions, plotted)]

    band = (f"M{upper[0][0]:.2f} {upper[0][1]:.2f} "
            + " ".join(f"L{x:.2f} {y:.2f}" for x, y in upper[1:])
            + f" L{lower[-1][0]:.2f} {lower[-1][1]:.2f} "
            + " ".join(f"L{x:.2f} {y:.2f}" for x, y in reversed(lower[:-1]))
            + " Z")

    long_span = (last - first).days >= LONG_SPAN_DAYS
    indexes = [0]
    if len(plotted) > 2:
        middle = min(range(1, len(plotted) - 1), key=lambda index: abs(positions[index] - 50.0))
        if MID_BAND[0] <= positions[middle] <= MID_BAND[1]:
            indexes.append(middle)
    indexes.append(len(plotted) - 1)
    places = ("first", "mid", "last") if len(indexes) == 3 else ("first", "last")
    delta = plotted[-1][1] - plotted[0][1]
    return {
        "band": band,
        "line": " ".join(f"{x:.2f},{y:.2f}" for x, y in line),
        "domain": {"bottom": bottom, "top": top},
        "ticks": [{"top": f"{at(value):.2f}", "text": tick_text(value)} for value in ticks],
        "labels": [date_label(plotted[index][0], positions[index], place, long_span)
                   for index, place in zip(indexes, places)],
        "point": {"x": f"{positions[-1]:.2f}", "y": f"{at(plotted[-1][1]):.2f}"},
        "aria_head": {"pattern": RATING_HEAD, "mu": f"{plotted[-1][1]:.1f}",
                      "sigma": f"{plotted[-1][2]:.1f}"},
        "aria_move": {**_move(delta, since=True), "date": full_date(first)},
        "caption": {"pattern": RATING_BAND, "move": _move(delta, since=False)},
    }


def rating_single(points: list[dict]) -> dict | None:
    """The one-point state: no band and no line, one ±σ bar, its μ mark, one centred full date.

    ``None`` unless the series holds exactly one readable reading.
    """
    plotted = _recorded(points)
    if len(plotted) != 1:
        return None
    moment, mu, sigma = plotted[0]
    ticks, bottom, top = y_ticks(mu - sigma, mu + sigma)

    def at(value: float) -> float:
        return (top - value) / (top - bottom) * PLOT_BOX

    return {
        "bar": {"top": f"{at(mu + sigma):.2f}",
                "height": f"{at(mu - sigma) - at(mu + sigma):.2f}"},
        "point": {"x": f"{PLOT_BOX / 2:.2f}", "y": f"{at(mu):.2f}"},
        "ticks": [{"top": f"{at(value):.2f}", "text": tick_text(value)} for value in ticks],
        "label": full_date(moment),
        "aria": {"pattern": RATING_ONCE, "mu": f"{mu:.1f}", "sigma": f"{sigma:.1f}",
                 "date": full_date(moment)},
        "caption": RATING_ONCE_CAPTION,
    }


def rating_view(rating: dict | None) -> dict | None:
    """The competitive panel's model from the read's answer, or ``None`` when there is no rating.

    ``None`` is what makes the block VANISH: a site whose database has no ``trueskill`` table
    renders no panel at all, rather than an empty one. The chart is dropped on its own when the
    series is too short, so a plugin without ``trueskill_hist`` still shows the current rating and
    the readout's count is omitted at zero rather than printed beside the empty sentence.
    """
    if not rating:
        return None
    try:
        value = float(rating.get("conservative"))
    except (TypeError, ValueError):
        return None
    try:
        sigma = f"{float(rating.get('sigma')):.1f}"
    except (TypeError, ValueError):
        sigma = "0.0"
    try:
        mu = f"{float(rating.get('mu')):.1f}"
    except (TypeError, ValueError):
        mu = "0.0"
    points = list(rating.get("points") or [])
    total = count(rating.get("count"))
    note = (_record(RATING_UPDATES, mu=mu, count=total) if total != "0"
            else _record(RATING_VALUE, mu=mu))
    return {
        "value": f"{value:.1f}",
        "sigma": sigma,
        "note": note,
        "chart": rating_chart(points),
        "single": rating_single(points),
        "empty": not points,
        "empty_text": RATING_EMPTY,
    }


# --------------------------------------------------------------------------------- the view

def view(resolved: Any, profile: dict) -> dict:
    """The page's whole model from the read's own answer — pure, so a test can pin it."""
    found = bool(profile.get("found"))
    totals = dict(profile.get("totals") or {})
    ucid = str(profile.get("ucid") or "")
    character = _character(resolved)
    aircraft = profile.get("per_aircraft") or []
    return {
        "found": found,
        "not_found": False,
        "unavailable": False,
        "name": profile.get("name") or "",
        "last_flown": last_flown(profile.get("recent") or []),
        "discord_linked": int(profile.get("discord_id") or -1) > 0,
        "character": character,
        "character_line": CHARACTER_LINES.get(character, CHARACTER_LINES["pvpve"]),
        "readouts": readouts(totals, character),
        "combat": combat(totals, character),
        "discipline": discipline(totals),
        "rating": rating_view(profile.get("rating")),
        "search_href": site_url(resolved, SEARCH_PATH),
        "leaderboard_href": site_url(resolved, NAV_PATHS["leaderboard"]),
        "airframes": airframes(aircraft, totals.get("playtime")),
        "most_flown": most_flown(aircraft),
        "servers": servers(profile.get("per_server") or []),
        "recent": recent(profile.get("recent") or []),
        "linked": linked(profile.get("ucids") or [], resolved, ucid),
    }


# --------------------------------------------------------------------------------- the read

def _empty_model(resolved: Any) -> dict:
    """The model of a profile that does not exist on this site: no figures, no links."""
    return view(resolved, {})


def _not_found(resolved: Any) -> dict:
    """The model of the not-found page: the page's own copy, no figure and no name."""
    model = _empty_model(resolved)
    model.update({"not_found": True})
    return model


# --------------------------------------------------------------------------------- the route

def add_route(router: APIRouter, site: Any) -> str:
    """Serve one pilot's profile at ``<site>/player/<ucid>`` and return the registered path."""
    path = site_url(site, f"{PLAYER_PATH}/{{ucid}}")

    async def site_player(request: Request, ucid: str) -> HTMLResponse:
        resolved = resolve_site(request, site)
        db = pool()
        if db is None:
            model = _empty_model(resolved)
            model.update({"unavailable": True})
            return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                    model=model)
        profile = await site_reads.pilot_profile(resolved, db, ucid)
        if not profile.get("found"):
            return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                    model=_not_found(resolved), status_code=404)
        model = view(resolved, profile)
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                model=model)

    router.add_api_route(path, site_player, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-player-{site.key}")
    return path
