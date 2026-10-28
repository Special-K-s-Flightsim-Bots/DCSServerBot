"""The ModManager pages: a node's root mods, a server's Mods tab, and the dialogs both owners use.

TWO OWNERS, ONE MODULE, and the owner is a PATH SEGMENT (``/mods/<owner>/<target>/<verb>``) rather
than a second page module. The difference between the owners is what the service does with a package,
not a choice a page makes:

======================  =====================================================  ====================
``owner``               target                                              package folder
======================  =====================================================  ====================
``node``                a node                                              ``RootFolder`` — the
                                                                            DCS installation,
                                                                            shared by every
                                                                            instance on it
``server``              one server                                          ``SavedGames`` — one
                                                                            instance
======================  =====================================================  ====================

THE NODE PAGE (``/nodes/<node>/mods``) is the root half's one home: the installed rows, the state
strip and the two page-level doors. The SERVER half is a TAB on the server's own page, and this module
supplies its BODY (:func:`tab_context`) — the tab's route, its place in the tab order and the
capability it is declared with belong to the server page, which owns the tab strip.

READS ON RENDER, READS ON DEMAND. The installed rows are the ``mm_packages`` bookkeeping, read through
the ``list_installed_mods`` action: a database read, so the page renders while a node is offline and
asks no member anything. The package FOLDER is a node read and happens only when a dialog is
requested, because the folder's path carries variables that only the node owning the disk can expand
(``%USERPROFILE%``) — a render would have to RPC a node to say what the folder holds.

ONE NODE READ PER RENDER, AND IT MAY FAIL. The installed table's version facts — which versions exist
and which one is newest — are a SECOND read of the same render, and it is ONE call for the whole
table (``list_mod_versions``), cached per ``(node, folder, sorted package names)`` for
:data:`VERSIONS_TTL_SECONDS` so a refresh reuses it. That read decides two things and nothing else:
whether a row's change-version door is drawn (a package with no other version has none) and whether
the row carries an update chip. A node that does not answer leaves BOTH facts unknown, and unknown is
rendered as the page rendered them before the read existed — every row keeps its change door and no
row carries a chip. It never becomes an error on the page: the state strip already states the node's
state.

NO WRITE IS DRAWN THAT CANNOT RUN. Every door and every row control is a POST form carrying the
session's CSRF token and the target in the body, and each posts to its DIALOG, never to the action:
the dialog mints the one-shot confirm token (``pages/actions.mint_confirm_token``) and the action's
own route refuses a POST that did not come through it (``consume_confirm_token``). A confirmation a
``curl`` can skip is decoration.

THE DISPLAY GATE IS NOT THE AUTHORITY. A page states whether a write is possible right now — every
server on the node down for ``RootFolder``, that one server down for ``SavedGames`` — from the
console's own in-process source, never by asking a node. It decides which controls are DRAWN and it is
re-checked by the write routes, but the SERVICE is what may refuse a write (a state guard, a package
that vanished, an installation log that could not be recreated), and its refusal is rendered as its
own sentence on the page the write returns to. The two facts are allowed to disagree — a server may
start between the render and the POST — and both are shown rather than reconciled here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from markupsafe import escape

from core.actions import ActionContext, call_action, read_action
from core.const import DEFAULT_TAG

from .. import i18n, permissions, readmodels, session
from ..readmodels import mods as mod_model
from . import dashboard as dashboard_page
from . import nodes as nodes_page
from . import servers as servers_page

__all__ = [
    "OWNER", "MODS_VIEW_CAPABILITY", "MODS_VIEW_ROLES", "MODS_MANAGE_CAPABILITY", "MODS_MANAGE_ROLES",
    "NODE_OWNER", "SERVER_OWNER", "OWNERS", "INSTALL", "DOWNLOAD", "CHANGE", "REMOVE", "VERBS",
    "NODE_PAGE_PATH", "MODS_TAB", "MODS_TEMPLATE", "BODY_TEMPLATE", "PICK_TEMPLATE",
    "DOWNLOAD_TEMPLATE", "CONFIRM_TEMPLATE",
    "PACKAGE_FIELD", "VERSION_FIELD", "URL_FIELD", "TARGET_FIELD", "OWNER_FIELD",
    "DEFAULT_VERSION", "NODE_TITLE", "TAB_HEADING",
    "RELEASES_QUERY", "RELEASES_TTL_SECONDS", "RELEASES_DEBOUNCE_MS", "VERSIONS_UNREADABLE",
    "VERSIONS_TTL_SECONDS",
    "node_page_url", "dialog_url", "run_url", "releases_url", "declare", "capabilities", "nav_items",
    "add_routes", "register", "node_page_context", "tab_context",
]

log = logging.getLogger(__name__)

#: the registrar owner of these pages: one name, so a path claimed twice is refused loudly.
OWNER = "mods"


def _capability_declarations() -> None:
    """Declare both capabilities. Idempotent for the same roles AND the same scope rule."""
    permissions.declare_capability(MODS_VIEW_CAPABILITY, MODS_VIEW_ROLES, scope_grants=True)
    permissions.declare_capability(MODS_MANAGE_CAPABILITY, MODS_MANAGE_ROLES)


# ── the two gates ──────────────────────────────────────────────────────────
# ``mods.view`` owns ONE server's installed list and therefore carries the console's manager scope
# grant (a hoster customer reads their own instance). ``mods.manage`` owns every write AND the node
# page, and carries NO scope grant for the reason the node lifecycle controls carry none: a root
# package changes the game files of every instance on a machine, and a manager's scope is a set of
# SERVERS. A manager therefore reads the tab, sees no root card and is refused the node page.
MODS_VIEW_CAPABILITY = "mods.view"
MODS_VIEW_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")

MODS_MANAGE_CAPABILITY = "mods.manage"
MODS_MANAGE_ROLES: tuple[str, ...] = ("Admin",)

# ── the owners and the verbs ───────────────────────────────────────────────
NODE_OWNER = "node"
SERVER_OWNER = "server"
OWNERS: tuple[str, ...] = (NODE_OWNER, SERVER_OWNER)

INSTALL = "install"
DOWNLOAD = "download"
CHANGE = "change"
REMOVE = "remove"
VERBS: tuple[str, ...] = (INSTALL, DOWNLOAD, CHANGE, REMOVE)

# ── the paths ──────────────────────────────────────────────────────────────
#: THE NODE PAGE. One literal suffix per node, exactly like the node's own settings and log pages, so
#: a link to it can only go where it says it goes.
NODE_PAGE_PATH = "/nodes/{node}/mods"

#: THE TAB KEY the server page's Mods tab is opened with (``/fleet/<name>?tab=mods``).
MODS_TAB = "mods"

#: ONE namespace for both owners' dialogs and action routes: the owner is a path segment, so a
#: per-owner page module would be two copies of one surface. The node page's own path is separate
#: because it is a PAGE (it renders the installed list); everything a page posts to lives here.
_DIALOG_BASE = "/mods/{owner}/{target}"

#: the path parameter each owner's target is read from. A server name may carry a space or ``&``, so
#: it takes the greedy ``path`` segment; a node name is one segment like every other node route.
_TARGET_PARAM = {NODE_OWNER: "node", SERVER_OWNER: "name"}
_TARGET_SEGMENT = {NODE_OWNER: "{node}", SERVER_OWNER: "{name:path}"}

#: the two folders, by owner
_OWNER_FOLDER = {NODE_OWNER: mod_model.ROOT_FOLDER, SERVER_OWNER: mod_model.SAVED_GAMES}

# ── the release read ───────────────────────────────────────────────────────
#: THE VERSION READ: a GET beside the dialogs on the same per-owner base, so it carries no CSRF
#: token — it changes nothing. It answers the releases of the URL the download would fetch, which is
#: what the dialog's version pick offers instead of a blind text box.
_RELEASES_SEGMENT = "releases"

#: the query the read takes the URL from.
RELEASES_QUERY = "url"

#: how long ONE ``(node, url)`` answer is reused, in seconds. The dialog asks on every blur and every
#: re-open; GitHub's unauthenticated API allows 60 requests an hour, and a minute collapses a burst
#: without hiding a release published since.
RELEASES_TTL_SECONDS = 60.0

#: the client's debounce on the URL field, in milliseconds: a keystroke restarts it, a blur fires it.
RELEASES_DEBOUNCE_MS = 600

#: the answer cache, keyed by ``(node name, url)`` and stamped with the monotonic clock. Only a
#: COMPLETED answer is stored, so a GitHub failure is asked again rather than served from the cache.
_RELEASES: dict[tuple[str, str], tuple[float, list[str]]] = {}

#: the id of the line the dialog shows under the version field when this link offers no list.
VERSIONS_HINT_ID = "ver-hint"

# ── the installed table's version read ─────────────────────────────────────
#: how long ONE answer about a table's packages is reused, in seconds. The read is a node RPC (a
#: folder enumeration plus, per package, its repository's release list), so a refresh of the page
#: within half a minute reuses it instead of asking the node again. A write clears it.
VERSIONS_TTL_SECONDS = 30.0

#: the answer cache, keyed by ``(node name, folder, sorted package names)`` and stamped with the
#: monotonic clock. Only a COMPLETED answer is stored, so a node that did not answer is asked again
#: on the next render rather than served a failure from the cache.
_VERSIONS: dict[tuple[str, str, tuple[str, ...]], tuple[float, dict]] = {}

# ── the templates ──────────────────────────────────────────────────────────
#: the tab body and the node page share ONE body: the two owners differ by their data, never by their
#: markup, and the page is the body plus the shell chrome.
MODS_TEMPLATE = "mods_page.html"
BODY_TEMPLATE = "mods_body.html"
#: the PICK dialog — the install and the version change are the same field (a package, or the versions
#: of one package) with a different heading, so they are one page.
PICK_TEMPLATE = "mods_install.html"
#: the download dialog: two typed fields, unlike the pick's one selection.
DOWNLOAD_TEMPLATE = "mods_download.html"
#: a removal confirms, which is exactly the console's shared dialog.
CONFIRM_TEMPLATE = "confirm.html"

# ── the form fields ────────────────────────────────────────────────────────
PACKAGE_FIELD = "package"
VERSION_FIELD = "version"
URL_FIELD = "url"
TARGET_FIELD = "target"
OWNER_FIELD = "owner"
DEFAULT_VERSION = "latest"

# ── the copy ───────────────────────────────────────────────────────────────
NODE_TITLE = i18n._("Root mods")
NODE_LEAD = i18n._("Installed into the DCS installation on this node — active in every instance on "
                   "it.")
TAB_HEADING = i18n._("Installed mods (Saved Games)")
ROOT_HEADING = i18n._("Installed root mods")

#: the word the breadcrumb and the tab strip use for this area — one msgid, so a page name cannot be
#: spelled two ways.
MODS_LABEL = i18n._("Mods")

INSTALL_LABEL = i18n._("Install a mod…")
INSTALL_TITLE = i18n._("Install a package that is already in the package folder")
DOWNLOAD_LABEL = i18n._("Download…")
DOWNLOAD_TITLE = i18n._("Download a package from GitHub or a URL into the package folder")

#: the ONE line the download dialog shows under the version field when this link cannot offer a
#: list: the text box stays, and the operator is told the version has to be typed.
VERSIONS_UNREADABLE = i18n._("The releases could not be read from this link — type the version.")


def declare() -> None:
    """Ensure both capabilities are declared (import-time, exactly like every other page module)."""
    _capability_declarations()


declare()


# ------------------------------------------------------------------------------- the urls

def node_page_url(node_name: str) -> str:
    """The node's root-mods page URL for *node_name*."""
    return NODE_PAGE_PATH.replace("{node}", quote(str(node_name), safe=""))


