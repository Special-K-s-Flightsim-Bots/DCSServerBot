"""The per-server page and its **Configuration** tab — the READ HALF of the console's config area.

A server gets its own page: Overview / Players / Missions / Log plus a Configuration tab. The module
adds one route — ``GET /servers/{name:path}`` (the ``:path`` converter carries a name containing
``/``) — and reuses the shell, the sidebar, the table component and the read models rather than
inventing a second page framework.

The name is resolved through :func:`services.webservice.pages.dashboard.scoped_server`, so the hoster
scope and the not-found refusal come for free. The page keeps the gate ``/servers`` has
(``servers.view``); the Configuration tab is gated on its own capability ``servers.config``
(``Admin`` only, NO scope grant — a server's config carries secrets).

The capability gates the TAB and its request, not merely its markup: a viewer without
``servers.config`` sees no Configuration tab, and a manual ``?tab=configuration`` is REFUSED with the
console's own 403 — never hidden-and-served.

Only the READ lives here; the WRITE is a single POST in ``pages/actions.py`` (the action
``set_server_config``), which the tab's form posts straight to. This module adds no write handler and
no second write path.
"""

from __future__ import annotations

import unicodedata

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import permissions, readmodels, session
from ..readmodels import serverconfig as server_config
from ..readmodels.access import attr
from ..readmodels.players import player_views
from ..readmodels.servers import server_url, server_view
# THE console's CLUSTER roles: a cluster role is never a "manager ONLY", so the config tab's manager
# scope grant excludes one — the same rule the write action applies.
from ..scope import CLUSTER_ROLES
# THE MANAGER DENY-LIST: the ONE declaration (``core.server_config``) the write actions also read, so
# the rows this page OMITS and the keys the action refuses cannot disagree. Imported here — a page
# module — and never in a read model, which stays free of ``core``.
from core.server_config import CHANNELS_ITEM, MANAGER_DENIED
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import servers as servers_page

__all__ = [
    "CRUMB_GROUP", "SERVER_DETAIL_PATH", "SERVER_DETAIL_CAPABILITY",
    "CONFIG_CAPABILITY", "CONFIG_ROLES", "CONFIG_TAB",
    "TAB_ORDER", "OPTIONAL_TAB_ORDER", "TAB_LABELS", "DEFAULT_TAB", "CONFIG_TEMPLATE",
    "PAGE_TITLE_SUFFIX", "capabilities", "nav_items", "add_routes",
    "normalise_tab", "tabs_for", "tab_views", "config_request_refused",
]

#: renders in the top bar's breadcrumb (chrome), same group as the Servers list page
CRUMB_GROUP = servers_page.lists_page.CRUMB_GROUP

#: the per-server route. The ``{name:path}`` CONVERTER is deliberate: the ONE encoder
#: (:func:`~..readmodels.servers.server_url`) encodes every reserved character — including ``/``,
#: which it writes as ``%2F`` — so a server name may contain slashes. The ASGI server decodes the
#: request path (``%2F`` -> ``/``) BEFORE routing, which turns the name into MORE than one path
#: segment and makes the default ``{name}`` converter (``[^/]+``) fail to match: the request lands on
#: 404 instead of the page. ``{name:path}`` matches ``.*`` (slashes included), so the decoded name
#: arrives whole and :func:`dashboard.scoped_server` resolves it exactly as it does for any other
#: name. NO second decode is applied on purpose: the server has already decoded the path, so a
#: ``name`` holding a literal percent-escape (``%2F`` typed as characters, encoded ``%252F``) would
#: be corrupted by one. The link SHAPE is unchanged — the path is still ``/servers/<encoded-name>``
#: — so every bookmarked, logged or pinned URL keeps working.
SERVER_DETAIL_PATH = "/servers/{name:path}"

#: The page keeps the same gate the Servers list page has (``servers.view``).
SERVER_DETAIL_CAPABILITY = servers_page.SERVERS_CAPABILITY

