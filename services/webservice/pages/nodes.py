"""The Nodes page: every node in the cluster, in full.

One of the two CLUSTER lists (see :mod:`services.webservice.pages.lists`): the mockups' role model
gives a DCS member the read-only Operations lists but not the cluster ones, so this page declares
the narrower pair — a DCS member is not offered the link and the route refuses them.

WHO READS IT, IN FULL: ``Admin``/``DCS Admin`` through a role, and a MANAGER through its scope
(``scope_grants``) — scoped to the nodes that carry their servers. **Reading** the list is that
wide; **operating** a node is not: the five writes below are declared with ``NODE_ROLES``
(``Admin`` only) and carry no scope grant, because they take every server on the node out of
service.

WHAT A ROW OFFERS (W4c-revised, W4d, restated by W5b):

* Restart, Shut down and Upgrade — the real node lifecycle (``Node.restart()`` / ``Node.shutdown()``
  / ``Node.upgrade()``, mirrored by ``plugins/admin/actions.py``). **Upgrade is OFFERED ONLY WHILE
  THE NODE REPORTS AN UPDATE PENDING** (W7b): the console polls ``Node.upgrade_pending()`` in the
  background (``services/webservice/upgrade.py``, never on a render) and hides the control once
  there is nothing to upgrade, saying ``"no update check yet"`` while the value is unknown;
* the POWER pair Take servers offline / Bring servers online — ``/node offline`` and ``/node online``,
  which act on the SERVERS the node carries: the first stops the servers that are up (through the
  engine's popup chain) and, unless its box is cleared, marks them as maintenance; the second reverts
  exactly that operation — the flags it set and the servers it stopped. They touch NO service. The
  node's services, including the webservice serving this page, keep running, which is why these two
  can be a browser control at all and why a node taken offline through them is brought back from this
  very page.

Every control is reachable only for ``Admin`` and only for a node the cluster can actually reach,
and it is OMITTED — never rendered disabled — otherwise.

THE PAIR IS GATED ON THE NODE'S OWN STATE, NOT ON THE SERVERS AND NOT ON A FLAG (W7a, restating W5b).
"offline" here means the SERVERS, never the node's own process — that is *Shut down*, the trio's own
third control. Which half is offered is decided by ``pages/actions.node_power_states``: a node that
is heartbeating, carries servers and has NO power-off on record is IN SERVICE and offers *Take servers
offline* — whether or not anything happens to be running — and a node whose power-off IS on record
offers *Bring servers online*. The servers' statuses decide neither (they did until W7a, which is what
put an online control on an online node), and the maintenance flag is not the row's business any more:
its own controls are on the SERVER row (``Maintenance`` / ``End maintenance``), one to a server, which
is what §10.4's "one home per concept" means.

THE TAG AND THE CONTROL NOW AGREE. The ONLINE/OFFLINE tag is the HEARTBEAT's verdict
(``readmodels.nodes``: ``node is not None`` in the master's registry) and the power pair is the node's
own power state, asked of the same reachability first — so a row tagged ONLINE offers the way back only
when a power-off is on record, and never because "some server is down". (The tag can still read ONLINE
with the servers stopped or under maintenance: that is the SERVERS' state, and a server's own flag has
its own control on the Servers page.)

WHAT A ROW STILL CANNOT OFFER: a node whose PROCESS is down is started by the MACHINE, not by a
browser — an OS-level job (a launcher, a service, a terminal) that no page can perform. The pair
above does not contradict that: it changes SERVERS, and it is offered only while the node's own
services answer.

THE LOG DOWNLOAD — the row's one READ control. *Download log* hands over THAT node's own
bot log file as a file download. It is an explicit user action, so it is a ROUTE
(:data:`NODES_LOG_PATH`, ``GET``), never a render step: the console's no-RPC-on-render rule stays
intact, and a page render asks no node for anything.

WHAT IT DOES — AND WHAT IT DOES NOT. It hands over ONE complete file, once, on demand. It does not
tail, stream, merge or live-update an agent's log; the console's log PANEL stays master-only
(the decision of 2026-09-30, audit Q3 deferred), and a node's log is here only ever a file
somebody asked for. Nothing about the file is parsed, filtered or clipped: an oversized log is
REFUSED with its own sentence (:data:`LOG_DOWNLOAD_MAX_BYTES`), never silently truncated into a
file that looks complete.

WHERE THE PATH COMES FROM. ``readmodels.log_path_for`` builds ``logs/dcssb-<node>.log`` — a
RELATIVE path, the very one ``run.py:85`` writes the node's own rotating log at — and it is sent to
``Node.read_file``, which resolves it in the WORKING DIRECTORY OF THE NODE THAT ANSWERS (the master
for its own row, the agent's own process for an agent's row). A master-local absolute path would be
wrong on any other machine, so none is ever built; ``plugins/admin/commands.py:515`` reads a
node-side file the same relative way. See :func:`log_download_path`.

WHO MAY. The gate is the LOG PANEL's OWN capability — ``logs.view``, declared Admin-only by
``pages/logs``, reused here as :data:`LOG_DOWNLOAD_CAPABILITY` rather than re-spelled. No new
capability is invented, the control is omitted for anybody the log panel is omitted for, and a
crafted request meets the same 403 the log panel answers with.

THE NODE LOG VIEWER — the row's SECOND read control, beside the download. *View log* opens
:data:`NODE_LOG_VIEW_PATH` (a page) and its follower endpoint :data:`NODE_LOG_WINDOW_PATH` (JSON),
which tail a NODE'S OWN bot log — the remote node's ``logs/dcssb-<node>.log`` — the SAME way the
server's Log tab tails a server's ``dcs.log``: a BOUNDED window (40 lines, 64 KiB), follow while
the page is open, and "Load older" to page back. It reuses the node's ALREADY-BUILT windowed read
(``Node.read_file_window``: the node writes only the window into the one-shot ``files`` drop-box
row and the master reads-and-deletes it) — NO protocol change, NO new table or column, NO new disk.
The console's log PANEL is still master-only by default; this is the ONE way to look at a REMOTE
node's log from the website, and it is the download's own contract widened from "hand over the
whole file once" to "follow a window".

EVERY FAILURE IS ITS OWN SENTENCE, never an empty viewer: a node the cluster cannot reach, no log
file on the node yet (the NORMAL "pending" state), a log ROTATED AWAY (the file is gone — the same
"pending" answer, with the stale tail cleared), a node that cannot read its own file, a node that
does not answer in time, a node that answers a failure CODE instead of bytes, and a rotation that
lands MID-WALK or between follows (the view RESETS and says so; two files are never spliced).
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .. import i18n, permissions, readmodels
from ..readmodels import dcslog as dcs_log
from . import dashboard as dashboard_page
from . import lists as lists_page
from . import logs as logs_page

__all__ = ["NODES_PATH", "NODES_CAPABILITY", "NODES_ROLES", "NODES_TABLE", "NODES_TITLE",
           "NODES_LEAD", "NAV_LABEL", "PAGE", "NODES_LOG_PATH", "LOG_DOWNLOAD_CAPABILITY",
           "LOG_DOWNLOAD_MAX_BYTES", "log_download_path", "log_download_control",
           "NODE_LOG_VIEW_PATH", "NODE_LOG_WINDOW_PATH", "NODE_LOG_TEMPLATE", "LOG_VIEW_LABEL",
           "log_view_path", "log_view_control", "node_log_window_path",
           "capabilities", "nav_items", "add_routes"]

log = logging.getLogger(__name__)

NODES_PATH = "/nodes"
NODES_CAPABILITY = "nodes.view"
NODES_ROLES: tuple[str, ...] = lists_page.CLUSTER_ROLES
NODES_TABLE = "nodes"
NODES_TITLE = i18n._("Nodes")
NODES_LEAD = i18n._("Every node in the cluster, with the instances and servers it carries. Acting "
                    "on a node takes every server on it down with it, so check the servers before "
                    "you act.")
NAV_LABEL = i18n._("Nodes")

PAGE = lists_page.ListPage(table=NODES_TABLE, path=NODES_PATH, capability=NODES_CAPABILITY,
                           roles=NODES_ROLES, title=NODES_TITLE, lead=NODES_LEAD,
                           nav_label=NAV_LABEL, scope_grants=True, writes=True)

#: THE NODE LOG DOWNLOAD's route — one literal TEMPLATE path (``{node}`` is one path segment, the
#: node's own name). It is not the page path and not a page: it answers GET, it renders no chrome,
#: and it is registered with the SAME capability the log panel declares.
NODES_LOG_PATH = "/nodes/{node}/log"

#: The capability the download route declares AND the row's control is offered on — the LOG PANEL's
#: own (``pages/logs``: ``logs.view``, ``Admin`` only). Named through ``logs_page`` rather than typed
#: again, so "the log is Admin-only" has ONE spelling in the console.
LOG_DOWNLOAD_CAPABILITY = logs_page.LOGS_CAPABILITY

#: HOW MUCH OF A NODE'S LOG THIS CONSOLE WILL HAND OVER IN ONE RESPONSE. The rule, stated where the
#: number is declared: a log AT OR BELOW this size is served whole; a log ABOVE it is REFUSED with
#: its own sentence and served not at all — never a truncated file, because a clipped log that
#: downloads like a complete one is worse than no download. 10 MiB is ``run.py``'s own default
#: ``logrotate_size`` (10485760), so the bot's own rotation keeps a log servable by construction.
#: NOTE (honest bound): the Node API's ``read_file`` is a WHOLE-FILE contract, so the bytes are read
#: before the check — this limit bounds what the CONSOLE HANDS BACK, not the node's own read.
LOG_DOWNLOAD_MAX_BYTES = 10 * 1024 * 1024

#: the content type of a served log. ``run.py`` writes the file utf-8, so the bytes are text.
LOG_DOWNLOAD_MEDIA_TYPE = "text/plain; charset=utf-8"

#: THE NODE LOG VIEWER's two routes. The VIEW page renders the node's own bot log as a tailed
#: window (the same card the server's Log tab renders); the WINDOW route answers the follower's
#: ``?offset=``/``?behind=`` requests with JSON. Both name the node as ONE path segment, exactly as
#: the download does, and both carry the download's OWN gate (``logs.view``) — a caller who cannot
#: download a node's log cannot view it either.
NODE_LOG_VIEW_PATH = "/nodes/{node}/log/view"
NODE_LOG_WINDOW_PATH = "/nodes/{node}/log/window"

#: the template the viewer page renders — a card driven by the SAME follower script the server's Log
#: tab uses (``static/server-log.js``), so the two viewers share one implementation.
NODE_LOG_TEMPLATE = "node_log.html"

#: the row control's own label (the control BESIDE the log download).
LOG_VIEW_LABEL = i18n._("View log")

#: THE VIEWER'S FAILURE SENTENCES — each its OWN ``i18n._``-marked TEMPLATE, its operator data
#: (``{node}``/``{path}``/``{error}``) interpolated AFTER the sentence is translated, so a node name
#: and a log path stay verbatim. The offline/unknown sentence is the SERVER log tab's own
#: (``server_detail.NODE_OFFLINE_SENTENCE``) — the same answer for the same fact — reused rather than
#: re-spelled, and imported lazily (``server_detail`` imports THIS module).
NODE_LOG_MISSING_SENTENCE = i18n._("No log file yet for node '{node}' (looked for '{path}').")
NODE_LOG_PERMISSION_SENTENCE = i18n._("Node '{node}' could not read its own log file: permission "
                                      "denied.")
NODE_LOG_TIMEOUT_SENTENCE = i18n._("Node '{node}' did not answer in time, so its log was not read.")
NODE_LOG_HANDOVER_SENTENCE = i18n._("Node '{node}' could not hand over its log ({error}).")
NODE_LOG_FAILURE_SENTENCE = i18n._("Node '{node}' answered with a failure code instead of its log.")

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def log_download_path(node_name: str) -> str:
    """The URL of *node_name*'s log download — the node's name as ONE percent-encoded path segment.

    One path segment and nothing else: a name carrying a ``/`` cannot walk out of this route, and a
    name carrying anything a URL must escape is escaped here rather than by a caller. The route
    decodes it again (``{node}`` is a path parameter), so the name the handler resolves is the name
    that was rendered.
    """
    return f"/nodes/{quote(readmodels.text(node_name), safe='')}/log"


def log_download_control(node_name: str, roles, manager: bool = False) -> dict | None:
    """The node row's *Download log* control, as DATA — or ``None`` for a caller who may not use it.

    The gate is the LOG PANEL's own capability (:data:`LOG_DOWNLOAD_CAPABILITY`,
    ``permissions.allows``) — the SAME predicate the route's access gate runs, so
    ``offered ⊆ authorised`` holds by construction: whoever is not offered the control is refused by
    the gate with the log panel's own refusal, and no second authorization rule exists to drift.

    The control is a LINK, not a form: ``path`` is a URL, and there is no CSRF token because a
    download changes nothing. ``label``/``title``/``aria`` name the node, so the control is never an
    unlabelled glyph (and a screen reader hears which node it belongs to).
    """
    if not permissions.allows(LOG_DOWNLOAD_CAPABILITY, roles, manager=manager):
        return None
    name = readmodels.text(node_name)
    return {
        "path": log_download_path(name),
        "label": i18n._("Download log"),
        # a per-request SENTENCE: the node's name is operator data, so it is interpolated AFTER the
        # translation, at render (the template's guarded ``say`` global) — never baked into the msgid.
        "title": {"msgid": i18n._("Download log — hand over {node}'s own bot log file as a download"),
                  "params": {"node": name}},
        "aria": {"msgid": i18n._("Download the bot log of node {node}"), "params": {"node": name}},
    }


def log_view_path(node_name: str) -> str:
    """The URL of *node_name*'s log VIEWER — the node's name as ONE percent-encoded path segment.

    The viewer's OWN route (a page), not the download's ``/log``: the two controls are separate
    requests, so a bookmarked view URL and a bookmarked download URL can never be mistaken for one
    another. Encoded exactly as :func:`log_download_path` encodes, and decoded again by the route's
    ``{node}`` parameter.
    """
    return f"/nodes/{quote(readmodels.text(node_name), safe='')}/log/view"


def node_log_window_path(node_name: str) -> str:
    """The URL of *node_name*'s window endpoint — the follower's own request base (L-viewer)."""
    return f"/nodes/{quote(readmodels.text(node_name), safe='')}/log/window"


def log_view_control(node_name: str, roles, manager: bool = False) -> dict | None:
    """The node row's *View log* control, as DATA — or ``None`` for a caller who may not use it.

    The SAME gate as :func:`log_download_control` (the log panel's own ``logs.view``), so the two
    read controls are offered to EXACTLY the same caller and a crafted request meets the same
    refusal: ``offered ⊆ authorised`` holds for both by construction. A LINK, not a form (a view
    changes nothing, so there is no CSRF token).
    """
    if not permissions.allows(LOG_DOWNLOAD_CAPABILITY, roles, manager=manager):
        return None
    name = readmodels.text(node_name)
    return {
        "path": log_view_path(name),
        "label": LOG_VIEW_LABEL,
        "title": {"msgid": i18n._("View log — follow {node}'s own bot log file while this page is "
                                  "open"),
                  "params": {"node": name}},
        "aria": {"msgid": i18n._("View the bot log of node {node}"), "params": {"node": name}},
    }


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {NODES_PATH: NODES_CAPABILITY, NODES_LOG_PATH: LOG_DOWNLOAD_CAPABILITY,
            NODE_LOG_VIEW_PATH: LOG_DOWNLOAD_CAPABILITY,
            NODE_LOG_WINDOW_PATH: LOG_DOWNLOAD_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (PAGE.nav_item(),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the node-log download, the node-log viewer, and the Nodes route to the shell's router."""
    add_log_download_route(router)
    add_node_log_routes(router)
    return lists_page.add_list_route(router, PAGE)


# ------------------------------------------------------------------------------- the log download

def _refuse(status_code: int, sentence: str) -> PlainTextResponse:
    """The honest refusal a download answers with: ONE sentence in plain text, never an empty file.

    A ``PlainTextResponse`` and NOT an ``HTTPException``: the shell renders a raised refusal as its
    own generic access-denied page, whose copy is about AUTHORIZATION ("your account does not have
    access"), which would be false for a log that simply is not there. A download has no page to
    render its failure into, so the sentence IS the answer — and a zero-byte 200 that downloads like
    an empty log is exactly what the card forbids.
    """
    log.info("Node log download answered %d: %s", status_code, sentence)
    return PlainTextResponse(sentence + "\n", status_code=status_code)


def _reachable_node(source, node_name: str):
    """The node object *node_name* names in the caller's own view — or ``None``.

    Reads the SAME scoped source the rows render from, so a node the caller cannot see is not
    resolvable here either; an entry that is ``None`` is a node the cluster knows and cannot reach,
    and is the same answer as a name that matches nothing (there is no log to fetch either way). The
    lookup folds case, exactly as the seam's ``resolve_node`` does, because a hand-typed or stale URL
    is not a different node.
    """
    entries = getattr(source, "nodes", None)
    if not isinstance(entries, dict):
        return None
    found = entries.get(node_name)
    if found is None:
        wanted = readmodels.text(node_name).casefold()
        for key, node in entries.items():
            if readmodels.text(key).casefold() == wanted:
                found = node
                break
    return found


def _safe_filename(name: str) -> str:
    """``dcssb-<node>.log`` as a SAFE download filename: config data must not shape a header."""
    cleaned = _UNSAFE_FILENAME.sub("_", readmodels.text(name)).strip("._")
    return cleaned or "node.log"


def add_log_download_route(router: APIRouter) -> APIRouter:
    """Add THE node-log download route (``GET``) — the row's one READ control.

    A GET and only a GET: nothing changes, so there is no CSRF dependency, and a POST is refused as
    a method the route does not answer. The target IS in the URL (this is a download link, not a
    write): it names a node of the caller's own view, and the capability gate — the log panel's own,
    Admin-only — is the authority, so the URL selects among nodes the caller may already read.

    THE FAILURES ARE EACH THEIR OWN SENTENCE, never a zero-byte download:

    * a node the cluster cannot reach, or a name that matches nothing — 404, "offline or unknown";
    * no log file on the node yet — 404, naming the path that was looked for;
    * a node that cannot read its own file — 403, "permission denied";
    * a node that does not answer in time — 504;
    * anything else the node raises, and a node that answers with a failure CODE instead of bytes
      (``Node.read_file`` is typed ``bytes | int``, and an ``int`` is never content) — 502;
    * a log above :data:`LOG_DOWNLOAD_MAX_BYTES` — 413, naming the size and the limit.
    """
    @router.get(NODES_LOG_PATH, name="node-log-download")
    async def node_log(request: Request, node: str) -> Response:
        requested = readmodels.text(node)
        source = dashboard_page.request_source(request)
        target = _reachable_node(source, requested)
        if target is None:
            return _refuse(404, f"Node '{requested}' is offline or unknown, so its log cannot be "
                                f"read from this console.")
        canonical = (readmodels.text(readmodels.safe(lambda: getattr(target, "name", ""), ""))
                     or requested)
        path = readmodels.log_path_for(canonical)
        if path is None:  # pragma: no cover - a reachable node always has a readable name
            return _refuse(404, f"Node '{canonical}' has no name to build a log file from.")
        try:
            data = await target.read_file(str(path))
        except FileNotFoundError:
            return _refuse(404, f"No log file on node '{canonical}' yet "
                                f"(looked for '{path}').")
        except PermissionError:
            return _refuse(403, f"Node '{canonical}' could not read its own log file: "
                                f"permission denied.")
        except (TimeoutError, asyncio.TimeoutError):
            return _refuse(504, f"Node '{canonical}' did not answer in time, so its log was not "
                                f"read.")
        except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
            log.warning("Node log download: node '%s' raised %s", canonical, type(ex).__name__,
                        exc_info=True)
            return _refuse(502, f"Node '{canonical}' could not hand over its log "
                                f"({type(ex).__name__}).")
        if not isinstance(data, (bytes, bytearray)):
            # ``read_file`` is ``bytes | int`` and an ``int`` is a FAILURE CODE, never content: a
            # download of it would be a nonsense file that looks like a real answer.
            return _refuse(502, f"Node '{canonical}' answered with a failure code instead of its "
                                f"log.")
        payload = bytes(data)
        if len(payload) > LOG_DOWNLOAD_MAX_BYTES:
            return _refuse(413, f"Node '{canonical}'s log is {len(payload)} bytes, above the "
                                f"{LOG_DOWNLOAD_MAX_BYTES}-byte limit this console hands over in "
                                f"one download.")
        return Response(content=payload, media_type=LOG_DOWNLOAD_MEDIA_TYPE,
                        headers={"Content-Disposition":
                                 f'attachment; filename="{_safe_filename(path.name)}"'})

    return router


# ------------------------------------------------------------------------------ the log VIEWER
# TAIL A NODE'S OWN BOT LOG — the SAME bounded window the server's Log tab reads, over the SAME
# node mechanism (``Node.read_file_window``: master ``seek`` / one-shot ``files`` drop-box row to an
# agent). For an agent this is the ONE way to see its log from the website; for the master it is the
# local file, read identically. The pure half (line format, window arithmetic) is the SHARED
# ``readmodels.dcslog`` module — the node's bot log has the same ``asctime.msecs LEVEL\\tmsg`` shape
# the DCS reader parses — and the window/back-walk helpers are ``pages.server_detail``'s, passed a
# node-worded read so the failures name the NODE's own log.

def _node_log_file_path(node_name: str) -> str | None:
    """The node-side RELATIVE path of *node_name*'s own bot log (``logs/dcssb-<node>.log``), or None.

    The VERY path ``run.py`` writes the node's rotating log at, resolved ON THE NODE (its own working
    directory) — never a master-local absolute path, which would be wrong on any other machine. The
    same construction the log DOWNLOAD uses (``readmodels.log_path_for``), so a view and a download
    read the same file.
    """
    path = readmodels.log_path_for(readmodels.text(node_name))
    return str(path) if path is not None else None


async def _read_window(node, path: str, node_name: str, *, offset=None,
                       behind=None) -> tuple[bytes, int, int | float]:
    """One NODE-log window read: ``(bytes, size, identity)``; every failure is the NODE's own sentence.

    The node-worded twin of ``server_detail._read_window``: same calls, same shape, but the sentences
    name the node's OWN log (the file read is ``logs/dcssb-<node>.log``, not a server's ``dcs.log``).
    ``FileNotFoundError`` is the NORMAL "no log file yet" state (``pending`` — the follower keeps its
    cadence; it is also the answer once a log has rotated AWAY, so the view never holds a stale tail).
    """
    from . import server_detail as detail_page
    try:
        raw, size, identity = await node.read_file_window(path, length=dcs_log.WINDOW_BYTES,
                                                          offset=offset, behind=behind)
    except FileNotFoundError:
        raise detail_page._LogUnavailable(NODE_LOG_MISSING_SENTENCE,
                                          params={"path": path, "node": node_name}, pending=True)
    except PermissionError:
        raise detail_page._LogUnavailable(NODE_LOG_PERMISSION_SENTENCE, params={"node": node_name})
    except (TimeoutError, asyncio.TimeoutError):
        raise detail_page._LogUnavailable(NODE_LOG_TIMEOUT_SENTENCE, params={"node": node_name})
    except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
        log.warning("Node log window: node '%s' raised %s", node_name, type(ex).__name__,
                    exc_info=True)
        raise detail_page._LogUnavailable(NODE_LOG_HANDOVER_SENTENCE,
                                          params={"node": node_name, "error": type(ex).__name__})
    if (not isinstance(raw, (bytes, bytearray)) or not isinstance(size, int)
            or not isinstance(identity, (int, float))):
        raise detail_page._LogUnavailable(NODE_LOG_FAILURE_SENTENCE, params={"node": node_name})
    return bytes(raw), size, identity


async def _node_log_context(request: Request, node, node_name: str, path: str) -> dict:
    """The viewer page's data: the window URL, the initial TAIL, the cursors, and the honest sentence.

    A render-time read of a BOUNDED window (the last ``dcs_log.TAIL_LINES`` complete lines), the
    SAME walk the server's Log tab uses. Any failure renders its OWN sentence — never a blank body:
    the body element is ALWAYS shipped so the follower can start from the empty state and turn the
    sentence into the log the moment the file appears.
    """
    from . import server_detail as detail_page
    registrar = getattr(request.app.state, "webui_registrar", None)
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    environment = i18n.environment_for(request)
    base = {
        "title": node_name,
        "page_title": node_name,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, NODES_TITLE, target=node_name),
        "nav_groups": dashboard_page.nav_groups(registrar, roles, current=NODES_PATH,
                                                manager=manager),
        "user": dashboard_page.identity_summary(request),
        "pills": [],
        "log_window_url": node_log_window_path(node_name),
        "log_follow_seconds": dcs_log.FOLLOW_SECONDS,
        "log_path": path,
        "log_node": node_name,
        "log_lines": (), "log_offset": 0, "log_before": 0, "log_size": 0, "log_identity": "",
        "log_at_start": False,
    }
    allowed = dcs_log.LEVEL_FILTERS["all"]
    try:
        state = await detail_page._collect_back(node, path, node_name, behind=dcs_log.BEHIND_END,
                                                allowed=allowed, want=dcs_log.TAIL_LINES,
                                                read=_read_window)
    except detail_page._LogUnavailable as ex:
        return base | {"log_available": False,
                       "log_message": detail_page._rendered(environment, ex.msgid, **ex.params)}
    message = ("" if state["lines"]
               else detail_page._rendered(environment, readmodels.LOG_EMPTY_MESSAGE))
    return base | {"log_available": True, "log_message": message,
                   "log_lines": state["lines"], "log_offset": state["offset"],
                   "log_before": state["before"], "log_size": state["size"],
                   "log_identity": state["identity"], "log_at_start": state["at_start"]}


