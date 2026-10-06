"""The per-server page and its **Configuration** tab — and (M2) the **Missions** tab and download.

A server gets its own page: Overview / Players / Missions / Log plus a Configuration tab. The module
adds four routes — ``GET /servers/{name:path}`` (the ``:path`` converter carries a name containing
``/``), ``GET /servers/{name:path}/status`` (the status poll, L5), ``GET
/servers/{name:path}/missions/download`` (the Missions tab's one read control) and the two log
routes (``.../log/window`` and ``.../log/download``) — registered FIRST so the page's greedy converter
cannot swallow them, and reuses the shell, the sidebar, the table component and the read models rather
than inventing a second page framework.

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

import asyncio
import logging
import re
import unicodedata
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import i18n, permissions, readmodels, session
# THE ONE guarded sentence renderer (``say``) the write dialogs use: the log-window JSON messages are
# rendered for the REQUEST's language here, server-side, so the JavaScript carries no translation
# logic (``templating.SENTENCE_GLOBAL``).
from ..templating import SENTENCE_GLOBAL
from ..readmodels import dcslog as dcs_log
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
from . import logs as logs_page
from . import nodes as nodes_page
from . import servers as servers_page

__all__ = [
    "CRUMB_GROUP", "SERVER_DETAIL_PATH", "SERVER_DETAIL_CAPABILITY",
    "CONFIG_CAPABILITY", "CONFIG_ROLES", "CONFIG_TAB",
    "MISSIONS_TAB", "MISSIONS_CAPABILITY", "MISSIONS_ROLES",
    "MISSIONS_DOWNLOAD_PATH", "MISSIONS_DOWNLOAD_MAX_BYTES", "MISSIONS_DOWNLOAD_MEDIA_TYPE",
    "LOG_TAB", "LOG_READ_CAPABILITY", "LOG_WINDOW_PATH", "log_window_path",
    "EVENTS_TAB", "LOG_DOWNLOAD_PATH", "LOG_DOWNLOAD_MAX_BYTES", "log_download_path",
    "SERVER_STATUS_PATH", "SERVER_STATUS_SECONDS", "server_status_path",
    "log_artifacts", "debug_plugin_active",
    "TAB_ORDER", "OPTIONAL_TAB_ORDER", "TAB_LABELS", "DEFAULT_TAB", "CONFIG_TEMPLATE",
    "PAGE_TITLE_SUFFIX", "capabilities", "nav_items", "add_routes",
    "normalise_tab", "tabs_for", "tab_views", "config_request_refused", "tab_request_refused",
    "mission_download_path", "mission_rows", "add_mission_download_route",
    "add_server_status_route",
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

#: THE LOG TAB — the server's OWN DCS log (``{instance.home}/Logs/dcs.log``), tailed (L1). Its
#: capability is the CLUSTER LOG PANEL's own ``logs.view`` (``Admin`` only, no scope grant): the log
#: carries UCIDs and IPs, so who may read this tab is exactly who may read ``/logs`` — no new
#: capability is invented, and the tab's link, the ``?tab=log`` refusal and the window route all read
#: THIS one predicate.
LOG_TAB = "log"
LOG_READ_CAPABILITY = logs_page.LOGS_CAPABILITY

#: THE EVENTS TAB — the debug plugin's ``events.log``, offered ONLY while the ``debug`` plugin is
#: active for the server (see :func:`debug_plugin_active`). It reads the same way the log tab does
#: (tail, page back, level filter) and its downloads cover ONLY ``events.log`` / ``events.log.old``.
EVENTS_TAB = "events"

#: the ONE sentence a request for the Events tab's data gets while the debug plugin is off — used by
#: BOTH the page's tab refusal and the window/download routes, so the console says it one way. Marked
#: so the extractor sees it; the window route renders it for the request's language (see ``_rendered``).
EVENTS_REFUSED_SENTENCE = i18n._("The Events tab is only offered while the debug plugin is active "
                                 "for this server.")

#: THE LOG-WINDOW route — a literal TEMPLATE path with the server's own ``{name:path}`` prefix (so a
#: name containing ``/`` still resolves, exactly as the page route does) plus one fixed suffix. It
#: answers GET with JSON: a window of the server's log, at ``?offset=`` (follow) or ``?behind=``
#: (page back), filtered by ``?level=`` and (for the Events tab) selected by ``?which=events``. It is
#: registered BEFORE the page route so the suffix is not swallowed by the greedy ``{name:path}`` of
#: ``GET /servers/{name}``.
LOG_WINDOW_PATH = "/servers/{name:path}/log/window"

#: THE LOG-DOWNLOAD route — one artifact's own READ control. The artifact is named by an IDENTITY
#: from the tab's own listing (a file NAME, never a path) and RE-RESOLVED against a fresh enumeration
#: in the route, so nothing a request states is ever joined to a directory. Registered BEFORE the page
#: route for the same greedy-converter reason as the window route.
LOG_DOWNLOAD_PATH = "/servers/{name:path}/log/download"

#: THE STATUS route — the server page's own status poll (L5). A GET answering the CURRENT status as
#: the markup the page itself renders (the partial ``_server_status.html``), so the mark "at the top"
#: of the page and the Overview card's copy of it keep up with the server WITHOUT a reload. The page
#: is NOT wired to the console's live path (that is the dashboard's SSE stream, whose fragments are
#: dashboard-shaped); the status therefore has its OWN poll — ONE updater for ONE fact. Registered
#: BEFORE the page route so the greedy ``{name:path}`` of ``GET /servers/{name}`` cannot swallow it.
SERVER_STATUS_PATH = "/servers/{name:path}/status"

#: how often the status poll asks (seconds) — carried to the page as ``data-server-status-seconds``.
#: The status is a single small mark, so it does not need the log follower's tighter cadence.
SERVER_STATUS_SECONDS = 10

#: HOW MUCH OF A LOG THIS CONSOLE HANDS OVER IN ONE RESPONSE — the SAME cap the node-log download
#: already declares (``pages/nodes.LOG_DOWNLOAD_MAX_BYTES``, 10 MiB), named through it rather than
#: re-spelled, so "the console's download cap" has ONE number. A log above it is REFUSED with its own
#: sentence (naming the size, the limit and the path) — never a silent truncation, never a big blob
#: through the database.
LOG_DOWNLOAD_MAX_BYTES = nodes_page.LOG_DOWNLOAD_MAX_BYTES

#: every tab, in render order. Overview/Players are offered to any viewer of the page; ``missions``
#: only to a viewer holding ``missions.view``; ``log`` only to a viewer holding ``logs.view`` (the
#: cluster log panel's own capability, ``Admin`` only); ``configuration`` only to a viewer holding
#: ``servers.config``.
TAB_ORDER: tuple[str, ...] = ("overview", "players", MISSIONS_TAB, LOG_TAB, EVENTS_TAB, CONFIG_TAB)
OPTIONAL_TAB_ORDER: tuple[str, ...] = ("overview", "players", MISSIONS_TAB, LOG_TAB, EVENTS_TAB)

TAB_LABELS: dict[str, str] = {
    "overview": i18n._("Overview"), "players": i18n._("Players"), MISSIONS_TAB: i18n._("Missions"),
    LOG_TAB: i18n._("Log"), EVENTS_TAB: i18n._("Events"), CONFIG_TAB: i18n._("Configuration"),
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

#: the shape of a file-identity token this route could itself have emitted (:func:`identity_token`):
#: a decimal integer or float, of ANY length — a POSIX inode above 2**53 is accepted and compared as
#: the exact string it was, never parsed into a float that would lose its low bits.
_IDENTITY_TOKEN = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\Z")


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


def tabs_for(roles, *, manager: bool = False, config_manager: bool = False,
             debug_plugin: bool = False) -> tuple[str, ...]:
    """The tabs a viewer holding *roles* (and, for a manager, the SCOPE) may be offered — and RENDER.

    ONE answer for the strip and for the tab in force: ``missions`` is offered exactly when
    ``missions.view`` allows it, ``log`` exactly when ``logs.view`` (the cluster log panel's own,
    ``Admin`` only) allows it, ``events`` exactly when ``logs.view`` allows it AND the ``debug``
    plugin is active for the server (``debug_plugin``), and ``configuration`` exactly when
    ``servers.config`` allows it — the SAME predicates the route's refusal reads, so a link and the
    refusal cannot disagree.

    TWO manager facts, because the two gated tabs declare DIFFERENT scope rules: ``missions.view``
    carries the server page's own scope grant and admits any manager of a server (``manager``, the
    general fact from ``permissions.manages_console``), while ``servers.config`` admits only a
    restricted-scope viewer WITHOUT a cluster role (``config_manager`` — a server's config carries
    secrets, so a cluster role is never a "manager ONLY"). Keeping them separate is what stops one
    tab's rule from widening the other's. ``logs.view`` carries NO scope grant, so the log and events
    tabs are offered on the ROLE alone.
    """
    tabs: list[str] = []
    for key in OPTIONAL_TAB_ORDER:
        if key == MISSIONS_TAB:
            if permissions.allows(MISSIONS_CAPABILITY, roles or (), manager=manager):
                tabs.append(key)
            continue
        if key in (LOG_TAB, EVENTS_TAB):
            # both log-family tabs need the log panel's own capability; ``events`` ADDITIONALLY needs
            # the debug plugin active — a tab that could never serve is never drawn (Frank's ruling).
            if not permissions.allows(LOG_READ_CAPABILITY, roles or ()):
                continue
            if key == EVENTS_TAB and not debug_plugin:
                continue
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

    ONLY a gated tab (``missions`` / ``log`` / ``configuration``) can reach here: every other tab is
    offered to any viewer of the page. The requirement text is the refused tab's OWN capability, so
    the sentence a person reads names the right rule — a crafted ``?tab=log`` answers the log
    requirement (``logs.view``, Admin-only), never the missions' or the configuration's.
    """
    capability = {CONFIG_TAB: CONFIG_CAPABILITY, MISSIONS_TAB: MISSIONS_CAPABILITY,
                  LOG_TAB: LOG_READ_CAPABILITY, EVENTS_TAB: LOG_READ_CAPABILITY}.get(
        tab, MISSIONS_CAPABILITY)
    requirement = permissions.requirement_text(capability)
    return HTTPException(status_code=403,
                         detail=f"Not authorized (needs one of: {requirement}).")