def _owner_url(owner: str, target: str, verb: str, *, run: bool) -> str:
    """The dialog (or the action) URL of ONE verb for ONE owner and ONE target."""
    base = (_DIALOG_BASE.replace("{owner}", owner)
            .replace("{target}", quote(str(target), safe="")))
    url = f"{base}/{verb}"
    return f"{url}/run" if run else url


def dialog_url(owner: str, target: str, verb: str) -> str:
    """The URL of the DIALOG one verb opens — where a control posts."""
    return _owner_url(owner, target, verb, run=False)


def run_url(owner: str, target: str, verb: str) -> str:
    """The URL of the ACTION route — where the dialog's own form posts."""
    return _owner_url(owner, target, verb, run=True)


def _dialog_pattern(owner: str, verb: str) -> str:
    """The registration pattern of one dialog (the target segment left open)."""
    return _DIALOG_BASE.replace("{owner}", owner).replace("{target}", _TARGET_SEGMENT[owner]) + f"/{verb}"


def _run_pattern(owner: str, verb: str) -> str:
    """The registration pattern of one action route."""
    return _dialog_pattern(owner, verb) + "/run"


def _releases_pattern(owner: str) -> str:
    """The registration pattern of one owner's release read (the target segment left open)."""
    return (_DIALOG_BASE.replace("{owner}", owner).replace("{target}", _TARGET_SEGMENT[owner])
            + f"/{_RELEASES_SEGMENT}")


def releases_url(owner: str, target: str) -> str:
    """The release read's URL for ONE owner and ONE target — where the dialog's script asks."""
    base = (_DIALOG_BASE.replace("{owner}", owner)
            .replace("{target}", quote(str(target), safe="")))
    return f"{base}/{_RELEASES_SEGMENT}"


def _page_of(owner: str, target: str) -> str:
    """The page a write of *owner* returns to: the node's own page, or the server's Mods tab."""
    if owner == NODE_OWNER:
        return node_page_url(target)
    return readmodels.server_url(target, tab=MODS_TAB)


# ------------------------------------------------------------------------------- the service

def _service():
    """The process's ``ModManagerService``, or ``None`` when the plugin is not enabled.

    Function-local for the two reasons the plugin's own action module states: the import pulls the
    bot's HTTP and database stack, and an ENABLED plugin is what makes the doors drawable at all.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.modmanager import ModManagerService

        return ServiceRegistry.get(ModManagerService)
    except Exception:
        log.debug("Mods pages: the modmanager service is not available in this process.")
        return None


def _service_missing_message() -> str:
    """The ``modmanager`` action module's own "plugin off" sentence, or ``""`` when unreachable.

    Imported function-locally for the reason ``_service`` imports the service that way: the plugin's
    action module pulls the bot's HTTP and database stack.
    """
    try:
        from plugins.modmanager.actions import SERVICE_MISSING

        return SERVICE_MISSING
    except Exception:
        log.debug("Mods pages: the modmanager action module is not importable.", exc_info=True)
        return ""


def _package_folder(owner: str) -> str:
    """The configured package-folder value for *owner*, printed VERBATIM (variables unexpanded).

    Read from the running service's own configuration — the process's copy of
    ``config/services/modmanager.yaml`` — so a render asks no node. A per-node override lives on that
    node's disk and is not readable here; the value shown is the one every node of the cluster is
    configured with, and the node expands it when it reads the folder.
    """
    service = _service()
    if service is None:
        return ""
    folder = _OWNER_FOLDER[owner]
    try:
        block = service.get_config() or {}
    except Exception:
        log.debug("Mods pages: the service configuration could not be read.", exc_info=True)
        return ""
    return readmodels.text(block.get(folder)) if isinstance(block, dict) else ""


# ------------------------------------------------------------------------------- the target

def _viewed_node(source, node_name: str) -> str | None:
    """The canonical name of *node_name* in the caller's own view, or ``None``.

    The same lookup the node settings page performs against the same source: a node the caller cannot
    see is not resolvable here either, and reachability is not consulted — the installed rows are a
    database read, so an offline node still has mods to show.
    """
    entries = getattr(source, "nodes", None)
    if not isinstance(entries, dict):
        return None
    if node_name in entries:
        return readmodels.text(node_name)
    wanted = readmodels.text(node_name).casefold()
    for key in entries:
        if readmodels.text(key).casefold() == wanted:
            return readmodels.text(key)
    return None


def _server_views(request: Request) -> list:
    """Every server visible to this caller, as read-model rows."""
    return readmodels.safe(lambda: readmodels.servers(dashboard_page.request_source(request)), []) or []


def _node_target(request: Request) -> tuple[str, bool]:
    """``(canonical node name, online)`` for the node this request names — or the console's 404."""
    source = dashboard_page.request_source(request)
    name = _viewed_node(source, readmodels.text(request.path_params.get("node")))
    if name is None:
        raise HTTPException(status_code=404)
    entries = getattr(source, "nodes", {}) or {}
    return name, entries.get(name) is not None


