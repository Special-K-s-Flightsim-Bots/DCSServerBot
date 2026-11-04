"""``create_setup_app(...)`` — the DB-free front end an unconfigured install serves.

The application is a SIBLING of the admin shell, never a mode of it. It is built by the launcher
only on a boot where ``main.yaml`` is absent, and installing it touches nothing the normal console
uses:

* it installs NO identity backend (``auth.install_auth`` is never called), so the process-global
  role/scope/manager resolvers stay at their deny-by-default values and no login door exists;
* it registers NO normal page and NO ``/api`` route — the only routes are the setup gate and the
  health probe, and every other path is a 404 by the router;
* it writes nothing: this module has no write path, and the config directory is only recorded on
  the application state for the tour that a later phase adds.

THE GATE. Before the code is accepted, the only reachable routes are ``GET /setup`` (a static page)
and ``POST /setup`` (an attempt). The static page deliberately touches no session, so it sets no
cookie; acceptance is what mints the session, and it stores the window's own fingerprint, so a
session left over from an earlier boot can never satisfy :func:`setup_window_dependency`. Every
route a later phase adds is expected to declare that dependency — an accepted session is the only
thing it grants.

The gate page carries the product name, one input and one fixed instruction. It carries no version,
no host name, no detected path and no link to ``/auth``, because those are machine-layout facts: the
tour's first render is behind the gate for exactly that reason, and nothing may leak before it.

AN EVENTUAL REMOTE MODE (not part of this phase). A setup console reachable from another machine
would need a bind the launcher chooses explicitly (never a widened default), transport security (TLS
material, or the documented cleartext acknowledgement), and the session cookie's ``Secure`` flag
resolved for that bind — the derivation in :mod:`services.webservice.session` sets ``Secure`` for a
non-loopback bind, and a browser does not return a ``Secure`` cookie over plain HTTP, so the tour's
session-held drafts would break one request after a successful entry.
"""

from __future__ import annotations

import hmac
import logging
from html import escape

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import permissions, session, shell
from ..registry import Registrar
from . import tour
from .boot import INSTALL, JOIN
from .window import ACCEPTED, EXPIRED, LOCKED, SPENT, WRONG, SetupWindow, normalise_code

__all__ = [
    "SETUP_LISTEN",
    "SETUP_PATH",
    "HEALTH_PATH",
    "SESSION_ACCEPTED_KEY",
    "SetupBindRefused",
    "SetupModeRefused",
    "require_loopback_bind",
    "create_setup_app",
    "setup_accepted",
    "accept_setup",
    "setup_window_dependency",
    "gate_page",
]

log = logging.getLogger(__name__)

#: The ONLY bind this console may use. A loopback socket cannot be reached from another machine, so
#: even a leaked code is useless to a network neighbour: the code is the second control here, not
#: the only one.
SETUP_LISTEN = "127.0.0.1"

#: The gate and the hello page are the same path, rendered from the session's acceptance.
SETUP_PATH = "/setup"

#: A liveness probe for a supervisor. It answers a constant and carries no version, no host name
#: and no detection result.
HEALTH_PATH = "/healthz"

#: The session key an accepted exchange writes: the accepting window's fingerprint.
SESSION_ACCEPTED_KEY = "setup_window"

#: The port the setup console listens on. The same stable port the admin console uses, so a
#: documented URL keeps working once setup is done.
SETUP_PORT = 9876


class SetupBindRefused(RuntimeError):
    """Raised when the setup console is asked to bind anything but the loopback interface."""


class SetupModeRefused(ValueError):
    """Raised when the setup console is asked for a mode it cannot serve.

    Today that is exactly ``mode=JOIN`` with no node name. The joining mode's whole subject is ONE
    node, named by the launcher; without it :func:`services.webservice.setup.tour._setup_finished`
    reads the inert joining check and would render the terminal Done screen for a node that was never
    added. The boot rule never produces the combination (``setup_mode`` answers ``JOIN`` only for a
    named node), so this is a fail-closed guard on the factory's own contract, not a live path.
    """