#: The capability that gates the CONFIGURATION TAB and the request for it. Declared with
#: ``roles=("Admin",)`` and a SCOPE GRANT: an Admin reads everything, and a MANAGER (an identity whose
#: scope holds a server's ``managed_by``) may open the tab for THEIR servers — the denied rows
#: (``core.server_config``) are OMITTED (not built), and the write action refuses a denied key. A DCS Admin
#: gets no capability: ``allows`` needs the ``Admin`` role OR a managing scope, and its scope is
#: unscoped.
CONFIG_CAPABILITY = "servers.config"
CONFIG_ROLES: tuple[str, ...] = ("Admin",)

CONFIG_TEMPLATE = "server_detail.html"
PAGE_TITLE_SUFFIX = " — DCSServerBot"

#: the tab that carries the DCS configuration
CONFIG_TAB = "configuration"

#: every tab, in render order. Overview/Players/Missions/Log are offered to any viewer of the page;
#: ``configuration`` only to a viewer holding ``servers.config``.
TAB_ORDER: tuple[str, ...] = ("overview", "players", "missions", "log", CONFIG_TAB)
OPTIONAL_TAB_ORDER: tuple[str, ...] = ("overview", "players", "missions", "log")

TAB_LABELS: dict[str, str] = {
    "overview": "Overview", "players": "Players", "missions": "Missions", "log": "Log",
    CONFIG_TAB: "Configuration",
}

DEFAULT_TAB = "overview"


def _clean_word(value, limit: int = 40) -> str:
    """*value* as a control-free, trimmed, capped word — the same cleaning the dashboard applies."""
    if value is None:
        return ""
    text = str(value)
    cleaned = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    return cleaned.strip()[:limit]


def normalise_tab(value) -> str:
    """The tab key in force: one of :data:`TAB_ORDER`, else :data:`DEFAULT_TAB`. Nothing else passes."""
    key = _clean_word(value).lower()
    return key if key in TAB_ORDER else DEFAULT_TAB


def tabs_for(roles, *, manager: bool = False) -> tuple[str, ...]:
    """The tabs a viewer holding *roles* (and, for a manager, the SCOPE) may be offered — and RENDER.

    ONE answer for the strip and for the tab in force: the optional tabs are always offered, and
    ``configuration`` is offered exactly when ``permissions.allows`` says so — the SAME predicate the
    route's refusal reads, so the link and the refusal cannot disagree. ``manager`` is the console's
    third kind of identity: a viewer whose SCOPE holds a server's ``managed_by`` satisfies the tab's
    scope grant, so the flag must reach ``allows`` or a manager would be refused the tab they are
    entitled to.
    """
    tabs = list(OPTIONAL_TAB_ORDER)
    if permissions.allows(CONFIG_CAPABILITY, roles or (), manager=manager):
        tabs.append(CONFIG_TAB)
    return tuple(tabs)


def config_request_refused() -> HTTPException:
    """The refusal a request for the Configuration tab without the capability gets (``403``).

    The console's own 403 shape (a page for a browser, JSON for an API caller, decided once by the
    shell's handler factory). Raised by the *handler* because the page route itself is reachable —
    the tab is not a separate route — so its detail names the same requirement the gate would.
    """
    requirement = permissions.requirement_text(CONFIG_CAPABILITY)
    return HTTPException(status_code=403,
                         detail=f"Not authorized (needs one of: {requirement}).")


