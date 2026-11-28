"""The public site's carrier pages: the trap-grid board, one pilot's record, one pass's trap sheet.

THE BOARD'S X-AXIS IS THE NUMBER OF TRAPS. :func:`view` composes one row per pilot from
:func:`~services.webservice.site_reads.carrier_board`'s answer and one column per TRAP: column *n* is
a pilot's n-th pass on these servers, oldest on the left. Two pilots side by side therefore show the
same ordinal rather than the same day, exactly like the deck's own board, and a quiet month leaves no
hole in the grid. Each cell draws the one pass it stands for — a night pass carries the black dot —
and its tooltip carries the day it was flown, so the calendar is still readable without becoming the
axis. A pilot with fewer passes than the board's width leaves the remaining cells blank.

THE GRADES ARE THE PLUGIN'S VOCABULARY. A tile's colour, a chip's colour and the legend's points are
read from ``plugins.greenieboard.const.GRADES`` at render time — never copied into this module — so
the site and the Discord board cannot disagree about what a colour means. The colour is written
inline on the element by :func:`grade`, which is the one place a colour literal appears on the page:
the stylesheet carries tokens only.

WHO MAY SEE A TRAP SHEET: THE PILOT THE PASS BELONGS TO, AND NOBODY ELSE. The sheet route answers an
image only for the signed-in viewer's own pass on this site's servers and answers the site's
not-found page for every other request, so it never confirms that a trap id exists. The public
record therefore draws no sheet and no link to the sheet route at all; the sheets are shown on the
viewer's OWN page (``pages/site_me.py``), reached from a session that is already identified.

THE GATES ARE TWO. The site's menu has to link to the page — the tick in the console's Menu block is
the only control, there is no separate switch (``sites.Site.page_enabled``) — and the GreenieBoard
plugin has to be loaded, because nothing else writes the ``traps`` table this page draws. With either
answer missing, the menu carries no entry and all three routes answer the not-found page without
running a read. With both, a database that holds no trap yet renders ONE sentence instead of an empty
grid.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .. import i18n, site_reads
from ..site_pages import (CARRIER_PATH, CARRIER_TRAP_PATH, ME_PATH, carrier_url, identity, pool,
                          page_offered, render_site_page, resolve_site, site_url, trap_sheet_url)
from . import site_leaderboard

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "RECORD_TEMPLATE",
    "AIRFRAME_PARAM",
    "CARRIER_PAGE_PARAM",
    "AIRFRAME_ALL",
    "SERVER_PARAM",
    "SERVER_ALL",
    "LEGEND_GRADES",
    "EMPTY",
    "NOT_FOUND",
    "UNAVAILABLE",
    "SECTION_EMPTY",
    "BADGE_LABELS",
    "count",
    "average",
    "grade",
    "legend",
    "day_label",
    "cells",
    "rows",
    "airframe_options",
    "server_options",
    "view",
    "detail_view",
    "trap_rows",
    "selected_pass",
    "section",
    "add_route",
    "detail_route",
    "sheet_route",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS`` — the menu's ``Carrier`` entry, and the id the site's
#: ``pages.carrier`` switch is keyed by.
NAV_ID = "carrier"

TEMPLATE = "site_carrier.html"

RECORD_TEMPLATE = "site_carrier_record.html"

#: the query parameters of the board's two controls, and the value meaning "no narrowing".
AIRFRAME_PARAM = "airframe"
AIRFRAME_ALL = "all"
SERVER_PARAM = site_leaderboard.SERVER_PARAM
SERVER_ALL = site_leaderboard.SERVER_ALL

#: the query parameter the viewer's own carrier section pages its list with.
CARRIER_PAGE_PARAM = "carrier_page"

#: the grades the legend names, in the plugin's own order of severity. ``NC`` (no count) and the
#: night marker are vocabulary rather than grades a board draws, so neither is a legend entry.
LEGEND_GRADES: tuple[str, ...] = ("_OK_", "OK", "(OK)", "B", "--", "WO", "C")

#: The board's one sentence when this database has never recorded a trap, and the record page's
#: not-found sentence. Both are printed by a page whose switch is ON.
EMPTY = i18n._("No carrier qualification has been recorded here yet.")

NOT_FOUND = i18n._("That pilot has no carrier record on this site.")

#: The board's sentence when this process holds no database yet.
UNAVAILABLE = i18n._("The statistics database is not available yet, so this page shows no figures.")

#: The viewer's own page: no pass recorded for this account on this site's servers (the plugin is
#: active — its absence removes the section instead of emptying it).
SECTION_EMPTY = i18n._("No carrier pass has been recorded for you on this site's servers yet.")

#: The six badges' NAMES, keyed by the id :data:`~services.webservice.site_reads.CARRIER_ACHIEVEMENTS`
#: carries. The rule (and its threshold) belongs to the read; the word belongs here, where the
#: extractor can see it and a locale can translate it.
BADGE_LABELS: dict[str, str] = {
    "first": i18n._("First trap"),
    "century": i18n._("Century"),
    "perfect": i18n._("Perfect pass"),
    "night": i18n._("Night qual"),
    "wires": i18n._("Every wire"),
    "clean": i18n._("Clean fifty"),
}


# --------------------------------------------------------------------------------- the grades

def _specs() -> dict[str, dict]:
    """``plugins.greenieboard.const.GRADES`` — the one grade vocabulary, imported where it is used.

    ``{}`` on a build without that plugin: every grade then draws in the brass token under its own id,
    so the page still renders (one sentence, no grid) instead of raising on the way to the legend.
    """
    try:
        from plugins.greenieboard.const import GRADES
    except ImportError:
        return {}
    return dict(GRADES)


def grade(grade_id: Any) -> dict:
    """One grade as the page draws it: its colour, its legend word and its rating.

    The colour is the plugin's own. A grade the vocabulary carries no colour for — the perfect pass,
    which the Discord board marks with its own image — draws in the page's brass token, so the sheet
    adds no colour literal of its own.
    """
    specs = _specs()
    key = str(grade_id or "")
    spec = specs.get(key) or {}
    return {
        "grade": key,
        "color": str(spec.get("color") or "var(--brass)"),
        "legend": str(spec.get("legend") or key),
        "rating": spec.get("rating"),
    }


def legend() -> list[dict]:
    """The legend's rows — one per grade of :data:`LEGEND_GRADES`, with its colour and its points."""
    out: list[dict] = []
    for key in LEGEND_GRADES:
        entry = grade(key)
        rating = entry["rating"]
        out.append({**entry,
                    "points": (f"{float(rating):.1f}" if isinstance(rating, (int, float))
                               else "")})
    return out


