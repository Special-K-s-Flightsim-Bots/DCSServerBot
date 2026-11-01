"""The music pages: the playlist list at ``/music`` and the playlist editor at ``/music/<playlist>``.

THE SURFACE OWNS NO DOMAIN KNOWLEDGE. Every read and every write goes through the ``music`` action
layer (``plugins/music/actions``), so the page holds no statement, no path and no tag read: it shapes
what the action returned for the template and posts a form to a route that calls the same actions the
console's other transports call.

TWO READS PER RENDER, and both are the action's own cached answers:

* the OVERVIEW reads the playlist list once, then one radio read per ACTIVE server (a radio belongs
  to one server) and one library summary per visible node. A node that does not answer renders as an
  unknown row; it never fails the page.
* the EDITOR reads the playlist once and the library of the node that playlist resolves on once. The
  library tree is the source pane; the playlist is the destination pane; there is no third list.

A PLAYLIST THAT DOES NOT EXIST YET IS A DRAFT. The editor of a name no playlist holds renders an
empty destination pane with the browser beside it: a playlist exists once it has a track (the ``add``
action creates it), so nothing here writes an empty one.

NO CONTROL IS DRAWN THAT CANNOT RUN. A write control exists only when its action is registered in this
process (``core.actions.action_available``) AND the caller holds the class's capability; with the
action registry empty every page here renders read-only. The row controls are GLYPH buttons whose word
lives in ``title``/``aria-label``.

ROW ORDER HAS ONE CONTROL AND ONE WRITE. A row's grip is the reorder control: ``static/reorder.js``
drags it, and the same grip answers Alt+ArrowUp / Alt+ArrowDown for the keyboard. Both post the WHOLE
new order to one write, which refuses anything that is not a permutation of the current rows. A
playlist row carries no up/down glyph pair: one row is one control, and the script needs no glyph to
enhance the table.

THE TWO SECTIONS ARE THE LIVE FRAGMENTS. The radios and the playlists table are rendered by their own
partials (``_music_radios.html`` / ``_music_playlists.html``) from ONE context builder
(:func:`sections_context`), and the page and the console's SSE stream (``pages/live``) both render
those. A streamed frame is therefore the same markup the page drew — same card, same state tag, same
controls — and it is re-emitted only when the rendered HTML changed.

A RADIO CARD IS A PLAYER, AND IT IS ONE PARTIAL. ``_music_player.html`` draws a radio for BOTH this
page and the server's Music tab, from the cards :func:`radio_cards` builds, so the two surfaces show
one player and a control added to it lands on both. The card carries the transport strip the tab
carries: four GLYPH buttons — play, stop, skip and the mode toggle — posted to
``pages.actions.MUSIC_CONTROL_PATH`` with the target in the body. ``control_radio`` accepts
play/stop/skip/mode — STOP is the pause, and no pause verb is offered. A radio whose server is not up
gets no card (its transport could not run), and a caller without ``music.control`` gets no controls.

A ROW WITH NO TAG STILL HAS A NAME. A track whose file carries no title tag renders the file's own
name without its audio suffix — never an empty cell. An unknown length renders an em dash, never
``0:00``.

THE LIBRARY CARD UPLOADS, ON ONE CAPABILITY OF ITS OWN. Its header carries an Upload button and a
plain ``multipart`` file input (the JavaScript-off path), and the card is the drop target
``static/music-upload.js`` enhances — a file dropped anywhere on it lands in the folder on screen, a
file dropped on a folder row lands in THAT folder. Both post to the same route, which enforces the
request cap, hands the bytes to the action and returns to the folder. A refusal the ROUTE makes
(over the cap, no file part) is audited there, because the action never runs for it.
"""
from __future__ import annotations

import datetime
import logging
import posixpath
from dataclasses import replace
from typing import Awaitable, Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from core.action_results import ActionResult
from core.actions import ActionContext, action_available, audit_action, call_action, read_action

from .. import i18n, permissions, readmodels, session
from ..registry import NavItem
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import live as live_page

__all__ = [
    "OWNER", "MUSIC_PATH", "PLAYLIST_PAGE_PATH", "NEW_DIALOG_PATH", "NEW_RUN_PATH",
    "DIALOG_PATH", "RUN_PATH", "PLAY_DIALOG_PATH", "PLAY_RUN_PATH",
    "ADD_PATH", "REMOVE_PATH", "MOVE_PATH", "REORDER_PATH", "UPLOAD_PATH",
    "MUSIC_STREAM_PATH", "MUSIC_FRAGMENTS_PATH", "MUSIC_REFRESH_SECONDS",
    "RADIOS_TARGET", "PLAYLISTS_TARGET", "RADIOS_TEMPLATE", "PLAYLISTS_TEMPLATE",
    "PLAYER_TEMPLATE", "TRANSPORT_GLYPHS", "TRANSPORT_LABELS", "MODE_GLYPHS", "MODE_OTHER",
    "MODE_TO_SHUFFLE", "MODE_TO_REPEAT", "radio_cards",
    "CAPABILITY_VIEW", "CAPABILITY_WRITE", "CAPABILITY_CONTROL", "CAPABILITY_UPLOAD",
    "VIEW_ROLES", "WRITE_ROLES", "CONTROL_ROLES", "UPLOAD_ROLES",
    "DIALOG_VERBS", "DIRECTIONS", "UPLOAD_MAX_BYTES",
    "OVERVIEW_TEMPLATE", "EDITOR_TEMPLATE", "DIALOG_TEMPLATE", "STYLE", "REORDER_SCRIPT",
    "UPLOAD_SCRIPT",
    "NAV_LABEL", "OVERVIEW_TITLE", "EDITOR_TITLE", "DIALOG_FIELD",
    "playlist_url", "dialog_url", "run_url", "add_url", "remove_url", "move_url", "reorder_url",
    "upload_url",
    "sections_context", "music_live_config", "render_for", "refuse_without_view", "event_stream",
    "current_fragments",
    "declare", "capabilities", "nav_items", "add_routes", "register",
]

log = logging.getLogger(__name__)

#: the registrar owner of these paths
OWNER = "music"


def _declare_capabilities() -> None:
    """Declare the four music capabilities, idempotently, once per class of write."""
    permissions.declare_capability(CAPABILITY_VIEW, VIEW_ROLES)
    permissions.declare_capability(CAPABILITY_WRITE, WRITE_ROLES)
    permissions.declare_capability(CAPABILITY_CONTROL, CONTROL_ROLES, scope_grants=True)
    permissions.declare_capability(CAPABILITY_UPLOAD, UPLOAD_ROLES)


# ── the capabilities ──────────────────────────────────────────────────────
# The names are the action layer's own (``plugins/music/actions.CAPABILITY_*``); the page declares
# them because a plugin may not import the console's permission module. Playlists are
# installation-wide, so neither the read nor the playlist write carries a scope grant; a RADIO write
# acts on one server and therefore carries the console's manager scope grant. The UPLOAD writes a
# file onto an INSTALL'S disk, so it is its own capability and carries no scope grant: the node it
# names is resolved through the caller's scope inside the action.
CAPABILITY_VIEW = "music.view"
CAPABILITY_WRITE = "music.write"
CAPABILITY_CONTROL = "music.control"
CAPABILITY_UPLOAD = "music.upload"

VIEW_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")
WRITE_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")
CONTROL_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")
UPLOAD_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")

# ── the paths ─────────────────────────────────────────────────────────────
#: the playlist list
MUSIC_PATH = "/music"
#: one playlist's editor. Registered LAST: a name is one segment, so every write path below is
#: matched before this page sees the path at all.
PLAYLIST_PAGE_PATH = "/music/{playlist:path}"

#: "New playlist" — a dialog that writes nothing and lands on the new name's DRAFT editor
NEW_DIALOG_PATH = "/music/new"
NEW_RUN_PATH = "/music/new/run"

#: one playlist's dialogs and the writes behind them, by verb (``DIALOG_VERBS``)
DIALOG_PATH = "/music/{playlist}/dialog/{verb}"
RUN_PATH = "/music/{playlist}/run/{verb}"

#: "play on…" is a RADIO write, and therefore its own path: a capability is declared per path, and
#: the playlist verbs must not inherit the radio write's scope grant.
PLAY_DIALOG_PATH = "/music/{playlist}/play"
PLAY_RUN_PATH = "/music/{playlist}/play/run"

#: the destination pane's row writes
ADD_PATH = "/music/{playlist}/add"
REMOVE_PATH = "/music/{playlist}/remove"
MOVE_PATH = "/music/{playlist}/move"
REORDER_PATH = "/music/{playlist}/reorder"
#: the LIBRARY card's upload — one file into the selected folder of the node's music directory
UPLOAD_PATH = "/music/{playlist}/upload"

#: THE LIVE FRAGMENTS. The two swap targets, the partials that render them, and the two ``/api``
#: paths that serve them. The ids are the contract between this module, the templates and
#: ``static/shell.js``: ``music.html`` draws the wrapper, and a frame replaces its INNER html, so a
#: fragment is the partial alone. Under ``/api`` so an EventSource is answered with a status and a
#: JSON body, never with a redirect it cannot follow.
RADIOS_TARGET = "mus-radios"
PLAYLISTS_TARGET = "mus-playlists"
RADIOS_TEMPLATE = "_music_radios.html"
PLAYLISTS_TEMPLATE = "_music_playlists.html"
#: THE PLAYER — one radio, drawn by this page AND by the server's Music tab. One template, one builder
#: (:func:`radio_cards`), so the two surfaces cannot drift; it carries no id of its own.
PLAYER_TEMPLATE = "_music_player.html"
MUSIC_STREAM_PATH = "/api/music/stream"
MUSIC_FRAGMENTS_PATH = "/api/music/fragments"
#: the stream's cadence, in seconds. Each tick re-reads the playlist list and one radio read per
#: visible server, so this is the page's own sampling rate and not the dashboard's 2 s.
MUSIC_REFRESH_SECONDS = 3