def require_loopback_bind(listen: str) -> str:
    """Return *listen* when it is loopback-only, refuse it otherwise.

    A HARD refusal, not a warning: a setup console on a reachable interface has no second control
    behind the code, and the operator never sees the socket a warning describes. What an eventual
    remote mode would need instead is named in this module's docstring.
    """
    listen = str(listen or "").strip() or SETUP_LISTEN
    if not session._is_loopback_listen(listen):
        raise SetupBindRefused(
            f"the setup console refuses to bind '{listen}': setup is local-only, and a bind "
            f"reachable beyond loopback would expose an unauthenticated console to the network. "
            f"Use {SETUP_LISTEN}.")
    return listen


# ------------------------------------------------------------------------------------- the session

def setup_accepted(request: Request, window: SetupWindow) -> bool:
    """Whether this request carries an accepted setup session FOR THIS WINDOW.

    Compared in constant time against the window's fingerprint. An accepted session from an earlier
    process — the session secret may be a configured one, so the cookie can outlive a restart — is
    NOT accepted here.
    """
    stored = request.session.get(SESSION_ACCEPTED_KEY)
    if not isinstance(stored, str) or not stored:
        return False
    return hmac.compare_digest(stored, window.fingerprint)


def accept_setup(request: Request, window: SetupWindow) -> None:
    """Mint the accepted session. This is the FIRST thing that writes a cookie on this application."""
    request.session[SESSION_ACCEPTED_KEY] = window.fingerprint


def setup_window_dependency(window: SetupWindow):
    """The dependency every tour route declares, and the only way a route is reachable unaccepted.

    An unaccepted caller gets **404**, not 403: a refusal that named the route would tell a stranger
    the tour's shape, and before acceptance there is nothing to authorise them for.
    """
    async def _dependency(request: Request) -> None:
        if not setup_accepted(request, window):
            raise HTTPException(status_code=404, detail="Not found.")

    #: a marker so a route-table test can see that a route declares this guard without importing it
    setattr(_dependency, "dcssb_setup_window_dependency", True)
    return _dependency


# --------------------------------------------------------------------------------- the application

def create_setup_app(window: SetupWindow, *, config_dir, listen: str = SETUP_LISTEN,
                     node: str = "", mode: str = INSTALL) -> FastAPI:
    """Build the setup console for *window*. Refuses a non-loopback bind.

    *mode* is ``boot.INSTALL`` or ``boot.JOIN`` — the state the window was opened in, recorded here
    and read by every render and every write for this application's whole life. It is NEVER read
    from the draft: a caller-writable file must not decide which rail is served or what a commit may
    write. ``JOIN`` names ONE node, so a joining mode with no node is REFUSED
    (:class:`SetupModeRefused`) rather than served: the joining rail's ``_setup_finished`` would
    otherwise read the inert check and render Done for a node that was never added.

    The app-level capability gate is the SAME floor the admin console uses
    (:func:`services.webservice.shell.create_app` attaches it at construction), so a route added
    later without a declaration is refused exactly as it is there. The setup routes declare
    :data:`services.webservice.permissions.PUBLIC` only to pass that floor; the real gate is
    :func:`setup_window_dependency`.
    """
    listen = require_loopback_bind(listen)
    if mode == JOIN and not str(node or "").strip():
        raise SetupModeRefused(
            "the setup console refuses mode='join' with no node name: the joining state is one named "
            "node absent from nodes.yaml, and without it nothing can tell this node apart from a "
            "configured install.")
    app = shell.create_app()
    # the tour's config directory: where its draft lives and where the Review step writes the real
    # configuration. Recorded here so a request never re-derives it.
    app.state.webui_config_dir = str(config_dir)
    # the node name the written nodes.yaml is keyed on. Recorded here for the same reason.
    app.state.webui_setup_node = str(node or "")
    # which state this console serves (see the docstring).
    app.state.webui_setup_mode = mode if mode in (INSTALL, JOIN) else INSTALL
    session.install_session_middleware(app, {"listen": listen})
    shell.install_refusal_handlers(app)
    registrar = Registrar(app)
    router = APIRouter()
    _add_routes(router, window)
    registrar.register_pages("setup", router, capabilities={
        SETUP_PATH: permissions.PUBLIC,
        tour.STEP_PATH: permissions.PUBLIC,
        tour.LANGUAGE_PATH: permissions.PUBLIC,
        tour.THEME_PATH: permissions.PUBLIC,
        tour.FLAGS_PATH + "/{name}": permissions.PUBLIC,
        HEALTH_PATH: permissions.PUBLIC,
    })
    app.state.webui_registrar = registrar
    log.info("Setup console: serving the gate, the tour and the health probe on %s (no identity "
             "backend, no normal page, no /api route).", listen)
    return app


