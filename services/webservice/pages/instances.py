"""The Instances page: every DCS instance on every node, in full.

One of the two CLUSTER lists (see :mod:`services.webservice.pages.lists`): the ports and the
node/server binding of every instance, which is cluster information rather than operations
information, so it declares the narrower role pair.

WHO READS IT, IN FULL: ``Admin``/``DCS Admin`` through a role, and a MANAGER through its scope
(``scope_grants``) — scoped to the instances bound to their own servers. A manager holds no node or
instance WRITE capability at all; node writes are Admin-only. See
:mod:`services.webservice.pages.lists`.

READ-ONLY: no start/stop/update/plugin control exists here, not even disabled.
"""
from __future__ import annotations

from fastapi import APIRouter

from .. import i18n
from . import lists as lists_page

__all__ = ["INSTANCES_PATH", "INSTANCES_CAPABILITY", "INSTANCES_ROLES", "INSTANCES_TABLE",
           "INSTANCES_TITLE", "INSTANCES_LEAD", "PAGE", "capabilities", "add_routes"]

INSTANCES_PATH = "/instances"
INSTANCES_CAPABILITY = "instances.view"
INSTANCES_ROLES: tuple[str, ...] = lists_page.CLUSTER_ROLES
INSTANCES_TABLE = "instances"
INSTANCES_TITLE = i18n._("Instances")
INSTANCES_LEAD = i18n._("Every instance the nodes carry, with its DCS and WebGUI ports. Read-only.")

#: NO NAV ENTRY: this page's content is the Dashboard's own Instances tab, so it is not in the main
#: menu (seen from the shell's `_core_router`, which registers this page's route but no nav item).
PAGE = lists_page.ListPage(table=INSTANCES_TABLE, path=INSTANCES_PATH,
                           capability=INSTANCES_CAPABILITY, roles=INSTANCES_ROLES,
                           title=INSTANCES_TITLE, lead=INSTANCES_LEAD,
                           scope_grants=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {INSTANCES_PATH: INSTANCES_CAPABILITY}


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Instances route to the shell's own router and return it."""
    return lists_page.add_list_route(router, PAGE)
