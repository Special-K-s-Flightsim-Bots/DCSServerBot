"""The public site's fleet: the Servers list and the page one server opens.

EVERY FIGURE COMES FROM ``site_reads``. The list runs :func:`~services.webservice.site_reads.server_list`
once; the per-server page runs :func:`~services.webservice.site_reads.server_detail`, which is that
same read plus the server's inventory. Nothing is queried here and no figure is typed by hand.

THE LIVE BLOCKS ARE CACHED, NEVER AWAITED. Weather and extensions are RPCs to the game server and
the weekly schedule is the scheduler plugin's own per-server config, so
:func:`services.webservice.site_reads.live_read` answers the snapshot this process already holds and
refreshes it in the background (300 s, the bot's own ``getFog`` window) — a render never reaches the
plugin. The page prints the block's state word when there is nothing to read — one sentence for
"not running", one for a failed read and one for the first view — never empty instruments. A BLOCK
states its own failure: the weather is drawn from the mission whether or not the node answers, and an
extensions render that did not return says so while the weather cells stay on the page. The schedule
is the one block a stopped server keeps: it is config, not a live figure.

THE EXTENSION TILES CARRY NO CREDENTIAL AND NO MARKUP. ``render_extensions(public=True)`` already
builds each value without its credential lines; this module flattens what it hands over into plain
rows (a Discord ``- `` bullet, ``**bold**`` marks and the like never reach the page), reads the
withheld count as a chip, and never filters assembled text for a word — the suppression is the
extension's own answer.

PRESENTATION IS COMPOSED HERE, NOT IN THE TEMPLATE: the state word and its dot, the clocks, the
side bars' widths, the empty and not-found states and the unit conversions are all decided in
:func:`view` / :func:`detail_view`, so the templates only read and the same figures can be asserted
without parsing HTML.

BOTH ADDRESSES ARE LOW-PRIORITY PUBLIC ROUTES, built through
:func:`services.webservice.site_pages.server_url` — the list at the ``servers`` nav entry's path and
one server as the same path plus one encoded name segment. A name that is not this site's server
answers this page's own not-found state; another site's server is never rendered here.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import i18n, sites, site_pages, site_reads
from ..site_pages import (NAV_PATHS, SERVER_PATH, render_site_page, resolve_site, server_url,
                          site_url)

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "DETAIL_TEMPLATE",
    "LIVE_PATH",
    "STATE_WORDS",
    "DEFAULT_STATE",
    "LIMIT_LABELS",
    "LIVE_NOT_RUNNING",
    "LIVE_FAILED",
    "LIVE_PENDING",
    "WEATHER_EMPTY",
    "NO_EXTENSIONS",
    "NO_MISSION",
    "NO_MAP",
    "NO_SLOTS",
    "COALITIONS_NOTE",
    "META_UP",
    "META_FLYING",
    "NOT_FOUND",
    "WIND_LEVELS",
    "SCHEDULE_DAYS",
    "SCHEDULE_STATES",
    "SCHEDULE_FAILED",
    "clock",
    "count",
    "side",
    "plain_lines",
    "cloud_cell",
    "schedule_view",
    "view",
    "live_cells",
    "detail_view",
    "add_route",
    "detail_route",
    "live_route",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS`` (its path inside a site is that table's ``"/fleet"``).
NAV_ID = "servers"

TEMPLATE = "site_servers.html"

DETAIL_TEMPLATE = "site_server.html"

#: the path (inside the site) of the fleet's live cells: the JSON the list page polls, declared
#: with every other site address in :mod:`services.webservice.site_pages`.
LIVE_PATH = site_pages.FLEET_LIVE_PATH

#: the query parameter that narrows the live answer to ONE server (the per-server page's poll).
SERVER_PARAM = "server"

#: the attribute that states, on a rendered page, the live state it was drawn from: the poll reads
#: it on the client and answers whether a page rendered before the read landed should be taken
#: again once it has.
LIVE_STATE_ATTR = "data-live-state"

#: the state vocabulary of the SITE's strip: raw status -> (word msgid, dot class). The classes are
#: ``site.css``' own (``.dot.pulse`` plus the four ``.dot.st-*`` marks); a status that folds into a
#: dead process keeps the stopped dot, so a reader can tell "off" from "the read broke".
STATE_WORDS: dict[str, tuple[str, str]] = {
    "running": (i18n._("Running"), "pulse"),
    "paused": (i18n._("Paused"), "st-paused"),
    "loading": (i18n._("Loading"), "st-loading"),
    "stopped": (i18n._("Stopped"), "st-stopped"),
    "shutdown": (i18n._("Stopped"), "st-stopped"),
    "shutting down": (i18n._("Stopped"), "st-stopped"),
    "unregistered": (i18n._("Unregistered"), "st-unreg"),
}

#: what a status nobody mapped renders as (never blank, never an exception).
DEFAULT_STATE: tuple[str, str] = (i18n._("Unknown"), "st-stopped")

#: the client-limit switch labels, by their ``settings`` key. One marker per literal, declared where
#: the page uses it, so the extractor sees a string and a catalog entry can only be the flag it is.
LIMIT_LABELS: dict[str, str] = {
    "allow_trial_only_clients": i18n._("No trial clients"),
    "require_pure_clients": i18n._("Pure clients"),
    "require_pure_scripts": i18n._("Pure scripts"),
    "require_pure_models": i18n._("Pure models"),
    "require_pure_textures": i18n._("Pure textures"),
}

#: The four sentences a live block can state instead of figures. One owner each, so a test can assert
#: the copy and a translation has one entry.
LIVE_NOT_RUNNING = i18n._("The server is not running, so there is no live report to read.")
LIVE_FAILED = i18n._("The server did not answer the live read, so there is no live report right now.")
LIVE_PENDING = i18n._("The live read has not answered yet; this page will show it once it has.")
WEATHER_EMPTY = i18n._("The mission reports no weather.")
NO_EXTENSIONS = i18n._("No extension publishes anything for this server.")

#: the per-server page's not-found sentence.
NOT_FOUND = i18n._("That server was not found on this site.")

#: The fallback wording the LIST prints in an empty cell, declared here so the first render and the
#: polled JSON cannot disagree. Each msgid is the template's own, so no catalog entry is added.
NO_MISSION = i18n._("No mission")
NO_MAP = i18n._("no map")
COALITIONS_NOTE = i18n._("Coalitions — seats are assigned by role")
NO_SLOTS = i18n._("No slots — not in a mission")

#: the list page's two headline sentences, as the page writes them: the same msgids the template
#: renders, so a polled update prints the sentence it replaced.
META_UP = i18n._("%(up)s of %(total)s up")
META_FLYING = i18n._("%(count)s pilots in the air")

#: the per-server page's sentence when the statistics database is not reachable yet. A missing
#: database is not "no such server": the address answers, and the page says what it cannot read.
DB_UNAVAILABLE = i18n._("The statistics database is not available yet, so this page shows no figures.")

#: the wind levels the instrument row prints, as ``(payload key, label)``. The first is a word; the
#: other two are altitudes, printed as numbers with the unit the embed uses.
WIND_LEVELS: tuple[tuple[str, str], ...] = (("ground", i18n._("Ground")), ("low", "6,600 ft"),
                                            ("high", "26,000 ft"))

#: a line that reads as a server address or a URL: a scheme, or a ``host:port`` pair.
_ADDRESS = re.compile(r"^(?:https?://|[\w.\-]+:\d+)")
_BULLET = re.compile(r"^(?:[-•·]\s+)")


# -------------------------------------------------------------------------------- arithmetic

def count(value: Any) -> str:
    """A whole figure with thousands separators; anything unreadable is ``0``."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def clock(seconds: Any) -> str:
    """A duration as ``H:MM:SS``; ``None`` (or anything unreadable) is the page's dash."""
    if seconds is None:
        return "—"
    try:
        total = max(int(seconds), 0)
    except (TypeError, ValueError):
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def side(entry: dict) -> dict:
    """One coalition's slot cell: its text, its share of the seats, and whether it has a figure."""
    current = max(int(entry.get("current") or 0), 0)
    total = max(int(entry.get("total") or 0), 0)
    return {
        "current": current,
        "total": total,
        "text": f"{current} / {total}" if total else "",
        "share": round(current * 100.0 / total, 1) if total else 0.0,
        "has_figure": bool(total),
    }