#: the verbs the generic dialog path serves — "play" has its own path (see above)
DIALOG_VERBS: tuple[str, ...] = ("rename", "duplicate", "delete")
#: the direction a row glyph moves one row
DIRECTIONS: tuple[str, ...] = ("up", "down")

# ── the assets ────────────────────────────────────────────────────────────
OVERVIEW_TEMPLATE = "music.html"
EDITOR_TEMPLATE = "music_playlist.html"
DIALOG_TEMPLATE = "music_dialog.html"
#: the page's own sheet — never ``shell.css``
STYLE = "music.css"
#: the shared row-drag script (a later table applies it through the same data attributes)
REORDER_SCRIPT = "reorder.js"
#: the library card's drop target (progressive enhancement: the form under the button is the no-JS path)
UPLOAD_SCRIPT = "music-upload.js"

#: The request cap of one upload, mirrored by the ACTION (``plugins/music/actions.py``): the console
#: refuses the request here, before the multipart parser decides, and the action refuses a caller that
#: reaches it another way. ``tests/test_music_upload.py`` pins the two numbers and the two sentences.
UPLOAD_MAX_BYTES = 32 * 1024 * 1024
#: The multipart envelope a one-file upload carries around the file — bounded far above the real thing
#: and far below any real track, so the declared-body check can never refuse a file that FITS.
UPLOAD_ENVELOPE_BYTES = 64 * 1024

# ── the form fields ───────────────────────────────────────────────────────
DIALOG_FIELD = "name"
FILE_FIELD = "file"
SONG_FIELD = "song_id"
ORDER_FIELD = "order"
DIRECTION_FIELD = "direction"
SERVER_FIELD = "server"
RADIO_FIELD = "radio"
FOLDER_FIELD = "folder"
NODE_FIELD = "node"
QUERY_FIELD = "q"
ALL_FIELD = "all"
REFRESH_FIELD = "refresh"
#: the separator a "play on…" choice carries, joining one server and one radio into one option value
PAIR_SEPARATOR = "|"

# ── the copy ──────────────────────────────────────────────────────────────
NAV_LABEL = i18n._("Music")
OVERVIEW_TITLE = i18n._("Music")
EDITOR_TITLE = i18n._("Playlist")

OVERVIEW_LEAD = i18n._("Playlists, the library on each node, and what every radio is playing right "
                       "now.")

#: page and card headings
PLAYLISTS_HEADING = i18n._("Playlists")
LIBRARY_HEADING = i18n._("Library · per node")
#: the radios section: it holds every radio of every active server, on air AND idle
RADIOS_HEADING = i18n._("Radios")
BROWSER_HEADING = i18n._("Library")
DESTINATION_HEADING = i18n._("Playlist")

#: the figures, rendered through ``i18n.translate`` so the number is never a template literal
FACT_TRACKS = i18n._("%(count)s track(s)")
FACT_TOTAL = i18n._("total %(length)s")
FACT_MISSING = i18n._("%(count)s file(s) missing on the node")
FACT_FOLDER = i18n._("%(folder)s · %(count)s track(s)")
FACT_SELECTED = i18n._("%(count)s selected")

#: the control words (the glyph carries the drawing, these carry the name)
OPEN_LABEL = i18n._("Open")
PLAY_LABEL = i18n._("Play on a radio")
PLAY_ACTION_LABEL = i18n._("Play on…")
RENAME_LABEL = i18n._("Rename")
DUPLICATE_LABEL = i18n._("Duplicate")
DELETE_LABEL = i18n._("Delete")
REMOVE_LABEL = i18n._("Remove")
ADD_LABEL = i18n._("Add")
ADD_ALL_LABEL = i18n._("Add all %(count)s in this folder")
REMOVE_MISSING_LABEL = i18n._("Remove %(count)s missing")
RESCAN_LABEL = i18n._("Rescan")
UPLOAD_LABEL = i18n._("Upload")
UPLOAD_HINT = i18n._("Upload into this folder, or drop .mp3 and .ogg files here or on a folder in "
                     "the rail.")
NEW_LABEL = i18n._("New playlist")
DRAG_HINT = i18n._("drag to reorder")
#: the word a "play on…" option carries for a radio that is already playing, and the one a card's
#: state tag carries: the console's own words for both radio states
ON_AIR_WORD = i18n._("on air")
IDLE_WORD = i18n._("idle")
#: the third state word: a transport press is in flight and the track is not there yet
LOADING_WORD = i18n._("Loading…")
#: the transport strip of a card — the words its four glyph buttons carry in `aria-label`/`title`
TRANSPORT_PLAY = i18n._("Play")
TRANSPORT_STOP = i18n._("Stop")
TRANSPORT_SKIP = i18n._("Skip")
TRANSPORT_LABELS: dict[str, str] = {"play": TRANSPORT_PLAY, "stop": TRANSPORT_STOP,
                                    "skip": TRANSPORT_SKIP}
#: THE GLYPH EACH VERB WEARS, by the names ``_icons.html`` carries. ``play`` is the set's own start
#: triangle (the drawing means "start it"), ``stop`` its filled square, and ``skip`` the triangle that
#: runs into a bar. There is no pause drawing: STOP is the pause.
TRANSPORT_GLYPHS: dict[str, str] = {"play": "play", "stop": "stop", "skip": "skip"}
#: THE MODE IS ONE TOGGLE, NOT A PICK: the button draws the mode IN FORCE and posts the other one, so
#: the set needs both drawings and the button needs the word it switches to.
MODE_GLYPHS: dict[str, str] = {"repeat": "repeat", "shuffle": "shuffle"}
MODE_OTHER: dict[str, str] = {"repeat": "shuffle", "shuffle": "repeat"}
MODE_TO_SHUFFLE = i18n._("Repeat is on — press to switch to shuffle")
MODE_TO_REPEAT = i18n._("Shuffle is on — press to switch to repeat")
#: why a transport button is drawn disabled — the Music tab's own three sentences
ALREADY_ON_AIR = i18n._("This radio is already on air.")
NOT_ON_AIR = i18n._("This radio is not on air.")
#: why Play and Skip are drawn disabled while a load is in flight — a second press would stack
LOADING_WHY = i18n._("A track is loading.")

#: the empty and read-only states
NO_PLAYLISTS = i18n._("No playlist exists yet.")
NO_RADIOS = i18n._("No radio is configured on this installation.")
#: the radios exist but no server is up, so no card can be drawn (its transport could not run)
NO_RADIO_SERVER = i18n._("No server is running, so no radio is shown.")
#: the caller's scope holds NO server at all, so the two sentences above would both be untrue
NO_SERVER_IN_SCOPE = i18n._("None of the servers your roles manage are in this view, so no radio is "
                            "shown.")
#: a card whose radio plays nothing right now
NO_CURRENT_TRACK = i18n._("Nothing is playing on this radio.")
NO_LIBRARY = i18n._("The music folder could not be read.")
NO_FILES = i18n._("No track in this folder.")
NO_MATCH = i18n._("No track matches the filter.")
NO_TRACKS = i18n._("This playlist holds no track yet.")
NAME_REQUIRED = i18n._("A playlist name is required.")
NOT_A_TRACK = i18n._("That is not a track of this playlist.")
#: the duplicate verb's refusal: the typed name is another playlist's, and the copy is not made
DUPLICATE_TAKEN = i18n._("Not duplicated: a playlist named '%(name)s' already exists. Choose another "
                         "name.")
#: the duplicate verb cannot compare names when the playlist listing itself cannot be read
PLAYLISTS_UNREAD = i18n._("The playlists could not be read.")
DRAFT_NOTE = i18n._("This playlist does not exist yet. Adding a track creates it.")
READ_ONLY = i18n._("The music actions are not available in this process, so this page is read-only.")
#: the upload control's own refusals — the missing file part, worded as the Mission tab's is
UPLOAD_REQUIRED = "A track file (.mp3 or .ogg) is required to upload."

#: the dialogs
NEW_HEADING = i18n._("New playlist")
RENAME_HEADING = i18n._("Rename the playlist")
DUPLICATE_HEADING = i18n._("Duplicate the playlist")
DELETE_HEADING = i18n._("Delete the playlist")
PLAY_HEADING = i18n._("Play the playlist on a radio")
NAME_LABEL = i18n._("Name")
RADIO_LABEL = i18n._("Radio")
NEW_GO = i18n._("Create")
DUPLICATE_GO = i18n._("Duplicate")
PLAY_GO = i18n._("Play")
NEW_LEAD = i18n._("The playlist exists once it holds its first track. Nothing is written by this "
                  "step.")
RENAME_LEAD = i18n._("The radios that play it move with the name.")
DUPLICATE_LEAD = i18n._("A copy of this playlist's tracks under a new name.")
DELETE_LEAD = i18n._("The playlist and the radio assignments that name it are removed.")
DELETE_WARNING = i18n._("Deleting a playlist cannot be undone from here.")
PLAY_LEAD = i18n._("The radio is reassigned and restarted on this playlist.")
#: the sentence the editor states where the "play on…" button would be, when nothing is running
PLAY_OFFLINE = i18n._("No server is running, so this playlist cannot be played.")


def declare() -> None:
    """Ensure the four capabilities are declared (import time, like every page module)."""
    _declare_capabilities()


declare()


# ── the urls ──────────────────────────────────────────────────────────────

def _quoted(name: str) -> str:
    """One path segment holding *name*."""
    return quote(str(name or ""), safe="")


def playlist_url(name: str) -> str:
    """The editor URL of one playlist name."""
    return f"{MUSIC_PATH}/{_quoted(name)}"


def add_url(name: str) -> str:
    """The add route of one playlist."""
    return f"{MUSIC_PATH}/{_quoted(name)}/add"


def remove_url(name: str) -> str:
    """The remove route of one playlist."""
    return f"{MUSIC_PATH}/{_quoted(name)}/remove"


