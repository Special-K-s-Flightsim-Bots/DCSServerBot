"""The Bans page: every ban the caller may see, one row per ban ENTRY, with the LIFT.

The dashboard is an attention surface and its tabs are derived from ``readmodels.views.TABLE_COLUMNS``
(and rebuilt from the pure, no-I/O Overview on every live tick), so a ban list — a Postgres read —
cannot be a tab. It is its own page, built out of the parts the console already has, exactly like the
Servers/Nodes/Instances/Players pages:

* the SAME validated view layer (``?sort=``/``?dir=``) and the SAME table component
  (``templates/_table.html``) — the bans columns live in ``views.PAGE_COLUMNS``, so one sort layer
  serves every table;
* the SAME capability/nav mechanism — the route declares ``bans.view`` and the nav item names the
  same string, so the sidebar cannot offer a door the route refuses.

THE ROWS COME FROM ONE READ. The route awaits the console's own ``list_bans`` action (the scoped
read: an Admin sees every ban, a ``managed_by`` holder only their servers' rows) and hands the shaped
rows to :func:`services.webservice.pages.lists.page_context`, which builds the table. The row's LIFT
control is shaped from the SAME rows (the page stashes them on the request, and
``pages/actions.controls_for`` reads them), so ``controls ⊆ rows`` holds by construction.

THE DGSA VIEW OPTION. DGSA bans (the bot-wide rows the cloud plugin writes) are WITHHELD on the
opening page; a LINK to ``?dgsa=1`` re-requests the page and renders them, and while they are shown
the page says so and offers the link back. It is a GET parameter, never script, so the option is
linkable and works with JavaScript off — and it is a view option, not a permission: it filters
INSIDE the caller's already-scoped set (:func:`services.webservice.readmodels.bans.split_dgsa`), so
a narrowed caller sees no DGSA row either way and the withheld count cannot name a ban outside their
view. While rows are withheld the page SAYS how many: a ban list that dropped them silently would
let a globally banned player read as unbanned.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.actions import read_action

from .. import i18n, readmodels
from ..readmodels import bans as ban_model
from ..readmodels.views import DIRECTION_PARAM, SORT_PARAM
from . import lists as lists_page

__all__ = ["BANS_PATH", "BANS_CAPABILITY", "BANS_ROLES", "BANS_TABLE", "BANS_TITLE", "BANS_LEAD",
           "BANS_DGSA_LABEL", "BANS_DGSA_HIDE_LABEL", "BANS_DGSA_SHOWN",
           "PAGE", "capabilities", "nav_items", "add_routes", "bans_context", "dgsa_context"]

BANS_PATH = "/bans"
BANS_CAPABILITY = "bans.view"
#: a member who may read the console may read the ban list (the dashboard's own role model), and a
#: MANAGER reaches it through the capability's ``scope_grants`` — the scoped read holds them to their
#: own servers.
BANS_ROLES: tuple[str, ...] = lists_page.READ_ONLY_ROLES
BANS_TABLE = "bans"
BANS_TITLE = i18n._("Bans")
BANS_LEAD = i18n._("Every ban this installation holds on the servers you manage. A row you may act "
                   "on offers Lift; a DGSA ban can be lifted here only.")

#: THE VIEW OPTION's two words: the link that REVEALS the withheld DGSA rows and the link that puts
#: them away again, plus the inverse sentence the shown state reads. Marked here (the page owns its
#: own copy) and translated at render, like every other page string.
BANS_DGSA_LABEL = i18n._("Show DGSA bans")
BANS_DGSA_HIDE_LABEL = i18n._("Hide DGSA bans")
BANS_DGSA_SHOWN = i18n._("DGSA bans shown")

PAGE = lists_page.ListPage(table=BANS_TABLE, path=BANS_PATH, capability=BANS_CAPABILITY,
                           roles=BANS_ROLES, title=BANS_TITLE, lead=BANS_LEAD,
                           nav_label=BANS_TITLE, scope_grants=True, writes=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers."""
    PAGE.declare()
    return {BANS_PATH: BANS_CAPABILITY}


