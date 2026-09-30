"""The standalone list pages — the full inventory behind the dashboard's attention view.

WHY THESE PAGES EXIST. The dashboard is deliberately an attention surface: it shows ONE list at a
time, its log placement follows the data, and its job is "what needs you now". An operator with 50
servers needs the opposite of that too — the whole list, linkable and bookmarkable, one page per
resource. That is what this module builds, and it builds it out of the parts the console already
has rather than as a second console:

* the SAME validated view layer (``?sort=``/``?dir=``/``?q=`` via
  :func:`services.webservice.readmodels.view_state`) — so a page that sorted differently from the
  dashboard tab for the same list would be a defect the shared call makes impossible;
* the SAME table component (``templates/_table.html``, which ``_state.html`` includes too), so
  there is one place where a column, a row or the players search is rendered;
* the SAME capability/nav mechanism (:mod:`services.webservice.permissions` and
  :class:`~services.webservice.registry.NavItem`) — the route declares the capability, the nav item
  names the same string, and ``visible_nav`` reads the very predicate the gate uses, so the sidebar
  cannot offer a door its route refuses;
* the read models' OWN empty-state copy (:func:`page_context` passes them through).

NO CAP HERE, and that is the point of the page: the read models hand over every row they have and
the table renders all of them. The dashboard's tab is the same list; the difference is that the
dashboard also has to share its width with a log column and a tab strip.

READ-ONLY EXCEPT THE SERVERS AND PLAYERS PAGES. The route here is a GET, no other method exists on
it, and the markup it renders writes nothing — but since W2 (the pause pilot) the Servers page
carries the console's write controls, and since W3 the Players page carries the Message control:
``writes=True`` on a page's spec makes :func:`page_context` hand the table the per-row control DATA
(``pages/actions``, asked for BY TABLE so one page can never be handed another's) plus the one-shot
notice of the last write, and ``_table.html`` renders each one as a POST form. The other two list
pages pass neither, so nothing about them changed. The forms a signed-in visitor meets elsewhere are
the shell's Sign out control and the Players page's search form, which is a GET and mutates nothing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import permissions, readmodels
from ..registry import NavItem
from . import dashboard as dashboard_page

__all__ = [
    "LIST_TEMPLATE", "CRUMB_GROUP", "READ_ONLY_ROLES", "CLUSTER_ROLES",
    "ListPage", "list_view", "page_context", "add_list_route",
]

log = logging.getLogger(__name__)

#: the ONE template every list page renders. One template for four pages on purpose: the four
#: differ by their spec (title, lead, path, table), never by their markup, so a change to a list
#: page's chrome or its table cannot land on one page and miss the other three.
LIST_TEMPLATE = "list.html"

#: rendered in the top bar's breadcrumb (chrome; the page name itself comes from the page's title)
CRUMB_GROUP = dashboard_page.CRUMB_GROUP

#: The dashboard's role model, reused: a member who may read the console may read the fleet and the
#: players. Named here rather than re-typed so the two pages cannot drift from it — and it is the
#: NARROWER model now (``Admin``, ``DCS Admin``): the console's third kind of reader is a MANAGER,
#: which is expressed by the capability's ``scope_grants`` and never as a role name.
READ_ONLY_ROLES: tuple[str, ...] = dashboard_page.DASHBOARD_ROLES

#: The cluster lists are the same two roles: Nodes and Instances are no more readable than Servers
#: now that ``DCS`` (the general member role) is out of the console entirely. Kept as its own name
#: because the two sets are conceptually different — the pages that render a server's own data
#: versus the pages that render the cluster's infrastructure — and a future divergence must be a
#: deliberate edit here rather than an accident of aliasing.
CLUSTER_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")


@dataclass(frozen=True, slots=True)
class ListPage:
    """One standalone list page: the table it renders, where it lives, and who may open it.

    The capability is declared with the roles here (:meth:`declare`) and named by both the route
    (:func:`add_list_route`) and the nav item (:meth:`nav_item`), which is what keeps the three from
    disagreeing — there is one string, held on this record.

    ``scope_grants`` is the console's access rule's third kind of reader (a MANAGER: an identity
    whose resolved scope holds a server's ``managed_by``). It defaults to ``False`` — the STRICTER
    answer — so a page added to the console has to say, here, that a manager may read it; the four
    pages of the console pass ``True`` because a hoster customer operates their own servers and
    reads no others (the scoped source guarantees the second half).
    """
    table: str
    path: str
    capability: str
    roles: tuple[str, ...]
    title: str
    lead: str
    nav_label: str
    scope_grants: bool = False
    #: whether this page's rows carry WRITE controls (``pages/actions``). Off by default — the
    #: stricter answer — so a page added to the console must say here that it renders controls, and
    #: the data a control is built from is never requested for a page that has none.
    writes: bool = False

    def declare(self) -> None:
        """Ensure the capability is declared. Idempotent for the same roles AND scope rule."""
        permissions.declare_capability(self.capability, self.roles,
                                      scope_grants=self.scope_grants)

    def nav_item(self) -> NavItem:
        """The sidebar entry for this page — the capability's own name, never a second spelling."""
        return NavItem(label=self.nav_label, url=self.path, capability=self.capability)


def list_view(request: Request, state: readmodels.Overview, path: str) -> readmodels.ViewState:
    """The validated view of THIS request for a standalone page.

    The dashboard resolves the same value through ``dashboard.view_state_for`` — and that helper is
    bound to the dashboard's path, so it cannot be reused here without its sort links pointing at
    ``/``. The call below is therefore the SAME layer, with this page's path: same allow-lists, same
    comparators, same search, same link builder, one function of the read model.
    """
    return readmodels.view_state(
        path,
        {"servers": state.servers, "nodes": state.nodes, "instances": state.instances,
         "players": state.players},
        params=readmodels.view_params(request.query_params))


def page_context(request: Request, page: ListPage) -> dict:
    """EVERYTHING the list template reads, in one value.

    The table is selected by the page's own spec, so a page cannot render another page's list, and
    the two counts the table needs for its search state come from the same :class:`ViewState` the
    table rows do — one source, so "1 of 2 players" and the single row cannot disagree.

    The SERVERS page additionally carries the write controls and the one-shot notice of a write
    (``pages/actions``): the control is DATA here and the markup is ``_table.html``'s, and both are
    built from the same request the page renders from. The other three pages pass neither, so
    ``_table.html`` renders no actions column for them at all.
    """
    environment = getattr(request.app.state, "webui_templates", None)
    registrar = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    view = list_view(request, state, page.path)
    context = {
        "title": page.title,
        "page_title": page.title,
        "lead": page.lead,
        "crumb": f"{CRUMB_GROUP} / {page.title}",
        "table": view.tables[page.table],
        "view": view,
        "empty": dashboard_page.empty_messages(state),
        # the search form posts back to the page it is on, and its only hidden state is what this
        # page carries (nothing: there is no log column and no tab here to preserve)
        "search_action": page.path,
        "search_clear": page.path,
        "search_hidden": {},
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=page.path,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
    }
    if page.writes:
        # the import is inside the branch on purpose: ``pages.actions`` reaches this module for the
        # page's own path, so a module-level import would be a cycle.
        from . import actions as actions_page

        context["notice"] = actions_page.pop_notice(request)
        # the controls are asked for BY TABLE: the page renders one table, so it can never be handed
        # another page's map (a servers page asking for server controls, a players page for player
        # ones) — and a table with no write at all gets an empty map, hence no column. The ORIGIN is
        # this page's own path, so a write returns here even when the same row is also on a dashboard
        # tab (card W4e, Defect 1).
        context["row_controls"] = actions_page.controls_for(request, page.table, page.path)
    return context


def add_list_route(router: APIRouter, page: ListPage) -> APIRouter:
    """Add one list page's route to the shell's own router and return it."""
    page.declare()

    @router.get(page.path, response_class=HTMLResponse, name=f"page-{page.table}")
    async def _list_page(request: Request):
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        return HTMLResponse(environment.get_template(LIST_TEMPLATE).render(
            **page_context(request, page)))

    return router