def move_url(name: str) -> str:
    """The single-step move route of one playlist: one row one place, the whole order written."""
    return f"{MUSIC_PATH}/{_quoted(name)}/move"


def reorder_url(name: str) -> str:
    """The drag route of one playlist — the whole new order in one POST."""
    return f"{MUSIC_PATH}/{_quoted(name)}/reorder"


def upload_url(name: str) -> str:
    """The library card's upload route of one playlist — one multipart POST per file."""
    return f"{MUSIC_PATH}/{_quoted(name)}/upload"


def _upload_cap_message() -> str:
    """The console's over-cap refusal — the SAME sentence the ACTION answers with, so an operator
    cannot tell which surface refused (the two caps and the two sentences are pinned equal by a test).
    """
    return (f"The track is larger than the {UPLOAD_MAX_BYTES // (1024 * 1024)} MiB limit and was not "
            f"uploaded.")


def dialog_url(name: str, verb: str = "") -> str:
    """The dialog URL of one playlist: ``verb`` empty is the "play on…" dialog."""
    if not verb:
        return f"{MUSIC_PATH}/{_quoted(name)}/play"
    return f"{MUSIC_PATH}/{_quoted(name)}/dialog/{_quoted(verb)}"


def run_url(name: str, verb: str = "") -> str:
    """The write URL behind one playlist dialog."""
    if not verb:
        return f"{MUSIC_PATH}/{_quoted(name)}/play/run"
    return f"{MUSIC_PATH}/{_quoted(name)}/run/{_quoted(verb)}"


# ── small readers ─────────────────────────────────────────────────────────

def _text(value) -> str:
    """*value* as text — ``""`` for an absent one, never ``"None"``."""
    return "" if value is None else str(value)


def _clock(seconds) -> str:
    """A track length as ``m:ss``."""
    total = max(0, int(round(float(seconds or 0))))
    return f"{total // 60}:{total % 60:02d}"


def _span(seconds) -> str:
    """A playlist's total length as ``h:mm`` (or ``m:ss`` under an hour)."""
    total = max(0, int(round(float(seconds or 0))))
    hours, rest = divmod(total, 3600)
    if hours:
        return f"{hours}h {rest // 60:02d}m"
    return f"{rest // 60}m {rest % 60:02d}s"


def _stamp(value) -> str:
    """An epoch as a local ``YYYY-MM-DD HH:MM``, or ``""`` when it is not one."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return datetime.datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M")


def _servers(request: Request) -> list:
    """Every server row visible to this caller."""
    source = dashboard_page.request_source(request)
    return readmodels.safe(lambda: readmodels.servers(source), []) or []


def _server_state(view) -> str:
    """One server row's state, named the way a control's ``statuses`` names it."""
    return actions_page.state_of(getattr(getattr(view, "status", None), "raw", ""))


def _active(servers: list) -> list:
    """The servers whose DCS process is UP — the one predicate the cards and the play door read.

    ``actions_page.MISSION_LIST_OFFLINE_STATES`` names the states in which the process is down
    (``UNREGISTERED`` / ``SHUTDOWN`` / ``SHUTTING_DOWN``); a merely ``STOPPED`` server still has DCS
    up and its radios still take a transport command.
    """
    return [view for view in servers
            if _server_state(view) not in actions_page.MISSION_LIST_OFFLINE_STATES]


def _running_servers(request: Request) -> list:
    """The servers visible to this caller whose DCS process is UP.

    A radio that plays a playlist needs its server up, so a "play on…" door exists only while at
    least one of them is.
    """
    return _active(_servers(request))


def _nodes(request: Request) -> list[str]:
    """Every node name visible to this caller."""
    source = dashboard_page.request_source(request)
    entries = getattr(source, "nodes", None) or {}
    return [str(name) for name in entries]


def _allowed(request: Request, capability: str) -> bool:
    """Whether this caller holds *capability* (the same predicate the gate uses)."""
    return permissions.allows(capability, permissions.role_names_for(request),
                              manager=permissions.manages_console(request))


def _ready(qualname: str) -> bool:
    """Whether *qualname* is registered in this process."""
    return action_available(qualname)


async def _read(request: Request, qualname: str, **params):
    """Run one READ action with this request's own context."""
    return await read_action(qualname, actions_page.context_for(request), **params)


def _read_only_note() -> str:
    """The one sentence a page without the music actions states.

    Read from the page's OWN catalog and never from the plugin module: importing ``plugins.music.actions``
    here would REGISTER its actions (the decorators run on import), which would make an action registry
    that started empty non-empty while rendering — the page would then draw controls the process did not
    discover.
    """
    return READ_ONLY


# ── the write seam ────────────────────────────────────────────────────────

def _identity(request: Request):
    """The signed-in identity of this request, or ``None``."""
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return None
    return manager.authenticate(request)


def _write_context(request: Request) -> ActionContext:
    """The seam's context for a WRITE: the caller's identity (the audit actor) plus its scope."""
    identity = _identity(request)
    if identity is None:
        raise HTTPException(status_code=403, detail=actions_page.REFUSAL_NO_IDENTITY)
    base = actions_page.context_for(request)
    ctx = ActionContext.from_web(identity, base.node, base.bus)
    return replace(ctx, _scope=base._scope, _roles=base._roles)


def _notice(request: Request, message: str, *, ok: bool, audit: str = "") -> None:
    """Store the one-shot notice of a write, in the shape ``pages/actions.pop_notice`` validates."""
    from core.actions import AUDIT_NOT_RECORDED

    text = _text(message)
    if len(text) > actions_page.NOTICE_MAX_CHARS:
        text = text[:actions_page.NOTICE_MAX_CHARS - 1] + "…"
    try:
        request.session[actions_page.NOTICE_KEY] = {
            "message": text, "ok": bool(ok), "audit": audit,
            "audit_warning": audit == AUDIT_NOT_RECORDED, "action": "",
            "revert": "", "revert_omitted": "", "refusal": False,
        }
    except (AssertionError, KeyError):      # pragma: no cover - a request with no session
        log.debug("Music pages: no session to carry a notice.")


def _remember(request: Request, result) -> None:
    """Store one action's own answer as the next page's notice."""
    data = getattr(result, "data", None) or {}
    audit = _text(data.get("audit")) if isinstance(data, dict) else ""
    _notice(request, _text(getattr(result, "message", "")),
            ok=bool(getattr(result, "success", False)), audit=audit)


def _back(request: Request, message: str, *, ok: bool, name: str = "",
          to: str = "") -> RedirectResponse:
    """Leave a notice and return to *to*, or to the playlist's editor / the list."""
    _notice(request, message, ok=ok)
    return RedirectResponse(to or (playlist_url(name) if name else MUSIC_PATH), status_code=303)


def _chrome(request: Request, *, title: str, lead: str, crumb: tuple, notice=None,
            current: str = MUSIC_PATH) -> dict:
    """The shell chrome every console page passes to ``base.html``."""
    registrar = getattr(getattr(request.app, "state", None), "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    return {
        "title": title,
        "page_title": title,
        "lead": lead,
        "crumb": crumb,
        "notice": notice,
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=current,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "style": STYLE,
    }


def _crumb(*parts: str) -> tuple:
    """A breadcrumb under the console's group word: the area, then the page."""
    return ((dashboard_page.CRUMB_GROUP, True), (NAV_LABEL, True)) + tuple(
        (part, True) for part in parts)


# ── the overview ──────────────────────────────────────────────────────────

def _playlist_rows(playlists: list[dict], radios_by_pair: dict, *, request: Request,
                   may_write: bool, may_control: bool, running: bool = False) -> list[dict]:
    """The playlist table's rows, each carrying its own figures and the controls it offers.

    *running* is whether ANY visible server's DCS process is up: the Play glyph is a door into a
    radio on a server's process, so it is drawn only while one can actually take the playlist. With
    every server down the row carries no Play glyph at all rather than one that opens a dead dialog.
    """
    token = session.get_csrf_token(request)
    rows: list[dict] = []
    for entry in playlists:
        name = _text(entry.get("name"))
        used = [pair for pair in (entry.get("used_by") or []) if isinstance(pair, dict)]
        on_air = any(radios_by_pair.get((_text(pair.get("server")), _text(pair.get("radio"))), {})
                     .get("on_air") for pair in used)
        row = {
            "name": name,
            "url": playlist_url(name),
            # the row's ONE door names what it does: the name and this glyph both open the editor
            "edit_title": {"msgid": EDIT_TITLE, "params": {"name": name}},
            "tracks": int(entry.get("tracks") or 0),
            "length": _span(entry.get("seconds")),
            "missing": int(entry.get("missing") or 0),
            "missing_sentence": (i18n.translate(request, FACT_MISSING,
                                                count=int(entry.get("missing") or 0))
                                 if entry.get("missing") else ""),
            "used": [f"{_text(pair.get('server'))} › {_text(pair.get('radio'))}" for pair in used],
            "on_air": on_air,
            "controls": [],
        }
        row["controls"] = _playlist_controls(request, name, token=token, may_write=may_write,
                                             may_control=may_control, running=running)
        rows.append(row)
    return rows


def _playlist_controls(request: Request, name: str, *, token: str, may_write: bool,
                       may_control: bool, running: bool = False) -> list[dict]:
    """One playlist row's glyph controls — each a form whose answer is a dialog page.

    Rename is NOT one of them: it lives on the playlist's own page, so the row carries ONE door into
    that page (the name and its edit glyph) and no second way to do what the page does. Play is drawn
    only while a server is running (*running*): the door opens a radio on a server's process, and a
    playlist cannot be played without one, so the glyph is absent rather than dead.
    """
    controls: list[dict] = []
    if may_control and running and _ready("set_radio_playlist") and _ready("control_radio"):
        controls.append({"path": dialog_url(name), "icon": "startup",
                         "title": {"msgid": PLAY_TITLE, "params": {"name": name}},
                         "aria": {"msgid": PLAY_TITLE, "params": {"name": name}},
                         "danger": False})
    if may_write and _ready("delete_playlist"):
        controls.append({"path": dialog_url(name, "delete"), "icon": "delete",
                         "title": {"msgid": DELETE_TITLE, "params": {"name": name}},
                         "aria": {"msgid": DELETE_TITLE, "params": {"name": name}},
                         "danger": True})
    for control in controls:
        control.update({"csrf_field": session.CSRF_FIELD, "csrf_token": token,
                        "hidden": ((PLAYLIST_FIELD, name),)})
    return controls


