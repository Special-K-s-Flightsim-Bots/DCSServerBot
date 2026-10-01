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
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from .. import permissions, readmodels
from . import dashboard as dashboard_page
from . import lists as lists_page
from . import logs as logs_page

__all__ = ["NODES_PATH", "NODES_CAPABILITY", "NODES_ROLES", "NODES_TABLE", "NODES_TITLE",
           "NODES_LEAD", "NAV_LABEL", "PAGE", "NODES_LOG_PATH", "LOG_DOWNLOAD_CAPABILITY",
           "LOG_DOWNLOAD_MAX_BYTES", "log_download_path", "log_download_control",
           "capabilities", "nav_items", "add_routes"]

log = logging.getLogger(__name__)

NODES_PATH = "/nodes"
NODES_CAPABILITY = "nodes.view"
NODES_ROLES: tuple[str, ...] = lists_page.CLUSTER_ROLES
NODES_TABLE = "nodes"
NODES_TITLE = "Nodes"
NODES_LEAD = ("Every node in the cluster, with the instances and servers it carries. An Admin's "
              "row for a node that is heartbeating offers Restart, Shut down and Upgrade — each "
              "takes every server on that node down with it, though Upgrade is offered only while "
              "the node itself reports an update pending — and the power pair: Take servers "
              "offline stops the servers that are up and, unless the box is cleared, marks them as "
              "maintenance; Bring servers online reverts exactly that — it clears the maintenance "
              "flags its own power-off set and starts the servers it stopped, while a flag somebody "
              "set by hand is left alone. Neither touches the node's services — this console keeps "
              "running, and the servers are brought back from this very page. Those two are offered "
              "on the NODE's own state, not on what its servers happen to be doing: a node that is "
              "heartbeating and has no power-off on record is in service and offers Take servers "
              "offline — whether or not anything is running — and a node whose power-off is on "
              "record offers Bring servers online. A server's own maintenance flag has "
              "its own control on the Servers page — one server to a row. An OFFLINE node offers no "
              "control: it cannot be reached at all, and starting it is a job for the machine "
              "itself, never for this browser. A node's ONLINE/OFFLINE tag is the heartbeat's "
              "verdict, so it can read ONLINE while its servers are stopped or under maintenance.")
NAV_LABEL = "Nodes"

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
        "label": "Download log",
        "title": f"Download log — hand over {name}'s own bot log file as a download",
        "aria": f"Download the bot log of node {name}",
    }


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    PAGE.declare()
    return {NODES_PATH: NODES_CAPABILITY, NODES_LOG_PATH: LOG_DOWNLOAD_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (PAGE.nav_item(),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the node-log download route and the Nodes route to the shell's own router."""
    add_log_download_route(router)
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
