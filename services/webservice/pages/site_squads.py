"""The public site's squadrons: the list (cards or a table) and one squadron's page.

ONE MODEL, TWO LAYOUTS. :func:`view` composes the list's rows ONCE from
:func:`~services.webservice.site_reads.squadrons`' answer and the template draws either branch from
the site's stored layout (``sites.<name>.layout.squadrons``, read off the site context by
:func:`layout_of`). The layout never changes a figure and never runs a second read: the cards branch
draws the campaign-points order with each bar scaled to the leader, and the list branch draws the
order its two control rows ask for.

THE LIST'S CONTROLS ARE ITS OWN. ``?by=`` (``campaign_points``, ``rating``, ``members``) and
``?server=`` (``all`` or one of THIS site's servers) are resolved the way
:mod:`services.webservice.pages.site_leaderboard` resolves its own: an unknown or missing value
falls back to the default instead of refusing the request, and each control link carries the other
parameter so switching one keeps the other. The CARDS layout ignores both — it has one order and no
control row — so a card page is never narrowed by a URL a visitor carried over.

THE SERVER SCOPE IS THE SHIPPED ONE. :func:`site_leaderboard.scoped_context` narrows the site
context to one server and the SAME :func:`~services.webservice.site_reads.squadrons` read answers
the narrowed figures; no second statement exists for a scoped squadron list. The squadron RATING is
not scoped that way — the competitive plugin holds one rating per pilot and has no server column —
which the page states under the list rather than pretending the figure moved.

ONE SQUADRON'S PAGE is :func:`~services.webservice.site_reads.squadron_detail`'s answer: the
squadron's row, its roster and its credit split, each figure read from the site's own views. An id
that is not one of THIS site's squadrons answers the page's not-found state, so one site never
serves another community's squadron.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from .. import i18n, sites, site_pages, site_reads
from ..site_pages import SQUADRON_PATH, render_site_page, resolve_site, site_url
from . import site_leaderboard

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "DETAIL_TEMPLATE",
    "RANK_PARAM",
    "SERVER_PARAM",
    "SERVER_ALL",
    "RANK_KEYS",
    "DEFAULT_RANK",
    "RANK_LABELS",
    "EMPTY",
    "NOT_FOUND",
    "DB_UNAVAILABLE",
    "LAYOUT_CARDS",
    "LAYOUT_LIST",
    "count",
    "hours",
    "rating",
    "monogram",
    "mark",
    "layout_of",
    "order",
    "rank_options",
    "server_options",
    "view",
    "detail_view",
    "add_route",
    "detail_route",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS`` — the menu's ``Squadrons`` entry.
NAV_ID = "squadrons"

TEMPLATE = "site_squads.html"

DETAIL_TEMPLATE = "site_squadron.html"

#: the layouts this page renders, as the site's ``layout:`` block spells them.
LAYOUT_CARDS = "cards"
LAYOUT_LIST = "list"

#: the query parameter that picks the list's order, and the one that narrows it to a server.
RANK_PARAM = "by"
SERVER_PARAM = site_leaderboard.SERVER_PARAM

#: the value the server control submits for the whole site.
SERVER_ALL = site_leaderboard.SERVER_ALL

#: the list's orders, in the order the control offers them. ``campaign_points`` is the default and
#: the order the cards layout always uses.
RANK_KEYS: tuple[str, ...] = ("campaign_points", "rating", "members")

DEFAULT_RANK = "campaign_points"

#: one msgid per order, declared here so the extractor sees the label and the control and the
#: template cannot disagree about a word.
RANK_LABELS: dict[str, str] = {
    "campaign_points": i18n._("Campaign points"),
    "rating": i18n._("Squadron rating"),
    "members": i18n._("Members"),
}

#: the list's one sentence when the site has no squadron to show.
EMPTY = i18n._("No squadron has flown here yet.")

#: the per-squadron page's not-found sentence.
NOT_FOUND = i18n._("That squadron was not found on this site.")

#: the per-squadron page's sentence when the statistics database is not reachable yet.
DB_UNAVAILABLE = i18n._("The statistics database is not available yet, so this page shows no figures.")

#: a name's leading digits, for the monogram of a squadron numbered in its name.
_LEADING_DIGITS = re.compile(r"^\d+")

#: a squadron's mark: an absolute ``http(s)://`` address with a host and no whitespace, quote or
#: angle bracket. Any other value — another scheme, a protocol-relative ``//`` form, a relative
#: path — is not an address this page prints.
_ABSOLUTE_MARK = re.compile(r"^https?://[^\s\"'<>/][^\s\"'<>]*$")


# --------------------------------------------------------------------------------- formatting

def count(value: Any) -> str:
    """A whole figure with thousands separators; anything unreadable is ``0``."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def hours(seconds: Any) -> str:
    """A duration as whole hours to one decimal (``318.4``); anything unreadable is ``0.0``."""
    try:
        return f"{max(float(seconds), 0.0) / 3600:.1f}"
    except (TypeError, ValueError):
        return "0.0"


