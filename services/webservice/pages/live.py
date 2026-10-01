"""Live updates for the dashboard: server-rendered fragments over Server-Sent Events.

THE TRANSPORT CHOICE (made upstream, recorded here so the next reader does not re-open it): the
traffic is one-directional (server -> browser), ``text/event-stream`` needs no dependency and no
handshake, and the payloads are HTML FRAGMENTS RENDERED BY THE SAME JINJA TEMPLATES the initial
page uses. WebSocket is deliberately not used; it belongs to the card that adds write actions with
live output, where the browser talks back.

WHAT IS ON THE WIRE. Every frame's ``data`` is one line of JSON (so no HTML newline can break the
framing) of the shape ``{"targets": {"<element id>": "<html>"}}``. A fresh connection gets one
``snapshot`` event carrying all targets; afterwards a ``log`` event carries only
:data:`LOG_TARGET` and a ``state`` event carries the server/KPI targets, and NOTHING is sent when
nothing changed — the decision is a SHA-256 of the rendered fragment, so a re-render that produces
identical markup is silent. A ``: ping`` comment is written when the heartbeat interval elapses,
so a proxy or the server does not cut an idle stream.

WHO GETS THE LOG TARGET. The bot log is Admin-only (``logs.view``, see
:mod:`services.webservice.pages.logs`), so :data:`LOG_TARGET` is only ever RENDERED for a session
that may read it: a connection without the capability gets a snapshot of the state and pill targets
and no log fragment on any later frame, and a request that asks for the log (``?log=expanded``) is
refused with the console's 403 before the generator exists. Every other target keeps flowing, so the
live refresh still works for the viewer — it simply has no log to show.

BOUNDED AND LEAK-FREE. A stream takes one slot in :class:`LiveRegistry` for as long as its
generator runs and releases it in a ``finally``, so a client that goes away (Starlette cancels the
response, which closes the generator) leaves nothing behind. Past the configured cap a client
still receives its snapshot and the stream then closes cleanly, rather than queueing behind a slot
it will never get.

The paths are CONSTANTS under ``/api/…`` on purpose: the whole ``/api`` area is what the
one-status-two-faces predicate treats as machine-facing, so an EventSource is answered with a
status and a JSON body and NEVER with a redirect to the login page — which an EventSource cannot
follow. The routes are gated with the dashboard's own capability, so "may open the page" and "may
stream it" cannot diverge.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import permissions, readmodels
from . import dashboard as dashboard_page

__all__ = [
    "DASHBOARD_STREAM_PATH", "DASHBOARD_FRAGMENTS_PATH",
    "LOG_TARGET", "STATE_TARGET", "PILLS_TARGET", "NOTICE_TARGET",
    "LOG_TEMPLATE", "STATE_TEMPLATE", "PILLS_TEMPLATE", "NOTICE_TEMPLATE",
    "SNAPSHOT_EVENT", "LOG_EVENT", "STATE_EVENT", "HEARTBEAT_FRAME",
    "DEFAULT_REFRESH_SECONDS", "DEFAULT_STREAM_CLIENTS", "HEARTBEAT_SECONDS",
    "LiveConfig", "LiveRegistry", "live_config", "dashboard_config", "get_registry",
    "page_context", "render_for", "fragments", "stream_frames", "event_stream",
    "log_allowed", "refuse_log_request", "write_notice",
    "capabilities", "add_routes",
]

log = logging.getLogger(__name__)

#: fixed, constant paths. Under ``/api`` so the refusal is the API's (a status + JSON), never a
#: document an EventSource cannot use.
DASHBOARD_STREAM_PATH = "/api/dashboard/stream"
DASHBOARD_FRAGMENTS_PATH = "/api/dashboard/fragments"

#: the DOM containers the client swaps, and the template that renders each one. The ids are the
#: contract between this module, the templates and ``static/shell.js``.
LOG_TARGET = "log-panel"
STATE_TARGET = "state-panel"
PILLS_TARGET = "status-pills"
#: the ONE-SHOT NOTICE of a write, carried on the live path too. A strip control submits in
#: the background and the page is handed over to a fresh render at once, so that render happens
#: BEFORE the route has stored the outcome; the live path is what delivers it afterwards — the same
#: way it delivers the row state. It is a target of its own because the notice is NOT part of the
#: state panel (a refresh must neither repeat nor drop it), and it is OMITTED from a frame when there
#: is nothing to say, so a frame can never blank a notice the page already rendered.
NOTICE_TARGET = "notice"

#: the templates — the SAME partials the initial page includes, so no markup lives in Python and
#: none is duplicated in JavaScript.
LOG_TEMPLATE = "_log_panel.html"
STATE_TEMPLATE = "_state.html"
PILLS_TEMPLATE = "_pills.html"
NOTICE_TEMPLATE = "_notice.html"

SNAPSHOT_EVENT = "snapshot"
LOG_EVENT = "log"
STATE_EVENT = "state"

#: an SSE comment frame. Sent when no fragment changed, to keep an idle stream alive.
HEARTBEAT_FRAME = ": ping\n\n"

DEFAULT_REFRESH_SECONDS = 2
DEFAULT_STREAM_CLIENTS = 16
HEARTBEAT_SECONDS = 15.0
#: the floors/ceilings a configured value is clamped into: a value outside them is not rejected, it
#: falls back to the default (a hand-edited file must not be able to busy-loop the server)
MIN_REFRESH_SECONDS = 1
MAX_REFRESH_SECONDS = 60
MIN_STREAM_CLIENTS = 1
MAX_STREAM_CLIENTS = 256

#: A frame whose target is NOT :data:`LOG_TARGET` belongs to the ``state`` event (the state panel
#: and the top bar's pill strip). The grouping is expressed by that one comparison below, so there
#: is no second list of target names to keep in sync.
_TRUE_VALUES = ("true", "yes", "on", "1")
_FALSE_VALUES = ("false", "no", "off", "0")


# ------------------------------------------------------------------------------ configuration

@dataclass(frozen=True, slots=True)
class LiveConfig:
    """The live-update settings, resolved once per request from the service config."""
    enabled: bool = True
    refresh_seconds: float = DEFAULT_REFRESH_SECONDS
    stream_clients: int = DEFAULT_STREAM_CLIENTS
    heartbeat_seconds: float = HEARTBEAT_SECONDS


def _as_bool(value, default: bool) -> bool:
    """Parse a configured boolean rather than truthiness-testing it (``bool('off')`` is True)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    word = str(value).strip().lower()
    if word in _TRUE_VALUES:
        return True
    if word in _FALSE_VALUES:
        return False
    return default


