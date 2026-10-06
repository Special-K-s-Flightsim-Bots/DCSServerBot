"""The Servers page: every server in the cluster, in full.

The dashboard's Servers tab is the same list — this page is where it is the whole page, uncapped and
linkable. It renders through :mod:`services.webservice.pages.lists` (same view layer, same table
component, same capability mechanism); this module owns only what makes it the Servers page.

IT CARRIES THE CONSOLE'S WRITE CONTROLS (``writes=True`` on the page spec): a row you may operate
renders its mission controls — Pause on a RUNNING server, Unpause on a PAUSED one — as forms posting
to :mod:`services.webservice.pages.actions` with the session's CSRF token. The controls are built
per request from the same scoped source the table renders from, so a row outside your scope has no
control at all, and a crafted POST naming it is refused with the same bare 403 as a name that does
not exist.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import i18n
from . import lists as lists_page

__all__ = ["SERVERS_PATH", "SERVERS_CAPABILITY", "SERVERS_ROLES", "SERVERS_TABLE", "SERVERS_TITLE",
           "SERVERS_LEAD", "NAV_LABEL", "PAGE", "capabilities", "nav_items", "add_routes"]

SERVERS_PATH = "/servers"
SERVERS_CAPABILITY = "servers.view"
#: a member who may read the console may read the fleet (the dashboard's own role model)
SERVERS_ROLES: tuple[str, ...] = lists_page.READ_ONLY_ROLES
SERVERS_TABLE = "servers"
SERVERS_TITLE = i18n._("Servers")
SERVERS_LEAD = i18n._("Every server on every node, in full. A row you may operate carries its "
                      "controls on the row; acting on one affects that server alone.")
NAV_LABEL = i18n._("Servers")

PAGE = lists_page.ListPage(table=SERVERS_TABLE, path=SERVERS_PATH, capability=SERVERS_CAPABILITY,
                           roles=SERVERS_ROLES, title=SERVERS_TITLE, lead=SERVERS_LEAD,
                           nav_label=NAV_LABEL, scope_grants=True, writes=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {SERVERS_PATH: SERVERS_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (PAGE.nav_item(),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Servers route to the shell's own router and return it."""
    return lists_page.add_list_route(router, PAGE)