def _player_controls(server: str, radio: str, *, token: str, mode: str, on_air: bool,
                     loading: bool = False, origin: str = MUSIC_PATH) -> dict:
    """The transport strip of one player: four GLYPH buttons, each its own form.

    The SAME write the server's Music tab posts — :data:`actions_page.MUSIC_CONTROL_PATH` with the
    server, the radio and the verb in the BODY (never in a URL), so a control cannot be pointed at
    another operation by editing a link. Each button is its own form, so no control nests.

    ``control_radio`` accepts play/stop/skip/mode and nothing else: STOP is the pause, so there is no
    pause verb to offer and no pause drawing to wear. A verb the radio's current state makes
    meaningless is drawn disabled with the sentence that says why, and the state tag beside it is what
    the live stream re-renders when the radio starts or stops.

    WHILE A LOAD IS IN FLIGHT (*loading*) Play and Skip are disabled with :data:`LOADING_WHY`, so a
    second press cannot stack a second launch; STOP stays enabled whatever ``on_air`` says, because it
    is the one control that cancels a load that never lands.

    THE MODE IS ONE TOGGLE: the button draws the mode IN FORCE (``repeat`` or ``shuffle``) and posts
    the OTHER one, so the word it will switch to rides the label — the pick it replaces needed a second
    button to save it, and this one writes on the press.

    ``origin`` is the page these forms were drawn on and it travels in the body
    (:data:`actions_page.ORIGIN_FIELD`), so the write's redirect lands back HERE. An empty origin is a
    surface that IS the write's default destination (the server's Music tab): the route then answers
    with the tab, and no field states what the route already knows.
    """
    hidden = ((actions_page.SERVER_CONFIG_FIELD, server),
              (actions_page.MUSIC_RADIO_FIELD, radio))
    if origin:
        hidden += ((actions_page.ORIGIN_FIELD, origin),)
    verbs: list[dict] = []
    for verb in ("play", "stop", "skip"):
        if verb == "stop":
            disabled, why = (not on_air and not loading, NOT_ON_AIR)
        elif loading:
            disabled, why = (True, LOADING_WHY)
        elif verb == "play":
            disabled, why = (on_air, ALREADY_ON_AIR)
        else:
            disabled, why = (not on_air, NOT_ON_AIR)
        verbs.append({"path": actions_page.MUSIC_CONTROL_PATH, "glyph": TRANSPORT_GLYPHS[verb],
                      "label": TRANSPORT_LABELS[verb], "disabled": disabled, "why": why,
                      "csrf_field": session.CSRF_FIELD, "csrf_token": token,
                      "hidden": hidden + ((actions_page.MUSIC_CONTROL_FIELD, verb),)})
    current = mode if mode in MODE_GLYPHS else "repeat"
    target = MODE_OTHER[current]
    return {
        "verbs": verbs,
        "mode": {"path": actions_page.MUSIC_CONTROL_PATH, "glyph": MODE_GLYPHS[current],
                 "label": MODE_TO_SHUFFLE if target == "shuffle" else MODE_TO_REPEAT,
                 "csrf_field": session.CSRF_FIELD, "csrf_token": token,
                 "hidden": hidden + ((actions_page.MUSIC_CONTROL_FIELD, "mode"),
                                     (actions_page.MUSIC_MODE_FIELD, target))},
    }


def radio_cards(radios: list[dict], *, token: str, may_control: bool,
                origin: str = MUSIC_PATH, may_open_playlist: bool = True) -> list[dict]:
    """One player per radio — the card :data:`PLAYER_TEMPLATE` draws, for BOTH music surfaces.

    The caller has already limited the READ to the radios it may draw: this page asks only ACTIVE
    servers (a radio lives in its server's process), and the server's Music tab asks the one server it
    renders. Every field the partial reads is built HERE, so the overview and the tab cannot disagree
    about a radio.

    *may_control* is the caller's own decision with the action-registered fact already folded in
    (``control_radio`` in this process): a strip is offered only to a caller holding ``music.control``
    whose installation can perform the write — the console's omission rule, never a disabled button.
    *origin* is the page the strip posts back (:func:`_player_controls`), and *may_open_playlist* is
    whether the caller may open the playlist EDITOR — a link to a page it cannot open is not drawn.
    """
    cards: list[dict] = []
    for entry in radios:
        server = _text(entry.get("server"))
        name = _text(entry.get("name"))
        on_air = bool(entry.get("on_air"))
        # the load stamp travels with the same read that carries on_air, so a card cannot claim one
        # state while its own radio is in the middle of the other
        loading = bool(entry.get("loading"))
        track = entry.get("current") or {}
        playlist = _text(entry.get("playlist"))
        cards.append({
            "server": server,
            "name": name,
            "display_name": _text(entry.get("display_name")),
            "on_air": on_air,
            "loading": loading,
            # the state tag's word: loading outranks both settled states
            "state_word": LOADING_WORD if loading else (ON_AIR_WORD if on_air else IDLE_WORD),
            "title": _text(track.get("title")),
            "artist": _text(track.get("artist")),
            "album": _text(track.get("album")),
            # the track's LENGTH, never a position: the radio read carries no progress
            "duration": _track_length(track.get("duration")),
            "no_track": NO_CURRENT_TRACK,
            "playlist": playlist,
            "playlist_href": (playlist_url(playlist)
                              if playlist and may_open_playlist else ""),
            "frequency": _text(entry.get("frequency")),
            "modulation": _text(entry.get("modulation")),
            "coalition": _text(entry.get("coalition")),
            "volume": _text(entry.get("volume")),
            "mode": _text(entry.get("mode")),
            "tracks": int(entry.get("tracks") or 0),
            "controls": (_player_controls(server, name, token=token,
                                          mode=_text(entry.get("mode")), on_air=on_air,
                                          loading=loading,
                                          origin=origin) if may_control else None),
        })
    return cards


def _labels() -> dict:
    """Every word the overview's cards draw, as msgids (the template translates them).

    One map for the two live fragments AND the library card: the page and a streamed frame read the
    same names, so a word cannot come from two places. The editor's own words live in its own map.
    """
    return {
        "radios": RADIOS_HEADING, "playlists": PLAYLISTS_HEADING, "library": LIBRARY_HEADING,
        "new": NEW_LABEL, "none": NO_PLAYLISTS, "no_library": NO_LIBRARY,
    }


async def _radios(request: Request, views: list) -> tuple[list[dict], dict]:
    """Every configured radio of the servers in *views*, plus the ``(server, radio)`` index of them.

    Only an ACTIVE server is asked: a radio of a server whose DCS process is down gets no card, so
    reading it would be a read the page could not draw — and such a server's radios are all idle
    anyway, so the playlist table's on-air column loses nothing.
    """
    radios: list[dict] = []
    pairs: dict = {}
    if not _ready("get_music_radios"):
        return radios, pairs
    for view in views:
        name = _text(getattr(view, "name", ""))
        if not name:
            continue
        result = await _read(request, "get_music_radios", server_name=name)
        data = getattr(result, "data", None) or {}
        for entry in (data.get("radios") or []) if isinstance(data, dict) else []:
            if not isinstance(entry, dict):
                continue
            entry = dict(entry, server=name)
            radios.append(entry)
            pairs[(name, _text(entry.get("name")))] = entry
    return radios, pairs


async def _libraries(request: Request) -> list[dict]:
    """One summary row per visible node: its track count, its scan stamp, or its refusal."""
    libraries: list[dict] = []
    if not _ready("read_music_library"):
        return libraries
    for node_name in _nodes(request):
        result = await _read(request, "read_music_library", node_name=node_name)
        data = getattr(result, "data", None) or {}
        success = bool(getattr(result, "success", False))
        libraries.append({
            "node": node_name,
            "files": int(data.get("files") or 0) if success else None,
            "scanned": _stamp(data.get("scanned")) if success else "",
            "failure": "" if success else _text(getattr(result, "message", "")),
        })
    return libraries


async def sections_context(request: Request) -> dict:
    """The two live fragments' variables — the ONE builder the page and the stream both render.

    Rebuilt from scratch on every call, so a tick re-reads the playlist list and one radio read per
    visible server, and the caller's own ``music.write`` / ``music.control`` capabilities re-decide
    every control. A streamed frame therefore cannot differ from the card the page drew, and a
    caller whose rights are revoked mid-stream loses the controls at the next tick.

    The playlist read's own refusal rides in this context (``message``) because it belongs INSIDE the
    playlists card: a failed read that only rendered its sentence on the initial page would make a
    streamed empty table indistinguishable from a playlist list that is genuinely empty.
    """
    missing = "" if _ready("list_music_playlists") else _read_only_note()
    playlists: list[dict] = []
    message = ""
    if not missing:
        result = await _read(request, "list_music_playlists")
        if getattr(result, "success", False):
            data = getattr(result, "data", None) or {}
            playlists = [row for row in (data.get("playlists") or []) if isinstance(row, dict)]
        else:
            message = _text(getattr(result, "message", ""))
    servers = _servers(request)
    views = _active(servers)
    radios, radios_by_pair = await _radios(request, views)
    token = session.get_csrf_token(request)
    may_write = _allowed(request, CAPABILITY_WRITE)
    may_control = _allowed(request, CAPABILITY_CONTROL)
    cards = radio_cards(radios, token=token, may_control=may_control and _ready("control_radio"),
                        origin=MUSIC_PATH)
    # WHY THE SECTION IS EMPTY, decided here so the template holds one branch: a radio with nothing
    # configured on a server that is up is a different fact from a fleet whose processes are down,
    # and a viewer whose own scope holds no server must not be told either of them.
    if cards:
        radios_empty = ""
    elif getattr(dashboard_page.request_source(request), "scoped_empty", False):
        radios_empty = NO_SERVER_IN_SCOPE
    elif views or not servers:
        radios_empty = NO_RADIOS
    else:
        radios_empty = NO_RADIO_SERVER
    return {
        "missing": missing,
        "message": message,
        "radios": cards,
        "radios_empty": radios_empty,
        # the door is drawn only when the write behind it is registered: a control that could
        # only open a dialog whose form the actions cannot serve is not drawn at all
        "new_control": ({"path": NEW_DIALOG_PATH, "label": NEW_LABEL,
                         "csrf_field": session.CSRF_FIELD, "csrf_token": token}
                        if may_write and _ready("add_playlist_track") else None),
        "playlists": _playlist_rows(playlists, radios_by_pair, request=request,
                                    may_write=may_write, may_control=may_control,
                                    running=bool(views)),
        "may_write": may_write,
        "labels": _labels(),
    }


