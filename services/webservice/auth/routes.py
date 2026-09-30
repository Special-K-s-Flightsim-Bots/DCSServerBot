"""``/auth/login`` and ``/auth/logout`` — absolute constant paths, declared PUBLIC.

THE PATHS ARE CONSTANTS, not a prefix from config and not derived from a request. The review's
§C.1 makes the point for the OAuth callback and it holds for the whole ``/auth`` area: a route
registered inside a router whose prefix comes from a user-editable config file silently moves the
moment somebody edits that file, while the developer-portal redirect URI and the session cookie's
``Path`` still point at the old path. :data:`LOGIN_PATH` and :data:`LOGOUT_PATH` are what the form
posts to, what the logout redirect is built from, and what the tests assert against the registered
``route.path``.

WHY THE LOGIN POST CARRIES A CSRF TOKEN. It is a cookie-authenticated write like any other; without
the token an attacker's page could submit a login (a "login CSRF") and bind the victim's browser to
the attacker's account. The GET mints the token into the session, which is why the login form is
one of the few pages allowed to set a cookie on first render.

THE LOGIN PAGE IS PUBLIC. It has to be — it is the door. It is declared ``PUBLIC`` explicitly at
registration (:func:`capabilities`), because with the app-level deny-by-default gate an undeclared
route is refused, so a forgotten declaration would lock the login page out rather than open it.

THE REFUSAL MESSAGE IS GENERIC. The reason (no session / unknown user / bad password) is logged by
the backend and never rendered, so the page cannot be used to enumerate accounts.

THE LOGIN STARTS A FRESH SESSION. ``request.session.clear()`` runs immediately before the identity
reference is written — the same order the Discord callback uses. Without it the value the browser
carried in (a planted or leaked signed cookie on a shared machine, a phishing handoff) would be the
value that is authenticated after the victim signs in: session fixation.
"""
from __future__ import annotations

import logging
import unicodedata

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import permissions, session
from . import discord_oauth

__all__ = ["LOGIN_PATH", "LOGOUT_PATH", "LOGIN_TEMPLATE", "FAILED_LOGIN_MESSAGE",
           "PASSWORD_DOOR_DISABLED_MESSAGE", "LoginDoors", "doors_for_manager", "login_doors",
           "login_redirect", "capabilities", "add_routes", "discord_link", "render_login_page"]

log = logging.getLogger(__name__)

#: absolute, constant, shared by the route, the form and the tests (never a config prefix)
LOGIN_PATH = "/auth/login"
LOGOUT_PATH = "/auth/logout"

#: the shell template that renders the form (read by the shell's ONE Jinja environment)
LOGIN_TEMPLATE = "login.html"

#: the one sentence a refused login shows. It names no mechanism and no reason.
FAILED_LOGIN_MESSAGE = "Sign-in failed. Check your username and password and try again."

#: what a direct POST to the password door is refused with when no username/password backend is
#: enabled. A REFUSAL, not a login attempt: the door is off, so the request never reaches a
#: credential check. (It flows through the ONE refusal handler, so a non-browser caller keeps the
#: JSON shape and a browser gets the door it can actually use.)
PASSWORD_DOOR_DISABLED_MESSAGE = ("This console does not accept a username and password sign-in "
                                  "(no username/password backend is enabled).")

#: where the site root is (logout lands here, per the card: never back on the login page -- except
#: in a Discord-only install, where the root would bounce the visitor straight back into the flow;
#: see the logout route)
SITE_ROOT = "/"

_STATE_ATTR = "webui_auth"