def _server_target(request: Request) -> str:
    """The canonical name of the server this request names — or the console's 404."""
    wanted = readmodels.text(request.path_params.get("name"))
    for view in _server_views(request):
        if readmodels.text(getattr(view, "name", "")) == wanted:
            return wanted
    folded = wanted.casefold()
    for view in _server_views(request):
        name = readmodels.text(getattr(view, "name", ""))
        if name.casefold() == folded:
            return name
    raise HTTPException(status_code=404)


def _server_view(request: Request, server_name: str):
    """The read-model row of *server_name*, or ``None``."""
    for view in _server_views(request):
        if readmodels.text(getattr(view, "name", "")) == server_name:
            return view
    return None


def _node_of(request: Request, server_view) -> str:
    """The node a server's row carries — the target every ``RootFolder`` read names."""
    return readmodels.text(getattr(server_view, "node_name", ""))


# ------------------------------------------------------------------------------- the gate

def _state_of(view) -> str:
    """The ``Status`` member name behind one server row, through the write seam's own reader.

    ``pages/actions.state_of`` is the ONE reading of "which state is this", shared with the row
    controls' own ``statuses`` filter — the display gate and the controls it withdraws can never
    disagree about a server.
    """
    from . import actions as actions_page

    status = getattr(view, "status", None)
    return actions_page.state_of(getattr(status, "raw", status))


def _running(views) -> list:
    """The server rows whose state is not ``SHUTDOWN`` — the ones holding a gate closed."""
    return [view for view in views if _state_of(view) != "SHUTDOWN"]


def _server_sentence(views: list) -> tuple[dict, list[dict]]:
    """``(sentence, links)`` for the servers a closed ROOT gate names, each with its own page link."""
    sentence = {
        "msgid": i18n._("The servers <b>{names}</b> are running on this node. Root mods are active in "
                        "every instance on it, so every one of them has to be shut down before a "
                        "package can be installed or removed."),
        "params": {"names": escape(", ".join(readmodels.text(getattr(view, "name", ""))
                                            for view in views))},
    } if len(views) > 1 else {
        "msgid": i18n._("Server <b>{name}</b> is running on this node. Root mods are active in every "
                        "instance on it, so it has to be shut down before a package can be installed "
                        "or removed."),
        "params": {"name": escape(readmodels.text(getattr(views[0], "name", "")))},
    }
    links = [{"href": readmodels.text(getattr(view, "link", "")) or readmodels.server_url(
                  readmodels.text(getattr(view, "name", ""))),
              "text": {"msgid": i18n._("Shut down {name}"),
                       "params": {"name": readmodels.text(getattr(view, "name", ""))}}}
             for view in views]
    return sentence, links


def _strip_sentence(owner: str, target: str, *, ok: bool) -> dict:
    """The ONE sentence the state strip states, for one owner and one gate state."""
    if owner == NODE_OWNER:
        if ok:
            return {"msgid": i18n._("Every server on this node is shut down — root mods can be "
                                    "changed."), "params": {}}
        return {"msgid": i18n._("Root mods cannot be changed while a server on this node is "
                                "running."), "params": {}}
    if ok:
        return {"msgid": i18n._("{server} is shut down — its Saved Games mods can be changed."),
                "params": {"server": target}}
    return {"msgid": i18n._("{server} is running, so its Saved Games mods cannot be changed. Shut it "
                            "down first."), "params": {"server": target}}


def _gate(owner: str, target: str, views, *, online: bool = True) -> dict:
    """The state strip as data: open/closed, its dot, its sentence and the links it offers.

    THE display fact, read from the console's own source on every render (never a node call) and
    re-checked by every write route. Open means every server on the node is ``SHUTDOWN`` for a root
    package, or that one server is for a Saved Games package; an OFFLINE node is closed, because a
    write would have to reach it.
    """
    if not online:
        return {"ok": False, "dot": "update",
                "sentence": _strip_sentence(owner, target, ok=False),
                "links": []}
    if owner == NODE_OWNER:
        running = _running(views)
        if not running:
            return {"ok": True, "dot": "run", "sentence": _strip_sentence(owner, target, ok=True),
                    "links": []}
        sentence, links = _server_sentence(running)
        return {"ok": False, "dot": "update", "sentence": sentence, "links": links}
    running = _running(views)
    return {"ok": not running, "dot": "run" if not running else "update",
            "sentence": _strip_sentence(owner, target, ok=not running), "links": []}


# ------------------------------------------------------------------------------- the controls

def _sentence(msgid: str, **params) -> dict:
    """A per-request sentence record the templates render through ``say``."""
    return {"msgid": msgid, "params": params}


def _door(request: Request, owner: str, target: str, verb: str, *, label: str, title: str) -> dict:
    """One page-level door: a POST form whose answer IS its dialog page."""
    return {
        "path": dialog_url(owner, target, verb),
        "label": label,
        "title": _sentence(title),
        "aria": _sentence(title),
        "owner": owner,
        "target": target,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }


def _doors(request: Request, owner: str, target: str, *, open_: bool,
           available: bool) -> list[dict]:
    """The two page-level doors — EMPTY while the gate is closed or no service can run them."""
    if not open_ or not available:
        return []
    return [
        _door(request, owner, target, INSTALL, label=INSTALL_LABEL, title=INSTALL_TITLE),
        _door(request, owner, target, DOWNLOAD, label=DOWNLOAD_LABEL, title=DOWNLOAD_TITLE),
    ]


def _row_strip(request: Request, owner: str, target: str, row: mod_model.ModRow, *,
               offered: bool, change: bool = True) -> dict:
    """The shared row strip for ONE installed row: change the version, remove the package.

    Two glyphs and nothing else — the word lives in ``title`` and ``aria-label`` (``_strip.html``).
    Both post to their DIALOG with the row's identity in the body; neither writes from the list.
    ``change`` is ``False`` for a package that has no other version: a change door would open a pick
    with nothing to pick, so it is NOT drawn at all (absent, never greyed).
    """
    if not offered:
        return {}
    token = session.get_csrf_token(request)
    hidden = ((OWNER_FIELD, owner), (TARGET_FIELD, target),
              (PACKAGE_FIELD, row.package), (VERSION_FIELD, row.version))
    strip = []
    if change:
        strip.append(
            {"path": dialog_url(owner, target, CHANGE), "label": i18n._("Change version"),
             "icon": "upgrade", "danger": False, "hidden": hidden,
             "csrf_field": session.CSRF_FIELD, "csrf_token": token,
             "title": _sentence(i18n._("Change the installed version of {package}"),
                                package=row.package),
             "aria": _sentence(i18n._("Change the installed version of {package}"),
                               package=row.package)})
    strip.append(
        {"path": dialog_url(owner, target, REMOVE), "label": i18n._("Remove"),
         "icon": "delete", "danger": True, "hidden": hidden,
         "csrf_field": session.CSRF_FIELD, "csrf_token": token,
         "title": _sentence(i18n._("Remove {package} v{version} from {target}"),
                            package=row.package, version=row.version, target=target),
         "aria": _sentence(i18n._("Remove {package} v{version} from {target}"),
                           package=row.package, version=row.version, target=target)})
    return {"strip": strip, "menu": (), "wide": False}