def add_node_log_routes(router: APIRouter) -> APIRouter:
    """Add the node-log VIEWER (a page) and its WINDOW endpoint (JSON).

    A page and a GET that answer JSON: the page tails a node's OWN bot log (the same follower
    script, ``static/server-log.js``, drives it), and the window route answers ``?offset=``
    (FOLLOW) and ``?behind=`` (PAGE BACK). Both are GETs and neither changes anything, so there is no
    CSRF dependency, and both carry the download's OWN gate (``logs.view``): a caller who is not
    offered the controls meets the same 403 the log panel does.

    THE FAILURES ARE EACH THEIR OWN SENTENCE (see the module docstring): an offline/unknown node;
    no log file yet; a log rotated away (the same "pending" answer); a node that cannot read its own
    file; a node that does not answer; a node that answers a failure CODE; and a rotation that lands
    mid-stream or mid-walk (the view resets and says so). The view page is the NODE-FIRST request —
    it resolves the node through the caller's own scoped source, so a node outside their view is a
    404, exactly as an unknown one.
    """
    from . import server_detail as detail_page

    @router.get(NODE_LOG_WINDOW_PATH, name="node-log-window")
    async def node_log_window(request: Request, node: str, offset: str | None = None,
                              behind: str | None = None, identity: str | None = None) -> Response:
        environment = i18n.environment_for(request)
        requested = readmodels.text(node)
        if not offset and not behind:
            return JSONResponse(_position_required(environment), status_code=400)
        source = dashboard_page.request_source(request)
        target = _reachable_node(source, requested)
        if target is None:
            return JSONResponse(detail_page._unavailable_payload(
                detail_page._rendered(environment, detail_page.NODE_OFFLINE_SENTENCE,
                                      node=requested)))
        canonical = (readmodels.text(readmodels.safe(lambda: getattr(target, "name", ""), ""))
                     or requested)
        path = _node_log_file_path(canonical)
        if path is None:  # pragma: no cover - a reachable node always has a readable name
            return JSONResponse(detail_page._unavailable_payload(
                detail_page._rendered(environment, NODE_LOG_MISSING_SENTENCE,
                                      node=canonical, path="logs/dcssb-%s.log" % canonical)))
        allowed = dcs_log.LEVEL_FILTERS["all"]
        try:
            if offset is not None:
                start = detail_page._byte(offset)
                if start is None:
                    return JSONResponse(_offset_invalid(environment), status_code=400)
                held = detail_page._identity(identity)
                raw, size, current = await _read_window(target, path, canonical, offset=start)
                if size < start or (held is not None and detail_page.identity_token(current) != held):
                    # A NEW LOG (a truncation, or a replacement — even one already bigger than the
                    # offset): reset the view, never splice two files.
                    state = await detail_page._collect_back(target, path, canonical,
                                                            behind=dcs_log.BEHIND_END,
                                                            allowed=allowed, want=dcs_log.TAIL_LINES,
                                                            read=_read_window)
                    message = ("" if state["lines"]
                               else detail_page._rendered(environment, readmodels.LOG_EMPTY_MESSAGE))
                    return JSONResponse(detail_page._available_payload(environment, state,
                                                                       reset=True, message=message))
                return JSONResponse(detail_page._available_payload(
                    environment, detail_page._follow_state(raw, start, size, current, allowed)))
            end = detail_page._byte(behind)
            if end is None:
                return JSONResponse(_behind_invalid(environment), status_code=400)
            state = await detail_page._collect_back(target, path, canonical, behind=end,
                                                    allowed=allowed, want=dcs_log.TAIL_LINES,
                                                    read=_read_window)
            if state["rotated"]:
                return JSONResponse(detail_page._available_payload(
                    environment, state, reset=True,
                    message=detail_page._rendered(environment, dcs_log.ROTATED_SENTENCE)))
            message = ("" if state["lines"]
                       else detail_page._rendered(environment, readmodels.LOG_EMPTY_MESSAGE))
            return JSONResponse(detail_page._available_payload(environment, state, message=message))
        except detail_page._LogUnavailable as ex:
            return JSONResponse(detail_page._unavailable_payload(
                detail_page._rendered(environment, ex.msgid, **ex.params), pending=ex.pending))

    @router.get(NODE_LOG_VIEW_PATH, response_class=HTMLResponse, name="page-node-log")
    async def node_log_view(request: Request, node: str) -> Response:
        requested = readmodels.text(node)
        source = dashboard_page.request_source(request)
        target = _reachable_node(source, requested)
        if target is None:
            raise HTTPException(status_code=404,
                                detail=f"Node '{requested}' is offline or unknown, so its log "
                                       f"cannot be read from this console.")
        canonical = (readmodels.text(readmodels.safe(lambda: getattr(target, "name", ""), ""))
                     or requested)
        path = _node_log_file_path(canonical)
        context = await _node_log_context(request, target, canonical, path or "")
        return HTMLResponse(i18n.render(request, NODE_LOG_TEMPLATE, **context),
                            headers={"Cache-Control": "no-store"})

    return router


def _position_required(environment) -> dict:
    """The honest 400 body a window request without a cursor gets (the sentence the tab can render)."""
    from . import server_detail as detail_page
    return {"available": False, "lines": "", "size": 0,
            "message": detail_page._rendered(environment,
                                             i18n._("An offset or a behind position is required."))}


def _offset_invalid(environment) -> dict:
    from . import server_detail as detail_page
    return {"available": False, "lines": "", "size": 0,
            "message": detail_page._rendered(environment, i18n._(
                "The offset must be a non-negative whole number."))}


def _behind_invalid(environment) -> dict:
    from . import server_detail as detail_page
    return {"available": False, "lines": "", "size": 0,
            "message": detail_page._rendered(environment, i18n._(
                "The behind position must be a non-negative whole number."))}