def _clamped_number(value, *, default, floor, ceiling, integer: bool):
    """*value* inside its floor/ceiling, or *default* when it is missing or unparseable."""
    if value is None or isinstance(value, bool):
        return default
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        return default
    if number < floor or number > ceiling:
        return default
    return number


def live_config(config: dict | None) -> LiveConfig:
    """The effective live-update settings from the service's own config block."""
    block = dict((config or {}).get("dashboard") or {})
    return LiveConfig(
        enabled=_as_bool(block.get("live"), True),
        refresh_seconds=_clamped_number(block.get("refresh_seconds"),
                                       default=DEFAULT_REFRESH_SECONDS,
                                       floor=MIN_REFRESH_SECONDS, ceiling=MAX_REFRESH_SECONDS,
                                       integer=True),
        stream_clients=_clamped_number(block.get("stream_clients"),
                                       default=DEFAULT_STREAM_CLIENTS,
                                       floor=MIN_STREAM_CLIENTS, ceiling=MAX_STREAM_CLIENTS,
                                       integer=True),
        heartbeat_seconds=HEARTBEAT_SECONDS,
    )


def dashboard_config(app) -> LiveConfig:
    """The live config of an application, read from the config the shell stored on it."""
    return live_config(getattr(getattr(app, "state", None), "webui_config", None) or {})


# --------------------------------------------------------------------------------- the registry

class LiveRegistry:
    """Counts the streams that are running right now, with a cap.

    One slot per connected client. :meth:`try_acquire` answers whether this client is within the
    cap; :meth:`release` returns the slot. The count is what makes "no task outlives its client"
    checkable instead of asserted.
    """

    def __init__(self, limit: int = DEFAULT_STREAM_CLIENTS):
        self.limit = max(MIN_STREAM_CLIENTS, int(limit))
        self._active = 0

    @property
    def active(self) -> int:
        return self._active

    def try_acquire(self) -> bool:
        """Take a slot, or answer ``False`` when the cap is reached (the caller still snapshots)."""
        if self._active >= self.limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        if self._active > 0:
            self._active -= 1


def get_registry(app, config: LiveConfig) -> LiveRegistry:
    """The application's registry, created on first use with its configured cap."""
    registry = getattr(getattr(app, "state", None), "webui_live_registry", None)
    if registry is None:
        registry = LiveRegistry(config.stream_clients)
        app.state.webui_live_registry = registry
    return registry


# ------------------------------------------------------------------------------- the fragments