# ------------------------------------------------------------------------------- the reads

async def _installed(request: Request, *, node_name: str, server_name: str = ""):
    """The installed-rows answer of the console's own scoped action (never a second query)."""
    from . import actions as actions_page

    return await read_action("list_installed_mods", actions_page.context_for(request),
                             node_name=node_name, server_name=server_name)


def _cached_versions(key: tuple[str, str, tuple[str, ...]]) -> dict | None:
    """The stored answer for *key* while it is younger than the window, else ``None``."""
    record = _VERSIONS.get(key)
    if record is None:
        return None
    stamp, answer = record
    if time.monotonic() - stamp > VERSIONS_TTL_SECONDS:
        _VERSIONS.pop(key, None)
        return None
    return answer


def _remember_versions(key: tuple[str, str, tuple[str, ...]], answer: dict) -> None:
    """Store one COMPLETED answer under *key* with the clock it is measured against."""
    _VERSIONS[key] = (time.monotonic(), answer)


def _forget_versions() -> None:
    """Drop every stored answer — a write changed what a node's package folder holds."""
    _VERSIONS.clear()


async def _versions(request: Request, *, node_name: str, folder: str,
                    packages: list[str]) -> dict | None:
    """The node's answer about these packages, or ``None`` when it could not be asked.

    ONE read for the whole table, cached per ``(node, folder, sorted package names)`` for
    :data:`VERSIONS_TTL_SECONDS`. ``None`` is the fail-soft answer: the caller then draws the same
    controls and chips the page drew before this read existed.
    """
    names = tuple(sorted({readmodels.text(name) for name in packages if readmodels.text(name)}))
    if not names:
        return None
    key = (node_name, folder, names)
    cached = _cached_versions(key)
    if cached is not None:
        return cached
    from . import actions as actions_page

    result = await read_action("list_mod_versions", actions_page.context_for(request),
                               node_name=node_name, folder=folder, packages=list(names))
    if not getattr(result, "success", False):
        log.debug("Mods pages: the package versions of %s on %s could not be read.",
                  folder, node_name)
        return None
    answer = (getattr(result, "data", None) or {}).get("packages") or {}
    if not isinstance(answer, dict):        # pragma: no cover - the action always answers a mapping
        return None
    _remember_versions(key, answer)
    return answer


async def _installed_table(request: Request, result, *, owner: str, target: str, node_name: str,
                           folder: str, path: str, carry: dict, offered: bool,
                           missing: str = "") -> dict:
    """One installed card, in the console's ONE table component.

    The rows are shaped by the read model and rendered by ``templates/_table.html`` — the same
    component the dashboard and every list page use — so the mods card has the component's own
    header links, its ``actions``/``read-only`` chip and its empty state, and no second copy of the
    table markup exists.

    ``offered`` says whether the ROW CONTROLS may be drawn — the gate is open, the caller holds
    ``mods.manage`` AND the read answered.
    A closed gate passes NO control map, so the component renders neither the Actions column nor an
    ``actions`` chip, and the rows stay fully readable: reading what is installed is how an operator
    decides. A read that failed and an empty answer are both the component's empty state, each with
    its OWN sentence, so an empty folder can never stand in for a failure. ``missing`` replaces the
    read's message only when the plugin is absent from the process, and is empty otherwise, so a
    genuine read failure keeps the sentence it came with.

    The VERSION facts are the table's second read (:func:`_versions`), and its two outcomes ride the
    rows: a change-version door only where the package has another version, and an update chip where
    the node names a newer one. A chip is information and is drawn for EVERY caller; the doors stay
    the gate's and the capability's business.
    """
    data = getattr(result, "data", None) or {}
    success = bool(getattr(result, "success", False))
    packages = data.get("packages") if success else None
    message = missing or readmodels.text(getattr(result, "message", ""))
    rows = mod_model.installed_rows(packages or (), folder) if success else []
    availability = await _versions(request, node_name=node_name, folder=folder,
                                   packages=[row.package for row in rows]) if rows else None
    rows = mod_model.with_updates(rows, availability)
    params = readmodels.view_params(request.query_params)
    table = readmodels.table_view(path, mod_model.MODS_TABLE, rows,
                                  sort=params.sort_for(mod_model.MODS_TABLE),
                                  direction=params.direction, params=carry,
                                  label=TAB_HEADING if owner == SERVER_OWNER
                                  else ROOT_HEADING)
    controls: dict = {}
    if offered and success:
        controls = {
            row.key: _row_strip(request, owner, target, row, offered=True,
                                change=mod_model.other_version(availability, row.package,
                                                               row.version))
            for row in table.rows
        }
    return {"table": table, "controls": controls, "empty": {mod_model.MODS_TABLE: message}}


def _root_card(result, *, node_name: str) -> dict | None:
    """The READ-ONLY root-mods card a server's tab carries underneath its own list.

    One home for the root half: this card names what is installed and links to the node's page — it
    offers no control of its own, so a manager who may not open that page is handed a sentence instead
    of a door (the card is still rendered: what is active on the server is not the manager's
    business to be told only half of).

    ``None`` when the server's node cannot be named at all.
    """
    if not node_name:
        return None
    data = getattr(result, "data", None) or {}
    packages = data.get("packages") if getattr(result, "success", False) else None
    success = bool(getattr(result, "success", False))
    rows = mod_model.installed_rows(packages or (), mod_model.ROOT_FOLDER) if success else []
    link = node_page_url(node_name)
    return {
        "heading": {"msgid": i18n._("Root mods on {node}"), "params": {"node": node_name}},
        "count": len(rows),
        "sentence": ({"msgid": i18n._("{count} root mod(s) are installed on {node} — they are active "
                                      "in this server too, and they are changed on the node's own "
                                      "page."),
                      "params": {"count": len(rows), "node": node_name}}
                     if success and rows else
                     {"msgid": mod_model.NO_ROOT_MODS, "params": {"node": node_name}}
                     if success else
                     {"msgid": i18n._("The root mods of {node} could not be read."),
                      "params": {"node": node_name}}),
        "failure": "" if success else readmodels.text(getattr(result, "message", "")),
        "link": link,
        "link_text": _sentence(i18n._("Root mods on {node} →"), node=node_name),
    }


# ------------------------------------------------------------------------------- the page body

async def _page_context(request: Request, *, owner: str, target: str, node_name: str,
                        server_name: str, online: bool, views, head: dict | None) -> dict:
    """Everything the shared body reads, for one owner and one target.

    The installed rows come from ONE read: for a server it names the node AND the server, so the
    answer carries both halves and the root card is split out of the same answer — one database read
    per render, and no second spelling of "what is installed".
    """
    result = await _installed(request, node_name=node_name, server_name=server_name)
    folder = _OWNER_FOLDER[owner]
    mods = {
        "owner": owner,
        "target": target,
        "head": head,
        "path": _package_folder(owner),
        "owner_field": OWNER_FIELD,
        "target_field": TARGET_FIELD,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "notice": None,
        "strip": None,
    }
    from . import actions as actions_page

    mods["notice"] = actions_page.pop_notice(request)
    gate = _gate(owner, target, views, online=online)
    may_manage = permissions.allows(MODS_MANAGE_CAPABILITY, permissions.role_names_for(request))
    available = _service() is not None
    mods["state"] = gate
    mods["doors"] = _doors(request, owner, target, open_=gate["ok"],
                           available=may_manage and available)
    mods["installed"] = await _installed_table(
        request, result, owner=owner, target=target, node_name=node_name, folder=folder,
        path=_page_of(owner, target).split("?", 1)[0],
        carry={"tab": MODS_TAB} if owner == SERVER_OWNER else {},
        offered=gate["ok"] and may_manage,
        missing="" if available else _service_missing_message())
    if owner == SERVER_OWNER:
        mods["other"] = _root_card(result, node_name=node_name)
        mods["strip"] = _state_strip(request, server_name) if not gate["ok"] else None
    else:
        mods["other"] = None
    return mods