# --------------------------------------------------------------------------------- formatting

def count(value: Any) -> str:
    """A whole figure with thousands separators; anything unreadable is ``0``."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def average(value: Any) -> str:
    """An average to two decimals, or ``—`` when the pilot has none."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    return f"{float(value):.2f}"


def column_headers(width: int) -> list[dict]:
    """The grid's header cells: one per trap, numbered from 1 and never from anything else.

    The number IS the axis — the trap count — so column 1 is a pilot's first pass on these servers
    whatever the board's width, and a capped board simply stops at its last column instead of
    starting somewhere in the middle of a long career.
    """
    return [{"index": number, "label": str(number)}
            for number in range(1, max(int(width), 0) + 1)]


def _tile(passage: dict) -> dict:
    """One 16px pass tile: its colour, its legend word, its night mark and the cell's tooltip."""
    entry = grade(passage.get("grade"))
    stamp = str(passage.get("time") or "")
    return {"color": entry["color"], "legend": entry["legend"], "night": bool(passage.get("night")),
            "points": average(passage.get("points")) if passage.get("points") is not None else "",
            "stamp": stamp.split("T", 1)[0] if stamp else "",
            "ordinal": passage.get("ordinal")}


def cells(entry: dict, width: int) -> list[dict]:
    """One pilot's grid cells, oldest first: exactly one pass per cell.

    The cell IS the pass — that is the whole point of the axis — so there is no ``+n`` and no bucket.
    A row is filled from the LEFT, the way the axis is read: column 1 under column 1, and a pilot
    with fewer traps than the board's width leaves the cells at the END blank.
    """
    passes = list(entry.get("passes") or ())
    width = max(int(width), 0)
    out: list[dict] = []
    for index in range(width):
        if index < len(passes):
            out.append({"index": index + 1, "tile": _tile(passes[index]), "empty": False})
        else:
            out.append({"index": index + 1, "tile": None, "empty": True})
    return out