def config_manager(request: Request) -> bool:
    """Whether this request is a MANAGER for the CONFIGURATION tab: a restricted scope, NO cluster role.

    The config capability's scope grant is for the identity whose access is the ``managed_by`` scope
    ALONE. A cluster role (``Admin`` / ``DCS Admin``, :data:`..scope.CLUSTER_ROLES`) is never a
    "manager ONLY": a DCS Admin reaches the console through its ROLE (which does not include
    ``servers.config``), so a DCS Admin that also declares a scope is NOT offered the tab — the same
    rule the write action applies. Using ``manages_console`` alone would count that account as a
    manager, which is right for the server/player rows but wrong for the config tab.

    Read from the SAME predicates the gate and the action use (``permissions.manages_console`` and
    ``permissions.role_names_for``), so the tab, the request refusal and the action cannot disagree.
    """
    if not permissions.manages_console(request):
        return False
    return not (set(CLUSTER_ROLES) & permissions.role_names_for(request))


def tab_views(server_name: str, tab: str, allowed: tuple[str, ...]) -> list[dict]:
    """The tab strip as data: key, label, whether active, and the link it renders.

    Built from the tabs this viewer may be offered (:func:`tabs_for`), so a tab they may not open is
    not in the strip and cannot be discovered by reading the markup either. Each link is built by the
    ONE encoder (:func:`~..readmodels.servers.server_url`), so a name with a space, ``&`` or ``#``
    opens the right tab instead of truncating the path.
    """
    return [{"key": key, "label": TAB_LABELS[key], "active": key == tab,
             "link": server_url(server_name, tab=key)}
            for key in allowed]