def _add_routes(router: APIRouter, window: SetupWindow) -> None:
    """The setup console's whole route table: the gate, the tour's step screen, the two display
    switches, the flag assets and the health probe. Every tour route declares the window guard, and
    the step write also declares ``session.csrf_protect`` (the gate's own POST stays token-free — see
    :func:`setup_submit`)."""
    window_guard = setup_window_dependency(window)

    @router.get(SETUP_PATH, response_class=HTMLResponse)
    async def setup_gate(request: Request, code: str | None = None) -> Response:
        """The gate, or — once accepted — the tour's current step.

        A ``code`` in the query string is exchanged and then redirected to the bare path, so the
        code leaves the address bar, the browser history and any ``Referer`` a later request
        would send.
        """
        if setup_accepted(request, window):
            return await tour.render_root(request)
        if code is None or not normalise_code(code):
            return HTMLResponse(gate_page())
        return _answer(await window.exchange(code, _client_ip(request)), request, window)

    @router.post(SETUP_PATH, response_class=HTMLResponse)
    async def setup_submit(request: Request) -> Response:
        """An attempt from the gate page's form.

        Deliberately WITHOUT CSRF protection, and safe without it — three facts, in order of what a
        cross-site request could reach:

        * there is no session to protect yet: the code IS the authentication, and a token would force
          this page to write a cookie, which the gate must not do (the leak test pins that);
        * pre-acceptance the worst a forged cross-site POST achieves is one wrong entry against the
          loopback bucket — a denial of service at most, learning nothing, and identical in effect to
          any other local flood (see the limiter note in :mod:`services.webservice.setup.window`);
        * post-acceptance the accepted cookie is ``SameSite=Lax`` (the shell's session default), so a
          cross-site POST is sent WITHOUT it and cannot act as the accepted operator.

        The tour's own writes are NOT on this route: they are ``POST /setup/step``, which carries
        ``session.csrf_protect`` (see :func:`_add_routes`).
        """
        if setup_accepted(request, window):
            return RedirectResponse(tour.STEP_PATH, status_code=303)
        form = await request.form()
        submitted = form.get("code")
        return _answer(await window.exchange(submitted, _client_ip(request)), request, window)

    @router.get(tour.STEP_PATH, response_class=HTMLResponse,
                dependencies=[Depends(window_guard)])
    async def setup_step(request: Request, n: str | None = None) -> Response:
        """A step screen. Only a completed step or the current one renders (a future row is not a
        link); the window guard is the reason this is 404 until the code is accepted."""
        return await tour.render_step(request, n)

    @router.post(tour.STEP_PATH, dependencies=[Depends(window_guard),
                                               Depends(session.csrf_protect)])
    async def setup_step_write(request: Request) -> Response:
        """A step's Continue / a re-render: the answers are saved through the one write primitive."""
        return await tour.submit_step(request)

    @router.get(tour.LANGUAGE_PATH, response_class=RedirectResponse)
    async def choose_language(request: Request, lang: str = "", next: str = "/") -> Response:
        """The tour's own language switch: a display cookie, so it needs no identity or CSRF."""
        return tour.language_redirect(request, lang, next)

    @router.get(tour.THEME_PATH, response_class=RedirectResponse)
    async def choose_theme(request: Request, theme: str = "", next: str = "/") -> Response:
        """The tour's own theme switch: a display cookie, like the language one."""
        return tour.theme_redirect(request, theme, next)

    @router.get(tour.FLAGS_PATH + "/{name}")
    async def setup_flag(name: str) -> Response:
        """The vendored flag SVGs, served from a fixed allow-list (the console serves no /static)."""
        return tour.render_flags(name)

    @router.get(HEALTH_PATH)
    async def setup_health() -> JSONResponse:
        return JSONResponse({"status": "ok"})