def plain_lines(value: Any) -> list[str]:
    """An extension value as plain rows: bullets stripped, Discord marks dropped, blank lines gone.

    The extensions write Discord-flavoured text (``- 3 s playback delay``, ``**Remote Ctrl [42675]**``)
    and the public tile must not print a markdown mark. A leading bullet becomes the line's own row,
    ``**``/``__`` pairs are removed, and every other character is kept — including a glyph an
    extension uses as its own icon, which reads as one on the page.
    """
    rows: list[str] = []
    for raw in str(value or "").splitlines():
        line = _BULLET.sub("", raw.strip())
        line = line.replace("**", "").replace("__", "").strip()
        if line:
            rows.append(line)
    return rows


def _address_line(line: str) -> bool:
    """Whether a tile line reads as an address, so it wears the tile's mono row."""
    return bool(_ADDRESS.match(line))


# -------------------------------------------------------------------------------- the weather

def _wind(entry: Any) -> str:
    """One wind level as the embed prints it (``340° / 08 kts``); ``''`` when it was not reported.

    The mission stores the direction the wind blows TOWARD; the page reports the one it blows FROM
    (offset by 180°, the embed's own arithmetic) and converts m/s to knots with the bot's constant.
    """
    if not isinstance(entry, dict):
        return ""
    from core import const
    direction = float(entry.get("dir") or 0) + 180
    speed = float(entry.get("speed") or 0) * const.METER_PER_SECOND_IN_KNOTS + 0.5
    return f"{int(direction) % 360}° / {int(speed):02d} kts"