# ---------------------------------------------------------------------------------------------
# the read data of the four non-config tabs — smaller than the Configuration tab on purpose;
# every figure comes from an existing read model, nothing is invented here.
# ---------------------------------------------------------------------------------------------


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers, plus ``servers.config``.

    ``servers.config`` (the READ) is declared ``("Admin",)`` WITH a scope grant: an Admin reads
    everything, and a MANAGER (an identity whose scope holds a server's ``managed_by``) may open the
    tab for THEIR servers — with the denied rows (``core.server_config``) omitted. A DCS
    Admin gets no capability: ``allows`` is satisfied by the ``Admin`` role OR by a managing scope,
    and a DCS Admin's scope is unscoped.
    """
    permissions.declare_capability(CONFIG_CAPABILITY, CONFIG_ROLES, scope_grants=True)
    return {SERVER_DETAIL_PATH: SERVER_DETAIL_CAPABILITY}


def nav_items():
    """No sidebar entry: the per-server page is reached from a Server row, not from the nav.

    (A config page carries no separate nav link, so the sidebar is identical for an Admin and a DCS
    Admin, and this page adds no item.)
    """
    return ()


def add_routes(router: APIRouter) -> APIRouter:
    """Add the per-server route to the shell's own router and return it."""

    @router.get(SERVER_DETAIL_PATH, response_class=HTMLResponse, name="page-server-detail")
    async def server_detail(request: Request, name: str):
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
        registrar = getattr(request.app.state, "webui_registrar", None)
        # THE ONE place a name becomes a server for a console READ — scoped, refusal-safe
        server = dashboard_page.scoped_server(request, name)
        server_name = readmodels.text(attr(server, "name", None)) or name

        roles = permissions.role_names_for(request)
        manager = permissions.manages_console(request)
        # THE CONFIG TAB'S OWN MANAGER FACT: a manager of the config tab is a restricted-scope viewer
        # WITHOUT a cluster role — the same rule the write action applies. ``manager`` stays the general
        # fact for the SIDEBAR (a scoped DCS Admin still sees the server pages).
        config_viewer = config_manager(request)
        allowed_tabs = tabs_for(roles, manager=config_viewer)
        tab = normalise_tab(request.query_params.get("tab"))
        if tab not in allowed_tabs:
            # the tab exists but this viewer may not open it (only ``configuration`` can be here):
            # REFUSE the request — never serve it hidden.
            raise config_request_refused()

        context = {
            "title": server_name,
            "page_title": server_name,
            "crumb": f"{CRUMB_GROUP} / {servers_page.SERVERS_TITLE} / {server_name}",
            "server_name": server_name,
            "server": server_view(server),
            "tab": tab,
            "tabs": tab_views(server_name, tab, allowed_tabs),
            "config_allowed": permissions.allows(CONFIG_CAPABILITY, roles, manager=config_viewer),
            "nav_groups": dashboard_page.nav_groups(registrar, roles,
                                                    current=servers_page.SERVERS_PATH,
                                                    manager=manager),
            "user": dashboard_page.identity_summary(request),
            "pills": [f"server {server_name}"],
        }
        context.update(await _tab_context(request, server, server_name, tab))
        return HTMLResponse(environment.get_template(CONFIG_TEMPLATE).render(**context))

    return router


async def _tab_context(request: Request, server, server_name: str, tab: str) -> dict:
    """The data one tab renders. Every branch reads an existing read model — no new source."""
    if tab == CONFIG_TAB:
        # THE MANAGER DENY-LIST: a manager's view OMITS the denied rows (they are not built) and the
        # whole channels card, rather than offering a control the action would refuse. The same
        # declaration the actions enforce, read here for presentation only — the refusal itself stays
        # server-side, in the action.
        denied = MANAGER_DENIED if config_manager(request) else {}
        # THE COALITION FACE: the cleartext is read through the READ ACTION (it lives in the bot's
        # database, not in ``server.settings``), so the page cannot read it in-process. ``None`` (the
        # action absent or refused) leaves the view's coalition rows empty and the card unrendered.
        coalitions = await actions_page.coalition_values(request, server_name)
        view = server_config.server_config_view(
            server, servers_yaml_mtime=actions_page._servers_yaml_mtime(),
            denied=denied, channels_denied=CHANNELS_ITEM in denied,
            coalitions=coalitions)
        action = actions_page.SERVER_CONFIG_ACTIONS[0]
        channels_action = actions_page.SERVER_CHANNEL_ACTIONS[0]
        coalition_action = actions_page.SERVER_COALITION_ACTIONS[0]
        return {
            "config": view,
            "group_labels": server_config.GROUP_LABELS,
            # THE WRITE: one action, declared once, posted directly — the tab renders a form bound to
            # it. NO other path exists: no state-changing GET, no second implementation.
            "config_action_path": action.path,
            "config_action_key": action.key,
            "config_pulse": actions_page.config_pulse(server, server_name, action.key),
            "config_available": actions_page.config_write_available(),
            "config_revision": view.revision,
            # THE CHANNELS WRITE: the bot face's OWN action, declared once, posted by its own form,
            # with its own capability; applied immediately (no state gate).
            "channels_action_path": channels_action.path,
            "channels_action_key": channels_action.key,
            "channels_pulse": actions_page.channels_pulse(server, server_name, channels_action.key),
            "channels_available": actions_page.channels_write_available(),
            "channel_unset_value": server_config.CHANNEL_UNSET_VALUE,
            # THE COALITION WRITE: its own action, form and capability; NO pulse (no observable moves).
            "coalitions_action_path": coalition_action.path,
            "coalitions_action_key": coalition_action.key,
            "coalitions_available": actions_page.coalitions_write_available(),
            "config_revert_field": actions_page.SERVER_CONFIG_REVERT_FIELD,
            "notice": actions_page.pop_notice(request),
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
        }
    if tab == "players":
        rows = player_views(server_name, attr(server, "players", {}) or {})
        view = readmodels.view_state(
            server_url(server_name),
            {"players": tuple(rows)},
            params=readmodels.view_params(request.query_params),
            carry={"tab": "players"})
        players_tab = server_url(server_name, tab="players")
        return {
            "table": view.tables["players"], "view": view,
            "search_action": players_tab,
            "search_clear": players_tab,
            "search_hidden": {"tab": "players"},
            "empty": {"players": readmodels.NO_PLAYERS_MESSAGE,
                      "players_matched": "No players match this search."},
        }
    # overview / missions / log: nothing beyond the shared server row the heading already reads
    return {}
