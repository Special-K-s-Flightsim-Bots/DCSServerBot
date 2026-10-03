from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import permissions, readmodels
from ..registry import NavItem
from . import dashboard as dashboard_page

__all__ = [
    "LOGS_PATH", "LOGS_CAPABILITY", "LOGS_ROLES", "LOGS_TEMPLATE", "LOGS_TITLE", "LOGS_LEAD",
    "NAV_LABEL", "NAV_GROUP", "capabilities", "nav_items", "add_routes", "console_for",
]

log = logging.getLogger(__name__)

#: the path this page owns (a literal path — never a dynamic prefix, so a link to it can only go
#: where it says it goes)
LOGS_PATH = "/logs"

#: the capability the route declares AND the nav item names — one string, so the two cannot drift.
LOGS_CAPABILITY = "logs.view"

#: who may read the log
LOGS_ROLES: tuple[str, ...] = ("Admin",)

LOGS_TEMPLATE = "logs.html"
LOGS_TITLE = "Logs"
LOGS_LEAD = "The bot log, full width, with the same level filter and placement as the dashboard."
NAV_LABEL = "Logs"
NAV_GROUP = dashboard_page.NAV_GROUP
CRUMB_GROUP = dashboard_page.CRUMB_GROUP


def declare() -> None:
    """Ensure the capability is declared. Idempotent for the same role set (see the dashboard)."""
    permissions.declare_capability(LOGS_CAPABILITY, LOGS_ROLES)


declare()


# ------------------------------------------------------------------------------- registration

def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    declare()
    return {LOGS_PATH: LOGS_CAPABILITY}


def nav_items() -> tuple[NavItem, ...]:
    """The sidebar entry this page contributes — data, not markup."""
    return (NavItem(label=NAV_LABEL, url=LOGS_PATH, capability=LOGS_CAPABILITY),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Logs route to the shell's own router and return it."""

    @router.get(LOGS_PATH, response_class=HTMLResponse)
    async def logs(request: Request):
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        registrar = getattr(request.app.state, "webui_registrar", None)
        state = readmodels.overview(dashboard_page.request_source(request),
                                    level=dashboard_page.log_level(request))
        roles = permissions.role_names_for(request)
        view = dashboard_page.view_state_for(request, state)
        html = environment.get_template(LOGS_TEMPLATE).render(
            title=LOGS_TITLE,
            page_title=LOGS_TITLE,
            lead=LOGS_LEAD,
            crumb=f"{CRUMB_GROUP} / {LOGS_TITLE}",
            state=state,
            view=view,
            empty=dashboard_page.empty_messages(state),
            console=console_for(request, state, view),
            nav_groups=dashboard_page.nav_groups(registrar, roles, current=LOGS_PATH),
            user=dashboard_page.identity_summary(request),
            env=dashboard_page.environment_marker(state),
            pills=dashboard_page.status_pills(state),
            live=dashboard_page.live_controls(request),
        )
        # no-store: the Logs page is a console page like the rest — a navigation back to it must
        # re-render rather than come out of the browser's cache.
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return router


def console_for(request: Request, state: readmodels.Overview, view=None) -> dict:
    """The console context of the Logs page: the same chrome, with the log's own level links.

    Two differences from the dashboard's, both deliberate: the level filter's hrefs point at
    ``/logs`` (a filter on this page must not bounce the reader to another page), and the placement
    tags are ABSENT — the log IS the page here, so ``?log=`` has nothing to decide and offering it
    would be a control that lies.
    """
    console = dashboard_page.console_context(state, request, view)
    console["log"] = dashboard_page.log_view(state, dashboard_page.view_params(request),
                                             dashboard_page.carry_params(request), path=LOGS_PATH)
    console["log"]["links"] = []
    return console
