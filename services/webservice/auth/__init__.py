"""The identity layer of the admin web UI: ONE interface, one resolver, N backends.

The interface (what every backend implements, so Discord OAuth and proxy-SSO drop in later
without touching the gate, the routes or the pages):

* ``authenticate(request) -> Identity | None``
  Resolve the *current* request into an identity, or ``None``. This is the per-request call: the
  backend reads the signed session, re-resolves the principal against its live configuration and
  returns it. It never mutates the session and it never trusts a role stored in the cookie.
* ``role_names_for(request) -> set[str]``
  The role NAMES the request holds -- what :func:`services.webservice.permissions.role_names_for`
  installs as the process's single role resolver. The default implementation is
  ``authenticate(request).roles``, so a backend only overrides it if it has a cheaper answer.
* ``verify_credentials(request, username, password) -> Identity | None``
  Only for the backends that take a username and password (the local backend and break-glass);
  such a backend sets ``supports_password = True``. An OAuth or proxy backend does not implement
  it and the login route never calls it on one.

WHERE THE IDENTITY LIVES. The signed session stores a *reference* only --
``{"backend": ..., "subject": ...}`` (:data:`SESSION_IDENTITY_KEY`) -- and the roles are resolved
from the backend's live configuration on every request. Storing the role set in the cookie would
make authorization a function of a value the client holds until it expires: a user removed from
``auth.local.users`` would keep every capability until their session aged out. Resolution is per
request for the same reason the bot object is (``ServiceRegistry.get(BotService).bot`` can be
``None`` early and after a takeover): the answer is configuration, not a snapshot.

WHAT MUST NOT BE REUSED (verified in the phase-1 review, §D):

* :func:`core.utils.discord.check_roles` returns ``False`` for every headless member -- its first
  guard is ``if not member or not isinstance(member, discord.Member)`` and ``DummyMember`` is a
  plain class -- so it would refuse every local login.
* :func:`core.utils.discord.app_has_role` indexes ``client.roles[role]`` (``DummyBot.roles`` has
  no ``"DCS"`` key) and its owner bypass needs an interaction. Neither exists in a web request.
* ``DummyGuild`` (relative ``config/services/bot.yaml`` path) -- read through ``node.config_dir``
  instead, so a request can never see a different ``bot.yaml`` than the running process.
* ``utils.set_password``/``get_password`` (pickle-based ``config/.secret/``): C2 keeps the local
  credentials in the schema-validated ``services/webservice.yaml`` and the pickle store out of the
  login path. See the lead's decision in the card.
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Iterable

from starlette.requests import Request

from .. import permissions
# the scope rule LIVES in ``core/utils/discord.py``; importing it here (as this package does) reaches
# the bot's ``core`` with it — deliberate: the console runs in the bot's OWN environment (the same
# venv) and CONSUMES the one shared rule instead of keeping a second copy. The direction is pinned
# by ``tests/test_layering_direction.py``.
from ..scope import CLUSTER_ROLES, Scope

__all__ = [
    "SESSION_IDENTITY_KEY",
    "FALLBACK_DISPLAY_NAME",
    "AVATAR_SCHEMES",
    "safe_avatar_url",
    "Identity",
    "AuthBackend",
    "AuthManager",
    "install_auth",
    "session_identity_ref",
    "set_session_identity",
    "clear_session_identity",
]

log = logging.getLogger(__name__)

#: session key holding the identity *reference* (backend + subject); never the role set
SESSION_IDENTITY_KEY = "webui_identity"

_STATE_ATTR = "webui_auth"

#: the label for a RESOLVED identity that carries no display name. Never the subject: a Discord
#: subject is a numeric id, and the id is precisely what the console stopped showing. Never empty
#: either — a chip with a blank label reads as a broken page.
FALLBACK_DISPLAY_NAME = "Signed in"

#: the only schemes an avatar URL may carry. The URL ends up in an ``img src``, so it is treated
#: as untrusted input: anything that is not an ABSOLUTE http(s) URL (``javascript:``, ``data:``,
#: a relative path, a protocol-relative ``//host/...``) is refused and the initials chip is used.
AVATAR_SCHEMES = ("http://", "https://")


def safe_avatar_url(url) -> str:
    """The avatar URL to render, or ``""`` when it may not be rendered.

    ONE owner for the rule, called from both sides of the trip: the backend uses it when it builds
    the identity, and the page uses it again before it renders (the URL is user-influenced data
    reaching an ``img`` attribute, and a second call costs nothing).

    A URL with whitespace in it is not a URL, and it would need quoting gymnastics in an attribute
    — refused. Quotes are NOT stripped here: Jinja's autoescape (which is on) is what makes a
    hostile-but-http(s) URL harmless in the attribute, and that behaviour is pinned by a test
    rather than papered over by this check.
    """
    text = str(url or "").strip()
    if not text or not text.startswith(AVATAR_SCHEMES):
        return ""
    if any(character.isspace() for character in text):
        return ""
    return text


# ------------------------------------------------------------------------------- the identity

@dataclass(frozen=True, slots=True)
class Identity:
    """Who a request is, as far as authorization is concerned.

    ``subject`` is the backend's stable name for the principal (a username for the local backend,
    a Discord id once the OAuth backend exists) and is what the session stores. ``roles`` are the
    canonical role NAMES of the bot's role model -- the same names the capability map in
    :mod:`services.webservice.permissions` is declared against.

    ``display_name``/``avatar_url`` are PRESENTATION only, and they are deliberately not part of
    the session reference: the cookie stays ``{backend, subject}`` (a name in a cookie goes stale on
    a rename and puts user-controlled text in a signed value the client holds), and the backend
    re-resolves them from its live source on every request, exactly like the roles. Both carry
    defaults so every construction written before them (and every test double) keeps working.

    ``scope`` is the PER-SERVER dimension of the same fact: which servers this identity may see and
    act on (``managed_by``, spec §10). Like the roles it is re-resolved per request and never stored
    in the cookie; unlike the roles it may be "unscoped" (Admin, break-glass, an undeclared local
    account). Its default is the FAIL-CLOSED scope, so a backend that forgets to state one grants
    the narrower view rather than the wider — the safe direction for a field that widens a view.
    """
    subject: str
    backend: str
    roles: frozenset[str]
    display_name: str = ""
    avatar_url: str = ""
    scope: Scope = field(default_factory=Scope.unresolved)

    @property
    def shown_name(self) -> str:
        """The label for this identity: the display name, else :data:`FALLBACK_DISPLAY_NAME`.

        Never the subject, and never empty.
        """
        return str(self.display_name or "").strip() or FALLBACK_DISPLAY_NAME



def session_identity_ref(request: Request) -> dict | None:
    """The stored identity reference, or ``None``. Deliberately carries no roles."""
    ref = request.session.get(SESSION_IDENTITY_KEY)
    return ref if isinstance(ref, dict) else None


def set_session_identity(request: Request, identity: Identity) -> None:
    """Record *who* signed in. Roles are resolved from configuration on every later request."""
    request.session[SESSION_IDENTITY_KEY] = {"backend": identity.backend,
                                             "subject": identity.subject}


def clear_session_identity(request: Request) -> None:
    request.session.pop(SESSION_IDENTITY_KEY, None)


# ------------------------------------------------------------------------------- the backend

class AuthBackend(abc.ABC):
    """The one signature every identity backend implements."""

    #: short, stable backend name; also what the session reference stores and the log names
    name: ClassVar[str] = ""

    #: True when this backend can check a username/password pair (see ``verify_credentials``)
    supports_password: ClassVar[bool] = False

    @abc.abstractmethod
    def authenticate(self, request: Request) -> Identity | None:
        """The identity this request holds, or ``None``. Logs the refusal REASON; the reason is
        never returned to the page."""

    def role_names_for(self, request: Request) -> set[str]:
        """The role names of the identity this request holds (empty when anonymous)."""
        identity = self.authenticate(request)
        return set(identity.roles) if identity is not None else set()

    def verify_credentials(self, request: Request, username: str, password: str) -> Identity | None:
        """Verify a username/password pair. Only meaningful when ``supports_password`` is True."""
        raise NotImplementedError(f"backend '{self.name}' does not accept a username and password")


# ------------------------------------------------------------------------------- the manager

class AuthManager:
    """The backends enabled on this application, and the one resolver they feed.

    Installed per application object (never at import) because ``WebService`` rebuilds its app
    after a takeover -- see :func:`install_auth`.
    """

    def __init__(self, backends: Iterable[AuthBackend] = ()):
        self.backends = tuple(backends)

    @property
    def password_backends(self) -> tuple[AuthBackend, ...]:
        return tuple(backend for backend in self.backends if backend.supports_password)

    def backend(self, name: str) -> AuthBackend | None:
        for backend in self.backends:
            if backend.name == name:
                return backend
        return None

    # ------------------------------------------------------------------ per request

    def authenticate(self, request: Request) -> Identity | None:
        """Resolve the request's session into an identity, logging WHY it was refused.

        A refusal here is EXPECTED whenever the request carries no session or a session this
        installation cannot resolve (an expired cookie, a backend that has been disabled). That is
        not an incident and it happens on every poll of an open dashboard, so it is recorded at
        DEBUG — the diagnosis is one line at a level an operator raises deliberately, never a
        WARNING or an INFO appended per request.
        """
        ref = session_identity_ref(request)
        if ref is None:
            log.debug("auth: refusing %s %s - no session", request.method, request.url.path)
            return None
        name = str(ref.get("backend") or "")
        backend = self.backend(name)
        if backend is None:
            log.debug("auth: refusing %s %s - the session names backend '%s', which is not "
                      "enabled on this installation (an invalid/expired session for this deploy)",
                      request.method, request.url.path, name)
            return None
        return backend.authenticate(request)

    def role_names_for(self, request: Request) -> set[str]:
        """THE role resolver. Installed through ``permissions.set_role_resolver``.

        Also records — on the request — whether an identity was actually RESOLVED, which is what
        lets the access gate tell an expected (anonymous/unresolvable) refusal from a signed-in
        caller lacking a capability. This module is the one place that knows the difference.
        """
        identity = self.authenticate(request)
        permissions.set_request_authenticated(request, identity is not None)
        return set(identity.roles) if identity is not None else set()

    def scope_for(self, request: Request) -> Scope:
        """THE scope resolver. Installed through ``permissions.set_scope_resolver``.

        THREE rules, in this order (the order IS the precedence, highest first):

        * an identity that could not be resolved is :meth:`Scope.unresolved` — FAIL CLOSED (the bot
          is restarting, the guild is unreachable, the member cache is empty). A scoped-to-nothing
          view is the only safe answer; a scoped-to-everything one is a leak;
        * an explicit DECLARATION wins over everything else, INCLUDING the cluster-role bypass
          below: a local account's ``auth.local.users[].scope`` is the operator's instruction, so it
          is honoured even when the account also holds a cluster role (a declaration can only ever
          shrink a cluster role's view, never widen a non-cluster one);
        * otherwise a CLUSTER role — ``Admin`` or ``DCS Admin`` (:data:`..scope.CLUSTER_ROLES`) — is
          unscoped, whatever it declared implicitly: the two cluster roles keep the whole cluster,
          so a scoped DCS Admin does not lose the untagged servers it has always administered. A
          Discord cluster role carries no declaration — its scope is derived from member roles — so
          it falls through to here. The bypass lives HERE, once, so no backend can forget it and no
          page can re-invent it.
        """
        identity = self.authenticate(request)
        if identity is None:
            return Scope.unresolved()
        if identity.scope.declared:
            return identity.scope
        if not set(CLUSTER_ROLES).isdisjoint(identity.roles):
            return Scope.everything()
        return identity.scope

    def manages_any_server(self, request: Request) -> bool:
        """THE manager resolver. Installed through ``permissions.set_manager_resolver``.

        The third half of an identity, beside its roles and its scope, and answered by the SAME
        scope value the pages render from (``self.scope_for``) — so the console's access rule and
        the hoster view cannot disagree about who this is. Fail CLOSED at every step:

        * no resolved identity -> ``False`` (an anonymous request is not a manager);
        * ``Admin`` -> ``False``: its scope is unscoped by the rule in :meth:`scope_for`, and it
          reaches the console through its ROLE. Being unscoped is never a manager's licence (a
          roleless local account is unscoped too, and must not be let in);
        * a scope that cannot be resolved -> ``False`` (:func:`services.webservice.scope.
          manages_any_server` answers ``False`` for it) — a bot that is restarting, a guild that is
          unreachable or an empty member cache refuses the manager rather than showing the cluster.

        What is looked at, for a non-Admin: the servers THIS console serves, read through the same
        seam the pages read (:func:`services.webservice.readmodels.console_source`) — a Discord
        member's tokens are every role they hold, so only a server that declares one of them in
        ``managed_by`` makes them a manager of it. A local account is in a different position: it
        cannot be compared against a Discord member at all, and its DECLARED scope is the fact
        (``Scope.declared``), so it needs no server to match.
        """
        from .. import scope as scope_api

        identity = self.authenticate(request)
        if identity is None:
            return False
        scope = self.scope_for(request)
        return scope_api.manages_any_server(scope, _console_servers(request))

    # ------------------------------------------------------------------ credentials

    def login(self, request: Request, username: str, password: str) -> Identity | None:
        """Try every password backend with the same credentials.

        Each backend logs its own refusal reason (unknown user / bad password); the caller shows
        the visitor one generic message.
        """
        candidates = self.password_backends
        if not candidates:
            log.warning("auth: a login was attempted but no username/password backend is enabled "
                        "(auth.local.enabled and auth.breakglass.enabled are both false)")
            return None
        for backend in candidates:
            identity = backend.verify_credentials(request, username or "", password or "")
            if identity is not None:
                return identity
        return None

    def describe(self) -> dict:
        return {"backends": [backend.name for backend in self.backends],
                "password_backends": [backend.name for backend in self.password_backends]}


# ------------------------------------------------------------------------------- installation

def install_auth(app, node=None, config: dict | None = None, *,
                 config_dir: str | Path | None = None) -> AuthManager:
    """Build the enabled backends from config, install them, and register the role resolver.

    Idempotent per application object, and called from ``install_shell`` -- which runs from
    ``WebService.start()`` on *both* app-creation paths, because the app object is replaced at
    runtime and anything installed in ``__init__`` only would be gone after a master/agent switch.

    Refusals here are startup refusals: an unknown role name, a missing password hash or a
    malformed hash raise ``ValueError`` rather than producing an installation that silently grants
    something nobody declared. A refused install leaves no half-configured identity layer behind
    (the state attribute is set only at the end), so the access gate keeps failing closed.
    """
    existing = getattr(app.state, _STATE_ATTR, None)
    if existing is not None:
        log.debug("Admin web UI auth already installed on this application, reusing it.")
        return existing

    auth_config = (config or {}).get("auth") or {}
    resolved_config_dir = Path(config_dir
                               or getattr(node, "config_dir", None) or "config")
    backends: list[AuthBackend] = []

    # imported here: the backends import this module for the interface, so a module-level import
    # would be circular (``auth/__init__.py`` defines the interface before importing them below)
    from .breakglass import breakglass_backend
    from .discord_oauth import discord_backend
    from .local import local_backend

    local = local_backend(auth_config.get("local"), config_dir=resolved_config_dir)
    if local is not None:
        backends.append(local)
    breakglass = breakglass_backend(auth_config.get("breakglass"))
    if breakglass is not None:
        backends.append(breakglass)
    # the Discord path reads the SAME auth.public_base_url the redirect URI is built from (never a
    # request header) and the SAME config dir the local backend validates role names through
    discord = discord_backend(auth_config.get("discord"), config_dir=resolved_config_dir,
                              public_base_url=auth_config.get("public_base_url"))
    if discord is not None:
        backends.append(discord)

    manager = AuthManager(backends)
    permissions.set_role_resolver(manager.role_names_for)
    # the second resolver of the same shape, installed at the same moment: which SERVERS this
    # identity may see and act on (per request, never cached, fail closed — see
    # permissions.scope_for / AuthManager.scope_for)
    permissions.set_scope_resolver(manager.scope_for)
    # and the third: whether the SCOPE is what makes this identity a manager of a server, which is
    # the console's access rule's third kind of identity (permissions.manages_console /
    # AuthManager.manages_any_server). Installed here, with the other two, so one place owns the
    # whole identity — and so no page can answer the question for itself.
    permissions.set_manager_resolver(manager.manages_any_server)
    setattr(app.state, _STATE_ATTR, manager)

    log.info("Admin web UI auth: backend(s) %s, password backend(s) %s, config dir '%s', "
             "public base url '%s'.", ", ".join(manager.describe()["backends"]) or "-",
             ", ".join(manager.describe()["password_backends"]) or "-", resolved_config_dir,
             auth_config.get("public_base_url") or "-")
    _audit_scope_declarations(local.declared_scopes() if local is not None else {})
    if not backends:
        # CRITICAL, not a warning: the login page has no door at all, so nobody can sign in. This is
        # a legitimate state while an installation is being configured (and the console also serves
        # API traffic, so it must NOT refuse to start) — but it must be impossible to miss in the
        # log, which is the only place an operator will look.
        log.critical("Admin web UI auth: NO sign-in method is enabled - the login page has no door "
                     "and every non-public page answers 403. Enable auth.local.enabled (username "
                     "and password) or auth.discord.enabled in services/webservice.yaml.")
    return manager


def _audit_scope_declarations(declared_by_user: dict[str, tuple[str, ...]]) -> None:
    """The two startup WARNINGs for the per-server scope (spec §10.6), each logged ONCE.

    Both compare a free-text ``managed_by`` world against the live installation, and both describe a
    condition a person can still fix (a typo in ``webservice.yaml``, a renamed Discord role that has
    silently become an access change). Only a RUNNING bot has the two inputs — the servers' declared
    ``managed_by`` values and the guild's role ids/names — so this is a diagnostic, not a refusal:
    with no bot object (early start, a takeover, a headless install) it says so at DEBUG and the
    console installs anyway. Safe to call again after a server-config reload: the audit re-reports
    its findings and the log stays quiet (``scope.audit_*`` logs one line per condition).
    """
    from .. import scope as scope_api

    bot = _current_bot()
    if bot is None:
        log.debug("Admin web UI scope: no bot object, so the scope declarations and the servers' "
                  "managed_by values are not audited at startup (this is a diagnostic, not a "
                  "refusal: the console installs either way).")
        return
    by_server, guild_roles = _live_scope_inputs(scope_api, bot)
    if by_server is None:
        return
    server_values = {value for _, values in by_server for value in values}
    scope_api.audit_local_scopes(declared_by_user, server_values)
    scope_api.audit_server_managed_by(by_server, guild_roles)


def _live_scope_inputs(scope_api, bot):
    """``(servers, guild roles)`` from the running bot, or ``(None, None)``.

    A STARTUP DIAGNOSTIC never refuses an install: this is called from ``install_auth``, so anything
    the bot object cannot supply (an early start, a takeover, a test double that deliberately forbids
    an attribute, a headless install) ends in ``(None, None)`` and a DEBUG line rather than a
    half-installed identity layer.
    """
    try:
        return scope_api.gather_live_scope(bot)
    except Exception as ex:
        log.debug("Admin web UI scope: the bot object could not supply the live scope inputs (%s), "
                  "so the servers' managed_by values are not audited at startup.",
                  type(ex).__name__)
        return None, None


def _console_servers(request) -> tuple:
    """The servers this console serves, through the SAME seam the pages read.

    ``readmodels.console_source`` is the one place that decides where a console's data comes from
    (the application's provider seam, else the live in-process source), so the manager decision is
    made about the very servers the pages would render — a test that hands the console a stub
    cluster gets a manager decision about that cluster, and an installation that pins the console to
    a node gets one about that node.

    Tolerant by construction: the source may be an ``EmptySource`` (no bot yet), and a request
    without an application at all (a unit test driving the manager directly) has no seam — both
    yield ``()``, which answers "not a manager" for a Discord-derived scope. That is the FAIL CLOSED
    direction: an unresolvable cluster never promotes anybody.
    """
    from .. import readmodels

    state = getattr(getattr(request, "app", None), "state", None)
    source = readmodels.console_source(state)
    return tuple(getattr(source, "servers", ()) or ())


def _current_bot():
    """``ServiceRegistry.get(BotService).bot``, or ``None``.

    The import is inside the function (like ``discord_oauth.current_bot``, which this deliberately
    reuses rather than re-deriving) because it resolves a RUNTIME object. This module DOES reach the
    bot's ``core`` at import time — ``..scope`` imports the shared ``managed_by`` rule from
    ``core/utils/discord.py`` — which is deliberate, not an accident to be undone.
    """
    try:
        from .discord_oauth import current_bot
        return current_bot()
    except Exception as ex:  # pragma: no cover - environment-dependent (no core / no service)
        log.debug("Admin web UI scope: the bot object is unavailable here (%s).", type(ex).__name__)
        return None
