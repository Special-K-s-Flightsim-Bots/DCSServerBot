"""The public site's "my statistics" — the viewer's own figures on THIS site's servers.

EVERY FIGURE IS ``site_reads.my_statistics``'S ANSWER, run once per request for the signed-in
identity's own Discord id: the UCIDs that Discord link owns on this site's servers, their combined
totals, their per-airframe hours and their per-server hours. The page composes and formats; it
never queries, and the formatting is :mod:`pages.site_player`'s — one rule for a figure, whether a
visitor reads somebody else's profile or their own.

WHAT AN ANONYMOUS VISITOR GETS: the page itself, with the sign-in control and no figure at all. It
is never a redirect and never a 403 — a search engine or a shared link must not land a stranger on
a login form, and a door that answers with a bounce is a door nobody can bookmark.

WHAT A SIGNED-IN VISITOR WITH NO RECORD HERE GETS: the same page over this site's servers, saying
there is no record. Not a refusal: the figures are already public on the pilot profile, so refusing
would protect nothing, and a refusal message would tell the visitor which group they are in.

THE BAND IS THE POINT. :data:`TEMPLATE` opens with the coloured "this is you" band so a viewer can
never mistake these figures for another pilot's; the band is rendered only for a resolved identity.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, site_reads
from ..site_pages import (ME_PATH, NAV_PATHS, SEARCH_PATH, identity, player_url, pool,
                          render_site_page, resolve_site, sign_in_href, sign_out_context, site_url)
from .site_player import airframes, count, hours, last_flown, ratio, sentence, servers

__all__ = [
    "NAV_ID",
    "TEMPLATE",
    "readouts",
    "view",
    "anonymous",
    "add_route",
]

log = logging.getLogger(__name__)

#: This page belongs to no menu entry: it is one visitor's own record, reached from the shell's
#: chip, so marking a nav id would highlight an entry the menu does not carry.
NAV_ID = ""

TEMPLATE = "site_me.html"


# --------------------------------------------------------------------------------- formatting

def readouts(totals: dict) -> list[dict]:
    """The five cumulative figures of a viewer's own record, in the profile page's own order.

    The labels and the two sentence notes are the pilot profile's msgids, so a figure is named the
    same way on both pages; a viewer with no record gets the same five rows at zero, because the
    strip is the page's shape and not a claim about the visitor.
    """
    return [
        {"key": "playtime", "value": count(hours(totals.get("playtime"))), "unit": i18n._("h"),
         "label": i18n._("Playtime"), "note": i18n._("on this site's servers")},
        {"key": "sessions", "value": count(totals.get("sessions")), "unit": None,
         "label": i18n._("Sessions"), "note": i18n._("recorded")},
        {"key": "sorties", "value": count(totals.get("takeoffs")), "unit": None,
         "label": i18n._("Sorties"), "note": i18n._("recorded takeoffs")},
        {"key": "kills", "value": count(totals.get("kills")), "unit": None,
         "label": i18n._("Kills"),
         "note": sentence(i18n._("{count} in PvP"), count(totals.get("pvp")))},
        {"key": "kd", "value": ratio(totals.get("kills"), totals.get("deaths")), "unit": None,
         "label": i18n._("K/D"),
         "note": sentence(i18n._("{count} deaths"), count(totals.get("deaths")))},
    ]


def _shown_name(who: Any) -> str:
    """The identity's display name, or ``""`` — never its subject (a Discord id is not a name)."""
    return str(getattr(who, "shown_name", "") or "")


def _ucids(rows: list[dict], resolved: Any) -> list[dict]:
    """The player IDs this Discord link owns here, each with that UCID's public profile URL.

    The page uses the first entry only: its name is the record's name and its URL is the "open my
    public profile" link. A second UCID still counts into the figures; it is not listed.
    """
    return [{"name": row.get("name") or str(row.get("ucid") or ""),
             "href": player_url(resolved, row.get("ucid"))}
            for row in (rows or []) if row.get("ucid")]


# --------------------------------------------------------------------------------- the model

def view(resolved: Any, who: Any, stats: dict) -> dict:
    """The page's whole signed-in model from the read's own answer — pure, so a test can pin it."""
    found = bool(stats.get("found"))
    totals = dict(stats.get("totals") or {})
    linked = _ucids(stats.get("ucids") or [], resolved)
    return {
        "signed_in": True,
        "unavailable": False,
        "found": found,
        "name": (linked[0]["name"] if linked else "") or _shown_name(who),
        "last_flown": last_flown(stats.get("recent") or []),
        "readouts": readouts(totals),
        "airframes": airframes(stats.get("per_aircraft") or [], totals.get("playtime")),
        "servers": servers(stats.get("per_server") or []),
        "public_href": linked[0]["href"] if linked else None,
        "search_href": site_url(resolved, SEARCH_PATH),
        "leaderboard_href": site_url(resolved, NAV_PATHS["leaderboard"]),
    }


def anonymous(resolved: Any, href: str | None) -> dict:
    """The model of the signed-out page: the door and nothing else."""
    return {"signed_in": False, "sign_in_href": href}


def _unavailable(resolved: Any, who: Any) -> dict:
    """The signed-in model of a process with no database: the identity, no figure."""
    model = view(resolved, who, {})
    model.update({"unavailable": True})
    return model


# --------------------------------------------------------------------------------- the route

def add_route(router: APIRouter, site: Any) -> str:
    """Serve the viewer's own statistics at ``<site>/me`` and return the registered path."""
    path = site_url(site, ME_PATH)

    async def site_me(request: Request, next: str = "") -> HTMLResponse:
        resolved = resolve_site(request, site)
        # ONE identity read for the whole request: the shell's chip and this page's model are the
        # same answer, so a visitor cannot be signed in for the chip and anonymous for the figures
        who = identity(request)
        if who is None:
            return render_site_page(request, site, TEMPLATE, resolved=resolved, who=who,
                                    model=anonymous(resolved,
                                                    sign_in_href(request, resolved, next)))
        db = pool()
        if db is None:
            model = _unavailable(resolved, who)
        else:
            stats = await site_reads.my_statistics(resolved, db, getattr(who, "subject", ""))
            model = view(resolved, who, stats)
        model["sign_out"] = sign_out_context(request)
        return render_site_page(request, site, TEMPLATE, resolved=resolved, who=who,
                                model=model)

    router.add_api_route(path, site_me, methods=["GET"], response_class=HTMLResponse,
                         name=f"site-me-{site.key}")
    return path