def _live_context(request: Request) -> dict | None:
    """The ``live`` context ``base.html`` reads to load the console's stream script, or ``None``.

    ``None`` when live updates are off: the page is then complete on load and loads no script.
    """
    if not live_page.dashboard_config(request.app).enabled:
        return None
    return {"stream": MUSIC_STREAM_PATH, "fragments": MUSIC_FRAGMENTS_PATH}


async def overview_context(request: Request) -> dict:
    """Everything ``music.html`` reads: the two live sections, the library per node and the shell."""
    context = _chrome(request, title=OVERVIEW_TITLE, lead=OVERVIEW_LEAD,
                      crumb=_crumb(), notice=actions_page.pop_notice(request))
    context.update(await sections_context(request))
    context.update({
        # the two wrappers' ids ARE the stream's target names: one constant, so the page cannot
        # listen on an id the stream never sends
        "radios_target": RADIOS_TARGET,
        "playlists_target": PLAYLISTS_TARGET,
        "libraries": await _libraries(request),
        "live": _live_context(request),
    })
    return context


# ── the live stream ───────────────────────────────────────────────────────

def music_live_config(app) -> live_page.LiveConfig:
    """The music page's live settings: the console's own live block at THIS page's cadence.

    The switch and the client cap are the dashboard's (one place decides whether the console streams
    at all); only the interval is the music page's, because each tick re-reads every visible
    server's radios.
    """
    return replace(live_page.dashboard_config(app), refresh_seconds=MUSIC_REFRESH_SECONDS)


def render_for(request: Request) -> Callable[[], Awaitable[dict[str, str]]]:
    """The fragment renderer of ONE connection: the two partials, from the ONE context builder."""
    async def render() -> dict[str, str]:
        context = await sections_context(request)
        return {RADIOS_TARGET: i18n.render_fragment(request, RADIOS_TEMPLATE, **context),
                PLAYLISTS_TARGET: i18n.render_fragment(request, PLAYLISTS_TEMPLATE, **context)}

    return render


async def current_fragments(request: Request) -> dict[str, str]:
    """One render of the two fragments — what the polling fallback reads."""
    return await render_for(request)()


def refuse_without_view(request: Request) -> None:
    """Refuse a caller without ``music.view`` BEFORE any generator exists.

    A refusal raised inside a ``StreamingResponse`` body arrives AFTER the headers, i.e. as a broken
    stream instead of the 403 the caller can read — and an EventSource would retry forever. The
    wording is the access gate's own, so a crafted call and a refused page say one sentence.
    """
    if not _allowed(request, CAPABILITY_VIEW):
        raise HTTPException(
            status_code=403,
            detail=f"Not authorized (needs one of: {permissions.requirement_text(CAPABILITY_VIEW)}).")


def event_stream(request: Request):
    """The SSE generator of one connection: the two fragments, polled at the music cadence.

    The frames are :mod:`pages.live`'s own (:func:`~pages.live.stream_frames`): a snapshot on
    connect, then only the targets whose rendered HTML changed, plus the idle heartbeat. Nothing is
    sampled here that the page would not sample — the same builder, the same partials.
    """
    refuse_without_view(request)
    return live_page.stream_frames(render=render_for(request),
                                   config=music_live_config(request.app),
                                   registry=live_page.get_registry(
                                       request.app, live_page.dashboard_config(request.app)))


# ── the editor ────────────────────────────────────────────────────────────

def _folder_rows(folders: list[dict], *, selected: str, name: str) -> list[dict]:
    """The folder rail: one link per directory, indented by its depth, carrying its own count."""
    rows: list[dict] = []
    for folder in folders:
        path = _text(folder.get("path"))
        label = _text(folder.get("name")) or "/"
        rows.append({
            "path": path,
            "label": label,
            "depth": int(folder.get("depth") or 0),
            "count": int(folder.get("count") or 0),
            "active": path == selected,
            "url": f"{playlist_url(name)}?{FOLDER_FIELD}=" + quote(path, safe=""),
            "style": f"padding-left: {int(folder.get('depth') or 0) * 14}px",
        })
    return rows


def _file_rows(files: list[dict], *, query: str) -> list[dict]:
    """The selected folder's tracks, filtered by *query* across their tags and their file name."""
    wanted = query.casefold()
    rows: list[dict] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        haystack = " ".join(_text(entry.get(key)) for key in
                            ("title", "artist", "album", "name", "path")).casefold()
        if wanted and wanted not in haystack:
            continue
        rows.append({
            "file": _text(entry.get("path")),
            "title": _text(entry.get("title")) or _text(entry.get("name")),
            "artist": _text(entry.get("artist")),
            "album": _text(entry.get("album")),
            "length": _track_length(entry.get("duration")),
        })
    return rows


def _track_length(seconds) -> str:
    """A row's length as ``m:ss``, or ``""`` when it is unknown.

    An unknown duration is not zero seconds: a reader must not be told a track is 0:00 long, and the
    length column renders an em dash for the empty string this returns.
    """
    try:
        whole = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ""
    return _clock(whole) if whole > 0 else ""


def _track_title(track: dict) -> str:
    """A row's title: its tag, or the file's own name without its suffix.

    An empty ``title`` is a file that carries no title tag (the normal case for an upload straight
    off a disk), and the row still needs a name: the last path segment of its ``file``, minus the
    suffix, is what the file is called on the node.
    """
    title = _text(track.get("title"))
    if title:
        return title
    base = posixpath.basename(_text(track.get("file")))
    stem, suffix = posixpath.splitext(base)
    return stem if suffix else base


def _track_rows(tracks: list[dict], *, name: str, request: Request, token: str,
                may_write: bool) -> list[dict]:
    """The destination pane's rows in playback order, each with the controls it offers.

    The row's ONLY order control is its grip (``static/reorder.js``): the remove glyph is the one
    form a row carries.
    """
    rows: list[dict] = []
    for index, track in enumerate(tracks):
        song_id = int(track.get("song_id") or 0)
        present = bool(track.get("present"))
        row = {
            "song_id": song_id,
            "number": index + 1,
            "title": _track_title(track),
            "artist": _text(track.get("artist")),
            "album": _text(track.get("album")),
            "length": _track_length(track.get("duration")) if present else "",
            "present": present,
            "controls": [],
        }
        if may_write:
            row["controls"].append({
                "path": remove_url(name), "icon": "delete",
                "title": {"msgid": REMOVE_LABEL},
                "aria": {"msgid": REMOVE_LABEL},
                "danger": True, "disabled": False,
                "hidden": ((SONG_FIELD, str(song_id)),),
                "csrf_field": session.CSRF_FIELD, "csrf_token": token,
            })
        rows.append(row)
    return rows


async def _playlist_state(request: Request, name: str, *,
                          refresh: bool = False) -> tuple[dict, bool, str, str]:
    """``(playlist read data, draft, node name, failure)`` for one name."""
    if not _ready("get_music_playlist"):
        return {}, False, "", _read_only_note()
    result = await _read(request, "get_music_playlist", playlist=name, refresh=refresh)
    if getattr(result, "success", False):
        data = getattr(result, "data", None) or {}
        return data, False, _text(data.get("node_name")), ""
    failure = _text(getattr(result, "message", ""))
    listing = await _read(request, "list_music_playlists") if _ready("list_music_playlists") else None
    if listing is not None and getattr(listing, "success", False):
        known = {_text(row.get("name")).casefold() for row in
                 ((getattr(listing, "data", None) or {}).get("playlists") or [])
                 if isinstance(row, dict)}
        if name.casefold() not in known:
            return {}, True, "", ""
    return {}, False, "", failure


