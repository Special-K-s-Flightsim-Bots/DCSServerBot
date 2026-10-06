"""The Players page: everyone online right now, in full, searchable.

The dashboard's Players tab is the same list; here it is the whole page, so the search has room and
the result is linkable. The search is the dashboard's own component (inside
``templates/_table.html``), server-side (``?q=``) and a plain GET form: it filters on the name or
the UCID, reports "X of Y" from the read models' own counts, and clears back to this page.

IT CARRIES THE CONSOLE'S FIRST PLAYER WRITE (``writes=True`` on the page spec): a row you may act on
renders the MESSAGE control — one small form per row (a message field, a mode, the session's CSRF
token, the target in the BODY) posting to :mod:`services.webservice.pages.actions`. The control is
built per request from the same scoped source the table renders from, so a player on a server outside
your scope has no control at all, and a crafted POST naming them is refused with the same bare 403 as
a name that does not exist. Kick and Ban are still absent: neither action exists in this release, and
a control is OMITTED rather than rendered disabled.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import i18n
from . import lists as lists_page

__all__ = ["PLAYERS_PATH", "PLAYERS_CAPABILITY", "PLAYERS_ROLES", "PLAYERS_TABLE", "PLAYERS_TITLE",
           "PLAYERS_LEAD", "NAV_LABEL", "PAGE", "capabilities", "nav_items", "add_routes"]

PLAYERS_PATH = "/players"
PLAYERS_CAPABILITY = "players.view"
#: a member who may read the console may read the player list (the dashboard's own role model)
PLAYERS_ROLES: tuple[str, ...] = lists_page.READ_ONLY_ROLES
PLAYERS_TABLE = "players"
PLAYERS_TITLE = i18n._("Players")
PLAYERS_LEAD = i18n._("Everyone online right now, across every server. Search by name or UCID. "
                      "A row you may act on offers Message: an in-game popup or a chat line.")
NAV_LABEL = i18n._("Players")

PAGE = lists_page.ListPage(table=PLAYERS_TABLE, path=PLAYERS_PATH, capability=PLAYERS_CAPABILITY,
                           roles=PLAYERS_ROLES, title=PLAYERS_TITLE, lead=PLAYERS_LEAD,
                           nav_label=NAV_LABEL, scope_grants=True, writes=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {PLAYERS_PATH: PLAYERS_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (PAGE.nav_item(),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Players route to the shell's own router and return it."""
    return lists_page.add_list_route(router, PAGE)