def _visibility(distance: Any) -> tuple[str, str]:
    """A visibility in metres as ``(figure, note)`` — the embed's two renderings."""
    from core import const
    try:
        metres = float(distance)
    except (TypeError, ValueError):
        return "", ""
    if metres >= 30000:
        return "10 km", "6 SM (+)"
    return f"{int(metres):,} m", f"{metres / const.METERS_IN_SM:.2f} SM"


def _qnh(mmhg: Any) -> tuple[str, str]:
    """``(inHg, hPa)`` for the mission's altimeter setting, or ``("", "")``."""
    from core import const
    try:
        value = float(mmhg)
    except (TypeError, ValueError):
        return "", ""
    return f"{value * const.MMHG_IN_INHG:.2f} inHg", f"{int(value * const.MMHG_IN_HPA)} hPa"


def cloud_cell(preset: str, density: Any, clouds: dict) -> tuple[str, str]:
    """The clouds cell as ``(value, note)``, with ``plugins/mission/status.WeatherInfo``' own rule.

    Three cases, in that order: a PRESET prints the preset's readable name and the cloudbase in the
    note; no preset with ``density == 0`` prints "Clear"; anything else prints "Dynamic" and puts
    the base, the density and the thickness in the note. The value never carries a figure twice.
    """
    from core import const
    if preset:
        base = clouds.get("base")
        note = (f"cloudbase {int(float(base) * const.METER_IN_FEET + 0.5):,} ft"
                if base is not None else "")
        return preset, note
    if density == 0:
        return i18n._("Clear"), ""
    parts: list[str] = []
    base = clouds.get("base")
    if base is not None:
        parts.append(f"{i18n._('Base')}: {int(float(base) * const.METER_IN_FEET + 0.5):,} ft")
    if density is not None:
        parts.append(f"{i18n._('Density')}: {density}/10")
    thickness = clouds.get("thickness")
    if thickness is not None:
        parts.append(f"{i18n._('Thickness')}: "
                     f"{int(float(thickness) * const.METER_IN_FEET + 0.5):,} ft")
    return i18n._("Dynamic"), " · ".join(parts)