def rows(board: dict, resolved: Any) -> list[dict]:
    """The board's rows: the read's own order, with each pilot's rank and its grid cells."""
    width = int(board.get("columns") or 0)
    out: list[dict] = []
    place = 0
    for entry in board.get("pilots") or ():
        if entry.get("ranked"):
            place += 1
        out.append({
            "ucid": entry.get("ucid"),
            "name": entry.get("name") or str(entry.get("ucid") or ""),
            "href": carrier_url(resolved, entry.get("ucid")),
            "ranked": bool(entry.get("ranked")),
            "rank": place if entry.get("ranked") else None,
            "medal": f"m{place}" if entry.get("ranked") and place <= 3 else "",
            "average": average(entry.get("average_points")),
            "traps": count(entry.get("traps")),
            "perfect": count(entry.get("perfect")),
            "night": count(entry.get("night")),
            "last_trap": str(entry.get("last_trap") or "").split("T", 1)[0],
            "cells": cells(entry, width),
        })
    return out


# --------------------------------------------------------------------------------- addresses

def _href(resolved: Any, *, airframe: str | None, server: str | None) -> str:
    """This page's address for one combination of the two controls."""
    query: dict[str, Any] = {AIRFRAME_PARAM: airframe or AIRFRAME_ALL,
                             SERVER_PARAM: server or SERVER_ALL}
    return f"{site_url(resolved, CARRIER_PATH)}?{urlencode(query)}"


def airframe_options(resolved: Any, chosen: str | None, server: str | None,
                     airframes: list[str]) -> list[dict]:
    """``All airframes`` first, then every airframe this site recorded, the chosen one marked."""
    entries = [{"key": AIRFRAME_ALL, "label": i18n._("All airframes"), "on": chosen is None,
                "href": _href(resolved, airframe=None, server=server)}]
    for name in airframes:
        entries.append({"key": name, "label": name, "on": chosen == name,
                        "href": _href(resolved, airframe=name, server=server)})
    return entries


def server_options(resolved: Any, chosen: str | None, airframe: str | None) -> list[dict]:
    """``All servers`` first, then the site's own servers in name order, the chosen one marked."""
    entries = [{"key": SERVER_ALL, "label": i18n._("All servers"), "on": chosen is None,
                "href": _href(resolved, airframe=airframe, server=None)}]
    for name in sorted(str(name) for name in (getattr(resolved, "servers", ()) or ())):
        entries.append({"key": name, "label": name, "on": chosen == name,
                        "href": _href(resolved, airframe=airframe, server=name)})
    return entries


# --------------------------------------------------------------------------------- the views

def view(resolved: Any, board: dict | None, airframe: str | None = None,
         server: str | None = None, unavailable: bool = False) -> dict:
    """The board page's whole model from the read's own answer — pure, so a test can pin it.

    ``board`` is ``None`` on an install without the ``traps`` table: the page then prints
    :data:`EMPTY` and draws no grid and no legend.
    """
    boards = board or {}
    rendered = rows(boards, resolved) if boards else []
    totals = boards.get("totals") or {}
    return {
        "available": bool(boards),
        "unavailable": bool(unavailable),
        "empty": not rendered,
        "sentence": EMPTY,
        "airframe": airframe,
        "server": server,
        "readouts": [
            {"key": "traps", "value": count(totals.get("traps")),
             "label": i18n._("Traps on these servers")},
            {"key": "pilots", "value": count(totals.get("pilots")),
             "label": i18n._("Pilots qualified")},
            {"key": "perfect", "value": count(totals.get("perfect")),
             "label": i18n._("Perfect passes")},
            {"key": "night", "value": count(totals.get("night")),
             "label": i18n._("Night traps")},
        ],
        "columns": column_headers(boards.get("columns") or 0),
        "rows": rendered,
        "legend": legend(),
        "airframe_options": airframe_options(resolved, airframe, server,
                                             boards.get("airframes") or []),
        "server_options": server_options(resolved, server, airframe),
    }