@dataclass(frozen=True, slots=True)
class LoginDoors:
    """WHICH sign-in methods this installation offers — the ONE value the page renders from.

    Built from the ENABLED BACKENDS (:func:`doors_for_manager`), never from a second reading of the
    config keys: the backends ARE the resolved configuration (``auth.local.enabled``,
    ``auth.breakglass.enabled``, ``auth.discord.enabled``) and they are also what the server
    enforces with, so the page and the server cannot disagree about which doors exist.

    ``password`` covers the local backend AND break-glass: both accept a username and password on
    the SAME ``/auth/login`` route, so a form the server would accept must be offered.
    """

    discord: bool = False
    password: bool = False

    @property
    def count(self) -> int:
        """How many doors exist (0, 1 or 2). The template uses it for the single-door layout."""
        return int(self.discord) + int(self.password)

    @property
    def both(self) -> bool:
        """Whether BOTH doors exist — the only case that carries the recommended/fallback framing."""
        return self.discord and self.password

    @property
    def none(self) -> bool:
        """Whether no door exists at all (a legitimate mid-configuration state, never a crash)."""
        return not (self.discord or self.password)

    @property
    def status_text(self) -> str:
        """The top bar's pill: a sentence that is TRUE of this configuration.

        It must never claim Discord is enabled when it is not, so it is derived from the same two
        booleans the layout is derived from.
        """
        if self.both:
            return "Discord and local sign-in enabled"
        if self.discord:
            return "Discord sign-in enabled"
        if self.password:
            return "Local sign-in enabled"
        return "No sign-in method enabled"


def doors_for_manager(manager) -> LoginDoors:
    """The doors the identity layer actually has (defensive: a manager without backends has none)."""
    if manager is None:
        return LoginDoors()
    lookup = getattr(manager, "backend", None)
    discord = bool(callable(lookup) and lookup(discord_oauth.BACKEND_NAME) is not None)
    password = bool(tuple(getattr(manager, "password_backends", ()) or ()))
    return LoginDoors(discord=discord, password=password)


def login_doors(source) -> LoginDoors:
    """The doors of the application behind *source* (a ``Request`` or an ``app.state``).

    ONE value, one owner: the login page renders from it, the login POST is refused against it and
    a refused page decides which door to send an anonymous browser to from it.
    """
    state = getattr(getattr(source, "app", None), "state", None) or source
    return doors_for_manager(getattr(state, _STATE_ATTR, None))


def login_redirect(request: Request) -> RedirectResponse | None:
    """Where an anonymous browser is sent when a guarded PAGE refused it, or ``None``.

    ``None`` means no identity layer is installed, so there is no door to send anyone to (the
    caller renders a page instead of redirecting into a 404).

    Two destinations, and the choice is the whole point of the doors value:

    * Discord is the ONLY door (``auth.discord.enabled: true``, no username/password backend): the
      visitor is sent STRAIGHT INTO THE SIGN-IN FLOW, through the existing start route, carrying the
      path they were headed for. The start route is what mints the single-use ``state`` and its
      session cookie — a hand-built authorize URL here would be a second, divergent path. Nobody
      gets a page with a button they would have to click;
    * otherwise: the login page, carrying the path (local-only shows the form; with both doors the
      visitor picks — the console never auto-picks a door for them).
    """
    manager = getattr(getattr(request.app, "state", None), _STATE_ATTR, None)
    if manager is None:
        return None
    doors = doors_for_manager(manager)
    # the path only: the query string is attacker-controlled and is never reflected
    path = request.scope.get("path", "/") or "/"
    landing = quote(path, safe="/")
    if doors.discord and not doors.password:
        return RedirectResponse(f"{discord_oauth.START_PATH}?next={landing}", status_code=302)
    return RedirectResponse(f"{LOGIN_PATH}?next={landing}", status_code=303)