def weather_view(payload: Any) -> dict:
    """The weather block's model: its state, its five instrument cells and its three wind rows."""
    if not isinstance(payload, dict) or not payload:
        return {"state": "empty", "sentence": WEATHER_EMPTY, "cells": [], "wind": [],
                "empty": True}
    raw_clouds = payload.get("clouds")
    clouds = raw_clouds if isinstance(raw_clouds, dict) else {}
    temperature = payload.get("temperature")
    temperature_text = f"{float(temperature):.1f} °C" if temperature is not None else ""
    qnh_value, qnh_note = _qnh(payload.get("qnh_mmhg"))
    preset = str(clouds.get("preset") or "")
    density = clouds.get("density")
    cloud_text, cloud_note = cloud_cell(preset, density, clouds)
    visibility_value, visibility_note = _visibility(payload.get("visibility"))
    raw_wind = payload.get("wind")
    wind = raw_wind if isinstance(raw_wind, dict) else {}
    cells = [
        {"key": "temperature", "label": i18n._("Temperature"), "value": temperature_text,
         "note": ""},
        {"key": "qnh", "label": i18n._("QNH"), "value": qnh_value, "note": qnh_note},
        {"key": "clouds", "label": i18n._("Clouds"), "value": cloud_text, "note": cloud_note},
        {"key": "visibility", "label": i18n._("Visibility"), "value": visibility_value,
         "note": visibility_note},
    ]
    rows = [{"label": label, "value": _wind(wind.get(key))} for key, label in WIND_LEVELS]
    return {"state": "ready", "sentence": "", "cells": cells, "wind": rows,
            "empty": not any(cell["value"] for cell in cells) and not any(row["value"]
                                                                          for row in rows)}


# -------------------------------------------------------------------------------- the schedule

#: The week's day cells, Monday first — the order the file's 7-character pattern uses.
SCHEDULE_DAYS: tuple[str, ...] = (i18n._("Mon"), i18n._("Tue"), i18n._("Wed"), i18n._("Thu"),
                                  i18n._("Fri"), i18n._("Sat"), i18n._("Sun"))

#: One entry per letter a day cell can carry: ``(class, word)``. ``Y`` runs, ``P`` shuts down with no
#: players, ``N`` off. The class is the page's own — ``on`` / ``idle`` / ``off`` — and the word is
#: both the cell's title and its legend row, so no state is carried by colour alone.
SCHEDULE_STATES: dict[str, tuple[str, str]] = {
    "Y": ("on", i18n._("runs")),
    "P": ("idle", i18n._("shuts down with no players")),
    "N": ("off", i18n._("off")),
}

#: the letters the legend names, in the order the mockup draws it.
SCHEDULE_LEGEND_ORDER: tuple[str, ...] = ("Y", "P", "N")

#: the block's one sentence when the read could not take the plugin's config apart.
SCHEDULE_FAILED = i18n._("The weekly schedule could not be read.")


def _period(period: Any) -> str:
    """A schedule period key as the page prints it: ``00-24`` becomes ``00:00 – 24:00``."""
    text = str(period or "").strip()
    start, _, end = text.partition("-")
    try:
        return f"{int(start):02d}:00 – {int(end):02d}:00"
    except (TypeError, ValueError):
        return text


def _week_sentence(days: str) -> str:
    """The week of one period as one sentence (``Mon runs, Tue runs, …``), for the block's label."""
    words = []
    for index, label in enumerate(SCHEDULE_DAYS):
        letter = days[index] if index < len(days) else "N"
        words.append(f"{label} {SCHEDULE_STATES.get(letter, SCHEDULE_STATES['N'])[1]}")
    return i18n._("The week: %(days)s.") % {"days": ", ".join(words)}


def schedule_view(payload: Any) -> dict:
    """The weekly-schedule block's model: whether it draws, its period rows, its legend.

    No block at all when the read states no schedule (no plugin, no ``schedule``, or the trivial
    always-on week). A read that failed draws its one sentence and no grid — never an empty week.
    """
    hidden = {"shown": False, "failed": False, "rows": [], "legend": [], "timezone": "",
              "sentence": ""}
    if not isinstance(payload, dict):
        return hidden
    state = payload.get("state")
    if state == site_reads.SCHEDULE_FAILED:
        return {**hidden, "shown": True, "failed": True, "sentence": SCHEDULE_FAILED}
    if state != site_reads.SCHEDULE_READY:
        return hidden
    rows: list[dict] = []
    for row in payload.get("rows") or ():
        if not isinstance(row, dict):
            continue
        days = str(row.get("days") or "")
        cells = []
        for index, label in enumerate(SCHEDULE_DAYS):
            letter = days[index] if index < len(days) else "N"
            cell_class, word = SCHEDULE_STATES.get(letter, SCHEDULE_STATES["N"])
            cells.append({"day": label, "state": cell_class, "word": word})
        rows.append({"period": _period(row.get("period")), "cells": cells,
                     "aria": _week_sentence(days)})
    if not rows:
        return {**hidden, "shown": True, "failed": True, "sentence": SCHEDULE_FAILED}
    legend = [{"state": SCHEDULE_STATES[letter][0], "word": SCHEDULE_STATES[letter][1]}
              for letter in SCHEDULE_LEGEND_ORDER]
    return {"shown": True, "failed": False, "rows": rows, "legend": legend,
            "timezone": str(payload.get("timezone") or ""), "sentence": ""}