def _state_strip(request: Request, server_name: str) -> dict | None:
    """The EXISTING row strip of one server, placed in the state strip while its gate is closed.

    The Servers page's own control record, asked for BY TABLE and taken for this one row: the shutdown
    control is the same write here as there, so the tab does not grow a second one. ``None`` when the
    record offers nothing (a caller without the capability, or a state that has no control).
    """
    from . import actions as actions_page

    records = actions_page.controls_for(request, "servers", _page_of(SERVER_OWNER, server_name))
    return records.get(server_name) or None


def _chrome(request: Request, *, title: str, lead: str, crumb: tuple, notice) -> dict:
    """The shell chrome every console page passes to ``base.html``."""
    registrar = getattr(getattr(request, "app", None), "state", None)
    registrar = getattr(registrar, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request), level=dashboard_page.log_level(request))
    return {
        "title": title,
        "page_title": title,
        "lead": lead,
        "crumb": crumb,
        "notice": notice,
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=NODE_PAGE_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
    }


# ------------------------------------------------------------------------------- the node page

async def node_page_context(request: Request) -> dict:
    """The node's root-mods page: the installed rows, the state strip and the two doors."""
    node_name, online = _node_target(request)
    views = [view for view in _server_views(request) if _node_of(request, view) == node_name]
    head = {"title": NODE_TITLE, "lead": NODE_LEAD, "node": node_name}
    mods = await _page_context(request, owner=NODE_OWNER, target=node_name, node_name=node_name,
                               server_name="", online=online, views=views, head=head)
    crumb = ((dashboard_page.CRUMB_GROUP, True), (nodes_page.NODES_TITLE, True),
             (node_name, False), (NODE_TITLE, True))
    context = _chrome(request, title=NODE_TITLE, lead=NODE_LEAD, crumb=crumb, notice=mods["notice"])
    context["mods"] = mods
    return context


async def tab_context(request: Request, server_name: str) -> dict:
    """The BODY of a server's Mods tab — the tab context the server page includes.

    The tab's own route, position in the tab strip and capability belong to the server page; this
    module owns what the body says. ``head`` is ``None``: the server page already rendered the page
    head for the server, so the body starts at the doors.
    """
    name = readmodels.text(server_name)
    view = _server_view(request, name)
    if view is None:
        raise HTTPException(status_code=404)
    node_name = _node_of(request, view)
    mods = await _page_context(request, owner=SERVER_OWNER, target=name, node_name=node_name,
                               server_name=name, online=True, views=[view], head=None)
    return {"mods": mods}


# ------------------------------------------------------------------------------- the dialogs

def _dialog_chrome(request: Request, *, crumb: tuple) -> dict:
    """The chrome of a DIALOG page — the same shell, no write control of its own.

    The page's own heading is the DIALOG's (it names the target and, for a version change, the
    package), so the chrome supplies the sidebar, the pills and the breadcrumb only.
    """
    return _chrome(request, title=MODS_LABEL, lead=MODS_LABEL, crumb=crumb, notice=None)


def _back(owner: str, target: str) -> str:
    """The page a dialog's Cancel returns to."""
    return _page_of(owner, target)


def _hidden(owner: str, target: str, **extra: str) -> tuple[tuple[str, str], ...]:
    """The dialog form's hidden fields: the target, repeated in the body beside its path segment."""
    pairs = [(OWNER_FIELD, owner), (TARGET_FIELD, target)]
    pairs.extend((name, value) for name, value in extra.items())
    return tuple(pairs)


async def _listed(request: Request, *, node_name: str, folder: str):
    """The package folder as the NODE reports it, read when a dialog is requested."""
    from . import actions as actions_page

    return await read_action("list_available_mods", actions_page.context_for(request),
                             node_name=node_name, folder=folder)


def _pick_dialog(request: Request, *, owner: str, target: str, node_name: str, result,
                 package: str = "") -> dict:
    """The pick dialog's context: the options, or the read's own sentence and NO primary button.

    A read that failed and a folder that is genuinely empty are both rendered as a sentence and no
    button — the first names the failure, the second says the folder holds nothing — so an empty pick
    can never stand in for a failure.
    """
    data = getattr(result, "data", None) or {}
    success = bool(getattr(result, "success", False))
    entries = data.get("packages") if success else None
    options = mod_model.package_options(entries or (), package=package or None) if success else []
    folder = _OWNER_FOLDER[owner]
    changing = bool(package)
    if changing:
        title = i18n._("Change the version of {package} on {target}")
        heading = i18n._("Change the version")
        lead = i18n._("The versions of {package} in the {folder} package folder on {node}, read when "
                      "this page opened.")
        what = [i18n._("Changing the version removes the installed one and installs the version you "
                       "pick — one operation, and the bot log records both halves.")]
        go = i18n._("Change")
        verb = CHANGE
    else:
        title = i18n._("Install a mod on {target}")
        heading = i18n._("Install a mod")
        lead = i18n._("The packages in the {folder} package folder on {node}, read when this page "
                      "opened.")
        what = [i18n._("Packages found in the {folder} package folder on {node} a moment ago. "
                       "Installing one unpacks it into this target and records it here and in the bot "
                       "log.")]
        go = i18n._("Install")
        verb = INSTALL
    params = {"target": target, "package": package, "folder": folder, "node": node_name}
    return {
        "owner": owner,
        "target": target,
        "kind": CHANGE if changing else INSTALL,
        "page_title": _sentence(title, **params),
        "heading": _sentence(heading, **params),
        "lead": _sentence(lead, **params),
        "what": [_sentence(message, **params) for message in what],
        "options": options,
        "message": "" if options else readmodels.text(getattr(result, "message", "")),
        "path": run_url(owner, target, verb),
        "back": _back(owner, target),
        "go": go,
        "go_title": _sentence(go),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "confirm_field": _confirm_field(),
        "confirm_token": _mint(request, owner, target, verb),
        "hidden": _hidden(owner, target),
    }


def _confirm_field() -> str:
    """The one-shot token's field name, from the write seam that mints it."""
    from . import actions as actions_page

    return actions_page.CONFIRM_FIELD


def _mint(request: Request, owner: str, target: str, verb: str) -> str:
    """Mint the one-shot token for ONE target's ONE verb — the dialog is the only place it is minted."""
    from . import actions as actions_page

    return actions_page.mint_confirm_token(request, run_url(owner, target, verb), target)


def _confirm_dialog(request: Request, *, owner: str, target: str, package: str, version: str,
                    where: str) -> dict:
    """The removal's context: the console's shared confirmation page, worded for one package."""
    from . import actions as actions_page

    return {
        "page_title": _sentence(i18n._("Remove {package} v{version} from {target}"),
                                package=package, version=version, target=target),
        "back": _back(owner, target),
        "dialog": {
            "path": run_url(owner, target, REMOVE),
            "heading": _sentence(i18n._("Remove {package} v{version}?"), package=package,
                                 version=version),
            "tag": i18n._("confirm"),
            "detail": _sentence(i18n._("The package is removed from {where}, and the bot log records "
                                       "the removal and your name."), where=where),
            "detail_notes": (),
            # no red-edged block: a removal is destructive but has no consequence to warn about
            # beyond the package going away, which the detail sentence already states.
            "warning": None,
            "target": target,
            "irreversible": False,
            "danger": True,
            "go": _sentence(i18n._("Remove")),
            "go_title": _sentence(i18n._("Remove the package")),
            "inputs": (),
            "hidden": _hidden(owner, target, **{PACKAGE_FIELD: package, VERSION_FIELD: version}),
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
            "confirm_field": actions_page.CONFIRM_FIELD,
            "confirm_token": _mint(request, owner, target, REMOVE),
        },
    }