def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers (see the registrar).

    The two Discord OAuth paths are declared here, in the shell's own declaration, because they are
    part of the same door: the start path and the callback are PUBLIC by decision (the gate is deny
    by default, so a forgotten declaration would lock the login flow out rather than open it).
    """
    declared = {LOGIN_PATH: permissions.PUBLIC, LOGOUT_PATH: permissions.PUBLIC}
    declared.update(discord_oauth.capabilities())
    return declared


def add_routes(router: APIRouter) -> APIRouter:
    """Add the auth routes to *router* (the shell's own router) and return it.

    The routes are added to the shell's router **directly**, never by ``include_router`` on a nested
    one. On the pinned FastAPI a nested include appends an ``_IncludedRouter`` wrapper that carries
    no ``.path``, so the shell's own routes would disappear from the registrar's enumeration -- the
    one source of truth the CSRF pin and the nav/route tests read. Flat is what keeps them visible.
    """

    @router.get(LOGIN_PATH, response_class=HTMLResponse)
    async def login_form(request: Request, next: str = SITE_ROOT):
        return _render_login(request, next_url=_safe_next(next))

    @router.post(LOGIN_PATH, response_class=HTMLResponse, dependencies=[Depends(session.csrf_protect)])
    async def login_submit(request: Request):
        manager = _manager(request)
        # THE SERVER ENFORCES THE SAME DOORS THE PAGE RENDERS. A direct POST while no
        # username/password backend is enabled is REFUSED before any credential is looked at: the
        # page never offers a door the server would refuse, and the server never accepts one the
        # configuration turned off.
        if not login_doors(request).password:
            log.warning("auth: refusing %s %s - no username/password backend is enabled "
                        "(auth.local.enabled is not true and break-glass is off)",
                        request.method, request.url.path)
            raise HTTPException(status_code=403, detail=PASSWORD_DOOR_DISABLED_MESSAGE)

        form = await request.form()
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")
        next_url = _safe_next(form.get("next"))

        identity = manager.login(request, username, password)
        if identity is None:
            # the reason is already in the log, twice: the backend names it, and nothing here
            # echoes it back
            log.info("auth: login refused for %s %s", request.method, request.url.path)
            return _render_login(request, next_url=next_url, error=FAILED_LOGIN_MESSAGE,
                                 status_code=401)

        from . import set_session_identity
        # SESSION FIXATION: nothing the browser carried BEFORE the login may survive into the
        # authenticated session. Without the clear, a signed cookie an attacker planted (shared
        # machine, a handoff) is authenticated by the victim's login with the very same value. The
        # landing target was read above, before the clear. Same order as the OAuth callback
        # (``discord_oauth.discord_callback``): clear, then write the identity reference.
        request.session.clear()
        set_session_identity(request, identity)
        log.info("auth: %r signed in via the %s backend", identity.subject, identity.backend)
        return RedirectResponse(next_url, status_code=303)

    @router.post(LOGOUT_PATH, dependencies=[Depends(session.csrf_protect)])
    async def logout(request: Request):
        identity = _manager(request).authenticate(request)
        doors = login_doors(request)
        from . import clear_session_identity
        clear_session_identity(request)
        request.session.clear()

        # WHERE SIGNING OUT LANDS. Normally the site root. In a DISCORD-ONLY install the root is a
        # guarded page whose anonymous visitor is bounced straight back into the OAuth flow (see
        # login_redirect) — signing out there would look broken and be unrepeatable (you would be
        # signed in again before the page finished). So there the destination is the door page: it
        # is PUBLIC, it never bounces, and it offers the door again.
        destination = LOGIN_PATH if (doors.discord and not doors.password) else SITE_ROOT
        response = RedirectResponse(destination, status_code=303)
        # delete the cookie the LOGIN actually set: the effective name comes from the installed
        # middleware's settings, never from a name of our own (session.SESSION_COOKIE is only the
        # default and a deployment may have changed it)
        response.delete_cookie(_cookie_name(request), path="/")
        log.info("auth: %r signed out", identity.subject if identity else "<anonymous>")
        return response

    # the second door: the Discord OAuth start and callback routes (PUBLIC, declared above)
    discord_oauth.add_routes(router)
    return router


def discord_link(request: Request, next_url: str) -> str | None:
    """The login page's "Sign in with Discord" target, or ``None`` when that backend is disabled.

    The control is rendered ONLY when the backend is enabled: a button that leads to a 404 page is
    worse than no button, and the local username/password form stays exactly as it was (it is the
    fallback for a headless/DummyBot install and for the day Discord is the thing that broke).
    """
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None or manager.backend(discord_oauth.BACKEND_NAME) is None:
        return None
    return f"{discord_oauth.START_PATH}?next={quote(next_url, safe='/')}"


# --------------------------------------------------------------------------- helpers

def _cookie_name(request: Request) -> str:
    """The cookie name the session middleware was installed with."""
    settings = getattr(request.app.state, "webui_session_settings", None) or {}
    return str(settings.get("cookie_name") or session.SESSION_COOKIE)


def _manager(request: Request):
    manager = getattr(request.app.state, _STATE_ATTR, None)
    if manager is None:
        # the routes are registered with the shell, so this cannot happen on an installed app;
        # fail closed and loudly rather than raising AttributeError into a 500
        log.error("auth: no identity layer on the application, refusing %s %s",
                  request.method, request.url.path)
        raise HTTPException(status_code=503, detail="The admin web UI identity layer is not "
                                                    "installed.")
    return manager


def _safe_next(value) -> str:
    """A local path or the site root. Never an absolute URL, never protocol-relative (``//``) and
    never a value carrying control characters.

    The value arrives from a query string or a form, so it is attacker-controlled: an unchecked
    ``next`` is an open redirect sitting on a page users reach right after typing a password.

    CONTROL CHARACTERS ARE REMOVED, then the result is re-validated -- the same rule the view layer
    applies to every URL-shaped input (``readmodels.views._clean``): a control character (Unicode
    category ``C*``: the C0/C1 sets, DEL, the invisible formatting characters) is part of no path,
    and this value is echoed into a ``Location`` header and into the login form's hidden field. The
    strip happens BEFORE the ``//`` check, so a CR/LF cannot be used to smuggle a protocol-relative
    target past it. Escaping is left to the response layer (Starlette percent-encodes ``Location``);
    this is about the value we carry, not about encoding it here.
    """
    if not isinstance(value, str) or not value:
        return SITE_ROOT
    cleaned = "".join(char for char in value
                      if not unicodedata.category(char).startswith("C"))
    if not cleaned.startswith("/") or cleaned.startswith("//") or "\\" in cleaned:
        return SITE_ROOT
    return cleaned


def render_login_page(request: Request, *, next_url: str = SITE_ROOT, error: str | None = None,
                      status_code: int = 200) -> HTMLResponse:
    """The console's REAL sign-in page, rendered through its one template and one doors value.

    PUBLIC on purpose: the OAuth routes render their refusal/cancel page through THIS function
    (``discord_oauth.error_page``), so the cancel page shows the same chrome, the same status pill
    and the same config-driven DOORS as ``GET /auth/login``. A second, hand-built page is how the
    cancel page came to advertise a username/password door an installation can have turned off.

    ``error`` is a GENERIC sentence; the reason a sign-in was refused belongs in the log.
    """
    environment = getattr(request.app.state, "webui_templates", None)
    if environment is None:  # pragma: no cover - installed by the shell
        raise HTTPException(status_code=503, detail="The admin web UI templates are not installed.")
    # THE DOORS are what the page is built from, and they are read from the SAME backends the
    # server enforces against (see LoginDoors).
    doors = login_doors(request)
    # The token is minted ONLY where a form will render it: a page that renders no form must not set
    # a session cookie (and with the password door off there is no form at all). The token field
    # still exists in the template, so StrictUndefined stays satisfied.
    token = session.get_csrf_token(request) if doors.password else ""
    html = environment.get_template(LOGIN_TEMPLATE).render(
        login_path=LOGIN_PATH, csrf_field=session.CSRF_FIELD, csrf_token=token,
        next_url=next_url, error=error, doors=doors,
        discord_url=discord_link(request, next_url) if doors.discord else None,
        discord_label=discord_oauth.CONTROL_LABEL, discord_scope=discord_oauth.SCOPE)
    return HTMLResponse(html, status_code=status_code)


def _render_login(request: Request, *, next_url: str = SITE_ROOT, error: str | None = None,
                  status_code: int = 200) -> HTMLResponse:
    """The login route's own renderer (one implementation — see :func:`render_login_page`)."""
    return render_login_page(request, next_url=next_url, error=error, status_code=status_code)