# -------------------------------------------------------------------------------- the tiles

def tile(entry: dict) -> dict:
    """One extension tile: name, version, its plain rows and the withheld chip's count."""
    rows = plain_lines(entry.get("value"))
    return {
        "name": entry.get("name") or "",
        "version": entry.get("version") or "",
        "rows": [{"text": line, "mono": _address_line(line)} for line in rows],
        "withheld": max(int(entry.get("withheld") or 0), 0),
    }


def extensions_view(payload: Any) -> dict:
    """The extensions block's model: its state, its tiles, and the one sentence when it has none."""
    if not isinstance(payload, dict) or not payload:
        return {"state": "empty", "sentence": "", "tiles": [], "empty": True}
    tiles = [tile(entry) for entry in payload.get("extensions") or () if isinstance(entry, dict)]
    return {"state": "ready", "sentence": "", "tiles": tiles, "empty": not tiles}


# -------------------------------------------------------------------------------- the views

def _row(row: dict, resolved: Any) -> dict:
    """One strip's model from the read's row: the state word, the clocks, the bars and the link."""
    word, dot = STATE_WORDS.get(row.get("status") or "", DEFAULT_STATE)
    return {
        "name": row.get("name") or "",
        "href": server_url(resolved, row.get("name")) if resolved is not None else "",
        "state": word,
        "dot": dot,
        "online": bool(row.get("online")),
        "locked": bool(row.get("locked")),
        "players": count(row.get("players")),
        "players_count": int(row.get("players") or 0),
        "max_players": count(row.get("max_players") or 0) if row.get("max_players") else "",
        "mission": row.get("mission") or "",
        "map": row.get("map") or "",
        "runtime": clock(row.get("runtime")),
        "restart": clock(row.get("restart_in")),
        "restart_hot": row.get("restart_in") is not None,
        "blue": side(row.get("blue") or {}),
        "red": side(row.get("red") or {}),
        "coalitions": bool(row.get("coalitions")),
        "in_mission": bool(row.get("mission")),
        "address": row.get("address") or "",
    }


def view(rows: list[dict], resolved: Any = None) -> dict:
    """The list page's whole model from the read's own rows — pure, so a test can pin it."""
    fleet = [_row(row, resolved) for row in (rows or [])]
    return {
        "rows": fleet,
        "total": count(len(fleet)),
        "up": count(sum(1 for row in fleet if row["online"])),
        "flying": count(sum(row["players_count"] for row in fleet)),
        "empty": not fleet,
    }


def live_cells(rows: list[dict], resolved: Any = None, say: Any = None) -> dict:
    """The fleet's fast-changing cells, composed ONCE for both faces of the page.

    The list renders these through Jinja and the polled JSON carries the very same values, so a
    refresh cannot disagree with the first paint about a word, a unit or a fallback sentence.
    ``say`` translates a msgid for the caller's request (Jinja does it for the page, and the JSON
    route passes the request's own ``gettext``); the default is the identity.
    """
    translate = say or (lambda message: message)
    fleet = view(rows, resolved)
    cells: list[dict] = []
    for row in fleet["rows"]:
        if row["coalitions"]:
            slots = "coalitions"
        elif row["blue"]["has_figure"] or row["red"]["has_figure"]:
            slots = "sides"
        else:
            slots = "none"
        cells.append({
            "name": row["name"],
            "state": translate(row["state"]),
            "dot": row["dot"],
            "online": row["online"],
            "locked": row["locked"],
            "players": row["players"],
            "in_mission": row["in_mission"],
            "mission": row["mission"] if row["in_mission"] else translate(NO_MISSION),
            "map": row["map"] or translate(NO_MAP),
            "runtime": row["runtime"],
            "restart": row["restart"],
            "restart_hot": row["restart_hot"],
            "slots": slots,
            "blue_text": row["blue"]["text"],
            "blue_share": row["blue"]["share"],
            "red_text": row["red"]["text"],
            "red_share": row["red"]["share"],
        })
    return {
        "up": translate(META_UP) % {"up": fleet["up"], "total": fleet["total"]},
        "flying": translate(META_FLYING) % {"count": fleet["flying"]},
        "labels": {"no_mission": translate(NO_MISSION), "no_map": translate(NO_MAP),
                   "coalitions": translate(COALITIONS_NOTE), "no_slots": translate(NO_SLOTS)},
        "rows": cells,
    }