def fragments(environment, state: readmodels.Overview, view=None, console=None,
              row_controls=None, include_log: bool = True, notice=None) -> dict[str, str]:
    """The fragments, rendered by the page's own partials. One render path for page + stream.

    An empty mapping key would be a broken swap, so every target is always present in the result
    and the client simply overwrites the ones an event carries. ``view`` is the validated view
    (the sorted tables and the players search) and ``console`` the chrome (banner, tabs, log card);
    both are optional so a caller that only needs the log/pills fragments keeps working, and both
    default to the same shape the page renders — a fragment must never be missing a name the
    template reads (StrictUndefined would raise, and an empty string would be worse).

    ``include_log=False`` DROPS the log target entirely — the shape the live stream takes for a
    session that may not read the bot log (``logs.view``, Admin-only). Dropping it rather than
    emptying it means the target is never on the wire, so no later ``log`` event can carry it: the
    client skips ids absent from a frame (see ``static/shell.js``), and the frames are compared by
    a hash of the targets that exist.

    ``notice`` is the ONE-SHOT outcome of a write, already POPPED for this render by the
    caller. It is included ONLY when there is something to say, for exactly the reason the log
    target is dropped rather than emptied: a frame that carried an empty notice would blank the
    notice the page's own render just showed (the value is popped, so the first frame after a page
    load would always be empty). The client skipping an absent id is what makes a once-only value
    safe to deliver on a path that renders many times.
    """
    if view is None:
        view = readmodels.view_state(
            dashboard_page.DASHBOARD_PATH,
            {"servers": state.servers, "nodes": state.nodes, "instances": state.instances,
             "players": state.players})
    if console is None:
        console = dashboard_page.console_context(state)
    targets = {
        STATE_TARGET: environment.get_template(STATE_TEMPLATE).render(
            state=state, view=view, empty=dashboard_page.empty_messages(state), console=console,
            # the tab's own controls (W4a): the strip on a server row. Passed through so a refreshed
            # table carries the SAME controls its initial render did — the stream and the page read
            # one map (``pages/dashboard.row_controls_for``).
            row_controls=row_controls or {}),
        PILLS_TARGET: environment.get_template(PILLS_TEMPLATE).render(
            pills=dashboard_page.status_pills(state)),
    }
    if include_log:
        targets[LOG_TARGET] = environment.get_template(LOG_TEMPLATE).render(state=state)
    if notice:
        targets[NOTICE_TARGET] = environment.get_template(NOTICE_TEMPLATE).render(notice=notice)
    return targets