def _download_dialog(request: Request, *, owner: str, target: str, node_name: str) -> dict:
    """The download dialog's context: a URL and a version, and nothing installed by it."""
    folder = _OWNER_FOLDER[owner]
    path = _package_folder(owner)
    return {
        "owner": owner,
        "target": target,
        "page_title": _sentence(i18n._("Download a package")),
        "heading": _sentence(i18n._("Download a package")),
        "lead": _sentence(i18n._("{folder} package folder on node {node}"), folder=folder,
                          node=node_name),
        # the folder is printed VERBATIM inside the sentence, as the configuration carries it
        "path_sentence": {
            "msgid": i18n._("The node downloads the package itself and writes it into the package "
                            "folder — <span class=\"mods-path\">{path}</span> — so a package fetched "
                            "here is available to every install on that node."),
            "params": {"path": escape(path)},
        },
        "note": i18n._("Nothing is installed by this step: the package has to be installed "
                       "afterwards. For a plain URL the file name decides the version and the "
                       "version field is ignored; for a repository download the field selects the "
                       "release, and a version with no matching release asset is refused before "
                       "anything is written."),
        "path": run_url(owner, target, DOWNLOAD),
        "back": _back(owner, target),
        "go": i18n._("Download"),
        "go_title": _sentence(i18n._("Download the package into the package folder")),
        "version": DEFAULT_VERSION,
        "url_field": URL_FIELD,
        "version_field": VERSION_FIELD,
        "versions_note": VERSIONS_UNREADABLE,
        # THE SCRIPT'S OWN PAGE FACTS: one record the dialog's inline script reads its route, its
        # field names, its preset and its debounce off. A script that shows a SENTENCE never looks
        # one up, so every string it displays is rendered by this page, in the request's language.
        "facts": {
            "releases_url": releases_url(owner, target),
            "url_query": RELEASES_QUERY,
            "url_field": URL_FIELD,
            "version_field": VERSION_FIELD,
            "hint_id": VERSIONS_HINT_ID,
            "latest": DEFAULT_VERSION,
            "delay_ms": RELEASES_DEBOUNCE_MS,
        },
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "confirm_field": _confirm_field(),
        "confirm_token": _mint(request, owner, target, DOWNLOAD),
        "hidden": _hidden(owner, target),
    }


# ------------------------------------------------------------------------------- the writes
#
# Every action route answers the same four questions in the same order — identity, target, the display
# gate, the one-shot token — and only then reaches the action. Each refusal is a 403 whose sentence is
# carried to the page the person lands on by the console's one-shot notice, and the SERVICE's own
# answer is rendered verbatim rather than re-worded here.

def _identity(request: Request):
    """The signed-in identity of this request, or ``None``."""
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return None
    return manager.authenticate(request)


def _write_context(request: Request) -> ActionContext:
    """The seam's context for a WRITE: the caller's identity, scope and roles, and its audit actor.

    ``context_for`` supplies the node, the bus and the caller's resolved scope/roles; the actor is
    what only a resolvable identity carries, so it is grafted from ``ActionContext.from_web`` — the
    ONE place the ``[web:backend/subject]`` token is built.
    """
    from . import actions as actions_page

    identity = _identity(request)
    if identity is None:
        raise HTTPException(status_code=403, detail=actions_page.REFUSAL_NO_IDENTITY)
    base = actions_page.context_for(request)
    ctx = ActionContext.from_web(identity, base.node, base.bus)
    return replace(ctx, _scope=base._scope, _roles=base._roles)


def _notice(request: Request, message: str, *, ok: bool, audit: str = "") -> None:
    """Store the one-shot notice of a write, in the shape ``pages/actions.pop_notice`` validates."""
    from . import actions as actions_page
    from core.actions import AUDIT_NOT_RECORDED

    text = readmodels.text(message)
    if len(text) > actions_page.NOTICE_MAX_CHARS:
        text = text[:actions_page.NOTICE_MAX_CHARS - 1] + "…"
    try:
        request.session[actions_page.NOTICE_KEY] = {
            "message": text, "ok": bool(ok), "audit": audit,
            "audit_warning": audit == AUDIT_NOT_RECORDED, "action": "",
            "revert": "", "revert_omitted": "", "refusal": False,
        }
    except (AssertionError, KeyError):       # pragma: no cover - no session on this request
        log.debug("Mods pages: no session to carry a notice.")


def _refuse(request: Request, detail: str) -> HTTPException:
    """Record *detail* as the page's next notice and return the console's 403 carrying it."""
    _notice(request, detail, ok=False)
    return HTTPException(status_code=403, detail=detail)


def _posted_target(request: Request, owner: str) -> str:
    """The target this request names — its path segment, or the console's 404/403."""
    if owner == NODE_OWNER:
        name, _online = _node_target(request)
        return name
    return _server_target(request)


def _check_posted(request: Request, form, owner: str, target: str) -> None:
    """Refuse a POST whose body names another owner or target than its path does.

    The target travels BOTH ways — as the path segment the route resolved and as a hidden field the
    dialog re-emits — so a form edited to point at somebody else's target is refused before anything
    is looked up. An absent field is the browser's own omission and is not a mismatch.
    """
    posted_owner = readmodels.text(form.get(OWNER_FIELD))
    posted_target = readmodels.text(form.get(TARGET_FIELD))
    if (posted_owner and posted_owner != owner) or (posted_target and posted_target != target):
        raise _refuse(request, i18n._("Not done (the form names a different target than its path)."))


async def _write_route(request: Request, owner: str, verb: str):
    """One action route: identity, target, gate, token, action — in that order, then a 303 back."""
    from . import actions as actions_page

    form = await request.form()
    target = _posted_target(request, owner)
    _check_posted(request, form, owner, target)
    ctx = _write_context(request)

    node_name, views, online = await _target_state(request, owner, target)
    gate = _gate(owner, target, views, online=online)
    if not gate["ok"]:
        raise _refuse(request, _gate_sentence(gate))

    posted = readmodels.text(form.get(actions_page.CONFIRM_FIELD))
    if not actions_page.consume_confirm_token(request, run_url(owner, target, verb), target, posted):
        raise _refuse(request, actions_page.REFUSAL_CONFIRM_REQUIRED)

    if verb == DOWNLOAD:
        result = await _run_download(request, ctx, owner, node_name, form)
    else:
        package, version = (_posted_row(request, form) if verb == REMOVE
                            else _posted_pick(request, form))
        if verb == REMOVE:
            # the removal's identity is re-resolved against what the target HOLDS, so a pair that was
            # edited into a path or into a package that is not there never reaches the service.
            await _require_installed(request, owner, target, node_name, package, version)
        elif verb in (INSTALL, CHANGE):
            await _require_available(request, owner, node_name, package, version)
        qualname = {INSTALL: "install_mod", CHANGE: "change_mod_version",
                    REMOVE: "uninstall_mod"}[verb]
        # the OWNER decides which reference the action needs: a root package belongs to a NODE, a
        # Saved Games package to one SERVER (``plugins/modmanager/actions``).
        if owner == NODE_OWNER:
            result = await call_action(qualname, ctx, folder=_OWNER_FOLDER[owner], package=package,
                                       version=version, node_name=target)
        else:
            result = await call_action(qualname, ctx, folder=_OWNER_FOLDER[owner], package=package,
                                       version=version, server_name=target)
    _remember(request, result)
    return RedirectResponse(_page_of(owner, target), status_code=303)


def _remember(request: Request, result) -> None:
    """Store the ACTION's own answer as the page's next notice.

    A successful write changed what a node's package folder holds, so the cached version answers are
    dropped and the page the write returns to reads them again.
    """
    data = getattr(result, "data", None) or {}
    audit = readmodels.text(data.get("audit")) if isinstance(data, dict) else ""
    if bool(getattr(result, "success", False)):
        _forget_versions()
    _notice(request, readmodels.text(getattr(result, "message", "")),
            ok=bool(getattr(result, "success", False)), audit=audit)