def _limits(items: Any) -> list[dict]:
    """The client limits as the page's ticked rows, translated labels included."""
    return [{"label": LIMIT_LABELS.get(item.get("key"), item.get("key") or ""),
             "enabled": bool(item.get("enabled"))} for item in (items or [])]


def _updated(value: Any) -> str:
    """The read's ISO timestamp as ``21 Nov 2026 · 14:22:31 UTC``; unchanged when unreadable."""
    text = str(value or "")
    try:
        from datetime import datetime
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return text
    return f"{moment:%d %b %Y} · {moment:%H:%M:%S} UTC"


def detail_view(answer: dict, resolved: Any = None, live: Any = None) -> dict:
    """The per-server page's model from ``server_detail``'s answer and the cached live read."""
    if not answer.get("found"):
        return {"found": False}
    server = answer.get("server") or {}
    row = _row(server, resolved)
    live = live if isinstance(live, dict) else {}
    state = live.get("state") or "pending"
    # THE ROSTER DECIDES WHETHER A LIVE FIGURE CAN EXIST AT ALL. A cached read says what it saw at
    # the moment it ran: a server that has started since then is not "down", and a stopped one is not
    # "ready". Serving the stale half printed "the server is not running" over a running server.
    if not row["online"]:
        state = "down"
    elif state == "down":
        state = "pending"
    sentence = {"down": LIVE_NOT_RUNNING, "failed": LIVE_FAILED,
                "pending": LIVE_PENDING}.get(state, "")
    # a block whose own read did not answer states THAT, even while the rest of the payload is
    # ready: the weather is drawn from the mission and the extensions are an RPC of their own.
    weather_failed = state == "ready" and live.get("weather_state") == site_reads.BLOCK_FAILED
    extensions_failed = state == "ready" and live.get("extensions_state") == site_reads.BLOCK_FAILED
    if state != "ready":
        weather = weather_view(None)
        weather_sentence = sentence or weather["sentence"]
    else:
        weather = weather_view(live.get("weather"))
        if weather["state"] == "ready":
            weather_sentence = ""
        elif weather_failed:
            weather_sentence = LIVE_FAILED
        else:
            weather_sentence = weather["sentence"]
    # the schedule is the scheduler plugin's config, read in the same cached live read: it states
    # itself whether the server is up or not, so it is taken from the payload, not from its state
    schedule = schedule_view(live.get("schedule"))
    if state != "ready":
        extensions = extensions_view(None)
        extension_sentence = sentence
    else:
        extensions = extensions_view({"extensions": live.get("extensions")})
        if extensions_failed:
            extension_sentence = LIVE_FAILED
        elif extensions["tiles"]:
            extension_sentence = ""
        else:
            extension_sentence = NO_EXTENSIONS
    limits = _limits(answer.get("limits"))
    blue, red = row["blue"], row["red"]
    total = blue["total"] + red["total"]
    used = blue["current"] + red["current"]
    return {
        "found": True,
        "name": row["name"],
        "state": row["state"],
        "dot": row["dot"],
        "online": row["online"],
        "address": row["address"],
        "locked": row["locked"],
        # the join password, ONLY when the server's own show_web_passwords is on — "" otherwise, and
        # the page then keeps its lock chip and its sentence. Read from the read's own row: the
        # view model above carries the lock, not the value.
        "password": server.get("password") or "",
        "blue": blue,
        "red": red,
        "coalitions": row["coalitions"],
        "slots": ({"used": count(used), "total": count(total),
                   "text": f"{used} / {total}"} if total else None),
        "mission": row["mission"],
        "map": row["map"],
        "mission_date": (answer.get("mission_clock") or {}).get("date") or "",
        "mission_time": (answer.get("mission_clock") or {}).get("time") or "",
        "runtime": row["runtime"],
        "restart": row["restart"],
        "limits": limits,
        "limits_in_force": [limit for limit in limits if limit["enabled"]],
        "dcs_version": answer.get("dcs_version") or "",
        "bot_version": answer.get("bot_version") or "",
        "updated": _updated(answer.get("updated")),
        "back_href": site_url(resolved, SERVER_PATH) if resolved is not None else "",
        "weather": weather,
        "weather_sentence": weather_sentence,
        "schedule": schedule,
        "extensions": extensions,
        "extensions_sentence": extension_sentence,
        "live_state": state,
    }