def _trap_row(entry: dict, resolved: Any) -> dict:
    """One trap row: when it was flown, in what, how it was graded, and whether a sheet exists.

    The sheet's ADDRESS is built here and only here; a row without a sheet carries ``""``, so the
    template never invents a URL for a pass the server holds no image for.
    """
    stamp = str(entry.get("time") or "")
    sheet = trap_sheet_url(resolved, entry.get("id")) if entry.get("has_sheet") else ""
    return {
        "id": entry.get("id"),
        "date": stamp.split("T", 1)[0],
        "time": stamp.split("T", 1)[1][:5] if "T" in stamp else "",
        "airframe": entry.get("unit_type") or "",
        "grade": grade(entry.get("grade")),
        "wire": entry.get("wire") if entry.get("wire") is not None else "—",
        "night": bool(entry.get("night")),
        "points": average(entry.get("points")) if entry.get("points") is not None else "",
        "comment": entry.get("comment") or "",
        "sheet_href": sheet,
    }


def trap_rows(answer: dict, resolved: Any) -> list[dict]:
    """The trap list of one pilot's record, newest first — the read's own order, formatted."""
    return [_trap_row(entry, resolved) for entry in (answer.get("traps") or ())]


def selected_pass(answer: dict, resolved: Any) -> dict | None:
    """The pass whose trap sheet the page shows large: the NEWEST pass of the page that has one.

    ``None`` when the page holds no sheet at all, which is what makes the panel vanish rather than
    draw an empty frame. Only a page the viewer owns ever carries sheet addresses
    (:func:`section`), so this never names an image the viewer will be refused.
    """
    for entry in (answer.get("traps") or ()):
        if entry.get("has_sheet"):
            return _trap_row(entry, resolved)
    return None


def _achievement(entry: dict) -> dict:
    """One badge from the read's answer: its label, whether it is earned and what is left until it."""
    need, have = int(entry.get("need") or 0), int(entry.get("have") or 0)
    ident = str(entry.get("id") or "")
    return {
        "id": ident,
        "label": BADGE_LABELS.get(ident, ident),
        "mark": entry.get("mark") or "",
        "earned": bool(entry.get("earned")),
        "progress": count(max(need - have, 0)),
        "note": count(have),
    }


def detail_view(answer: dict | None, resolved: Any) -> dict:
    """One pilot's record page model from the read's own answer, or the not-found state.

    The page carries the totals, the badges, the trap list and the LSO's remark — and NO trap sheet:
    a sheet is the pass owner's own, and it lives on their own page.
    """
    if not answer:
        return {"found": False, "sentence": NOT_FOUND,
                "back_href": site_url(resolved, CARRIER_PATH)}
    totals = dict(answer.get("totals") or {})
    return {
        "found": True,
        "ucid": answer.get("ucid"),
        "name": answer.get("name") or str(answer.get("ucid") or ""),
        "totals": {
            "traps": count(totals.get("traps")),
            "perfect": count(totals.get("perfect")),
            "night": count(totals.get("night")),
            "average": average(totals.get("average_points")),
            "last_trap": str(totals.get("last_trap") or "").split("T", 1)[0],
        },
        "readouts": [
            {"key": "traps", "value": count(totals.get("traps")), "label": i18n._("Traps")},
            {"key": "perfect", "value": count(totals.get("perfect")),
             "label": i18n._("Perfect passes")},
            {"key": "night", "value": count(totals.get("night")), "label": i18n._("At night")},
            {"key": "last", "value": str(totals.get("last_trap") or "").split("T", 1)[0],
             "label": i18n._("Last trap")},
        ],
        "average": average(totals.get("average_points")),
        "badges": [_achievement(entry) for entry in (answer.get("achievements") or ())],
        "traps": trap_rows(answer, resolved),
        "truncated": bool(answer.get("truncated")),
        "back_href": site_url(resolved, CARRIER_PATH),
    }