async def editor_context(request: Request, playlist: str) -> dict:
    """Everything the editor reads: the destination playlist and the source library beside it."""
    name = _text(playlist)
    refresh = bool(_text(request.query_params.get(REFRESH_FIELD)))
    data, draft, node_name, failure = await _playlist_state(request, name, refresh=refresh)
    tracks = [track for track in (data.get("tracks") or []) if isinstance(track, dict)]
    token = session.get_csrf_token(request)
    may_write = _allowed(request, CAPABILITY_WRITE)
    may_control = _allowed(request, CAPABILITY_CONTROL)
    may_upload = _allowed(request, CAPABILITY_UPLOAD)
    missing = [int(track.get("song_id") or 0) for track in tracks if not track.get("present")]

    folders: list[dict] = []
    library_message = ""
    library_node = node_name
    if _ready("read_music_library"):
        result = (await _read(request, "read_music_library", node_name=node_name, refresh=refresh)
                  if node_name else
                  await _read(request, "read_music_library", refresh=refresh))
        library_data = getattr(result, "data", None) or {}
        if getattr(result, "success", False):
            folders = [folder for folder in (library_data.get("folders") or [])
                       if isinstance(folder, dict)]
            library_node = _text(library_data.get("node")) or node_name
        else:
            library_message = _text(getattr(result, "message", ""))
    else:
        library_message = _read_only_note()

    wanted = _text(request.query_params.get(FOLDER_FIELD))
    selected = next((folder for folder in folders if _text(folder.get("path")) == wanted), None)
    if selected is None and folders:
        selected = next((folder for folder in folders if not _text(folder.get("path"))), folders[0])
    query = _text(request.query_params.get(QUERY_FIELD))
    files = _file_rows((selected or {}).get("files") or [], query=query)

    # THE PLAY DOOR NEEDS A RUNNING SERVER: a playlist is handed to a radio, and a radio belongs to a
    # server's process. With every visible server down the page states that where the button was.
    may_play = may_control and _ready("set_radio_playlist") and _ready("control_radio")
    running = _running_servers(request) if may_play else []

    context = _chrome(request, title=EDITOR_TITLE, lead=_text(data.get("name")) or name,
                      crumb=_crumb(name), notice=actions_page.pop_notice(request),
                      current=MUSIC_PATH)
    context.update({
        "playlist": _text(data.get("name")) or name,
        "node": library_node,
        "draft": draft,
        "failure": failure,
        "missing": missing,
        "track_count": len(tracks),
        "length": _span(data.get("seconds")),
        "tracks": _track_rows(tracks, name=name, request=request, token=token,
                              may_write=may_write),
        "folders": _folder_rows(folders, selected=_text((selected or {}).get("path")),
                                name=name),
        "files": files,
        "selected_folder": _text((selected or {}).get("path")),
        "selected_folder_count": int((selected or {}).get("count") or 0),
        "query": query,
        "library_message": library_message,
        "may_write": may_write,
        "may_control": may_play,
        "can_add": may_write and _ready("add_playlist_track"),
        "can_remove": may_write and _ready("remove_playlist_track"),
        "can_order": may_write and _ready("reorder_playlist"),
        "can_rename": may_write and _ready("rename_playlist"),
        "can_delete": may_write and _ready("delete_playlist"),
        "can_duplicate": (may_write and _ready("get_music_playlist")
                          and _ready("list_music_playlists")
                          and _ready("add_playlist_track")),
        # the library card's upload: its OWN capability, and only when the write behind it is here
        "can_upload": may_upload and _ready("upload_music_track"),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": token,
        "add_path": add_url(name),
        "remove_path": remove_url(name),
        "move_path": move_url(name),
        "reorder_path": reorder_url(name),
        "upload_path": upload_url(name),
        "play_path": dialog_url(name) if may_play and running else "",
        "play_offline": PLAY_OFFLINE if may_play and not running else "",
        "rename_path": dialog_url(name, "rename"),
        "delete_path": dialog_url(name, "delete"),
        "duplicate_path": dialog_url(name, "duplicate"),
        "reorder_field": ORDER_FIELD,
        "reorder_script": REORDER_SCRIPT,
        "upload_script": UPLOAD_SCRIPT,
        "page_path": playlist_url(name),
        "folder_field": FOLDER_FIELD,
        "node_field": NODE_FIELD,
        "query_field": QUERY_FIELD,
        "file_field": FILE_FIELD,
        "song_field": SONG_FIELD,
        "all_field": ALL_FIELD,
        "labels": {
            "browser": BROWSER_HEADING, "destination": DESTINATION_HEADING,
            "tracks": i18n.translate(request, FACT_TRACKS, count=len(tracks)),
            "total": i18n.translate(request, FACT_TOTAL, length=_span(data.get("seconds"))),
            "missing": i18n.translate(request, FACT_MISSING, count=len(missing)),
            "folder": i18n.translate(request, FACT_FOLDER,
                                     folder=_text((selected or {}).get("name")) or "/",
                                     count=int((selected or {}).get("count") or 0)),
            "selected": i18n.translate(request, FACT_SELECTED, count=0),
            "selected_template": FACT_SELECTED,
            "add": ADD_LABEL, "add_all": i18n.translate(request, ADD_ALL_LABEL,
                                                        count=int((selected or {}).get("count") or 0)),
            "remove_missing": i18n.translate(request, REMOVE_MISSING_LABEL, count=len(missing)),
            "rescan": RESCAN_LABEL, "rename": RENAME_LABEL, "duplicate": DUPLICATE_LABEL,
            "delete": DELETE_LABEL, "play": PLAY_ACTION_LABEL, "drag": DRAG_HINT,
            "upload": UPLOAD_LABEL, "upload_hint": UPLOAD_HINT,
            "no_files": NO_FILES, "no_match": NO_MATCH, "no_tracks": NO_TRACKS,
            "draft": DRAFT_NOTE, "open": OPEN_LABEL,
        },
    })
    return context


# ── the dialogs ───────────────────────────────────────────────────────────

#: the title each playlist control names its target in — a SENTENCE RECORD (brace placeholders),
#: rendered by the console's ``say`` global like every other row control
EDIT_TITLE = i18n._("Edit the playlist '{name}'")
DELETE_TITLE = i18n._("Delete the playlist '{name}'")
PLAY_TITLE = i18n._("Play the playlist '{name}' on a radio")
#: the field alias the row controls use for the playlist's own name
PLAYLIST_FIELD = "playlist"


async def _radio_choices(request: Request, *, selected: str = "") -> list[dict]:
    """One option per (server, radio), carrying whether it is on air and what it plays.

    Only a RUNNING server contributes options: a radio lives in its server's process, so handing a
    playlist to a stopped one would be a write that cannot take effect.
    """
    choices: list[dict] = []
    if not _ready("get_music_radios"):
        return choices
    suffix = f" · {i18n.translate(request, ON_AIR_WORD)}"
    for view in _running_servers(request):
        server_name = _text(getattr(view, "name", ""))
        if not server_name:
            continue
        result = await _read(request, "get_music_radios", server_name=server_name)
        data = getattr(result, "data", None) or {}
        for entry in (data.get("radios") or []) if isinstance(data, dict) else []:
            if not isinstance(entry, dict):
                continue
            radio_name = _text(entry.get("name"))
            value = f"{server_name}{PAIR_SEPARATOR}{radio_name}"
            choices.append({
                "value": value,
                "label": f"{server_name} › {radio_name}"
                         + (suffix if entry.get("on_air") else ""),
                "on_air": bool(entry.get("on_air")),
                "playlist": _text(entry.get("playlist")),
                "checked": value == selected,
            })
    return choices


async def dialog_context(request: Request, *, verb: str, playlist: str) -> dict:
    """Everything ``music_dialog.html`` reads for one verb (``""`` is "play on…")."""
    name = _text(playlist)
    is_new = verb == "new"
    choices = await _radio_choices(request) if verb == "" else []
    if verb == "":
        heading, lead, go, danger, warning = (PLAY_HEADING, PLAY_LEAD, PLAY_GO, False, "")
    elif verb == "rename":
        heading, lead, go, danger, warning = (RENAME_HEADING, RENAME_LEAD, RENAME_LABEL,
                                              False, "")
    elif verb == "duplicate":
        heading = DUPLICATE_HEADING
        lead = DUPLICATE_LEAD
        go = DUPLICATE_GO
        danger, warning = False, ""
    elif verb == "delete":
        heading, lead, go = DELETE_HEADING, DELETE_LEAD, DELETE_LABEL
        danger, warning = True, DELETE_WARNING
    else:
        heading, lead, go, danger, warning = (NEW_HEADING, NEW_LEAD, NEW_GO, False, "")
    context = _chrome(request, title=heading, lead=lead, crumb=_crumb(name or heading),
                      notice=None, current=MUSIC_PATH)
    context.update({
        "heading": heading,
        "lead": lead,
        "page_title": heading,
        "verb": verb,
        "playlist": name,
        "is_new": is_new,
        "field": DIALOG_FIELD,
        "name_value": "" if is_new else (f"{name} (copy)" if verb == "duplicate" else name),
        "needs_name": verb in ("new", "rename", "duplicate"),
        "choices": choices,
        "radio_field": RADIO_FIELD,
        "radio_label": RADIO_LABEL,
        "warning": warning,
        "danger": danger,
        "go": go,
        "path": NEW_RUN_PATH if is_new else run_url(name, verb),
        "back": MUSIC_PATH if is_new else playlist_url(name),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "no_radios": NO_RADIOS,
        "labels": {"name": NAME_LABEL, "cancel": i18n._("Cancel")},
    })
    return context


# ── the write routes ──────────────────────────────────────────────────────

async def _run_add(request: Request, name: str) -> RedirectResponse:
    """Add the posted tracks — or the whole selected folder — to one playlist."""
    ctx = _write_context(request)
    form = await request.form()
    folder = _text(form.get(FOLDER_FIELD))
    if _text(form.get(ALL_FIELD)):
        node_name = _text(form.get(NODE_FIELD))
        result = (await _read(request, "read_music_library", node_name=node_name)
                  if node_name else await _read(request, "read_music_library")) \
            if _ready("read_music_library") else None
        data = getattr(result, "data", None) or {}
        folders = [entry for entry in (data.get("folders") or []) if isinstance(entry, dict)] \
            if getattr(result, "success", False) else []
        chosen = next((entry for entry in folders if _text(entry.get("path")) == folder), None)
        paths = [_text(file.get("path")) for file in (chosen or {}).get("files") or []
                 if isinstance(file, dict)]
    else:
        paths = [_text(value) for value in form.getlist(FILE_FIELD) if _text(value)]
    if not paths:
        return _back(request, _read_only_note() if not _ready("add_playlist_track")
                     else i18n.translate(request, NO_FILES), ok=False, name=name)
    result = await call_action("add_playlist_track", ctx, playlist=name, song_files=paths)
    _remember(request, result)
    label = _text((getattr(result, "data", None) or {}).get("playlist")) or name
    return RedirectResponse(playlist_url(label), status_code=303)


