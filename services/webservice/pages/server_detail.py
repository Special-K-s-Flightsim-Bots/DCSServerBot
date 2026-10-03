"""The per-server page and its **Configuration** tab — and (M2) the **Missions** tab and download.

A server gets its own page: Overview / Players / Missions / Log plus a Configuration tab. The module
adds two routes — ``GET /servers/{name:path}`` (the ``:path`` converter carries a name containing
``/``) and ``GET /servers/{name:path}/missions/download`` (the Missions tab's one read control,
registered FIRST so the page's greedy converter cannot swallow it) — and reuses the shell, the
sidebar, the table component and the read models rather than inventing a second page framework.

The name is resolved through :func:`services.webservice.pages.dashboard.scoped_server`, so the hoster
scope and the not-found refusal come for free. The page keeps the gate ``/servers`` has
(``servers.view``); the Configuration tab is gated on its own capability ``servers.config``
(``Admin`` only, NO scope grant — a server's config carries secrets), and the Missions tab and its
download on ``missions.view`` (the server page's own read roles PLUS a scope grant, so a hoster sees
their own servers' missions).

A capability gates the TAB and its request, not merely its markup: a viewer without ``servers.config``
sees no Configuration tab (and a manual ``?tab=configuration`` is REFUSED with the console's own 403),
a viewer without ``missions.view`` sees no Missions tab (and ``?tab=missions`` is REFUSED), and the
download route carries the same ``missions.view`` the tab's link reads — never hidden-and-served.

Only the READ lives here; the config WRITE is a single POST in ``pages/actions.py`` (the action
``set_server_config``), which the tab's form posts straight to. This module adds no write handler and
no second write path. The Missions tab touches no mission file: the list and the bytes both come
through the mission plugin's READ actions (``pages/actions.mission_list`` / ``download_mission``).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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
    "MISSIONS_TAB", "MISSIONS_CAPABILITY", "MISSIONS_ROLES",
    "MISSIONS_DOWNLOAD_PATH", "MISSIONS_DOWNLOAD_MAX_BYTES", "MISSIONS_DOWNLOAD_MEDIA_TYPE",
    "TAB_ORDER", "OPTIONAL_TAB_ORDER", "TAB_LABELS", "DEFAULT_TAB", "CONFIG_TEMPLATE",
    "PAGE_TITLE_SUFFIX", "capabilities", "nav_items", "add_routes",
    "normalise_tab", "tabs_for", "tab_views", "config_request_refused", "tab_request_refused",
    "mission_download_path", "mission_rows", "add_mission_download_route",
]

log = logging.getLogger(__name__)

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

#: The capability that gates the MISSIONS TAB, the link to it and the mission-download route.
#: Declared with the SAME read roles as the server page (``servers.view``) PLUS a scope grant, so a
#: hoster (a manager) sees the missions of THEIR servers and nobody else's — the link, the request
#: refusal and the download route all read THIS one predicate through ``permissions.allows``.
MISSIONS_CAPABILITY = "missions.view"
MISSIONS_ROLES: tuple[str, ...] = servers_page.SERVERS_ROLES

#: the tab that carries a server's mission list
MISSIONS_TAB = "missions"

CONFIG_TEMPLATE = "server_detail.html"
PAGE_TITLE_SUFFIX = " — DCSServerBot"

#: the tab that carries the DCS configuration
CONFIG_TAB = "configuration"

#: every tab, in render order. Overview/Players/Missions/Log are offered to any viewer of the page;
#: ``missions`` only to a viewer holding ``missions.view``; ``configuration`` only to a viewer holding
#: ``servers.config``.
TAB_ORDER: tuple[str, ...] = ("overview", "players", MISSIONS_TAB, "log", CONFIG_TAB)
OPTIONAL_TAB_ORDER: tuple[str, ...] = ("overview", "players", MISSIONS_TAB, "log")

TAB_LABELS: dict[str, str] = {
    "overview": "Overview", "players": "Players", MISSIONS_TAB: "Missions", "log": "Log",
    CONFIG_TAB: "Configuration",
}

DEFAULT_TAB = "overview"

#: THE MISSION-DOWNLOAD route — a literal TEMPLATE path with the server's own ``{name:path}`` prefix
#: (so a name containing ``/`` still resolves, exactly as the page route does) plus one fixed suffix.
#: It answers GET, renders no chrome, and is registered BEFORE the page route so the suffix is not
#: swallowed by the greedy ``{name:path}`` of ``GET /servers/{name}``. The MISSION is a QUERY
#: parameter (an index or a name, never a path), so nothing a request states can become a directory.
MISSIONS_DOWNLOAD_PATH = "/servers/{name:path}/missions/download"

#: HOW MUCH OF A MISSION THIS CONSOLE WILL HAND OVER IN ONE RESPONSE. The rule: a mission AT OR
#: BELOW this size is served whole; one ABOVE it is REFUSED with its own sentence and served not at
#: all — never a truncated ``.miz`` that downloads like a complete one. 100 MiB is the SAME cap the
#: mission-file action declares for an UPLOAD (``plugins/mission/actions.py``), so a mission this
#: console accepted can always be handed back.
MISSIONS_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024

#: the content type of a served mission — a ``.miz`` is a binary archive.
MISSIONS_DOWNLOAD_MEDIA_TYPE = "application/octet-stream"

#: anything that is not a safe filename character, for the Content-Disposition header (config data
#: must not shape a header, even a name the action already validated).
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


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


def tabs_for(roles, *, manager: bool = False, config_manager: bool = False) -> tuple[str, ...]:
    """The tabs a viewer holding *roles* (and, for a manager, the SCOPE) may be offered — and RENDER.

    ONE answer for the strip and for the tab in force: ``missions`` is offered exactly when
    ``missions.view`` allows it, and ``configuration`` exactly when ``servers.config`` allows it —
    the SAME predicates the route's refusal reads, so a link and the refusal cannot disagree.

    TWO manager facts, because the two gated tabs declare DIFFERENT scope rules: ``missions.view``
    carries the server page's own scope grant and admits any manager of a server (``manager``, the
    general fact from ``permissions.manages_console``), while ``servers.config`` admits only a
    restricted-scope viewer WITHOUT a cluster role (``config_manager`` — a server's config carries
    secrets, so a cluster role is never a "manager ONLY"). Keeping them separate is what stops one
    tab's rule from widening the other's.
    """
    tabs: list[str] = []
    for key in OPTIONAL_TAB_ORDER:
        if key == MISSIONS_TAB:
            if permissions.allows(MISSIONS_CAPABILITY, roles or (), manager=manager):
                tabs.append(key)
            continue
        tabs.append(key)
    if permissions.allows(CONFIG_CAPABILITY, roles or (), manager=config_manager):
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


def tab_request_refused(tab: str) -> HTTPException:
    """The refusal a request for a GATED tab this viewer may not open gets — naming that tab's rule.

    ONLY a gated tab (``missions`` / ``configuration``) can reach here: every other tab is offered to
    any viewer of the page. The requirement text is the refused tab's OWN capability, so the sentence
    a person reads names the right rule — a crafted ``?tab=missions`` answers the missions
    requirement, never the configuration's.
    """
    capability = CONFIG_CAPABILITY if tab == CONFIG_TAB else MISSIONS_CAPABILITY
    requirement = permissions.requirement_text(capability)
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
    """The capability declaration for the paths this module registers, plus the two tab capabilities.

    ``servers.config`` (the READ) is declared ``("Admin",)`` WITH a scope grant: an Admin reads
    everything, and a MANAGER (an identity whose scope holds a server's ``managed_by``) may open the
    tab for THEIR servers — with the denied rows (``core.server_config``) omitted. A DCS
    Admin gets no capability: ``allows`` is satisfied by the ``Admin`` role OR by a managing scope,
    and a DCS Admin's scope is unscoped.

    ``missions.view`` carries the SAME read roles as the server page PLUS a scope grant, so a hoster
    sees the missions of their own servers. It also gates the mission-download route, declared here
    for ``MISSIONS_DOWNLOAD_PATH`` — the ONE predicate the tab link, the tab request and the
    download route all read.
    """
    permissions.declare_capability(CONFIG_CAPABILITY, CONFIG_ROLES, scope_grants=True)
    permissions.declare_capability(MISSIONS_CAPABILITY, MISSIONS_ROLES, scope_grants=True)
    return {SERVER_DETAIL_PATH: SERVER_DETAIL_CAPABILITY,
            MISSIONS_DOWNLOAD_PATH: MISSIONS_CAPABILITY}


def nav_items():
    """No sidebar entry: the per-server page is reached from a Server row, not from the nav.

    (A config page carries no separate nav link, so the sidebar is identical for an Admin and a DCS
    Admin, and this page adds no item.)
    """
    return ()


def mission_download_path(server_name: str) -> str:
    """The URL of *server_name*'s mission download BASE — the name as ONE percent-encoded segment.

    The MISSION rides in a QUERY parameter (``?mission=<index|name>``), never in the path: this
    console never builds a filesystem path from request data, so there is nothing a request can turn
    into a directory component. A name carrying a ``/`` is encoded here (``%2F``) and decoded again by
    the route's ``{name:path}`` parameter, exactly as the page URL is.
    """
    return f"/servers/{quote(readmodels.text(server_name), safe='')}/missions/download"


def mission_rows(data: dict, server_name: str) -> list[dict]:
    """The rows the Missions tab renders, from the ``get_mission_list`` action's ``data``.

    ONE row per LOGICAL mission — the action already resolves the newest physical copy (primary /
    ``.dcssb`` / ``*.orig``) and names it once, so a mission that exists as two files is ONE row. Each
    row carries the ``index`` (every write posts it; it is NO LONGER rendered as a ``#N`` label), the
    logical name, the missions-directory-relative ``path`` (NEVER an absolute server path), the copy
    ``kind`` (kept in the data for the read model, NOT rendered — the ``primary`` / ``secondary`` /
    ``orig`` tag was dropped, M9), whether it is the loaded mission (``current``) and whether it is the
    START one (``active`` — the row ``listStartIndex`` names, a DIFFERENT fact the tab marks with the
    ``START`` tag while ``current`` gets the live dot), and the download link (the mission as a
    1-based index — never a path).
    """
    base = mission_download_path(server_name)
    rows: list[dict] = []
    for entry in data.get("missions") or []:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        rows.append({
            "index": index,
            "name": readmodels.text(entry.get("name")),
            "path": readmodels.text(entry.get("path")),
            "copy": readmodels.text(entry.get("copy")),
            "current": bool(entry.get("current")),
            "active": bool(entry.get("active")),
            "resolved": bool(entry.get("resolved", True)),
            "download": f"{base}?mission={quote(str(index), safe='')}",
        })
    return rows


def add_mission_download_route(router: APIRouter) -> APIRouter:
    """Add THE mission-download route (``GET``) — one row's own READ control.

    A GET and only a GET: nothing changes, so there is no CSRF dependency. It hands over ONE
    mission's bytes, on demand, through the ``download_mission`` READ action — the console never
    opens a file, resolves a ``.dcssb`` copy or reads ``missionList`` itself. The MISSION is an
    index or a name (a query parameter, never a path): the action resolves and validates it, so a
    request that tries to name a PATH simply resolves to nothing.

    THE FAILURES ARE EACH THEIR OWN SENTENCE, never a zero-byte download:

    * no mission stated, or a mission the action cannot resolve (unknown name, server the caller
      cannot reach) — a 4xx with the action's own sentence, or "required" for an empty request;
    * a NON-POSITIVE numeric index (``?mission=0``) — a 404 saying plainly that the console's mission
      indexes are 1-based, never "mission '0' not found" (which does not say why);
    * anything else the action raises, and a result that carries NO bytes — an empty payload
      included — 502;
    * a mission above :data:`MISSIONS_DOWNLOAD_MAX_BYTES` — 413, naming the size and the limit.

    A SERVER WHOSE NAME ITSELF ENDS IN ``/missions/download`` cannot be swallowed by this route: its
    own page URL carries the SAME shape as a download URL for a shorter server, so a request that
    states NO mission is first checked against the LONGER name (``<name>/missions/download``) through
    the SAME scoped lookup the page uses; when that is a server the caller can see, ITS PAGE is
    rendered — the download route must never make a server's page unreachable.
    """
    @router.get(MISSIONS_DOWNLOAD_PATH, name="mission-download")
    async def mission_download(request: Request, name: str, mission: str = "") -> Response:
        requested = readmodels.text(name)
        raw = readmodels.text(mission)
        if not raw:
            # No mission stated: this path may BE a server's own page. Guard the name — resolve the
            # LONGER name (the fixed suffix belongs to it) and, when it is a visible server, render
            # its page. Otherwise the missing mission is the honest answer.
            colliding = f"{requested}/missions/download"
            if dashboard_page.server_named(request, colliding) is not None:
                return await _render_server_detail(request, colliding)
            return _refuse(400, "A mission index or name is required to download a mission.")
        if raw.isdigit() and int(raw) < 1:
            # The console's mission indexes are 1-BASED (the number its tab shows and its row links
            # post), so index 0 — or anything below it — names nothing. Refused in plain words via
            # the ONE sentence every console surface uses, never resolved as if it were a mission
            # ("Mission '0' not found"), which does not say WHY 0 cannot be one.
            return _refuse(404, actions_page.mission_index_required(raw))
        # an INDEX (all digits) or a NAME — never a path. A path resolves to nothing in the action,
        # which is the honest answer.
        target = int(raw) if raw.isdigit() else raw
        result = await actions_page.download_mission(request, requested, target)
        if result is None:  # pragma: no cover - the access gate refuses an unresolved identity
            return _refuse(403, "Not authorized.")
        if not bool(getattr(result, "success", False)):
            message = readmodels.text(getattr(result, "message", "")) or "The mission could not be read."
            status = 404 if "not found" in message.lower() else 502
            return _refuse(status, message)
        content = getattr(result, "content", None)
        if not isinstance(content, (bytes, bytearray)) or not len(content):
            # NO usable bytes: a non-bytes value (an agent's failure code) AND a zero-byte payload
            # are the same answer — a successful read of an EMPTY file must never become a
            # zero-byte 200 that downloads like a real mission.
            filename = readmodels.text(getattr(result, "filename", "")) or requested
            return _refuse(502, f"Mission '{filename}' answered with no file content.")
        payload = bytes(content)
        if len(payload) > MISSIONS_DOWNLOAD_MAX_BYTES:
            filename = readmodels.text(getattr(result, "filename", "")) or requested
            return _refuse(413, f"Mission '{filename}' is {len(payload)} bytes, above the "
                                f"{MISSIONS_DOWNLOAD_MAX_BYTES}-byte limit this console hands over "
                                f"in one download.")
        filename = _safe_filename(getattr(result, "filename", "") or "mission.miz")
        return Response(content=payload, media_type=MISSIONS_DOWNLOAD_MEDIA_TYPE,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return router


def _refuse(status_code: int, sentence: str) -> PlainTextResponse:
    """The honest refusal a download answers with: ONE sentence in plain text, never an empty file.

    Modeled on the node-log route's own refusal: a ``PlainTextResponse`` and NOT an
    ``HTTPException``, whose rendered page would talk about AUTHORIZATION (false for a mission that
    simply is not there). A download has no page to render its failure into, so the sentence IS the
    answer — and a zero-byte 200 that downloads like an empty mission is exactly what the card
    forbids.
    """
    log.info("Mission download answered %d: %s", status_code, sentence)
    return PlainTextResponse(sentence + "\n", status_code=status_code)


def _safe_filename(name: str) -> str:
    """A logical ``.miz`` name as a SAFE download filename: config data must not shape a header."""
    cleaned = _UNSAFE_FILENAME.sub("_", readmodels.text(name)).strip("._")
    return cleaned or "mission.miz"


async def _render_server_detail(request: Request, name: str) -> HTMLResponse:
    """Render a server's page for *name* — the ONE body the page route and the download route share.

    Shared so the collision guard in :func:`add_mission_download_route` (a server whose own name ends
    in ``/missions/download``) can render that server's page without a second implementation of it.
    The name is resolved through :func:`dashboard.scoped_server`, so the scope and the refusal are
    identical however this was reached.
    """
    environment = getattr(request.app.state, "webui_templates", None)
    if environment is None:  # pragma: no cover - installed by the shell
        raise HTTPException(status_code=503,
                            detail="The admin web UI templates are not installed.")
    registrar = getattr(request.app.state, "webui_registrar", None)
    # THE ONE place a name becomes a server for a console READ — scoped, refusal-safe
    server = dashboard_page.scoped_server(request, name)
    server_name = readmodels.text(attr(server, "name", None)) or name

    roles = permissions.role_names_for(request)
    # THE GENERAL manager fact: what the SIDEBAR and the MISSIONS tab's scope grant read.
    manager = permissions.manages_console(request)
    # THE CONFIG TAB'S OWN MANAGER FACT: a manager of the config tab is a restricted-scope viewer
    # WITHOUT a cluster role — the same rule the write action applies.
    config_viewer = config_manager(request)
    allowed_tabs = tabs_for(roles, manager=manager, config_manager=config_viewer)
    tab = normalise_tab(request.query_params.get("tab"))
    if tab not in allowed_tabs:
        # the tab exists but this viewer may not open it: REFUSE the request, naming ITS OWN
        # requirement — never serve it hidden.
        raise tab_request_refused(tab)

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
    # no-store: this is the page a mission write returns to, and its list must be re-rendered from
    # the post-write state — never served from the browser's cache (Frank's stale-mission-list
    # report). The download route shares this body for its name-collision case, so both get it.
    return HTMLResponse(environment.get_template(CONFIG_TEMPLATE).render(**context),
                        headers={"Cache-Control": "no-store"})


def add_routes(router: APIRouter) -> APIRouter:
    """Add the mission-download route and the per-server route to the shell's own router.

    The DOWNLOAD route is registered FIRST, deliberately: its path is ``/servers/{name:path}/missions/
    download`` and the page route's ``{name:path}`` is greedy (``.*``), so on this router's
    match-in-order resolution the download must be seen before the page or a real server name ending
    in ``/missions/download`` — and the download URL itself — would be swallowed by the page route.
    The reverse collision (a server's OWN page under that shape) is the download route's to resolve:
    see :func:`add_mission_download_route`.
    """
    add_mission_download_route(router)

    @router.get(SERVER_DETAIL_PATH, response_class=HTMLResponse, name="page-server-detail")
    async def server_detail(request: Request, name: str):
        return await _render_server_detail(request, name)

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
    if tab == MISSIONS_TAB:
        # THE MISSIONS LIST — read through the READ action, never off ``server.settings``. A refused
        # result (the action absent, the server unreachable, the missions dir missing) renders an
        # EXPLAINED empty state carrying the action's own sentence, never a broken page.
        result = await actions_page.mission_list(request, server_name)
        context: dict = await _missions_write_context(request, server, server_name)
        if result is None or not bool(getattr(result, "success", False)):
            message = (readmodels.text(getattr(result, "message", ""))
                       if result is not None else "")
            context.update({"missions_available": False,
                            "missions_message": message or "The mission list could not be read."})
            return context
        data = getattr(result, "data", None)
        data = data if isinstance(data, dict) else {}
        rows = mission_rows(data, server_name)
        context.update({
            "missions_available": True,
            "missions": rows,
            # THE SERVER-RENDERED COUNT'S OWN TOTAL (M9-fix): the bar counts SELECTABLE rows only —
            # the running row's box is disabled and never counted — so with JavaScript off the number
            # shown here and, with JavaScript on, the script's ``N of M`` mean the SAME thing.
            "missions_selectable": sum(1 for row in rows if not row["current"]),
        })
        return context
    # overview / log: nothing beyond the shared server row the heading already reads
    return {}


async def _missions_write_context(request: Request, server, server_name: str) -> dict:
    """The WRITE surface of the Missions tab: which controls this viewer may use, and their data.

    ONE predicate per write (:func:`permissions.allows`, the SAME one the route's gate runs) ANDed
    with the ``action_available`` fact (the plugin's action is registered in this process), so a
    control is OMITTED — never drawn disabled — when the viewer may not use it or the installation
    cannot perform it. The scope is applied by the route (``resolve_scoped_server``); this decides
    presentation only. The ADD picker is read through the action, and only when the Add control is
    offered — a viewer without ``missions.add`` never triggers the read.
    """
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)

    def allowed(capability: str, kind: str) -> bool:
        return (permissions.allows(capability, roles, manager=manager)
                and actions_page.mission_write_available(kind))

    add_allowed = allowed(actions_page.MISSIONS_ADD_CAPABILITY, "add")
    load_allowed = allowed(actions_page.MISSIONS_LOAD_CAPABILITY, "load")
    activate_allowed = allowed(actions_page.MISSIONS_ACTIVATE_CAPABILITY, "activate")
    upload_allowed = allowed(actions_page.MISSIONS_UPLOAD_CAPABILITY, "upload")
    state = actions_page.state_of(getattr(server, "status", None))
    # THE M5 OFFLINE-ONLY WRITE: move up/down. Its control is offered only while the DCS process is
    # DOWN (``MISSION_LIST_OFFLINE_STATES``), because a file write made while it is up is reverted —
    # the tab states the reason where it omits the pair, and the action refuses a POST outside those
    # states too.
    reorder_capable = allowed(actions_page.MISSIONS_REORDER_CAPABILITY, "reorder")
    offline = state in actions_page.MISSION_LIST_OFFLINE_STATES
    # THE BULK REMOVE'S OWN GATE (M8/M9): every state except ``LOADING``. It is the ROW's delete,
    # repeated, so it follows the bot's own per-mission gate — the tab offers the selection AND its
    # bar in every state but ``LOADING``, and states the action's own refusal there (never the
    # offline-only sentence, which belongs to the move pair alone).
    delete_bulk_capable = allowed(actions_page.MISSIONS_DELETE_CAPABILITY, "delete_bulk")
    bulk_allowed = delete_bulk_capable and state in actions_page.MISSIONS_BULK_DELETE_STATES
    picker: list = []
    if add_allowed:
        found = await actions_page.addable_missions(request, server_name)
        if found is not None and bool(getattr(found, "success", False)):
            data = getattr(found, "data", None) or {}
            picker = list(data.get("missions") or []) if isinstance(data, dict) else []
    return {
        "missions_server_field": actions_page.SERVER_CONFIG_FIELD,
        "missions_server": server_name,
        "missions_field": actions_page.MISSION_FIELD,
        "missions_add_path": actions_page.MISSIONS_ADD_PATH,
        "missions_load_path": actions_page.MISSIONS_LOAD_PATH,
        "missions_activate_path": actions_page.MISSIONS_ACTIVATE_PATH,
        "missions_upload_path": actions_page.MISSIONS_UPLOAD_PATH,
        "missions_tab_url": server_url(server_name, tab=MISSIONS_TAB),
        #: the DIALOG path suffix (``/confirm``): the ADD opener and the selection bar post to
        #: ``<path><suffix>``, which RENDERS the console's one dialog component (``confirm.html``)
        #: rather than performing the write — the picker/load choice and the delete-file choice are
        #: made there (``pages/actions._mission_confirm_handler``).
        "missions_confirm_suffix": actions_page.CONFIRM_SUFFIX,
        "missions_add_allowed": add_allowed,
        # the Load control is offered only where ``load_mission`` accepts the state
        "missions_load_allowed": load_allowed and state in ("RUNNING", "PAUSED", "STOPPED"),
        #: THE LOAD CONTROL'S HONEST WORDING, state-dependent: at ``STOPPED`` a load ALSO becomes the
        #: start mission (the bot's ``loadMission`` sets ``listStartIndex`` and starts the server); in
        #: ``RUNNING``/``PAUSED`` it only loads now and the start index is untouched (``Set as start``
        #: is what moves that). The tab picks its per-row Load title off this fact.
        "missions_load_sets_start": state in actions_page.MISSION_LOAD_SETS_START_STATES,
        # the ``Set as start`` control is offered in every state but ``LOADING`` (the action refuses
        # that one, where the write would be silently reverted)
        "missions_activate_allowed": activate_allowed
        and state in actions_page.MISSIONS_ACTIVATE_STATES,
        #: M11: the activate CAPABILITY alone (ignoring the state) and the state that blocks the whole
        #: selection, so the bar's omission in ``LOADING`` is spoken in the ACTIVATE action's OWN
        #: sentence rather than the removal's — never a second copy of the per-state rule.
        "missions_activate_capable": activate_allowed,
        "missions_activate_reason": actions_page.MISSION_ACTIVE_LOADING_REFUSAL,
        "missions_select_blocked": state not in actions_page.MISSIONS_BULK_DELETE_STATES,
        #: THE BAR'S START REASON (M11): the sentence the activate route refuses a selection that is
        #: not exactly one mission with. Rendered onto the bar's button as ``data-sel-why`` so the
        #: client script states the SAME wording in the count when the tick is none or several — never
        #: a second copy of the rule written in JavaScript.
        "missions_activate_why": actions_page.MISSIONS_ACTIVATE_SELECTION_REFUSAL,
        "missions_upload_allowed": upload_allowed,
        # THE M5 OFFLINE-ONLY MOVE PAIR: its gate is the offline one; the reason is carried so the
        # tab can state it where it omits the pair.
        "missions_reorder_path": actions_page.MISSIONS_REORDER_PATH,
        "missions_direction_field": actions_page.MISSION_DIRECTION_FIELD,
        # THE STALE-PAGE COMPANION: the field each row posts beside its 1-based index, carrying the
        # row's LOGICAL name ("<index>=<name>") so a write whose list changed since render is refused.
        "missions_name_field": actions_page.MISSION_NAME_FIELD,
        "missions_reorder_capable": reorder_capable,
        "missions_reorder_allowed": reorder_capable and offline,
        "missions_delete_bulk_path": actions_page.MISSIONS_BULK_DELETE_PATH,
        "missions_delete_bulk_dialog": (actions_page.MISSIONS_BULK_DELETE_PATH
                                        + actions_page.CONFIRM_SUFFIX),
        "missions_delete_bulk_capable": delete_bulk_capable,
        # THE SELECTION AND ITS BAR: present in every state but ``LOADING`` (``bulk_allowed``), the
        # reason stated where they are absent — the action's OWN refusal sentence, not the move's.
        "missions_select_allowed": bulk_allowed,
        "missions_delete_bulk_reason": actions_page.MISSION_DELETE_LOADING_REFUSAL,
        "missions_offline": offline,
        "missions_offline_reason": actions_page.MISSION_LIST_OFFLINE_ONLY,
        "missions_picker": picker,
        "missions_can_add": bool(picker),
        "missions_write_available": actions_page.missions_write_available(),
        "notice": actions_page.pop_notice(request),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }
