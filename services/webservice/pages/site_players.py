"""The public site's pilot search — the top bar's search box, submitting ``GET ?q=``.

EVERY ROW IS ``site_reads.pilot_search``'S ANSWER, run once per request and only when a query was
given: the read answers no rows for an empty query and runs no statement. The page composes the
result table, the page window and the paging links; it never queries.

THE MATCH IS A CONTAINS TERM. ``pilot_search`` filters
``LOWER(p.name) LIKE <escaped contains term> ESCAPE '\\'``: the visitor's words are escaping-neutral
and are matched anywhere in the name, so typing the middle of a name finds it. The expression is not
one ``idx_players_name_lower`` can serve; that is accepted (``players`` is small on every
installation), and the page passes the words through untouched — widening or narrowing the match is
the read's decision, never the page's.

PAGING IS THE READ'S CAP: :data:`site_reads.SEARCH_PAGE_SIZE` rows per offset, so a page is
``(page - 1) * size`` and a next link exists when a full page came back.

An unknown term is an EMPTY STATE with its own copy, never a refusal: nothing about a name that does
not exist is an error. The route is registered ``public`` by
:mod:`services.webservice.site_pages`; nothing here reads a session.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import i18n, site_reads
from ..readmodels.views import PAGE_PARAM, normalise_page
from ..site_pages import (NAV_PATHS, SEARCH_JSON_PATH, player_url, pool, render_site_page,
                          resolve_site, site_url)

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "QUERY_PARAM",
    "SUGGESTION_LIMIT",
    "MIN_SUGGESTION_CHARS",
    "count",
    "rows",
    "suggestions",
    "view",
    "add_route",
    "search_route",
]

log = logging.getLogger(__name__)

#: this page's id in ``site_pages.NAV_PATHS`` — the menu's ``Pilots`` entry and the top bar's
#: search form both point here.
NAV_ID = "players"

TEMPLATE = "site_players.html"

#: the search box's query parameter — the page's and the suggestion route's alike.
QUERY_PARAM = "q"

#: the most suggestions the JSON route offers. The read's own page (:data:`site_reads.SEARCH_PAGE_SIZE`)
#: is smaller, so this is the ceiling a page of rows cannot exceed, never a second query.
SUGGESTION_LIMIT = 25

#: the shortest fragment the JSON route answers. An empty query runs nothing; ONE character names
#: almost every pilot and is not worth a request, so the popup waits for the second one.
MIN_SUGGESTION_CHARS = 2


# --------------------------------------------------------------------------------- formatting

def count(value: Any) -> str:
    """A whole figure with thousands separators; anything unreadable is ``0``."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def rows(answer: dict, resolved: Any) -> list[dict]:
    """The answer's rows with their profile link and the DATE of their last session."""
    rendered: list[dict] = []
    for row in (answer or {}).get("rows") or []:
        seen = str(row.get("last_seen") or "")
        rendered.append({"name": row.get("name") or "",
                         "href": player_url(resolved, row.get("ucid")) if row.get("ucid") else "",
                         "last_seen": seen.split("T", 1)[0]})
    return rendered


# --------------------------------------------------------------------------------- addresses

def suggestions(resolved: Any, answer: dict) -> list[dict]:
    """The read's rows as ``{"name", "href"}`` suggestions, most recently seen first, capped.

    The read orders by name because the page is a table; a suggestion list is ordered by when a
    pilot was last on the site. The cap is :data:`SUGGESTION_LIMIT`, and the read's own page size is
    the tighter bound — this function never asks for more rows. A row without a name or a UCID has
    nothing to offer and is dropped.
    """
    found = [row for row in (answer or {}).get("rows") or []
             if row.get("name") and row.get("ucid")]
    found.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    return [{"name": row["name"], "href": player_url(resolved, row["ucid"])}
            for row in found[:SUGGESTION_LIMIT]]


def _href(resolved: Any, query: str, page: int) -> str:
    """This page's address for *query* and *page* (the page parameter is left out at 1)."""
    params: dict[str, Any] = {QUERY_PARAM: query}
    if page > 1:
        params[PAGE_PARAM] = page
    return f"{site_url(resolved, NAV_PATHS[NAV_ID])}?{urlencode(params)}"


# --------------------------------------------------------------------------------- the view

def view(resolved: Any, query: str, page: int, answer: dict, unavailable: bool = False) -> dict:
    """The page's whole model from the read's own answer — pure, so a test can pin it."""
    found = rows(answer, resolved)
    offset = int((answer or {}).get("offset") or 0)
    size = int((answer or {}).get("limit") or site_reads.SEARCH_PAGE_SIZE)
    return {
        "query": query,
        "searched": bool(query),
        "rows": found,
        "page": page,
        "size": size,
        "first": offset + 1 if found else 0,
        "last": offset + len(found),
        "has_prev": page > 1,
        "prev_href": _href(resolved, query, page - 1) if page > 1 else None,
        "next_href": _href(resolved, query, page + 1) if len(found) == size else None,
        "first_href": _href(resolved, query, 1),
        "empty": not found,
        "past_end": not found and page > 1,
        "unavailable": bool(unavailable),
    }


# --------------------------------------------------------------------------------- the route

def add_route(router: APIRouter, site: Any) -> str:
    """Serve the pilot search at *site*'s ``players`` address and return the registered path."""
    path = site_url(site, NAV_PATHS[NAV_ID])

    async def site_players(request: Request) -> HTMLResponse:
        resolved = resolve_site(request, site)
        query = str(request.query_params.get(QUERY_PARAM) or "").strip()
        page = normalise_page(request.query_params.get(PAGE_PARAM))
        db = pool()
        if db is None:
            model = view(resolved, query, page, {}, unavailable=True)
        else:
            offset = max(page - 1, 0) * site_reads.SEARCH_PAGE_SIZE
            answer = await site_reads.pilot_search(resolved, db, query, offset=offset)
            model = view(resolved, query, page, answer)
        return render_site_page(request, site, TEMPLATE, active=NAV_ID, resolved=resolved,
                                search_value=query, model=model)

    router.add_api_route(path, site_players, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-players-{site.key}")
    return path


def search_route(router: APIRouter, site: Any) -> str:
    """Serve the search box's JSON suggestions at *site*'s :data:`SEARCH_JSON_PATH`.

    The same read the page uses (:func:`site_reads.pilot_search`) at the first offset, so a
    suggestion costs one page of rows and no new statement. An empty or one-character query answers
    an empty list and runs NOTHING: a lone letter is not a search. A site without a bus answers the
    same empty list instead of refusing — the plain form still submits.
    """
    path = site_url(site, SEARCH_JSON_PATH)

    async def site_search_json(request: Request) -> JSONResponse:
        resolved = resolve_site(request, site)
        query = str(request.query_params.get(QUERY_PARAM) or "").strip()
        if len(query) < MIN_SUGGESTION_CHARS:
            return JSONResponse({"pilots": []})
        db = pool()
        if db is None:
            return JSONResponse({"pilots": []})
        answer = await site_reads.pilot_search(resolved, db, query, offset=0)
        return JSONResponse({"pilots": suggestions(resolved, answer)})

    router.add_api_route(path, site_search_json, methods=["GET"], response_class=JSONResponse,
                         name=f"site-search-{site.key}")
    return path