def rating(value: Any) -> str:
    """A combined rating to two decimals, or ``''`` when the squadron has none."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return f"{float(value):.2f}"


def monogram(name: Any) -> str:
    """A squadron's mark when it has no image: its leading digits, else the initials of its words.

    A name numbered in its own text keeps that number (``303rd Fighter Wing`` -> ``303``), because
    it is what a reader recognises; any other name answers the initials of its first three words,
    upper-cased (``Night Owls`` -> ``NO``). A name with nothing in it answers ``?``, never a blank
    tile.
    """
    words = [word for word in str(name or "").split() if word]
    if not words:
        return "?"
    digits = _LEADING_DIGITS.match(words[0])
    if digits:
        return digits.group(0)[:3]
    return "".join(word[0] for word in words[:3]).upper()


def mark(value: Any) -> str:
    """A squadron's mark when it is an absolute ``http(s)`` URL, else ``''`` — the monogram draws.

    Only an absolute ``http``/``https`` address is returned. Another scheme (``javascript:``,
    ``data:``), a protocol-relative ``//host/…`` form, a relative path, an empty or whitespace value
    all answer ``''``, so no other address reaches an ``<img src>`` on a public page. The value is
    never trimmed, rewritten or repaired into validity: it is printed as it stands or not at all.
    """
    if not isinstance(value, str):
        return ""
    return value if _ABSOLUTE_MARK.match(value) else ""


def layout_of(resolved: Any) -> str:
    """The layout this page renders in, read off the site context; the shipped default otherwise.

    The context's own :meth:`~services.webservice.sites.SiteContext.page_layout` is the one
    resolution of a site's stored layout, so a context without it — a standalone render — answers
    :data:`~services.webservice.sites.DEFAULT_PAGE_LAYOUT`.
    """
    getter = getattr(resolved, "page_layout", None)
    if callable(getter):
        return str(getter(NAV_ID) or sites.DEFAULT_PAGE_LAYOUT)
    return sites.DEFAULT_PAGE_LAYOUT


# --------------------------------------------------------------------------------- the order

def _rank(value: Any) -> str:
    """The requested order when this page offers it, else the default."""
    wanted = str(value or "").strip().lower()
    return wanted if wanted in RANK_KEYS else DEFAULT_RANK


def _server(value: Any, resolved: Any) -> str | None:
    """The requested server when it is one of THIS site's, else ``None`` (the whole site).

    :func:`site_leaderboard._server`'s rule, called rather than restated so the two pages cannot
    disagree about what a server scope is.
    """
    return site_leaderboard._server(value, resolved)


def scoped_context(resolved: Any, server: str | None) -> Any:
    """The site context bound to ONE of its servers, or *resolved* itself for the whole site."""
    return site_leaderboard.scoped_context(resolved, server)


def _sort_key(rank: str):
    """The comparator :func:`order` sorts by for one of :data:`RANK_KEYS`."""
    def key(row: dict) -> tuple:
        name = str(row.get("name") or "").lower()
        if rank == "rating":
            value = row.get("rating")
            figure = float(value) if isinstance(value, (int, float)) else None
            # a squadron with no competitive rating never outranks a rated one
            return (figure is None, -(figure or 0.0), name)
        return (False, -int(row.get(rank) or 0), name)
    return key


def order(rows: list[dict], rank: str) -> list[dict]:
    """The list's order for *rank*: descending by the figure, name ascending as the tie-break.

    ``campaign_points`` and ``members`` descend numerically; ``rating`` descends with an absent
    rating sorted LAST. The name breaks every tie, so the same answer always renders in the same
    order and a page is never reshuffled between two requests.
    """
    return sorted(rows or [], key=_sort_key(rank if rank in RANK_KEYS else DEFAULT_RANK))


# --------------------------------------------------------------------------------- addresses

def _href(resolved: Any, *, rank: str, server: str | None) -> str:
    """This page's address for one combination of the two controls."""
    query: dict[str, Any] = {RANK_PARAM: rank, SERVER_PARAM: server or SERVER_ALL}
    return f"{site_url(resolved, SQUADRON_PATH)}?{urlencode(query)}"


def rank_options(resolved: Any, chosen: str, server: str | None) -> list[dict]:
    """One entry per order, the chosen one marked, the current server kept."""
    return [{"key": key, "label": RANK_LABELS[key], "on": key == chosen,
             "href": _href(resolved, rank=key, server=server)} for key in RANK_KEYS]


def server_options(resolved: Any, chosen: str | None, rank: str) -> list[dict]:
    """``All servers`` first, then the site's own servers in name order, the chosen one marked."""
    entries = [{"key": SERVER_ALL, "label": i18n._("All servers"), "on": chosen is None,
                "href": _href(resolved, rank=rank, server=None)}]
    for name in sorted(str(name) for name in (getattr(resolved, "servers", ()) or ())):
        entries.append({"key": name, "label": name, "on": chosen == name,
                        "href": _href(resolved, rank=rank, server=name)})
    return entries


# --------------------------------------------------------------------------------- the views

def _row(entry: dict, position: int, resolved: Any, leader: int) -> dict:
    """One list row from the read's own entry: its standing, its figures and its link."""
    points = int(entry.get("campaign_points") or 0)
    return {
        "rank": position,
        "medal": f"m{position}" if position <= 3 else "",
        "id": entry.get("id"),
        "name": entry.get("name") or "",
        "description": entry.get("description") or "",
        "image_url": mark(entry.get("image_url")),
        "monogram": monogram(entry.get("name")),
        "href": (site_url(resolved, f"{SQUADRON_PATH}/{entry.get('id')}")
                 if entry.get("id") is not None else ""),
        "members": count(entry.get("members")),
        "points": count(points),
        "share": round(points * 100.0 / leader, 1) if leader else 0.0,
        "rating": rating(entry.get("rating")),
        "rating_available": bool(entry.get("rating_available")),
    }


def view(resolved: Any, rows: list[dict], layout: str, rank: str, server: str | None,
         unavailable: bool = False) -> dict:
    """The list page's whole model from the read's own rows — pure, so a test can pin it."""
    shown = layout if layout in (LAYOUT_CARDS, LAYOUT_LIST) else LAYOUT_CARDS
    chosen = rank if shown == LAYOUT_LIST else DEFAULT_RANK
    scoped = server if shown == LAYOUT_LIST else None
    ordered = order(rows, chosen)
    leader = max((int(row.get("campaign_points") or 0) for row in ordered), default=0)
    rendered = [_row(entry, index, resolved, leader)
                for index, entry in enumerate(ordered, start=1)]
    return {
        "layout": shown,
        "cards": shown == LAYOUT_CARDS,
        "table": shown == LAYOUT_LIST,
        "rank": chosen,
        "server": scoped,
        "rows": rendered,
        "total": count(len(rendered)),
        "first": 1 if rendered else 0,
        "last": len(rendered),
        "empty": not rendered,
        "sentence": EMPTY,
        "rating_available": any(row["rating_available"] for row in rendered),
        "rank_options": rank_options(resolved, chosen, scoped),
        "server_options": server_options(resolved, scoped, chosen),
        "unavailable": bool(unavailable),
    }


def _roster_row(entry: dict, resolved: Any) -> dict:
    """One roster row: the pilot's link, its three figures and the date it was last seen."""
    kd = entry.get("kd")
    return {
        "name": entry.get("name") or "",
        "href": site_pages.player_url(resolved, entry.get("ucid")) if entry.get("ucid") else "",
        "sorties": count(entry.get("sorties")),
        "playtime": hours(entry.get("playtime")),
        "kd": f"{float(kd):.2f}" if isinstance(kd, (int, float)) else "",
        "last_seen": str(entry.get("last_seen") or "").split("T", 1)[0],
    }


def _credits(answer: dict) -> tuple[list[dict], int]:
    """The credit split's bars and their total, largest first, each scaled to the total."""
    entries = [entry for entry in (answer.get("credits") or ()) if isinstance(entry, dict)]
    total = sum(int(entry.get("points") or 0) for entry in entries)
    bars = [{"name": entry.get("name") or "", "points": count(entry.get("points")),
             "share": round(int(entry.get("points") or 0) * 100.0 / total, 1) if total else 0.0}
            for entry in entries]
    return bars, total