def _answer(outcome: str, request: Request, window: SetupWindow):
    """Turn an exchange outcome into the response the caller gets."""
    if outcome == ACCEPTED:
        accept_setup(request, window)
        return RedirectResponse(SETUP_PATH, status_code=303)
    if outcome == LOCKED:
        return HTMLResponse(gate_page(_LOCKED_TEXT), status_code=429,
                            headers={"Retry-After": str(window.lock_seconds_left(_client_ip(request)))})
    return HTMLResponse(gate_page(_MESSAGES[outcome]), status_code=403)


def _client_ip(request: Request) -> str | None:
    """The peer address, for the per-address attempt limiter.

    Taken from the socket and never from a forwarded header: a header is caller-controlled, and a
    caller who could rename itself could step around the limiter.
    """
    client = getattr(request, "client", None)
    return getattr(client, "host", None)


# ------------------------------------------------------------------------------------ the pages

#: The copy of the two fixed outcomes. No sentence names a route, a version or a host.
_MESSAGES = {
    WRONG: "That code is not correct.",
    EXPIRED: "This code has expired. Restart DCSServerBot to get a new one.",
    SPENT: "This code has already been used. Restart DCSServerBot to get a new one.",
}
_LOCKED_TEXT = "Too many incorrect codes from this address. Wait a few minutes and try again."

#: Namespaced under ``.setup`` and inline: these pages are standalone documents. They must not
#: depend on the shell's asset path (which this application does not serve) and must not fetch
#: anything, so the page is one response with nothing to trace.
_SETUP_STYLE = """<style>
.setup { max-width: 34rem; margin: 12vh auto; padding: 0 1.5rem;
  font: 16px/1.5 system-ui, sans-serif; }
.setup h1 { font-size: 1.35rem; margin: 0 0 .75rem; }
.setup p { margin: 0 0 1rem; }
.setup label { display: block; margin: 0 0 .35rem; }
.setup input { width: 100%; box-sizing: border-box; padding: .5rem .6rem; font: inherit;
  font-family: ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase; }
.setup button { margin-top: .75rem; padding: .5rem 1rem; font: inherit; }
.setup .setup-note { opacity: .75; }
</style>"""


def gate_page(message: str | None = None) -> str:
    """The pre-acceptance page: the product name, one input, one fixed instruction."""
    body = f'<p class="setup-note">{escape(message)}</p>' if message else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>DCSServerBot setup</title>\n"
        f"{_SETUP_STYLE}\n"
        '</head>\n<body>\n<main class="setup">\n'
        "<h1>DCSServerBot setup</h1>\n"
        "<p>Enter the code printed by DCSServerBot.</p>\n"
        f"{body}\n"
        f'<form method="post" action="{SETUP_PATH}">\n'
        '<label for="code">Setup code</label>\n'
        '<input id="code" name="code" type="text" autocomplete="one-time-code" '
        'autocapitalize="characters" spellcheck="false" autofocus required>\n'
        "<button type=\"submit\">Continue</button>\n"
        "</form>\n"
        "</main>\n</body>\n</html>\n"
    )