def events_request_refused() -> HTTPException:
    """The refusal a request for the Events tab gets while the ``debug`` plugin is not active (403).

    The viewer may hold ``logs.view`` — this is NOT an authorization failure — but the tab's file is
    written by the debug plugin's DCS-side ``log.set_output('events', …)``, so with the plugin off
    there is nothing to serve and the tab is not offered at all. Refused rather than served: a dead
    tab is exactly what the card forbids.
    """
    return HTTPException(status_code=403, detail=EVENTS_REFUSED_SENTENCE)


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

    THE LOG TAB is gated by the CLUSTER LOG PANEL's own ``logs.view`` (declared by ``pages/logs``,
    ``Admin`` only, no scope grant) — reused here rather than re-spelled, and declared for
    ``LOG_WINDOW_PATH`` (the tab's own window route), so the tab link, the ``?tab=log`` refusal and
    the window route read ONE predicate. No new capability is invented.
    """
    permissions.declare_capability(CONFIG_CAPABILITY, CONFIG_ROLES, scope_grants=True)
    permissions.declare_capability(MISSIONS_CAPABILITY, MISSIONS_ROLES, scope_grants=True)
    logs_page.declare()
    return {SERVER_DETAIL_PATH: SERVER_DETAIL_CAPABILITY,
            MISSIONS_DOWNLOAD_PATH: MISSIONS_CAPABILITY,
            LOG_WINDOW_PATH: LOG_READ_CAPABILITY,
            LOG_DOWNLOAD_PATH: LOG_READ_CAPABILITY,
            # THE STATUS POLL is a READ of the server page's own data (the status the page head and
            # the Overview card render), so it carries the PAGE's capability and its scope — the same
            # predicate the page route is gated with, never a new one.
            SERVER_STATUS_PATH: SERVER_DETAIL_CAPABILITY}


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


# ---------------------------------------------------------------------------------- the Log tab (L1)
# The server's OWN DCS log, read as a BOUNDED WINDOW (never the whole file) and followed only while
# the tab is open. The bytes come from the node read path's WINDOWED sibling
# (``Node.read_file_window`` — a local ``seek`` on the master, the same one-shot drop-box row to an
# agent); everything below turns a window into COMPLETE lines and the cursors the browser follows.
# The pure half (path resolution, line format, window splitting) lives in
# ``readmodels/dcslog.py``; the RPC lives here because a read model must not await.

def log_window_path(server_name: str) -> str:
    """The URL of *server_name*'s log-window endpoint — the name as ONE percent-encoded segment."""
    return f"/servers/{quote(readmodels.text(server_name), safe='')}/log/window"


def server_status_path(server_name: str) -> str:
    """The URL of *server_name*'s status endpoint — the name as ONE percent-encoded segment (L5)."""
    return f"/servers/{quote(readmodels.text(server_name), safe='')}/status"


class _LogUnavailable(Exception):
    """A log the console cannot read, carrying the ONE honest sentence to render.

    ``msgid`` is the ``i18n._``-marked English TEMPLATE and ``params`` its operator data (a node name,
    a path, an error type) — carried APART so the sentence can be translated first and the data
    interpolated after (the log-window route renders it for the request's language through ``say``;
    the JavaScript only shows the result). ``sentence`` is the English rendering, for the callers that
    need a plain string (the tab's own initial render, a download refusal).

    ``pending`` says the file simply IS NOT THERE YET (a server that is still starting writes no
    ``dcs.log`` until it boots). That is a NORMAL state, not a failure: the tab must keep polling at
    the normal cadence so the log appears on its own, and only a GENUINE failure (a node that is
    unreachable, a permission error, a timeout, a bad answer) drives the follower's backoff.
    """

    def __init__(self, msgid: str, *, params=None, pending: bool = False):
        self.msgid = msgid
        self.params = dict(params or {})
        self.pending = pending
        try:
            self.sentence = msgid.format(**self.params)
        except (KeyError, IndexError, ValueError):  # pragma: no cover - msgid is source, not data
            self.sentence = msgid
        super().__init__(self.sentence)


#: the sentence for a node the cluster cannot reach — the same answer for offline and unknown. A
#: TRANSLATABLE TEMPLATE: the sentence is translated first, the node name interpolated after, so the
#: operator's own node name stays verbatim.
NODE_OFFLINE_SENTENCE = i18n._("Node '{node}' is offline or unknown, so its log cannot be read "
                               "from this console.")

#: the read failures — each its own TRANSLATABLE TEMPLATE, ``{node}``/``{error}`` filled in after the
#: sentence is translated (see :class:`_LogUnavailable`).
NODE_PERMISSION_SENTENCE = i18n._("Node '{node}' cannot read this server's log file "
                                  "(permission denied).")
NODE_TIMEOUT_SENTENCE = i18n._("Node '{node}' did not answer in time, so the log was not read.")
NODE_HANDOVER_SENTENCE = i18n._("Node '{node}' could not hand over the log ({error}).")
NODE_FAILURE_SENTENCE = i18n._("Node '{node}' answered with a failure code instead of the log.")

#: the artifacts-listing failures (the download list beside the log) — TRANSLATABLE TEMPLATES too.
ARTIFACTS_NO_DIR_SENTENCE = i18n._("No log directory yet for this server (looked for '{directory}' "
                                   "on node '{node}').")
ARTIFACTS_PERMISSION_SENTENCE = i18n._("Node '{node}' cannot list this server's log directory "
                                       "(permission denied).")
ARTIFACTS_TIMEOUT_SENTENCE = i18n._("Node '{node}' did not answer in time, so the log files were "
                                    "not listed.")
ARTIFACTS_FAILURE_SENTENCE = i18n._("Node '{node}' could not list the log files ({error}).")


def _node_offline_sentence(node_name: str) -> str:
    """The sentence for a node the cluster cannot reach — the same answer for offline and unknown."""
    return NODE_OFFLINE_SENTENCE.format(node=node_name)


def _reachable_log_node(request: Request, server):
    """The node *server* runs on, resolved through the SCOPED source — or ``None`` when unreachable.

    Reads the SAME scoped source the rows render from, so a node the caller cannot see is not
    resolvable here either, and treats an entry that is ``None`` as OFFLINE — exactly as the Nodes
    page does. A server whose node name is simply absent from the mapping falls back to the server's
    own node object (there is no reason to refuse a log the server itself could hand over).
    """
    name = readmodels.text(attr(attr(server, "node", None), "name", None))
    entries = getattr(dashboard_page.request_source(request), "nodes", None)
    if isinstance(entries, dict) and name:
        if name in entries:
            return entries.get(name)
        wanted = name.casefold()
        for key, entry in entries.items():
            if readmodels.text(key).casefold() == wanted:
                return entry
        return attr(server, "node", None)
    return attr(server, "node", None)


async def _read_window(node, path: str, node_name: str, *, offset=None,
                       behind=None) -> tuple[bytes, int, int | float]:
    """One window read through the node: ``(bytes, size, identity)``; every failure is ITS OWN sentence."""
    try:
        raw, size, identity = await node.read_file_window(path, length=dcs_log.WINDOW_BYTES,
                                                          offset=offset, behind=behind)
    except FileNotFoundError:
        # THE FILE IS NOT THERE YET (a server that is still starting writes no ``dcs.log``): a NORMAL
        # state, not a failure — marked ``pending`` so the follower keeps polling at the normal
        # cadence instead of backing off, and the log appears on its own.
        raise _LogUnavailable(dcs_log.MISSING_SENTENCE,
                              params={"path": path, "node": node_name}, pending=True)
    except PermissionError:
        raise _LogUnavailable(NODE_PERMISSION_SENTENCE, params={"node": node_name})
    except (TimeoutError, asyncio.TimeoutError):
        raise _LogUnavailable(NODE_TIMEOUT_SENTENCE, params={"node": node_name})
    except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
        log.warning("Server log window: node '%s' raised %s", node_name, type(ex).__name__,
                    exc_info=True)
        raise _LogUnavailable(NODE_HANDOVER_SENTENCE,
                              params={"node": node_name, "error": type(ex).__name__})
    if (not isinstance(raw, (bytes, bytearray)) or not isinstance(size, int)
            or not isinstance(identity, (int, float))):
        raise _LogUnavailable(NODE_FAILURE_SENTENCE, params={"node": node_name})
    return bytes(raw), size, identity


def _end_of(lines, fallback: int) -> int:
    """The absolute offset just PAST the last complete line, or *fallback* when there is none."""
    if not lines:
        return fallback
    offset, body = lines[-1]
    return offset + len(body) + 1


def identity_token(value) -> str:
    """A file identity as the LOSSLESS STRING token that crosses JSON (L2-fix).

    The identity is compared EQUAL or NOT-EQUAL and never as a magnitude, so a POSIX inode above
    ``2**53`` — real on network/64-bit-inode filesystems — must not go out as a JSON NUMBER: a
    JavaScript ``JSON.parse`` turns that into a double and loses the low bits, so a value that never
    changed would come back CHANGED and announce a rotation that never happened. The value the node
    read returns is therefore stringified at this ONE boundary (the node read is Python-to-Python and
    exact); the browser treats the result as an opaque token and echoes it back unchanged, and the
    route compares tokens as STRINGS, so the round trip cannot move the value on any node.
    """
    return "" if value is None else str(value)


def _empty_message(allowed) -> str:
    """The honest sentence for a page that came back with no line: the level filter's own when a
    filter is in force, otherwise the file's own "empty" message."""
    from ..readmodels.model import LOG_EMPTY_MESSAGE, LOG_LEVEL_EMPTY_MESSAGE
    return LOG_LEVEL_EMPTY_MESSAGE if allowed else LOG_EMPTY_MESSAGE


async def _collect_back(node, path: str, node_name: str, *, behind, allowed,
                        want: int) -> dict:
    """Walk BACK through windows until *want* matching lines are collected OR the start is reached.

    A FILTERED PAGE MUST NEVER LIE BY BEING EMPTY (Frank's report): if the window ending at *behind*
    holds no line at the chosen level, the walk continues into OLDER windows until the page is filled
    or the beginning of the file is reached, and it REPORTS which of the two happened (``at_start``).
    Never "take the last N rows, then filter", which returns almost nothing on a DEBUG-heavy tail.

    THE WALK CARRIES THE FILE'S IDENTITY (L2-fix). The FIRST read's identity is remembered, and any
    later read whose identity DIFFERS — or whose ``size`` fell below the cursor — means a ROTATION (or
    a truncation) landed MID-WALK: the walk STOPS right there, the lines gathered so far (the OLD
    file's alone) are the page, and ``rotated`` says so. Two files are NEVER spliced into one page —
    the same rule the follow path's identity check enforces, applied to the page-back walk.

    Returns a state dict: the rows OLDEST FIRST, the follow ``offset`` (taken from the first — the EOF
    — read, for the tail), the older cursor ``before`` (0 when the start was reached, so the button
    can say so), ``at_start``, ``rotated``, the partial flag, the size and the identity token.
    """
    collected: list[tuple[int, "dcs_log.LogLine"]] = []
    cursor = behind
    at_start = False
    rotated = False
    offset = 0
    partial = False
    size = 0
    identity: str | None = None      # the token of the file the walk STARTED on
    first = True
    while True:
        raw, size, current = await _read_window(node, path, node_name, behind=cursor)
        token = identity_token(current)
        if first:
            identity = token
        elif token != identity or size < cursor:
            # A ROTATION/TRUNCATION LANDED DURING THE WALK: end the page HERE. The lines gathered so
            # far are the PREVIOUS file's only; this read's bytes belong to the replacement and are
            # discarded, so no page can ever contain lines from two files.
            rotated = True
            break
        window_start = max(0, min(cursor, size) - len(raw))
        lines, partial = dcs_log.split_complete(raw, start=window_start,
                                                drop_leading=window_start > 0)
        if first:
            # the follow cursor only means anything for the EOF (tail) read; a page-back ignores it
            offset = _end_of(lines, window_start)
            first = False
        for off, body in reversed(lines):            # newest first WITHIN the window
            row = dcs_log.parse_line(body)
            if dcs_log.keeps(row, allowed):
                collected.append((off, row))
                if len(collected) >= want:
                    break
        if len(collected) >= want:
            break
        if window_start <= 0:                        # the start of the file: nothing older exists
            at_start = True
            break
        cursor = window_start
    collected.reverse()                              # oldest first, as the panel renders them
    rows = [row for _, row in collected]
    # a rotated walk has no older cursor to offer: continuing from it would read the REPLACEMENT file
    before = 0 if (at_start or rotated or not collected) else collected[0][0]
    return {"lines": rows, "offset": offset, "before": before, "at_start": at_start,
            "rotated": rotated, "partial": partial, "size": size, "identity": identity}


def _follow_state(raw: bytes, offset: int, size: int, identity: int | float, allowed) -> dict:
    """FOLLOW: the complete lines appended since *offset* AT LEVEL, plus the next offset.

    Forward only — a follower never walks BACK; a new line at another level is simply not shown here
    (it is not a lie: the level filter is the reader's own choice)."""
    lines, partial = dcs_log.split_complete(raw, start=offset, drop_leading=False)
    rows = [dcs_log.parse_line(body) for _, body in lines]
    rows = [row for row in rows if dcs_log.keeps(row, allowed)]
    return {"lines": rows, "offset": _end_of(lines, offset),
            "before": lines[0][0] if lines else offset,
            "partial": partial, "size": size, "identity": identity}


def _lines_html(environment, lines) -> str:
    """The lines rendered by the SAME partial the page includes — no markup lives in Python."""
    return environment.get_template("_dcs_log_lines.html").render(lines=lines)


def _available_payload(environment, state: dict, *, reset: bool = False,
                       message: str = "") -> dict:
    """A successful window answer: the rendered lines plus the cursors and identity the browser needs.

    ``identity`` is the file's identity AT THIS READ (see :func:`core.data.impl.nodeimpl.file_identity`)
    — the value the browser echoes back as ``?identity=`` so the next follow can be told a ROTATION
    (changed identity) from an APPEND (unchanged identity), even when the new file is already bigger.
    It is carried as the LOSSLESS STRING token (:func:`identity_token`), never a JSON number, so an
    inode above ``2**53`` survives the browser's ``JSON.parse`` unchanged. ``at_start`` (a filtered
    back/tail page) says the beginning of the file was reached, so the button can say so rather than
    offer a page that would come back empty. ``message`` carries the honest sentence a filtered page
    with no matching line renders — never a blank panel.
    """
    payload = {"available": True, "message": message,
               "lines": _lines_html(environment, state["lines"]),
               "size": state.get("size", 0),
               "identity": identity_token(state.get("identity")),
               "partial": bool(state.get("partial", False))}
    if "offset" in state:
        payload["offset"] = state["offset"]
    if "before" in state:
        payload["before"] = state["before"]
    if "at_start" in state:
        payload["at_start"] = bool(state["at_start"])
    if reset:
        payload["reset"] = True
    return payload


def _unavailable_payload(sentence: str, *, pending: bool = False) -> dict:
    """The honest empty answer: ONE sentence in the console's voice — never a blank panel.

    ``pending`` marks the NORMAL "no log file yet" state (the server is still starting): the follower
    keeps its normal cadence on such an answer, where a genuine failure (an unreachable node, a
    timeout) drives the backoff. The flag is carried in the body so the client can tell the two apart.
    """
    payload = {"available": False, "message": sentence, "lines": "", "size": 0}
    if pending:
        payload["pending"] = True
    return payload


def _rendered(environment, msgid: str, **params) -> str:
    """One log-window message, rendered for THIS request's language — server-side.

    The log follower shows whatever ``message`` the JSON body carries, in the DOM, VERBATIM: it holds
    no translation logic and no per-string mechanism of its own (I18N.md, "the console's JavaScript: a
    served message map"). So the sentence is rendered on the SERVER, through the SAME guarded ``say``
    global the write dialogs render their sentence records through (``templating.SENTENCE_GLOBAL``) —
    the ONE place a translation meets its placeholders — on the request's per-language environment. An
    ``i18n._``-marked English TEMPLATE becomes the request's own language here, and the operator data
    in ``params`` (a node name, a path, an error type) is interpolated AFTER the translation, so it
    stays verbatim. A complete sentence with no data passes no ``params``. The JavaScript carries none
    of this — it only swaps the finished sentence into the DOM.
    """
    return environment.globals[SENTENCE_GLOBAL]({"msgid": msgid, "params": params})


def _byte(value) -> int | None:
    """*value* as a non-negative whole number, or ``None`` — the route's one input validator."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _identity(value) -> str | None:
    """The client's HELD file-identity token (*value*) as a STRING, or ``None`` — the one validator.

    The browser echoes the identity it holds back as ``?identity=``; the value is compared EQUAL or
    NOT-EQUAL against the node's own token, as STRINGS, so an inode above ``2**53`` is not mangled by
    a float round trip. A value that could not have come from a prior read (not a decimal token) is
    treated as ABSENT, so a forged string is ignored and only the node's own identity decides.
    """
    if value is None:
        return None
    token = str(value).strip()
    if not _IDENTITY_TOKEN.match(token):
        return None
    return token


def debug_plugin_active(node) -> bool:
    """Whether the ``debug`` plugin is active on *node* — the console's OWN plugin-awareness.

    The SAME accessor the shell uses to discover a plugin's actions (``PluginManager(node).plugins``,
    which falls back to the node's configured plugin list): ONE spelling of "which plugins are
    loaded", never a second switch. A node that cannot answer is treated as having no debug plugin,
    so the Events tab is simply not offered (never a dead tab).
    """
    try:
        from core.plugin_manager import PluginManager
        names = list(PluginManager(node).plugins)
    except Exception:  # noqa: BLE001 - a node that cannot answer offers no plugin
        names = list(getattr(node, "plugins", None) or ())
    return "debug" in {readmodels.text(name).lower() for name in names}


def log_download_path(server_name: str) -> str:
    """The URL of *server_name*'s log-artifact download BASE — the name as ONE percent-encoded segment."""
    return f"/servers/{quote(readmodels.text(server_name), safe='')}/log/download"


def _human_size(size) -> str:
    """A byte count as a compact human string (``1.2 MiB``) — for the artifact listing."""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"  # pragma: no cover - unreachable, the loop returns at TiB


def _human_time(mtime) -> str:
    """A Unix epoch second as ``YYYY-MM-DD HH:MM`` (UTC) — for the artifact listing."""
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(mtime), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


async def log_artifacts(node, directory: str, node_name: str, patterns) -> tuple[list[dict], dict | None]:
    """The server's log artifacts as listing ROWS — enumerated SERVER-SIDE, newest first (L2).

    Returns ``(rows, record)``: each row carries the artifact's IDENTITY (its file name — never a
    path), its size (raw bytes and a human string) and its modification time; ``record`` is the honest
    failure sentence's ``{"msgid", "params"}`` (an ``i18n._``-marked template plus its operator data)
    a listing that could not be read renders, else ``None``. A RECORD rather than a composed string so
    the sentence is translated first and the node name / directory interpolated after, staying verbatim.
    The directory is enumerated on the node, so nothing a request states ever becomes a path — the
    download route re-resolves the row's identity against a FRESH enumeration of the same set.
    """
    try:
        entries = await node.list_files(directory, pattern=list(patterns))
    except FileNotFoundError:
        return [], {"msgid": ARTIFACTS_NO_DIR_SENTENCE,
                    "params": {"directory": directory, "node": node_name}}
    except PermissionError:
        return [], {"msgid": ARTIFACTS_PERMISSION_SENTENCE, "params": {"node": node_name}}
    except (TimeoutError, asyncio.TimeoutError):
        return [], {"msgid": ARTIFACTS_TIMEOUT_SENTENCE, "params": {"node": node_name}}
    except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
        log.warning("Log artifacts: node '%s' raised %s", node_name, type(ex).__name__,
                    exc_info=True)
        return [], {"msgid": ARTIFACTS_FAILURE_SENTENCE,
                    "params": {"node": node_name, "error": type(ex).__name__}}
    rows: list[dict] = []
    for path, size, mtime in entries:
        name = dcs_log.artifact_id(path)
        rows.append({"id": name, "name": name, "path": readmodels.text(path),
                     "size": int(size), "size_text": _human_size(size),
                     "mtime": _human_time(mtime)})
    return rows, None


def add_log_window_route(router: APIRouter) -> APIRouter:
    """Add THE log-window route (``GET``) — the Log / Events tab's follow / page-back endpoint.

    A GET that answers JSON: a WINDOW of the server's own log at ``?offset=`` (continue from a
    position — FOLLOW) or ``?behind=`` (read the window ending there — PAGE BACK), FILTERED by
    ``?level=`` (the SAME vocabulary the bot-log panel offers) and, for the Events tab, selected by
    ``?which=events``. A GET and only a GET: it changes nothing, so there is no CSRF dependency, and a
    POST is refused as a method the route does not answer. The gate is the cluster log panel's own
    ``logs.view`` (declared for :data:`LOG_WINDOW_PATH`), so the route is exactly as reachable as the
    tab.

    THE FILTER NEVER LIES BY BEING EMPTY: a back/tail page WALKS BACK through older windows until a
    full page is collected OR the start of the file is reached (:func:`_collect_back`), and reports
    which happened (``at_start``). A page that holds no matching line carries its own sentence.

    THE FAILURES ARE EACH THEIR OWN SENTENCE, carried in the JSON body (the tab has a panel to render
    them into): an unreachable node, no log file yet, a node that cannot read it, a node that does not
    answer, a node that answers a failure code, and the Events tab while its plugin is off.

    A NEW LOG means the view is RESET (``reset: true``) and the fresh file's tail is returned — two
    files are never spliced together silently. It is detected TWO ways (L1-fix): the offset no longer
    fits the file (``size < offset``, a truncation), OR the file's IDENTITY changed since the reader
    last looked (``?identity=``, a replacement) — the second catches a rotation whose replacement file
    is ALREADY BIGGER than the old offset, which a size check alone cannot see.
    """
    @router.get(LOG_WINDOW_PATH, name="server-log-window")
    async def server_log_window(request: Request, name: str, offset: str | None = None,
                                behind: str | None = None, identity: str | None = None,
                                level: str | None = None, which: str = "") -> Response:
        environment = i18n.environment_for(request)
        if not offset and not behind:
            # A server whose OWN name ends in ``/log/window`` shares this shape; with nothing stated
            # this path may BE that server's page — render it rather than a 400 nobody meant.
            colliding = f"{readmodels.text(name)}/log/window"
            if dashboard_page.server_named(request, colliding) is not None:
                return await _render_server_detail(request, colliding)
            return JSONResponse({"available": False, "lines": "", "size": 0,
                                 "message": _rendered(environment, i18n._(
                                     "An offset or a behind position is required."))},
                                status_code=400)
        server = dashboard_page.scoped_server(request, name)
        server_name = readmodels.text(attr(server, "name", None)) or name
        node_name = readmodels.text(attr(attr(server, "node", None), "name", None))
        events = readmodels.text(which) == dcs_log.EVENTS_WHICH
        node = _reachable_log_node(request, server)
        if events and not debug_plugin_active(node if node is not None else attr(server, "node", None)):
            return JSONResponse(_unavailable_payload(_rendered(environment, EVENTS_REFUSED_SENTENCE)),
                                status_code=403)
        path = dcs_log.events_log_path(server) if events else dcs_log.dcs_log_path(server)
        # THE EVENTS TAB HAS NO LEVEL FILTER (Frank's ruling): every entry the debug plugin writes is
        # DEBUG, so a filter there could only ever hide the tab's whole content. The shared vocabulary
        # is untouched — this tab simply keeps everything, whatever ``?level=`` the request carried.
        allowed = (dcs_log.LEVEL_FILTERS["all"] if events
                   else dcs_log.LEVEL_FILTERS[dashboard_page.log_level(request)])
        if node is None:
            return JSONResponse(_unavailable_payload(
                _rendered(environment, NODE_OFFLINE_SENTENCE, node=node_name)))
        try:
            if offset is not None:
                start = _byte(offset)
                if start is None:
                    return JSONResponse({"available": False, "lines": "", "size": 0,
                                         "message": _rendered(environment, i18n._(
                                             "The offset must be a non-negative whole number."))},
                                        status_code=400)
                held = _identity(identity)
                raw, size, current = await _read_window(node, path, node_name, offset=start)
                if size < start or (held is not None and identity_token(current) != held):
                    # A NEW LOG: the offset no longer fits the file (a truncation) OR the file's
                    # IDENTITY changed (a replacement — even one already bigger than the offset). Read
                    # the fresh file's tail and reset the view; never splice two files together.
                    state = await _collect_back(node, path, node_name, behind=dcs_log.BEHIND_END,
                                                allowed=allowed, want=dcs_log.TAIL_LINES)
                    message = "" if state["lines"] else _rendered(environment, _empty_message(allowed))
                    return JSONResponse(_available_payload(environment, state, reset=True,
                                                           message=message))
                return JSONResponse(_available_payload(
                    environment, _follow_state(raw, start, size, current, allowed)))
            end = _byte(behind)
            if end is None:
                return JSONResponse({"available": False, "lines": "", "size": 0,
                                     "message": _rendered(environment, i18n._(
                                         "The behind position must be a non-negative whole number."))},
                                    status_code=400)
            state = await _collect_back(node, path, node_name, behind=end, allowed=allowed,
                                        want=dcs_log.TAIL_LINES)
            if state["rotated"]:
                # A ROTATION LANDED DURING THE WALK: the page the walk gathered is the PREVIOUS
                # file's alone, and the browser resets the view and says the log restarted — two files
                # are never spliced into one page.
                return JSONResponse(_available_payload(environment, state, reset=True,
                                                       message=_rendered(environment,
                                                                         dcs_log.ROTATED_SENTENCE)))
            message = "" if state["lines"] else _rendered(environment, _empty_message(allowed))
            return JSONResponse(_available_payload(environment, state, message=message))
        except _LogUnavailable as ex:
            return JSONResponse(_unavailable_payload(_rendered(environment, ex.msgid, **ex.params),
                                                     pending=ex.pending))

    return router


async def _log_tab_context(request: Request, server, server_name: str, *,
                           which: str = "") -> dict:
    """A log tab's data: the config-driven path, the initial TAIL AT LEVEL, the cursors, the artifacts.

    A render-time read of a BOUNDED window (the last :data:`~..readmodels.dcslog.TAIL_LINES` complete
    lines AT THE CHOSEN LEVEL — the walk back fills the page rather than returning an empty one), like
    the Missions tab's render-time read, PLUS the server's log artifacts (enumerated server-side) for
    the download list. Any failure renders its OWN sentence — never a blank panel.
    ``log_window_url`` is the base URL the follow / page-back requests append ``?offset=`` /
    ``?behind=`` to; ``log_which`` selects the Events file on that shared route.
    """
    node_name = readmodels.text(attr(attr(server, "node", None), "name", None))
    events = readmodels.text(which) == dcs_log.EVENTS_WHICH
    path = dcs_log.events_log_path(server) if events else dcs_log.dcs_log_path(server)
    directory = dcs_log.dcs_log_dir(server)
    patterns = dcs_log.EVENTS_ARTIFACT_PATTERNS if events else dcs_log.DCS_ARTIFACT_PATTERNS
    chosen = dashboard_page.log_level(request)
    if events:
        # THE EVENTS TAB HAS NO FILTER (Frank's ruling): every entry is DEBUG, so the tab offers no
        # level links and keeps everything. The shared vocabulary is untouched — the Log tab still
        # offers it, and ``log_level`` is empty here so the follower sends no ``?level=``.
        allowed = dcs_log.LEVEL_FILTERS["all"]
        levels: list[dict] = []
    else:
        allowed = dcs_log.LEVEL_FILTERS[chosen]
        levels = [{"key": key, "label": label,
                   "href": f"{server_url(server_name, tab=LOG_TAB)}&level={key}",
                   "active": key == chosen}
                  for key, label in dcs_log.LEVEL_CHOICES]
    base = {
        "log_window_url": log_window_path(server_name),
        "log_follow_seconds": dcs_log.FOLLOW_SECONDS,
        "log_path": path,
        "log_node": node_name,
        "log_which": dcs_log.EVENTS_WHICH if events else "",
        "log_level": "" if events else chosen,
        "log_levels": levels,
        "log_download_base": log_download_path(server_name),
        "log_lines": (), "log_offset": 0, "log_before": 0, "log_size": 0, "log_identity": "",
        "log_at_start": False, "log_artifacts": (), "log_artifacts_note": "",
    }
    node = _reachable_log_node(request, server)
    # The tab's INITIAL note is the SAME sentence class the window route serves; it is rendered for
    # the request's language HERE too, so the first paint and every follow agree on the language.
    environment = i18n.environment_for(request)
    if node is None:
        return base | {"log_available": False,
                       "log_message": _rendered(environment, NODE_OFFLINE_SENTENCE, node=node_name)}
    artifacts, artifact = await log_artifacts(node, directory, node_name, patterns)
    note = _rendered(environment, artifact["msgid"], **artifact["params"]) if artifact else ""
    base = base | {"log_artifacts": artifacts, "log_artifacts_note": note}
    try:
        state = await _collect_back(node, path, node_name, behind=dcs_log.BEHIND_END,
                                    allowed=allowed, want=dcs_log.TAIL_LINES)
    except _LogUnavailable as ex:
        return base | {"log_available": False,
                       "log_message": _rendered(environment, ex.msgid, **ex.params)}
    message = "" if state["lines"] else _rendered(environment, _empty_message(allowed))
    return base | {"log_available": True, "log_message": message,
                   "log_lines": state["lines"], "log_offset": state["offset"],
                   "log_before": state["before"], "log_size": state["size"],
                   "log_identity": state["identity"], "log_at_start": state["at_start"]}


def _log_refuse(status_code: int, sentence: str) -> PlainTextResponse:
    """The honest refusal a log download answers with: ONE sentence in plain text, never an empty file.

    A ``PlainTextResponse`` and NOT an ``HTTPException`` (whose rendered page would talk about
    AUTHORIZATION, false for a log that is simply not there): a download has no page to render its
    failure into, so the sentence IS the answer.
    """
    log.info("Log download answered %d: %s", status_code, sentence)
    return PlainTextResponse(sentence + "\n", status_code=status_code)


def add_log_download_route(router: APIRouter) -> APIRouter:
    """Add THE log-download route (``GET``) — one artifact's own READ control on the Log / Events tab.

    A GET and only a GET: nothing changes, so there is no CSRF dependency. The gate is the cluster log
    panel's own ``logs.view`` (declared for :data:`LOG_DOWNLOAD_PATH`), so the route is exactly as
    reachable as the tab.

    THE PATH NEVER COMES FROM THE REQUEST. The caller names an artifact by the IDENTITY the tab's own
    listing produced (its file NAME); the route RE-ENUMERATES the server's log directory on the node
    and matches that name against the FRESH listing — the full path it then reads is the enumeration's
    own, never a join of request data onto a directory. A crafted id that names nothing in the fresh
    listing is refused, so it can never resolve to a path outside the log directory. The set enumerated
    is the DCS artifacts, PLUS the Events pair only while the debug plugin is active — so ``events.log``
    is served only when it may be, and the Events tab's downloads stay limited to its two files.

    THE FAILURES ARE EACH THEIR OWN SENTENCE, never a zero-byte download: no artifact stated, a node
    the cluster cannot reach, no such file (the listing does not carry it), a node that cannot read it,
    a node that does not answer, a node that answers a failure code, and a log above
    :data:`LOG_DOWNLOAD_MAX_BYTES` — the size, the limit AND the path on the server, so it can be
    fetched from the host. The size is checked BEFORE the bytes are read, so an oversized log is never
    moved through the database.
    """
    @router.get(LOG_DOWNLOAD_PATH, name="server-log-download")
    async def server_log_download(request: Request, name: str, artifact: str = "",
                                  which: str = "") -> Response:
        requested = readmodels.text(name)
        wanted = readmodels.text(artifact)
        if not wanted:
            # A server whose OWN name ends in ``/log/download`` shares this shape; with no artifact
            # stated this path may BE that server's page — render it rather than a 400 nobody meant.
            colliding = f"{requested}/log/download"
            if dashboard_page.server_named(request, colliding) is not None:
                return await _render_server_detail(request, colliding)
            return _log_refuse(400, "An artifact id is required to download a log file.")
        server = dashboard_page.scoped_server(request, name)
        node_name = readmodels.text(attr(attr(server, "node", None), "name", None))
        events = readmodels.text(which) == dcs_log.EVENTS_WHICH
        node = _reachable_log_node(request, server)
        if events and not debug_plugin_active(node if node is not None else attr(server, "node", None)):
            return _log_refuse(403, EVENTS_REFUSED_SENTENCE)
        if node is None:
            return _log_refuse(404, _node_offline_sentence(node_name))
        directory = dcs_log.dcs_log_dir(server)
        # THE EVENTS TAB'S DOWNLOADS ARE ITS TWO FILES (Frank's limit): ``which=events`` enumerates
        # ONLY the events pair, so a crafted id for another log is simply not in the listing. The log
        # tab enumerates only the DCS set.
        patterns = (list(dcs_log.EVENTS_ARTIFACT_PATTERNS) if events
                    else list(dcs_log.DCS_ARTIFACT_PATTERNS))
        try:
            entries = await node.list_files(directory, pattern=patterns)
        except FileNotFoundError:
            return _log_refuse(404, f"No log directory on node '{node_name}' yet "
                                    f"(looked for '{directory}').")
        except PermissionError:
            return _log_refuse(403, f"Node '{node_name}' cannot list this server's log directory "
                                    f"(permission denied).")
        except (TimeoutError, asyncio.TimeoutError):
            return _log_refuse(504, f"Node '{node_name}' did not answer in time, so the log files "
                                    f"were not listed.")
        except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
            log.warning("Log download: node '%s' raised %s listing", node_name, type(ex).__name__,
                        exc_info=True)
            return _log_refuse(502, f"Node '{node_name}' could not list the log files "
                                    f"({type(ex).__name__}).")
        chosen = None
        for path, size, mtime in entries:
            if dcs_log.artifact_id(path) == wanted:
                chosen = (path, int(size))
                break
        if chosen is None:
            return _log_refuse(404, f"No log file named '{wanted}' is in this server's log "
                                    f"directory on node '{node_name}'.")
        path, size = chosen
        if size > LOG_DOWNLOAD_MAX_BYTES:
            return _log_refuse(413, f"'{wanted}' is {size} bytes, above the "
                                    f"{LOG_DOWNLOAD_MAX_BYTES}-byte limit this console hands over in "
                                    f"one download. Fetch it from the host: {path}")
        try:
            data = await node.read_file(path)
        except FileNotFoundError:
            return _log_refuse(404, f"'{wanted}' was gone before it could be read (looked for "
                                    f"'{path}').")
        except PermissionError:
            return _log_refuse(403, f"Node '{node_name}' could not read '{wanted}' "
                                    f"(permission denied).")
        except (TimeoutError, asyncio.TimeoutError):
            return _log_refuse(504, f"Node '{node_name}' did not answer in time, so '{wanted}' was "
                                    f"not read.")
        except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
            log.warning("Log download: node '%s' raised %s reading '%s'", node_name,
                        type(ex).__name__, path, exc_info=True)
            return _log_refuse(502, f"Node '{node_name}' could not hand over '{wanted}' "
                                    f"({type(ex).__name__}).")
        if not isinstance(data, (bytes, bytearray)):
            # ``read_file`` is ``bytes | int`` and an ``int`` is a FAILURE CODE, never content.
            return _log_refuse(502, f"Node '{node_name}' answered with a failure code instead of "
                                    f"'{wanted}'.")
        payload = bytes(data)
        if len(payload) > LOG_DOWNLOAD_MAX_BYTES:
            return _log_refuse(413, f"'{wanted}' is {len(payload)} bytes, above the "
                                    f"{LOG_DOWNLOAD_MAX_BYTES}-byte limit this console hands over in "
                                    f"one download. Fetch it from the host: {path}")
        return Response(content=payload, media_type=dcs_log.artifact_media_type(wanted),
                        headers={"Content-Disposition":
                                 f'attachment; filename="{_safe_filename(wanted)}"'})

    return router


def add_server_status_route(router: APIRouter) -> APIRouter:
    """Add THE status route (``GET``) — the server page's own status poll (L5).

    A GET answering JSON with the CURRENT status as the markup the page itself renders (the partial
    ``_server_status.html``): the dot and the word, from the SAME read model the initial render used
    (``readmodels.servers.server_view``). The gate is the server page's own capability
    (``servers.view``, declared for :data:`SERVER_STATUS_PATH`) applied through the SCOPED name
    resolution, so this poll is exactly as reachable as the page it refreshes — a caller who may not
    open the page cannot poll its status either.

    A SERVER WHOSE NAME ITSELF ENDS IN ``/status`` cannot be swallowed by this route: its own page URL
    carries the SAME shape as a status URL for a shorter server, so the LONGER name
    (``<name>/status``) is checked first through the SAME scoped lookup — when that names a server the
    caller can see, ITS PAGE is rendered, and the status route must never make a server's page
    unreachable. The failure shapes are the page's own: an unknown name is the console's 404, and an
    out-of-scope one the console's 403 (both from ``scoped_server``).
    """
    @router.get(SERVER_STATUS_PATH, name="server-status")
    async def server_status(request: Request, name: str) -> Response:
        requested = readmodels.text(name)
        colliding = f"{requested}/status"
        if dashboard_page.server_named(request, colliding) is not None:
            return await _render_server_detail(request, colliding)
        server = dashboard_page.scoped_server(request, requested)
        view = server_view(server)
        html = i18n.render_fragment(request, "_server_status.html", status=view.status)
        # no-store: a polled status must always be the CURRENT one, never a cached mark.
        return JSONResponse({"status": html}, headers={"Cache-Control": "no-store"})

    return router


async def _render_server_detail(request: Request, name: str) -> HTMLResponse:
    """Render a server's page for *name* — the ONE body the page route and the download route share.

    Shared so the collision guard in :func:`add_mission_download_route` (a server whose own name ends
    in ``/missions/download``) can render that server's page without a second implementation of it.
    The name is resolved through :func:`dashboard.scoped_server`, so the scope and the refusal are
    identical however this was reached.
    """
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
    # THE DEBUG PLUGIN FACT: read the SAME way the shell discovers a plugin's actions — from the
    # server's own node — so the Events tab is offered exactly when it can serve (never a dead tab).
    events_offered = debug_plugin_active(_reachable_log_node(request, server)
                                         or attr(server, "node", None))
    allowed_tabs = tabs_for(roles, manager=manager, config_manager=config_viewer,
                            debug_plugin=events_offered)
    tab = normalise_tab(request.query_params.get("tab"))
    if tab not in allowed_tabs:
        # the tab exists but this viewer may not open it: REFUSE the request, naming ITS OWN
        # requirement — never serve it hidden. The Events tab is its own case: a viewer who HOLDS
        # ``logs.view`` is refused because the plugin is off, not for lack of a capability.
        if tab == EVENTS_TAB and permissions.allows(LOG_READ_CAPABILITY, roles):
            raise events_request_refused()
        raise tab_request_refused(tab)

    context = {
        "title": server_name,
        "page_title": server_name,
        "crumb": dashboard_page.crumb(CRUMB_GROUP, servers_page.SERVERS_TITLE,
                                     target=server_name),
        "server_name": server_name,
        "server": server_view(server),
        # THE STATUS POLL (L5): the URL and the cadence the page head carries so the mark "at the top"
        # (and the Overview card's copy) keep current without a reload.
        "server_status_url": server_status_path(server_name),
        "server_status_seconds": SERVER_STATUS_SECONDS,
        "tab": tab,
        "tabs": tab_views(server_name, tab, allowed_tabs),
        "config_allowed": permissions.allows(CONFIG_CAPABILITY, roles, manager=config_viewer),
        "nav_groups": dashboard_page.nav_groups(registrar, roles,
                                                current=servers_page.SERVERS_PATH,
                                                manager=manager),
        "user": dashboard_page.identity_summary(request),
        "pills": [{"msgid": i18n._("server %(name)s"), "params": {"name": server_name},
                   "text": f"server {server_name}"}],
    }
    context.update(await _tab_context(request, server, server_name, tab))
    # no-store: this is the page a mission write returns to, and its list must be re-rendered from
    # the post-write state — never served from the browser's cache (Frank's stale-mission-list
    # report). The download route shares this body for its name-collision case, so both get it.
    return HTMLResponse(i18n.render(request, CONFIG_TEMPLATE, **context),
                        headers={"Cache-Control": "no-store"})


def add_routes(router: APIRouter) -> APIRouter:
    """Add the mission-download route, the log-window route, the log-download route and the page.

    The THREE non-page routes are registered FIRST, deliberately: their paths are
    ``/servers/{name:path}/missions/download``, ``/servers/{name:path}/log/window`` and
    ``/servers/{name:path}/log/download``, and the page route's ``{name:path}`` is greedy (``.*``), so
    on this router's match-in-order resolution they must be seen before the page or a real server name
    ending in one of those suffixes — and the routes' own URLs — would be swallowed by the page route.
    The reverse collision (a server's OWN page under any of those shapes) is each route's own to
    resolve.
    """
    add_mission_download_route(router)
    add_log_window_route(router)
    add_log_download_route(router)
    add_server_status_route(router)

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
                      "players_matched": i18n._("No players match this search.")},
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
                            "missions_message": message or i18n._("The mission list could not be read.")})
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
    if tab == LOG_TAB:
        # THE SERVER'S OWN DCS LOG (L1): the config-driven path and the initial TAIL, read as a
        # BOUNDED window. Failures render their own sentence, never a blank panel.
        return await _log_tab_context(request, server, server_name)
    if tab == EVENTS_TAB:
        # THE DEBUG PLUGIN'S events.log (L2): the same presentation as the log tab, selected by
        # ``which=events`` on the shared window route; downloads limited to its two files.
        return await _log_tab_context(request, server, server_name, which=dcs_log.EVENTS_WHICH)
    # overview: nothing beyond the shared server row the heading already reads
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