def _digest(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _frame(event: str, targets: dict[str, str]) -> str:
    """One SSE frame. The payload is a single line of JSON, so no fragment newline can break it."""
    return f"event: {event}\ndata: {json.dumps({'targets': targets})}\n\n"


async def stream_frames(*, render: Callable[[], dict[str, str]], config: LiveConfig,
                        registry: LiveRegistry, sleep=asyncio.sleep, monotonic=time.monotonic):
    """Yield the SSE frames of one connection.

    ``render`` returns the current fragments; ``sleep`` and ``monotonic`` are injectable so a test
    drives ticks without waiting on the wall clock.
    """
    acquired = registry.try_acquire() if config.enabled else False
    try:
        current = render()
        yield _frame(SNAPSHOT_EVENT, current)
        if not config.enabled or not acquired:
            # over the cap (or live updates are off): the client got its snapshot, and the stream
            # closes cleanly rather than queueing for a slot it will never be given
            return
        digests = {name: _digest(html) for name, html in current.items()}
        last_beat = monotonic()
        while True:
            await sleep(config.refresh_seconds)
            now = monotonic()
            if now - last_beat >= config.heartbeat_seconds:
                yield HEARTBEAT_FRAME
                last_beat = now
            current = render()
            log_changed: dict[str, str] = {}
            state_changed: dict[str, str] = {}
            for name, html in current.items():
                digest = _digest(html)
                if digest == digests.get(name):
                    continue
                digests[name] = digest
                (log_changed if name == LOG_TARGET else state_changed)[name] = html
            if log_changed:
                yield _frame(LOG_EVENT, log_changed)
            if state_changed:
                yield _frame(STATE_EVENT, state_changed)
    finally:
        if acquired:
            registry.release()


# -------------------------------------------------------------------------------- the wiring

def page_context(request: Request) -> dict | None:
    """What ``base.html`` needs to load the live script, or ``None`` when it is disabled.

    THE LEVEL RIDES IN THE URL. The level control is a set of plain links (``/?level=…``), so a
    click reloads the page and the initial render honours the parameter — but an EventSource (and
    the polling fallback) opens a SEPARATE request. Handing the browser a stream URL without the
    level makes that request fall back to the configured default, so the snapshot and every later
    frame silently replace the panel at the wrong level: the reader's choice reverts after about
    two seconds. The fix is server-side and needs no new client logic: both URLs carry the level
    already in force for THIS request, resolved through the same one validator the page used
    (:func:`~services.webservice.pages.dashboard.log_level`), so the stream cannot ask for a level
    the page did not render and an unknown value cannot be laundered into the stream's query.
    """
    if not dashboard_config(request.app).enabled:
        return None
    # The sort rides along only when the table this URL will RENDER honours it: the fragment
    # endpoint re-resolves the tab from this same URL, so that table is the tab in force — a
    # `sort` belonging to another table (Finding 3) would be a key the page never showed. The roles
    # are resolved ONCE here and handed to both calls (the tab scope and the carried tab are the
    # same decision, so asking the resolver twice could only produce two answers).
    roles = permissions.role_names_for(request)
    params = dashboard_page.view_params(request)
    query = urlencode({**dashboard_page.carry_params(request, roles),
                       **params.carry(table=dashboard_page.tab_from(request, roles))})
    return {"stream": f"{DASHBOARD_STREAM_PATH}?{query}",
            "fragments": f"{DASHBOARD_FRAGMENTS_PATH}?{query}"}


def _environment(request: Request):
    environment = getattr(getattr(request.app, "state", None), "webui_templates", None)
    if environment is None:  # pragma: no cover - installed by the shell
        raise HTTPException(status_code=503,
                            detail="The admin web UI templates are not installed.")
    return environment


def log_allowed(request: Request) -> bool:
    """Whether THIS connection may read the bot log (``logs.view``, Admin-only — see the dashboard).

    The one predicate the stream, the polling fallback and the refusal below all read, so "the log
    fragment is sent" and "the log page opens" can never disagree.
    """
    return dashboard_page.may_read_log(permissions.role_names_for(request))


def refuse_log_request(request: Request) -> None:
    """Refuse a request that ASKS for the log (``?log=expanded``) from a session that may not read it.

    Hiding the fragment is not enough: a request that names the log has to be answered, not trimmed,
    or the caller learns that the log exists and is merely withheld. The refusal is the console's own
    (an ``HTTPException(403)``), which the two-face predicate renders as a status + JSON body for the
    ``/api/…`` paths an EventSource uses — never a page it cannot read.
    """
    if dashboard_page.asks_for_expanded_log(request) and not log_allowed(request):
        raise dashboard_page.log_refusal()


def write_notice(request: Request) -> dict | None:
    """The ONE-SHOT outcome of the last write, POPPED for this render — or ``None``.

    THE OUTCOME RIDES THE LIVE PATH. A strip control now submits in the background and the page is
    handed over to a fresh render at once, so the page the person lands on renders BEFORE the route
    has stored the write's outcome; the live path is what delivers it afterwards — the same way it
    delivers the row state. This is the SAME value the page itself pops (``pages/actions.
    pop_notice``): one store and one partial, so the notice a page shows and the notice a frame shows
    can never be two different things. The import is inside the function because ``pages/actions``
    reaches ``pages/dashboard``, which this module already imports.
    """
    from . import actions as actions_page
    return actions_page.pop_notice(request)


def _viewer(request: Request) -> tuple[readmodels.Overview, bool]:
    """``(state, may_read_log)`` for one connection — resolved ONCE, read by the whole tick.

    A session that may not read the log gets the source WITHOUT a log file (the dashboard's
    ``source_for_viewer``), so the file is not read at all and the banner cannot count ERROR lines
    out of something the viewer may not see.
    """
    allowed = log_allowed(request)
    state = readmodels.overview(
        dashboard_page.source_for_viewer(dashboard_page.request_source(request),
                                         may_read_log=allowed),
        level=dashboard_page.log_level(request))
    return state, allowed


def render_for(request: Request) -> Callable[[], dict[str, str]]:
    """The fragment renderer for ONE connection.

    The level and the view (``?sort=``/``?dir=``/``?q=``) are resolved from THIS request (see
    :func:`~services.webservice.pages.dashboard.log_level` and
    :func:`~services.webservice.pages.dashboard.view_state_for`), so a connection opened with
    ``?level=all&sort=players&dir=desc&q=viper`` keeps rendering at ALL, sorted by players and
    filtered, for as long as it lives — the same state the initial page rendered from the same
    parameters. That is the seam between the controls and the stream: the URL the shell hands the
    browser carries the view, and this is what it lands in. The view is resolved on EVERY tick, so
    no frame can fall back to a different one.

    Whether the log target is rendered is resolved per tick too, from the SAME request: a session
    that may not read the log never receives it, on the snapshot or on any later frame.

    THE ONE-SHOT NOTICE of a write is popped per tick as well (:func:`write_notice`), so a write whose
    outcome is stored after the connection opened is still delivered — by the polling fallback on the
    next poll, and on the snapshot for any outcome already stored when the connection opened (card
    W4h: a strip control hands the page over to a fresh render, so its outcome is written a moment
    after the page the person is looking at has already rendered).
    """
    environment = _environment(request)

    def render() -> dict[str, str]:
        state, allowed = _viewer(request)
        view = dashboard_page.view_state_for(request, state)
        console = dashboard_page.console_context(state, request, view, log_allowed=allowed)
        return fragments(environment, state, view, console,
                         row_controls=dashboard_page.row_controls_for(request, console["tab"]),
                         include_log=allowed, notice=write_notice(request))

    return render


def event_stream(request: Request):
    """The SSE generator for one request: the same fragments, polled on the configured interval.

    The log request is refused HERE, before the generator exists: a refusal raised inside a
    ``StreamingResponse`` body would arrive after the headers, i.e. as a broken stream rather than as
    the 403 the caller can read (and an EventSource would retry forever).

    A LONG-LIVED STREAM OUTLIVES THE SESSION COOKIE, on purpose and acceptably (review finding, kept
    as documented behaviour, not a bug to rediscover): the session is decoded ONCE, when the request
    starts, so the cookie's ``max_age`` is never re-checked while this generator ticks. What IS
    re-resolved on every tick is the identity's ROLES (``render_for`` → ``_viewer``), so a session
    whose roles are revoked loses its data at the next tick — the revocation that matters is not
    delayed. What is delayed is only the cookie's own expiry, and it is bounded from the other side:
    the stream caps (``dashboard_config``), the heartbeat and the disconnect handling below close the
    connection, and the client reconnects through the normal gate, which refuses the expired cookie.
    Re-validating the cookie per tick would need the raw request cookie and a second session decode
    per tick — cost for a window the caps already bound.
    """
    refuse_log_request(request)
    config = dashboard_config(request.app)
    registry = get_registry(request.app, config)
    return stream_frames(
        render=render_for(request),
        config=config, registry=registry)


def capabilities() -> dict[str, str]:
    """Both stream paths are gated with the DASHBOARD's capability — one declaration, one door."""
    dashboard_page.declare()
    return {DASHBOARD_STREAM_PATH: dashboard_page.DASHBOARD_CAPABILITY,
            DASHBOARD_FRAGMENTS_PATH: dashboard_page.DASHBOARD_CAPABILITY}


def add_routes(router: APIRouter) -> APIRouter:
    """Add the stream and fragment endpoints to the shell's own router and return it."""

    @router.get(DASHBOARD_STREAM_PATH, response_class=StreamingResponse)
    async def dashboard_stream(request: Request):
        return StreamingResponse(event_stream(request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @router.get(DASHBOARD_FRAGMENTS_PATH)
    async def dashboard_fragments(request: Request):
        refuse_log_request(request)
        environment = _environment(request)
        state, allowed = _viewer(request)
        view = dashboard_page.view_state_for(request, state)
        console = dashboard_page.console_context(state, request, view, log_allowed=allowed)
        # The console chrome is built from THIS request too, exactly like the SSE renderer: the
        # polling fallback reads this endpoint with the URL the page handed it (``?tab=…&level=…``),
        # and without the request the state panel would come back on the DEFAULT tab — the reader's
        # tab would snap back on the first poll, which is the same defect the stream's ``render_for``
        # avoids. One render path for both transports means one place to get this right — including
        # whether the log target is on the wire at all.
        #
        # THE ROW CONTROLS RIDE ALONG TOO, for the same reason and by the same bridge the page and
        # the stream use (``pages/dashboard.row_controls_for``): a polled table that dropped its
        # Actions column would rebuild every row WITHOUT its controls about two seconds after the
        # page loaded — and the origin a write returns to is part of that column, so the
        # fallback would also lose which page the control came from.
        return JSONResponse(fragments(environment, state, view, console,
                                      row_controls=dashboard_page.row_controls_for(request,
                                                                                  console["tab"]),
                                      include_log=allowed, notice=write_notice(request)))

    return router