async def _run_remove(request: Request, name: str) -> RedirectResponse:
    """Remove the posted rows (one track, or every row a toolbar control flagged)."""
    ctx = _write_context(request)
    form = await request.form()
    song_ids = [_text(value) for value in form.getlist(SONG_FIELD) if _text(value)]
    if not song_ids:
        return _back(request, NOT_A_TRACK, ok=False, name=name)
    result = await call_action("remove_playlist_track", ctx, playlist=name, song_ids=song_ids)
    _remember(request, result)
    return _back(request, _text(getattr(result, "message", "")),
                 ok=bool(getattr(result, "success", False)), name=name)


def _moved_order(tracks: list[dict], song_id: int, direction: str) -> list[int] | None:
    """The whole new order after moving one row, or ``None`` when it cannot move that way."""
    order = [int(track.get("song_id") or 0) for track in tracks]
    if song_id not in order:
        return None
    index = order.index(song_id)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(order):
        return None
    order[index], order[target] = order[target], order[index]
    return order


async def _run_move(request: Request, name: str) -> RedirectResponse:
    """Move one row one place and write the whole new order.

    A named row is moved rather than a posted order, so a caller that only knows one row can still
    write a correct order: the current list is read, the row moved in it, and the RESULT handed to
    the reorder write like any other order. The page's grip posts the whole order to
    :data:`REORDER_PATH` directly; this route is the single-step shape of the same write.
    """
    ctx = _write_context(request)
    form = await request.form()
    try:
        song_id = int(_text(form.get(SONG_FIELD)))
    except (TypeError, ValueError):
        return _back(request, NOT_A_TRACK, ok=False, name=name)
    direction = _text(form.get(DIRECTION_FIELD))
    if direction not in DIRECTIONS:
        return _back(request, NOT_A_TRACK, ok=False, name=name)
    found = await _read(request, "get_music_playlist", playlist=name)
    tracks = [track for track in ((getattr(found, "data", None) or {}).get("tracks") or [])
              if isinstance(track, dict)] if getattr(found, "success", False) else []
    order = _moved_order(tracks, song_id, direction)
    if order is None:
        return _back(request, _text(getattr(found, "message", "")) or NOT_A_TRACK,
                     ok=False, name=name)
    result = await call_action("reorder_playlist", ctx, playlist=name, order=order)
    _remember(request, result)
    return _back(request, _text(getattr(result, "message", "")),
                 ok=bool(getattr(result, "success", False)), name=name)


def _order_of(form) -> list[str]:
    """The posted whole order, as the action reads it (repeated fields or one list)."""
    return [_text(value) for value in form.getlist(ORDER_FIELD) if _text(value)]


async def _run_reorder(request: Request, name: str) -> JSONResponse:
    """Write one drop's whole new order and answer the script that posted it.

    A refusal is the ACTION's own sentence (a non-permutation, a vanished playlist) and is answered
    with ``ok`` false so the page can reload rather than claim the order was saved.
    """
    ctx = _write_context(request)
    form = await request.form()
    order = _order_of(form)
    result = await call_action("reorder_playlist", ctx, playlist=name, order=order)
    data = getattr(result, "data", None) or {}
    _notice(request, _text(getattr(result, "message", "")),
            ok=bool(getattr(result, "success", False)),
            audit=_text(data.get("audit") if isinstance(data, dict) else ""))
    # ``song_ids``: the rows' ids AS THE WRITE LEFT THEM. The write renumbers ``song_id`` (the order IS
    # the id), so the ids the page drew with are stale the moment a drop succeeds. The script re-stamps
    # its rows from this list, which is why the next drop names ids that exist instead of being refused
    # as a non-permutation (the "cannot drag a second time" report).
    return JSONResponse({"ok": bool(getattr(result, "success", False)),
                         "message": _text(getattr(result, "message", "")),
                         "song_ids": list(data.get("song_ids") or [])},
                        status_code=200, headers={"Cache-Control": "no-store"})


def _wants_json(request: Request) -> bool:
    """Whether the caller asked for the JSON answer (the library card's drop script) rather than a page."""
    return "application/json" in _text(request.headers.get("accept")).lower()


def _upload_answer(request: Request, name: str, folder: str, message: str,
                   ok: bool, *, audit: str = "") -> RedirectResponse | JSONResponse:
    """Answer one upload: JSON to the script that posted it, a notice and the folder's page otherwise.

    The JSON answer is NOT also stored as a notice: the script words the sentence it was given, and a
    stored copy would surface again as a stale line on the page it reloads. *audit* is the fact the
    console's own refusal has a trail (``"recorded"`` / ``"not-recorded"``), rendered with the notice.
    """
    if _wants_json(request):
        return JSONResponse({"ok": bool(ok), "message": message}, status_code=200,
                            headers={"Cache-Control": "no-store"})
    _notice(request, message, ok=ok, audit=audit)
    return RedirectResponse(f"{playlist_url(name)}?{FOLDER_FIELD}={quote(folder, safe='')}",
                            status_code=303)


async def _audit_refusal(ctx: ActionContext, message: str) -> str:
    """Write the trail of a refusal the ROUTE made, and answer the audit outcome.

    The action never runs for a request the console refuses before it (a body over the cap, a missing
    file part), so the route is the only place the attempt can be recorded — one entry per attempt,
    the same rule the action follows. Answers ``data["audit"]``, or ``""`` when there is none.
    """
    result = await audit_action(ctx, ActionResult(success=False, message=message))
    return _text((getattr(result, "data", None) or {}).get("audit"))


async def _run_upload(request: Request, name: str) -> RedirectResponse | JSONResponse:
    """Upload ONE track from a multipart POST into the folder the operator is looking at.

    THE CAP IS REFUSED AS THE SIZE CROSSES IT, never after the whole file has been buffered — the
    same three steps the Missions tab's upload takes, because a track is just as capable of filling
    the console's memory:

    1. the request's DECLARED ``Content-Length`` is checked BEFORE the body is parsed;
    2. the parsed part's own size (``UploadFile.size``) is checked before the file is read at all;
    3. the bytes are then read in chunks with a running total, so a body that lied about its length
       (or arrived chunked, with none) is refused the moment the total crosses the cap.

    Every refusal is one sentence — the ACTION's own cap sentence — so an operator cannot tell which
    surface refused. A refusal made HERE never reaches the action, so it writes its own audit entry
    (:func:`_audit_refusal`), exactly as the mission route does. The name and the folder travel to the
    action unchanged: the action validates the name it is handed and the service validates the folder
    against the music directory it holds.
    """
    ctx = _write_context(request)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() \
            and int(declared) > UPLOAD_MAX_BYTES + UPLOAD_ENVELOPE_BYTES:
        message = _upload_cap_message()
        await _audit_refusal(ctx, message)
        raise HTTPException(status_code=413, detail=message)
    form = await request.form()
    folder = _text(form.get(FOLDER_FIELD))
    node_name = _text(form.get(NODE_FIELD))
    upload = form.get(FILE_FIELD)
    is_file = upload is not None and not isinstance(upload, str)
    if not is_file:
        return _upload_answer(request, name, folder, UPLOAD_REQUIRED, False,
                              audit=await _audit_refusal(ctx, UPLOAD_REQUIRED))
    declared_size = getattr(upload, "size", None)
    if isinstance(declared_size, int) and declared_size > UPLOAD_MAX_BYTES:
        message = _upload_cap_message()
        return _upload_answer(request, name, folder, message, False,
                              audit=await _audit_refusal(ctx, message))
    data = await actions_page._read_upload_capped(upload, UPLOAD_MAX_BYTES)
    if data is None:
        message = _upload_cap_message()
        return _upload_answer(request, name, folder, message, False,
                              audit=await _audit_refusal(ctx, message))
    result = await call_action("upload_music_track", ctx, folder=folder,
                               name=_text(getattr(upload, "filename", "")),
                               node_name=node_name, source=data)
    message = _text(getattr(result, "message", ""))
    ok = bool(getattr(result, "success", False))
    # a SUCCESS lands on the folder the file went into (the service states it, folded); a refusal
    # lands where the operator already was
    stored = _text((getattr(result, "data", None) or {}).get("folder"))
    return _upload_answer(request, name, (stored or folder) if ok else folder, message, ok)


def _split_choice(value: str) -> tuple[str, str]:
    """``(server, radio)`` from one "play on…" option value."""
    server, _, radio = _text(value).partition(PAIR_SEPARATOR)
    return server.strip(), radio.strip()


async def _run_play(request: Request, name: str) -> RedirectResponse:
    """Assign the playlist to the chosen radio and start it: two writes, one sentence."""
    ctx = _write_context(request)
    form = await request.form()
    server_name, radio_name = _split_choice(_text(form.get(RADIO_FIELD)))
    if not server_name or not radio_name:
        return _back(request, i18n.translate(request, NO_RADIOS), ok=False, name=name)
    assigned = await call_action("set_radio_playlist", ctx, server_name=server_name,
                                 radio_name=radio_name, playlist=name)
    if not getattr(assigned, "success", False):
        _remember(request, assigned)
        return _back(request, _text(getattr(assigned, "message", "")), ok=False, name=name)
    started = await call_action("control_radio", ctx, server_name=server_name,
                                radio_name=radio_name, control="play")
    _remember(request, started)
    message = _text(getattr(assigned, "message", ""))
    if not getattr(started, "success", False):
        message = f"{message} {_text(getattr(started, 'message', ''))}".strip()
    return _back(request, message, ok=bool(getattr(started, "success", False)), name=name)


async def _run_new(request: Request) -> RedirectResponse:
    """Land on the new name's draft editor — nothing is written by this step."""
    ctx = _write_context(request)
    form = await request.form()
    name = _text(form.get(DIALOG_FIELD))
    if not name:
        return _back(request, NAME_REQUIRED, ok=False)
    listing = await _read(request, "list_music_playlists") if _ready("list_music_playlists") else None
    known = {_text(row.get("name")): _text(row.get("name")) for row in
             ((getattr(listing, "data", None) or {}).get("playlists") or [])
             if isinstance(row, dict)} if listing is not None else {}
    for stored in known:
        if stored.casefold() == name.casefold():
            return RedirectResponse(playlist_url(known[stored]), status_code=303)
    return RedirectResponse(playlist_url(name), status_code=303)