def _gate_sentence(gate: dict) -> str:
    """The plain sentence a closed gate refuses a write with — its words, without the markup."""
    sentence = gate.get("sentence") or {}
    msgid = readmodels.text(sentence.get("msgid"))
    params = sentence.get("params") or {}
    try:
        plain = msgid.format(**{name: str(value) for name, value in params.items()})
    except (KeyError, IndexError, ValueError):       # pragma: no cover - a sentence always fits
        plain = msgid
    return plain.replace("<b>", "").replace("</b>", "")


def _posted_pick(request: Request, form) -> tuple[str, str]:
    """The ``(package, version)`` the PICK's own option names — one field, split into its halves.

    The pick offers ONE entry per (package, version) pair, so the pair travels as one option value
    (``<package>/<version>``) and is split here. Nothing the request states is joined to a path: the
    action re-resolves the pair against a fresh folder listing before it writes.
    """
    pair = mod_model.split_option(form.get(PACKAGE_FIELD))
    if pair is None:
        raise _refuse(request, i18n._("Not done (no package was chosen)."))
    return pair


def _posted_row(request: Request, form) -> tuple[str, str]:
    """The ``(package, version)`` a ROW's own control names — two fields, both already resolved.

    Nothing here is trusted: the removal route re-resolves the pair against the target's INSTALLED
    rows (``_require_installed``) before it reaches the action, which is where a pair edited into a
    path stops.
    """
    package = readmodels.text(form.get(PACKAGE_FIELD))
    version = readmodels.text(form.get(VERSION_FIELD))
    if not package or not version:
        raise _refuse(request, i18n._("Not done (no package was named)."))
    return package, version


#: the seam's own sentence when a removal names a pair the target does not hold. A plain sentence and
#: not a msgid: it is composed where the refusal happens and rendered verbatim, like an action's own
#: message, so it never depends on a catalog having been regenerated.
NOT_INSTALLED = "'{package}' v{version} is not installed in {where}: nothing was removed."


async def _require_installed(request: Request, owner: str, target: str, node_name: str,
                             package: str, version: str) -> None:
    """Refuse a removal whose pair is not one of the target's INSTALLED rows.

    The row the dialog rendered is what the request SAYS; the bookkeeping is what the target HOLDS.
    The pair is re-resolved against the same read the page renders (``list_installed_mods``, for this
    owner's folder), so a pair edited into a path — or into a package that is not there — is refused
    with this seam's sentence and never reaches the service. A read that fails refuses with its OWN
    sentence, so "the rows could not be read" and "this pair is not installed" stay two answers.
    """
    result = await _installed(request, node_name=node_name,
                             server_name=target if owner == SERVER_OWNER else "")
    if not getattr(result, "success", False):
        raise _refuse(request, readmodels.text(getattr(result, "message", "")))
    data = getattr(result, "data", None) or {}
    folder = _OWNER_FOLDER[owner]
    for row in data.get("packages") or ():
        if not isinstance(row, dict):
            continue
        if (readmodels.text(row.get("package")) == package
                and readmodels.text(row.get("version")) == version
                and readmodels.text(row.get("folder")) == folder):
            return
    raise _refuse(request, NOT_INSTALLED.format(package=package, version=version,
                                                where=_where_sentence(owner, target)))


async def _target_state(request: Request, owner: str, target: str):
    """``(node name, server views, online)`` for a write's target."""
    if owner == NODE_OWNER:
        source = dashboard_page.request_source(request)
        name = _viewed_node(source, target)
        if name is None:
            raise HTTPException(status_code=404)
        entries = getattr(source, "nodes", {}) or {}
        views = [view for view in _server_views(request) if _node_of(request, view) == name]
        return name, views, entries.get(name) is not None
    view = _server_view(request, target)
    if view is None:
        raise HTTPException(status_code=404)
    return _node_of(request, view), [view], True


async def _require_available(request: Request, owner: str, node_name: str, package: str,
                             version: str) -> None:
    """Refuse a write whose package is not in the folder NOW — the read that just answered disagreed.

    A package can be deleted between the dialog's read and the POST, and installing a name that is no
    longer there would reach the service as a mystery. The enumeration is re-run here (a write, so a
    node read is allowed) and the pair must be found; a read that fails refuses with its OWN sentence,
    so "the node did not answer" and "the package vanished" stay two different answers.
    """
    result = await _listed(request, node_name=node_name, folder=_OWNER_FOLDER[owner])
    if not getattr(result, "success", False):
        raise _refuse(request, readmodels.text(getattr(result, "message", "")))
    data = getattr(result, "data", None) or {}
    for entry in data.get("packages") or ():
        name = readmodels.text(entry.get("name") if isinstance(entry, dict) else "")
        found = readmodels.text(entry.get("version") if isinstance(entry, dict) else "")
        if name == package and found == version:
            return
    raise _refuse(request, i18n._("'{package}' v{version} is no longer in the package folder — "
                                  "nothing was changed.").format(package=package, version=version))


async def _run_download(request: Request, ctx: ActionContext, owner: str, node_name: str, form):
    """Run one download: the node fills its own package folder, and nothing is installed."""
    link = readmodels.text(form.get(URL_FIELD))
    if not link:
        raise _refuse(request, i18n._("Not done (no URL was given)."))
    version = readmodels.text(form.get(VERSION_FIELD)) or DEFAULT_VERSION
    return await call_action("download_mod", ctx, node_name=node_name,
                             folder=_OWNER_FOLDER[owner], url=link, version=version)


# ------------------------------------------------------------------------------- the release read
#
# The dialog's own version pick asks THIS process for the releases of the URL it names. The read is
# an ordinary seam read (``list_repo_versions``), so the node is resolved through the one scope rule
# every mod read uses; the route adds nothing the seam does not already do, it only answers JSON.

def _release_target(request: Request, owner: str) -> tuple[str, str]:
    """``(canonical target, node name)`` for the release read, or ``("", "")`` for no scope.

    The SAME sources the page and the dialogs resolve their target against, so a target this caller
    cannot see is unresolvable here too. An unresolvable target is ``("", "")`` rather than a 404:
    the read fails closed with a refusal the caller cannot use to enumerate the cluster.
    """
    source = dashboard_page.request_source(request)
    wanted = readmodels.text(request.path_params.get(_TARGET_PARAM[owner]))
    if owner == NODE_OWNER:
        name = _viewed_node(source, wanted)
        return (name or ""), (name or "")
    folded = wanted.casefold()
    for view in _server_views(request):
        name = readmodels.text(getattr(view, "name", ""))
        if name.casefold() == folded:
            return name, _node_of(request, view)
    return "", ""


def _releases_answer(status_code: int, *, versions=(), error: str = "") -> JSONResponse:
    """ONE answer of the release read: ``{"versions": [...], "error": "<sentence>"}``, never cached."""
    return JSONResponse({"versions": [readmodels.text(item) for item in versions],
                         "error": readmodels.text(error)},
                        status_code=status_code, headers={"Cache-Control": "no-store"})


def _cached_releases(node_name: str, url: str) -> list[str] | None:
    """The stored answer for ``(node_name, url)`` while it is younger than the window, else ``None``."""
    record = _RELEASES.get((node_name, url))
    if record is None:
        return None
    stamp, versions = record
    if time.monotonic() - stamp > RELEASES_TTL_SECONDS:
        _RELEASES.pop((node_name, url), None)
        return None
    return list(versions)


def _remember_releases(node_name: str, url: str, versions: list[str]) -> None:
    """Store one COMPLETED answer under ``(node_name, url)`` with the clock it is measured against."""
    _RELEASES[(node_name, url)] = (time.monotonic(), list(versions))