def nav_items():
    """The sidebar entry for this page — the capability's own name, never a second spelling."""
    return (PAGE.nav_item(),)


async def bans_context(request: Request) -> dict:
    """Everything the Bans page's template reads, in one value.

    The rows are read through the console's own scoped action (``list_bans``), so the page never
    builds a second query and the scope rule lives in ONE place. The rows are then split by the DGSA
    view option — the WITHHELD rows are not rendered and carry no control — and the rows that ARE
    rendered are stashed on the request for
    :func:`services.webservice.pages.actions.controls_for`, which is synchronous and cannot read the
    database itself.
    """
    # imported inside the function on purpose: ``pages.actions`` reaches this module for the page's
    # own path (``_bans_path``), so a module-level import would be a cycle.
    from . import actions as actions_page

    # A READ page renders for an authorised caller even with no identity BACKEND: the gate has
    # already authorised the request, and the roles/scope resolvers are what it read.
    ctx = actions_page.context_for(request)
    result = await read_action("list_bans", ctx)
    data = getattr(result, "data", None) or {}
    entries = data.get("bans") if getattr(result, "success", False) else None
    rows = readmodels.ban_views(entries or ())
    show = ban_model.dgsa_requested(request.query_params)
    shown, hidden = ban_model.split_dgsa(rows, show=show)
    request.state.bans_rows = shown
    context = lists_page.page_context(request, PAGE, rows=shown)
    if show:
        # the option rides in every sort link, so ordering the list does not silently switch the
        # DGSA rows back off — the same reason the toggle's own form carries the current sort.
        params = {**context["view"].params, ban_model.DGSA_PARAM: ban_model.DGSA_ON}
        table = context["table"]
        context["table"] = readmodels.table_view(PAGE.path, PAGE.table, shown,
                                                 sort=table.sort, direction=table.direction,
                                                 params=params, label=PAGE.title)
    context.update(dgsa_context(context, show=show, hidden=hidden))
    return context


def dgsa_context(context: dict, *, show: bool, hidden: int) -> dict:
    """The DGSA view option as data: the two sentences and their links, with no control of its own.

    The option is a GET parameter on this page's path, so it is TWO LINKS and not a form: the hidden
    state carries the link that reveals the rows (``?dgsa=1``), the shown state carries the inverse
    sentence and the link back. Both links carry the page's CURRENT sort/direction, so toggling the
    option does not throw the order away.

    ``hidden`` is the number of DGSA rows the caller's own scoped set held — never a wider count.
    """
    table = context["table"]
    # the carry keys are the VIEW LAYER's own parameter names (:data:`~..readmodels.views.SORT_PARAM`
    # / :data:`~..readmodels.views.DIRECTION_PARAM`), never a second spelling of ``sort``/``dir``.
    carry = {SORT_PARAM: table.sort, DIRECTION_PARAM: table.direction}
    return {
        "dgsa": {
            "show": show,
            "hidden_count": hidden,
            "show_label": BANS_DGSA_LABEL,
            "hide_label": BANS_DGSA_HIDE_LABEL,
            "shown_sentence": BANS_DGSA_SHOWN,
            "show_link": f"{BANS_PATH}?{urlencode({**carry, ban_model.DGSA_PARAM: ban_model.DGSA_ON})}",
            "hide_link": f"{BANS_PATH}?{urlencode(carry)}",
        }
    }


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Bans route to the shell's own router and return it."""
    PAGE.declare()

    @router.get(BANS_PATH, response_class=HTMLResponse, name=f"page-{BANS_TABLE}")
    async def _bans_page(request: Request):
        # no-store: a lift returns to this page, and the browser must not serve the pre-lift page
        # from its cache when it does (the same reason every console page sets it).
        return HTMLResponse(i18n.render(request, lists_page.LIST_TEMPLATE, **await bans_context(request)),
                            headers={"Cache-Control": "no-store"})

    return router