async def _run_dialog(request: Request, name: str, verb: str) -> RedirectResponse:
    """Run one playlist dialog's write (rename, duplicate, delete)."""
    ctx = _write_context(request)
    form = await request.form()
    new_name = _text(form.get(DIALOG_FIELD))
    if verb == "rename":
        if not new_name:
            return _back(request, actions_page.REFUSAL_NAME_MISMATCH, ok=False, name=name)
        result = await call_action("rename_playlist", ctx, playlist=name, name=new_name)
        _remember(request, result)
        target = _text((getattr(result, "data", None) or {}).get("playlist")) or new_name
        return _back(request, _text(getattr(result, "message", "")),
                     ok=bool(getattr(result, "success", False)),
                     name=target if getattr(result, "success", False) else name)
    if verb == "duplicate":
        if not new_name:
            return _back(request, actions_page.REFUSAL_NAME_MISMATCH, ok=False, name=name)
        # A TAKEN NAME IS REFUSED HERE: ``add_playlist_track`` CREATES a missing playlist but ADDS to
        # an existing one, so a copy posted under another playlist's name would merge the two. The
        # listing is read the way the "new playlist" verb reads it, and the comparison is casefold.
        listing = await _read(request, "list_music_playlists") \
            if _ready("list_music_playlists") else None
        playlists = list((getattr(listing, "data", None) or {}).get("playlists") or []) \
            if listing is not None and getattr(listing, "success", False) else None
        if playlists is None:
            return _back(request, _text(getattr(listing, "message", ""))
                         or i18n.translate(request, PLAYLISTS_UNREAD), ok=False, name=name)
        known = {_text(row.get("name")) for row in playlists if isinstance(row, dict)}
        if any(stored.casefold() == new_name.casefold() for stored in known if stored):
            return _back(request, i18n.translate(request, DUPLICATE_TAKEN, name=new_name),
                         ok=False, name=name)
        source = await _read(request, "get_music_playlist", playlist=name)
        tracks = [track for track in ((getattr(source, "data", None) or {}).get("tracks") or [])
                  if isinstance(track, dict)] if getattr(source, "success", False) else []
        files = [_text(track.get("file")) for track in tracks if _text(track.get("file"))]
        if not files:
            return _back(request, _text(getattr(source, "message", "")) or
                         i18n.translate(request, NO_TRACKS), ok=False, name=name)
        result = await call_action("add_playlist_track", ctx, playlist=new_name, song_files=files)
        _remember(request, result)
        target = _text((getattr(result, "data", None) or {}).get("playlist")) or new_name
        return _back(request, _text(getattr(result, "message", "")),
                     ok=bool(getattr(result, "success", False)),
                     name=target if getattr(result, "success", False) else name)
    result = await call_action("delete_playlist", ctx, playlist=name)
    _remember(request, result)
    if getattr(result, "success", False):
        return _back(request, _text(getattr(result, "message", "")), ok=True, to=MUSIC_PATH)
    return _back(request, _text(getattr(result, "message", "")), ok=False, name=name)


# ── the routes ────────────────────────────────────────────────────────────

def capabilities() -> dict[str, str]:
    """The capability declaration for every path this module registers.

    The two live paths carry the PAGE's own ``music.view``: "may open the page" and "may stream it"
    are one declaration, so the stream cannot outlive a revocation of the page.
    """
    declare()
    return {
        MUSIC_PATH: CAPABILITY_VIEW,
        PLAYLIST_PAGE_PATH: CAPABILITY_VIEW,
        MUSIC_STREAM_PATH: CAPABILITY_VIEW,
        MUSIC_FRAGMENTS_PATH: CAPABILITY_VIEW,
        NEW_DIALOG_PATH: CAPABILITY_WRITE,
        NEW_RUN_PATH: CAPABILITY_WRITE,
        DIALOG_PATH: CAPABILITY_WRITE,
        RUN_PATH: CAPABILITY_WRITE,
        PLAY_DIALOG_PATH: CAPABILITY_CONTROL,
        PLAY_RUN_PATH: CAPABILITY_CONTROL,
        ADD_PATH: CAPABILITY_WRITE,
        REMOVE_PATH: CAPABILITY_WRITE,
        MOVE_PATH: CAPABILITY_WRITE,
        REORDER_PATH: CAPABILITY_WRITE,
        UPLOAD_PATH: CAPABILITY_UPLOAD,
    }


def nav_items() -> tuple:
    """The sidebar entry for the playlist list — the capability's own name.

    The entry is advertised only while the music feature exists in this process: its read action
    (``get_music_radios``) is registered. A website process without the music plugin, or with its
    service unregistered, therefore offers no Media › Music entry — the page itself stays reachable
    and states that the feature is unavailable.
    """
    declare()
    return (NavItem(label=NAV_LABEL, url=MUSIC_PATH, capability=CAPABILITY_VIEW,
                    group="Media", glyph="note",
                    available=lambda: _ready("get_music_radios")),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the two pages, the dialogs and their writes to the shell's own router."""
    declare()

    @router.get(MUSIC_PATH, response_class=HTMLResponse, name="page-music")
    async def _music_page(request: Request):
        # no-store: a write returns here, and a cached pre-write render would hide the change
        return HTMLResponse(i18n.render(request, OVERVIEW_TEMPLATE, **await overview_context(request)),
                            headers={"Cache-Control": "no-store"})

    # THE TWO LIVE ENDPOINTS. Under ``/api`` so an EventSource is answered with the API face; both
    # refuse a caller without ``music.view`` before a generator exists, and both render the SAME two
    # partials from the SAME context builder the page used.
    @router.get(MUSIC_STREAM_PATH, response_class=StreamingResponse, name="music-stream")
    async def _music_stream(request: Request):
        return StreamingResponse(event_stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @router.get(MUSIC_FRAGMENTS_PATH, name="music-fragments")
    async def _music_fragments(request: Request):
        refuse_without_view(request)
        return JSONResponse(await current_fragments(request),
                            headers={"Cache-Control": "no-store"})

    @router.post(NEW_DIALOG_PATH, dependencies=[Depends(session.csrf_protect)],
                 name="music-new-dialog")
    async def _new_dialog(request: Request):
        return HTMLResponse(i18n.render(request, DIALOG_TEMPLATE,
                                        **await dialog_context(request, verb="new", playlist="")),
                            headers={"Cache-Control": "no-store"})

    @router.post(NEW_RUN_PATH, dependencies=[Depends(session.csrf_protect)], name="music-new-run")
    async def _new_run(request: Request):
        return await _run_new(request)

    @router.post(PLAY_DIALOG_PATH, dependencies=[Depends(session.csrf_protect)],
                 name="music-play-dialog")
    async def _play_dialog(request: Request):
        name = _text(request.path_params.get("playlist"))
        return HTMLResponse(i18n.render(request, DIALOG_TEMPLATE,
                                        **await dialog_context(request, verb="", playlist=name)),
                            headers={"Cache-Control": "no-store"})

    @router.post(PLAY_RUN_PATH, dependencies=[Depends(session.csrf_protect)], name="music-play-run")
    async def _play_run(request: Request):
        return await _run_play(request, _text(request.path_params.get("playlist")))

    @router.post(DIALOG_PATH, dependencies=[Depends(session.csrf_protect)], name="music-dialog")
    async def _dialog(request: Request):
        name = _text(request.path_params.get("playlist"))
        verb = _text(request.path_params.get("verb"))
        if verb not in DIALOG_VERBS:
            raise HTTPException(status_code=404)
        return HTMLResponse(i18n.render(request, DIALOG_TEMPLATE,
                                        **await dialog_context(request, verb=verb, playlist=name)),
                            headers={"Cache-Control": "no-store"})

    @router.post(RUN_PATH, dependencies=[Depends(session.csrf_protect)], name="music-run")
    async def _run(request: Request):
        name = _text(request.path_params.get("playlist"))
        verb = _text(request.path_params.get("verb"))
        if verb not in DIALOG_VERBS:
            raise HTTPException(status_code=404)
        return await _run_dialog(request, name, verb)

    @router.post(ADD_PATH, dependencies=[Depends(session.csrf_protect)], name="music-add")
    async def _add(request: Request):
        return await _run_add(request, _text(request.path_params.get("playlist")))

    @router.post(REMOVE_PATH, dependencies=[Depends(session.csrf_protect)], name="music-remove")
    async def _remove(request: Request):
        return await _run_remove(request, _text(request.path_params.get("playlist")))

    @router.post(MOVE_PATH, dependencies=[Depends(session.csrf_protect)], name="music-move")
    async def _move(request: Request):
        return await _run_move(request, _text(request.path_params.get("playlist")))

    @router.post(REORDER_PATH, dependencies=[Depends(session.csrf_protect)], name="music-reorder")
    async def _reorder(request: Request):
        return await _run_reorder(request, _text(request.path_params.get("playlist")))

    @router.post(UPLOAD_PATH, dependencies=[Depends(session.csrf_protect)], name="music-upload")
    async def _upload(request: Request):
        return await _run_upload(request, _text(request.path_params.get("playlist")))

    # THE EDITOR IS REGISTERED LAST: a name is one segment here, and every path above must be
    # matched before this page sees the request.
    @router.get(PLAYLIST_PAGE_PATH, response_class=HTMLResponse, name="page-music-playlist")
    async def _playlist_page(request: Request):
        name = _text(request.path_params.get("playlist"))
        return HTMLResponse(i18n.render(request, EDITOR_TEMPLATE,
                                        **await editor_context(request, name)),
                            headers={"Cache-Control": "no-store"})

    return router


def register(registrar) -> None:
    """Register these pages with the registrar, under this module's own owner name."""
    router = APIRouter()
    add_routes(router)
    registrar.register_pages(OWNER, router, nav=nav_items(), capabilities=capabilities())