async def _releases_route(request: Request, owner: str) -> JSONResponse:
    """Read one link's releases for one target, as ``{"versions": [...], "error": "<sentence>"}``.

    THE ORDER: a signed-in identity, then the target's scope, then the cached answer, then the seam.
    A caller without a resolvable identity and a caller whose target is outside their view are both a
    403 with a sentence — the read never answers what the caller may not see, and outside the scope
    an unknown name and a foreign one read the same.

    A GitHub failure is a 200 with ``error`` set and no versions: the dialog then keeps its text box.
    The page's own line is its translated sentence, so ``error`` is the technical reason the read
    failed; it never replaces that line.
    """
    from . import actions as actions_page

    identity = _identity(request)
    if identity is None:
        return _releases_answer(403, error=actions_page.REFUSAL_NO_IDENTITY)
    _target, node_name = _release_target(request, owner)
    if not node_name:
        return _releases_answer(403, error=actions_page.REFUSAL_OUT_OF_SCOPE)
    link = readmodels.text(request.query_params.get(RELEASES_QUERY))
    cached = _cached_releases(node_name, link)
    if cached is not None:
        return _releases_answer(200, versions=cached)
    result = await read_action("list_repo_versions", actions_page.context_for(request),
                               node_name=node_name, url=link)
    if not getattr(result, "success", False):
        return _releases_answer(200, error=readmodels.text(getattr(result, "message", "")))
    data = getattr(result, "data", None) or {}
    versions = [readmodels.text(item) for item in (data.get("versions") or ())]
    versions = [item for item in versions if item]
    # only a completed answer is stored: a repository that names no release is a real answer, a
    # refused or failed read is not
    _remember_releases(node_name, link, versions)
    return _releases_answer(200, versions=versions,
                            error="" if versions else readmodels.text(getattr(result, "message", "")))


def _releases_handler(owner: str):
    """The route that ANSWERS one owner's release read. It changes nothing."""

    async def handle(request: Request) -> JSONResponse:
        return await _releases_route(request, owner)

    return handle


# ------------------------------------------------------------------------------- the dialogs,
# registered
#
# One handler factory per (owner, verb): the two owners differ by the reference they resolve and by
# which reference a removal names, never by the markup that answers them.

def _dialog_target(request: Request, owner: str) -> tuple[str, str]:
    """``(canonical target, node name)`` for a dialog — or the console's 404."""
    if owner == NODE_OWNER:
        name, _online = _node_target(request)
        return name, name
    name = _server_target(request)
    view = _server_view(request, name)
    if view is None:                              # pragma: no cover - _server_target already found it
        raise HTTPException(status_code=404)
    return name, _node_of(request, view)


def _dialog_crumb(owner: str, target: str) -> tuple[tuple[str, bool], ...]:
    """The breadcrumb of a dialog: the page its door sits on, then the page name."""
    parent = nodes_page.NODES_TITLE if owner == NODE_OWNER else servers_page.SERVERS_TITLE
    return ((dashboard_page.CRUMB_GROUP, True), (parent, True), (target, False),
            (MODS_LABEL, True))


async def _render_dialog(request: Request, *, owner: str, verb: str) -> HTMLResponse:
    """Render ONE dialog: its target's state, its own copy, and the one-shot token it mints."""
    target, node_name = _dialog_target(request, owner)
    form = await request.form()
    if verb == REMOVE:
        package, version = _posted_row(request, form)
        where = _where_sentence(owner, target)
        context = _confirm_dialog(request, owner=owner, target=target, package=package,
                                  version=version, where=where)
        chrome = _dialog_chrome(request, crumb=_dialog_crumb(owner, target))
        chrome["page_title"] = context["page_title"]
        # ``confirm.html`` reads the return path at the TOP level (its Cancel link and its form's
        # ``data-back``) and everything else off ``dialog``.
        return HTMLResponse(i18n.render(request, CONFIRM_TEMPLATE, **chrome,
                                       back=context["back"], dialog=context["dialog"]),
                            headers={"Cache-Control": "no-store"})
    if verb == DOWNLOAD:
        dialog = _download_dialog(request, owner=owner, target=target, node_name=node_name)
        template = DOWNLOAD_TEMPLATE
    else:
        package = readmodels.text(form.get(PACKAGE_FIELD))
        result = await _listed(request, node_name=node_name, folder=_OWNER_FOLDER[owner])
        dialog = _pick_dialog(request, owner=owner, target=target, node_name=node_name,
                             result=result, package=package)
        template = PICK_TEMPLATE
    chrome = _dialog_chrome(request, crumb=_dialog_crumb(owner, target))
    return HTMLResponse(i18n.render(request, template, **chrome, dialog=dialog),
                        headers={"Cache-Control": "no-store"})


def _where_sentence(owner: str, target: str) -> str:
    """WHERE a package of *owner* lives, as the removal's own sentence names it."""
    if owner == NODE_OWNER:
        return i18n._("the DCS installation of node '{target}'").format(target=target)
    return i18n._("Saved Games on '{target}'").format(target=target)


def _dialog_handler(owner: str, verb: str):
    """The route that RENDERS one dialog. It changes nothing."""

    async def handle(request: Request) -> HTMLResponse:
        return await _render_dialog(request, owner=owner, verb=verb)

    return handle


# ------------------------------------------------------------------------------- registration

def capabilities() -> dict[str, str]:
    """The capability declaration for every path this module registers.

    Every one of them — the node page, the dialogs and the action routes — is ``mods.manage``: the
    node page IS the root half's read and write surface, and opening a dialog is not a lesser right
    than performing the write it leads to. ``mods.view`` owns the server TAB, whose route belongs to
    the server page and is declared there with this module's constant.
    """
    declare()
    paths = {NODE_PAGE_PATH: MODS_MANAGE_CAPABILITY}
    for owner in OWNERS:
        for verb in VERBS:
            paths[_dialog_pattern(owner, verb)] = MODS_MANAGE_CAPABILITY
            paths[_run_pattern(owner, verb)] = MODS_MANAGE_CAPABILITY
        # the release read is the DOWNLOAD's own version pick: reading what a link offers is the door
        # to the write it feeds, so it is not a lesser right than the download itself.
        paths[_releases_pattern(owner)] = MODS_MANAGE_CAPABILITY
    return paths


def nav_items() -> tuple:
    """NO menu entry: the node page hangs off a Nodes row and the other half is a tab.

    The main menu is a pinned six items (``tests/test_webui_nav.py``), and neither surface is a place
    of its own on it — the node page is reached from the node's row, the tab from the server's page.
    """
    return ()


def add_routes(router: APIRouter) -> APIRouter:
    """Add the node page, the four dialogs and their four action routes, for BOTH owners."""
    declare()

    @router.get(NODE_PAGE_PATH, response_class=HTMLResponse, name="page-node-mods")
    async def node_mods_page(request: Request):
        # no-store: a write returns to this page, and the browser must not serve the pre-write page
        # from its cache when it does (the same reason every console page sets it).
        return HTMLResponse(i18n.render(request, MODS_TEMPLATE, **await node_page_context(request)),
                            headers={"Cache-Control": "no-store"})

    for owner in OWNERS:
        for verb in VERBS:
            # the DIALOG is a POST: its control is a POST form, so a GET names nothing to open.
            router.add_api_route(_dialog_pattern(owner, verb), _dialog_handler(owner, verb),
                                 methods=["POST"],
                                 dependencies=[Depends(session.csrf_protect)],
                                 name=f"mods-{owner}-{verb}")
            router.add_api_route(_run_pattern(owner, verb), _run_handler(owner, verb),
                                 methods=["POST"],
                                 dependencies=[Depends(session.csrf_protect)],
                                 name=f"mods-{owner}-{verb}-run")
        # the RELEASE READ is a GET: it changes nothing, so it carries no CSRF token and nothing a
        # forged request could act on. Gated by the download's own capability (see ``capabilities``).
        router.add_api_route(_releases_pattern(owner), _releases_handler(owner), methods=["GET"],
                             name=f"mods-{owner}-releases")
    return router


def _run_handler(owner: str, verb: str):
    """The route that PERFORMS one write — the dialog's own form posts here."""

    async def handle(request: Request) -> RedirectResponse:
        return await _write_route(request, owner, verb)

    return handle


def register(registrar) -> None:
    """Register these pages with the registrar, under this module's own owner name."""
    router = APIRouter()
    add_routes(router)
    registrar.register_pages(OWNER, router, capabilities=capabilities())