def _not_found(name: str) -> dict:
    """The model of a name this site does not carry: the sentence and the name as typed."""
    return {"found": False, "name": name, "sentence": NOT_FOUND}


# -------------------------------------------------------------------------------- the routes

def add_route(router: APIRouter, site: Any) -> str:
    """Serve the fleet list at *site*'s ``servers`` address and return the registered path."""
    path = site_url(site, SERVER_PATH)

    async def site_servers(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        db = site_pages.pool()
        rows = await site_reads.server_list(resolved, db) if db is not None else []
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                live_href=site_url(resolved, LIVE_PATH),
                                model=view(rows, resolved))

    router.add_api_route(path, site_servers, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-servers-{site.key}")
    return path


def detail_route(router: APIRouter, site: Any) -> str:
    """Serve one server's page at the list's path plus one encoded name segment."""
    path = site_url(site, f"{SERVER_PATH}/{{name}}")

    async def site_server(request: Request, name: str) -> HTMLResponse:
        resolved = resolve_site(request, site)
        db = site_pages.pool()
        back = site_url(resolved, SERVER_PATH)
        if db is None:
            model = {"found": False, "name": name, "sentence": DB_UNAVAILABLE}
            return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=model, back_href=back)
        answer = await site_reads.server_detail(resolved, db, None, name)
        if not answer.get("found"):
            return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=_not_found(name),
                                    back_href=back, status_code=404)
        server = sites.live_servers().get(answer["name"])
        model = detail_view(answer, resolved, site_reads.live_read(server))
        return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                resolved=resolved, live_href=site_url(resolved, LIVE_PATH),
                                model=model)

    router.add_api_route(path, site_server, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-server-{site.key}")
    return path


def live_route(router: APIRouter, site: Any) -> str:
    """Serve the fleet's live cells as JSON at *site*'s :data:`LIVE_PATH`.

    Composed by :func:`live_cells` — the same function the page's first paint uses — so a polled
    answer and the rendered one cannot drift. Roster only: no statement, no RPC, which is why a
    visitor's browser may ask for it every few seconds.

    ``?server=<name>`` narrows the answer to ONE server of this site and adds the state of its
    cached live read (weather, extensions, schedule), which is what the per-server page polls: it
    repaints its own strip and, when the page was rendered before the live read had answered, lets
    the browser take the finished page instead of asking the visitor to reload.
    """
    path = site_url(site, LIVE_PATH)

    async def site_fleet_live(request: Request) -> JSONResponse:
        resolved = resolve_site(request, site)
        rows = site_reads.fleet_live(resolved)
        answer = live_cells(rows, resolved,
                            say=lambda message: i18n.translate(request, message))
        wanted = str(request.query_params.get(SERVER_PARAM) or "").strip()
        if not wanted:
            # THE FLEET VIEW WARMS EVERY SERVER IT SHOWS, so the first visit to one of their pages
            # is not the one that pays its live read (weather, extensions).
            site_reads.prefetch_live({row["name"]: sites.live_servers().get(row["name"])
                                      for row in rows})
            return JSONResponse(answer, headers={"Cache-Control": "no-store"})
        cell = next((row for row in answer["rows"] if row["name"] == wanted), None)
        if cell is None:
            # a name this site does not carry is not server data: 404, exactly as the page answers
            return JSONResponse({"found": False}, status_code=404,
                                headers={"Cache-Control": "no-store"})
        live = site_reads.live_read(sites.live_servers().get(wanted))
        return JSONResponse({"found": True, "row": cell, "live_state": live.get("state") or "",
                             "live_entries": len(live.get("extensions") or ())},
                            headers={"Cache-Control": "no-store"})

    router.add_api_route(path, site_fleet_live, methods=["GET"], response_class=JSONResponse,
                         name=f"site-fleet-live-{site.key}")
    return path