def detail_view(answer: dict | None, resolved: Any) -> dict:
    """One squadron's page model from ``squadron_detail``'s answer — pure, so a test can pin it."""
    if not answer:
        return {"found": False, "sentence": NOT_FOUND}
    roster = [_roster_row(entry, resolved) for entry in (answer.get("roster") or ())]
    playtime = sum(float(entry.get("playtime") or 0) for entry in (answer.get("roster") or ()))
    sorties = sum(int(entry.get("sorties") or 0) for entry in (answer.get("roster") or ()))
    credits, credits_total = _credits(answer)
    return {
        "found": True,
        "id": answer.get("id"),
        "name": answer.get("name") or "",
        "description": answer.get("description") or "",
        "image_url": mark(answer.get("image_url")),
        "monogram": monogram(answer.get("name")),
        "rank": answer.get("rank") or 0,
        "total": answer.get("total") or 0,
        "members": count(answer.get("members")),
        "points": count(answer.get("campaign_points")),
        "playtime": hours(playtime),
        "sorties": count(sorties),
        "rating": rating(answer.get("rating")),
        "rating_available": bool(answer.get("rating_available")),
        "roster": roster,
        "credits": credits,
        "credits_total": count(credits_total),
        "has_credits": bool(credits_total),
        "back_href": site_url(resolved, SQUADRON_PATH) if resolved is not None else "",
    }


# --------------------------------------------------------------------------------- the reads

async def _rows(resolved: Any, db: Any) -> list[dict] | None:
    """The site's squadrons, ``None`` only when this process holds no pool.

    ``None`` and ``[]`` are different answers and the page states them differently: no pool is "the
    statistics database is not available yet", while a database without the CreditSystem plugin's
    tables has no squadron to describe at all — :func:`~services.webservice.site_reads.squadrons`
    answers ``[]`` for that, off its own single connection.
    """
    if db is None:
        return None
    return await site_reads.squadrons(resolved, db)


# --------------------------------------------------------------------------------- the routes

def _served(resolved: Any) -> bool:
    """Whether this site serves the Squadrons pages: ONE gate, the site's menu entry.

    A page exists where the site's bar links to it — the same rule every page follows, the carrier
    board included. A context built by hand (a test, a standalone render) carries no method: the
    answer is then "served", so nothing disappears from a document rendered outside a site.
    """
    getter = getattr(resolved, "page_enabled", None)
    return bool(getter(NAV_ID)) if callable(getter) else True


def _not_served(request: Request, site: Any, resolved: Any) -> Response:
    """The site's own not-found answer for a page this site's menu does not link to."""
    return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                            model=view(resolved, [], layout_of(resolved), DEFAULT_RANK, None),
                            status_code=404)


def add_route(router: APIRouter, site: Any) -> str:
    """Serve the squadron list at *site*'s ``squadrons`` address and return the registered path."""
    path = site_url(site, SQUADRON_PATH)

    async def site_squads(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        if not _served(resolved):
            return _not_served(request, site, resolved)
        layout = layout_of(resolved)
        query = request.query_params
        # the controls belong to the list: a card page has one order, the whole site and no row
        rank = _rank(query.get(RANK_PARAM)) if layout == LAYOUT_LIST else DEFAULT_RANK
        server = _server(query.get(SERVER_PARAM), resolved) if layout == LAYOUT_LIST else None
        db = site_pages.pool()
        rows = await _rows(scoped_context(resolved, server), db)
        model = view(resolved, rows or [], layout, rank, server, unavailable=rows is None)
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                model=model)

    router.add_api_route(path, site_squads, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-squads-{site.key}")
    return path


def detail_route(router: APIRouter, site: Any) -> str:
    """Serve one squadron's page at the list's path plus the squadron's id.

    The id is the ``squadrons`` table's primary key — an integer that survives a rename — and a
    value that is not one of THIS site's squadrons answers the page's own not-found state, never
    another community's squadron. The segment is taken as TEXT and converted by the read: a typed
    integer parameter makes a non-numeric segment the framework's 422 instead of this page's
    not-found answer, and every address the site declares must answer as a page.
    """
    path = site_url(site, f"{SQUADRON_PATH}/{{squadron_id}}")

    async def site_squadron(request: Request, squadron_id: str) -> HTMLResponse:
        resolved = resolve_site(request, site)
        if not _served(resolved):
            return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=detail_view(None, resolved),
                                    status_code=404)
        db = site_pages.pool()
        if db is None:
            model = {"found": False, "sentence": DB_UNAVAILABLE,
                     "back_href": site_url(resolved, SQUADRON_PATH)}
            return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=model)
        answer = await site_reads.squadron_detail(resolved, db, squadron_id)
        if not answer:
            model = {**detail_view(None, resolved),
                     "back_href": site_url(resolved, SQUADRON_PATH)}
            return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                    resolved=resolved, model=model, status_code=404)
        return render_site_page(request, site, DETAIL_TEMPLATE, active=NAV_ID,
                                resolved=resolved, model=detail_view(answer, resolved))

    router.add_api_route(path, site_squadron, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-squadron-{site.key}")
    return path