def _page_href(resolved: Any, page: int) -> str:
    """The own page's address for one page of the viewer's passes."""
    return f"{site_url(resolved, ME_PATH)}?{urlencode({CARRIER_PAGE_PARAM: page})}"


def section(answer: dict | None, resolved: Any, *, page: int = 1) -> dict | None:
    """The viewer's own Carrier section: their passes on this site, with their trap sheets.

    ``None`` is the answer for an install whose database carries no ``traps`` table, so the caller
    renders NO section at all — the same degrade ``progress_view`` applies to the event stream. A
    viewer who has not trapped here gets the section with :data:`SECTION_EMPTY` in it: the plugin is
    active, so the page says so instead of hiding the band.
    """
    if answer is None:
        return None
    totals = dict(answer.get("totals") or {})
    rendered = trap_rows(answer, resolved)
    truncated = bool(answer.get("truncated"))
    return {
        "empty": not rendered,
        "prev_href": _page_href(resolved, page - 1) if page > 1 else "",
        "next_href": _page_href(resolved, page + 1) if truncated else "",
        "sentence": SECTION_EMPTY,
        "page": int(page),
        "readouts": [
            {"key": "traps", "value": count(totals.get("traps")), "label": i18n._("Traps")},
            {"key": "perfect", "value": count(totals.get("perfect")),
             "label": i18n._("Perfect passes")},
            {"key": "night", "value": count(totals.get("night")), "label": i18n._("Night traps")},
            {"key": "average", "value": average(totals.get("average_points")),
             "label": i18n._("Average points")},
        ],
        "last_trap": str(totals.get("last_trap") or "").split("T", 1)[0],
        "rows": rendered,
        "selected": selected_pass(answer, resolved),
        "truncated": truncated,
    }


# --------------------------------------------------------------------------------- the reads

def _switch_on(resolved: Any) -> bool:
    """Whether this page exists here — the ONE gate every route in this module reads.

    TWO answers, both required: the GreenieBoard plugin is loaded (nothing else writes the traps this
    page draws, so without it there is no page at all), and the site links to the page. The menu tick
    IS the switch — a page this site's bar does not link to is not served here.
    """
    if not page_offered(NAV_ID):
        return False
    getter = getattr(resolved, "page_enabled", None)
    return bool(getter(NAV_ID)) if callable(getter) else False


# --------------------------------------------------------------------------------- the routes

def add_route(router: APIRouter, site: Any) -> str:
    """Serve the carrier board at *site*'s ``carrier`` address and return the registered path."""
    path = site_url(site, CARRIER_PATH)

    async def site_carrier(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        if not _switch_on(resolved):
            return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                    model=view(resolved, None), status_code=404)
        query = request.query_params
        server = site_leaderboard._server(query.get(SERVER_PARAM), resolved)
        scoped = site_leaderboard.scoped_context(resolved, server)
        db = pool()
        if db is None:
            return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                    model={**view(resolved, None), "unavailable": True,
                                           "sentence": UNAVAILABLE})
        # ONE read answers both the board and the airframe control: the airframe list is not
        # narrowed by the chosen airframe, so a value this site never recorded is recognised from the
        # answer it produced and the board is read once more, unnarrowed
        airframe = str(query.get(AIRFRAME_PARAM) or "").strip() or None
        board = await site_reads.carrier_board(scoped, db, airframe=airframe)
        if airframe and airframe not in (board or {}).get("airframes", []):
            airframe = None
            board = await site_reads.carrier_board(scoped, db)
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                model=view(resolved, board, airframe=airframe, server=server))

    router.add_api_route(path, site_carrier, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-carrier-{site.key}")
    return path


def detail_route(router: APIRouter, site: Any) -> str:
    """Serve one pilot's carrier record at the board's path plus the pilot's UCID.

    The UCID is taken as TEXT and encoded exactly like the profile link's own segment, and a pilot
    with no trap on this site's servers answers the page's own not-found state — never another
    community's record.
    """
    path = site_url(site, f"{CARRIER_PATH}/{{ucid}}")

    async def site_carrier_record(request: Request, ucid: str) -> HTMLResponse:
        resolved = resolve_site(request, site)
        if not _switch_on(resolved):
            return render_site_page(request, site, RECORD_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=detail_view(None, resolved),
                                    status_code=404)
        db = pool()
        if db is None:
            model = {**detail_view(None, resolved), "unavailable": True, "sentence": UNAVAILABLE}
            return render_site_page(request, site, RECORD_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=model)
        answer = await site_reads.carrier_record(resolved, db, ucid)
        if not answer or not (answer.get("totals") or {}).get("traps"):
            return render_site_page(request, site, RECORD_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=detail_view(None, resolved),
                                    status_code=404)
        return render_site_page(request, site, RECORD_TEMPLATE, active=NAV_ID, resolved=resolved,
                                model=detail_view(answer, resolved))

    router.add_api_route(path, site_carrier_record, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-carrier-record-{site.key}")
    return path


def sheet_route(router: APIRouter, site: Any) -> str:
    """Serve one pass's trap sheet image — to the pilot who flew it, and to nobody else.

    THE OWNERSHIP RULE, in one place. The request is answered only when all four hold: the viewer is
    signed in, the pass belongs to a pilot that same identity is linked to on this site, the pass is
    one of this site's servers' and it carries a sheet. Every other case — an anonymous visitor, a
    signed-in viewer looking at somebody else's pass, a pass of another site, a NULL sheet — answers
    the page's not-found document with ``404``, exactly like an address that does not exist: the
    route must not confirm that a trap id is real. No bytes are read unless the rule holds, because
    the read itself is filtered by the viewer's own UCID.

    ONE request, one read: the viewer's whole UCID array is bound into the sheet statement (it takes
    one UCID or a list), so a viewer who owns several pilot rows costs the same as one who owns one
    — a probe and one statement, not a probe and a statement per UCID.

    The trap id is an integer segment, so a non-numeric address is not this route at all.
    """
    path = site_url(site, f"{CARRIER_TRAP_PATH}/{{trap_id:int}}")

    async def site_carrier_sheet(request: Request, trap_id: int) -> Response:
        resolved = resolve_site(request, site)
        if not _switch_on(resolved):
            return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                    model=view(resolved, None), status_code=404)
        db = pool()
        who = identity(request)
        if db is None or who is None:
            return _not_found_page(request, site, resolved)
        ucids = await site_reads.my_ucids(resolved, db, getattr(who, "subject", ""))
        image = await site_reads.trap_sheet(resolved, db, trap_id, ucids) if ucids else None
        if image is None:
            return _not_found_page(request, site, resolved)
        return Response(content=image, media_type="image/png",
                        headers={"Cache-Control": "private, max-age=300"})

    router.add_api_route(path, site_carrier_sheet, methods=["GET"], response_class=Response,
                         name=f"site-carrier-sheet-{site.key}")
    return path


def _not_found_page(request: Request, site: Any, resolved: Any) -> HTMLResponse:
    """The site's not-found answer for a sheet request: the board's own not-found state, at 404."""
    return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                            model={"available": False, "unavailable": False, "empty": True,
                                   "sentence": NOT_FOUND, "readouts": [], "columns": [], "rows": [],
                                   "legend": [], "airframe_options": [], "server_options": [],
                                   "airframe": None, "server": None},
                            status_code=404)
