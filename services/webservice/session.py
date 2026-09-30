"""Session cookie and CSRF handling for the admin web UI.

Cookie policy comes from **explicit configuration** (``services/webservice.yaml``, key
``session``) and never from a request-derived value: the redirect URI and the public base URL
have to be configured values, and this module deliberately knows nothing about them. What it
does own is the part that is easy to get backwards:

* ``same_site`` defaults to ``lax`` and that default is load-bearing — the OAuth callback in C2
  is a cross-site top-level GET, and ``strict`` does not send the cookie on it, so every login
  would read an empty session and fail the nonce check (symptom: "first attempt fails, retry
  works"). Setting it to something else is allowed but warned about, because the failure only
  appears behind a real login.
* ``https_only`` is never reached by accident. An explicit boolean wins; with nothing (or an empty
  value) configured it is DERIVED from the service's own bind address — a loopback-only console may
  send the cookie over plain HTTP (a Secure cookie cannot be returned over plain HTTP, so forcing it
  there breaks the login of the one deployment that cannot leak), anything reachable from the
  network is Secure unless an operator writes ``false`` down, which is logged as a warning. Nothing
  derives it from the scheme of a request.
* ``max_age`` bounds the session, which is also the compensating control for exempting signed-in
  callers from any anonymous-traffic limit elsewhere.

CSRF: a token is minted into the session and **accepted, not spent**. A page renders one token
and every action on that page posts it, so invalidating it on first use turns the second action
on the same page into a bare 403 for a normal user. Exactly one token per session is kept, minted
on first need and returned thereafter — there is no rotation and therefore no ring (see
:func:`get_csrf_token`).

No CSRF *middleware* is installed: a blanket "unsafe method needs a token" check would refuse
the RestAPI plugin's token-authenticated POSTs (they carry a Bearer token, no cookie and no CSRF
token), i.e. it would change a working surface. The check is a dependency (:func:`csrf_protect`)
that a write route attaches, so it can be audited route by route.
"""
from __future__ import annotations

import ipaddress
import logging
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

__all__ = ["SESSION_COOKIE", "CSRF_FIELD", "CSRF_HEADER", "DEFAULT_LISTEN", "session_settings",
           "install_session_middleware", "get_csrf_token", "validate_csrf_token", "csrf_protect"]

log = logging.getLogger(__name__)

#: default cookie name; overridable per deployment (``session.cookie_name``)
SESSION_COOKIE = "dcssb_session"

#: form field and header a write may carry the CSRF token in
CSRF_FIELD = "_csrf_token"
CSRF_HEADER = "x-csrf-token"

_SESSION_KEY = "_csrf_tokens"
_ALLOWED_SAME_SITE = ("lax", "strict", "none")

#: the bind address the service assumes when none is configured (`service.py` reads
#: ``cfg.get('listen', DEFAULT_LISTEN)`, so a web UI whose config omits it is reachable on every
#: interface — which is what the https_only derivation below has to assume as well)
DEFAULT_LISTEN = "0.0.0.0"

#: "not configured", as distinct from a configured `False`. An EMPTY value (absent, ``None``, ``""``,
#: ``"   "``) means unset and is never read as "off".
_UNSET = object()

_TRUE_VALUES = ("true", "yes", "on", "1")
_FALSE_VALUES = ("false", "no", "off", "0")

DEFAULTS: dict = {
    "cookie_name": SESSION_COOKIE,
    "max_age": 24 * 60 * 60,
    "same_site": "lax",
    "secret": None,
}


def _listen_host(listen) -> str:
    """The host part of a configured bind address, without its (optional) port.

    ``listen`` is a bind address, so it is written in whichever form the deployment uses:
    ``127.0.0.1``, ``127.0.0.1:9876``, ``localhost:9876``, ``[::1]``, ``[::1]:9876``, ``::1``. The
    port is not part of the host, and an IPv6 literal is the one address shape that contains colons
    of its own — so a bare IPv6 address (more than one colon, no brackets) is returned untouched,
    and only a bracketed literal or a single-colon ``host:port`` is split.
    """
    raw = str(listen or "").strip()
    if not raw:
        return ""
    if raw.startswith("["):
        # bracketed IPv6 literal, optionally followed by :port — the closing bracket is the split
        return raw.split("]", 1)[0][1:]
    if raw.count(":") == 1:
        # exactly one colon: host:port (an IPv4 literal or a name, never a bare IPv6)
        return raw.partition(":")[0]
    return raw


def _is_loopback_listen(listen) -> bool:
    """Whether a configured bind address is loopback-only (nothing leaves the machine).

    Loopback is recognised in every spelling the config allows — the IP literals (``127.0.0.1``,
    ``::1``) and the name ``localhost`` — with or without a port; anything that names another
    interface (``0.0.0.0``, ``::``, a LAN address, an unknown hostname) is reachable and returns
    False. A port must never decide this: ``ip_address("127.0.0.1:9941")`` raises, and reading that
    as "reachable" is what put a Secure cookie on a plain-HTTP loopback console.
    """
    host = _listen_host(listen)
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # a hostname that is not "localhost" names some other interface - assume it is reachable
        return False


def _parse_bool(value, key: str) -> bool:
    """Parse a configured boolean, refusing nonsense instead of guessing.

    A string is *parsed* rather than truthiness-tested: ``bool("0")`` and ``bool("false")`` are both
    ``True``, so the naive form turns the secure direction on from the value that means "off" —
    the same trap as a form reading ``bool('0')``. An unknown word is a refusal, not a default.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_VALUES:
            return True
        if word in _FALSE_VALUES:
            return False
    raise ValueError(f"{key} must be a boolean, got {value!r} "
                     f"(accepted strings: {', '.join(_TRUE_VALUES + _FALSE_VALUES)})")


def _resolve_https_only(session_config: dict, config: dict) -> bool:
    """The effective ``Secure`` flag: explicit wins, unset is derived from the bind address.

    An empty value is *unset* (never "off"), so a deployment that removes the value — or writes an
    empty one — cannot silently downgrade the cookie; it falls back to the safe derivation. The
    derivation exists because neither fixed default is right for every install: ``True`` on a
    plain-HTTP loopback console breaks the login (browsers do not send a Secure cookie back over
    http), ``False`` on a network-reachable console ships the session in cleartext.
    """
    configured = session_config.get("https_only", _UNSET)
    if configured is None or (isinstance(configured, str) and not configured.strip()):
        configured = _UNSET
    if configured is not _UNSET:
        return _parse_bool(configured, "session.https_only")

    listen = str(config.get("listen") or "").strip() or DEFAULT_LISTEN
    # a loopback-only bind: the cookie cannot leave the machine, so plain HTTP is fine
    derived = not _is_loopback_listen(listen)
    if derived:
        # The derived-True case used to be silent, and the consequence is invisible from the outside:
        # the login POST succeeds (the cookie IS set) and the next request is refused, because a
        # browser does not send a Secure cookie back over plain HTTP. The two ways out are the two
        # the operator actually has — this service never terminates TLS itself.
        log.warning("Web UI session: this console listens on %s, which is reachable beyond loopback, "
                    "and no TLS is configured (session.https_only is unset) - so the session cookie "
                    "is derived Secure, and a browser will NOT return a Secure cookie over plain "
                    "HTTP: a login appears to succeed and the very next request is refused. This "
                    "service does not terminate TLS itself. Bind to 127.0.0.1, or terminate TLS in "
                    "front of the console and set session.https_only: true.", listen)
    return derived


def _warn_about_an_insecure_cookie(settings: dict, config: dict) -> None:
    """Name the exposure when the cookie is knowingly sent without ``Secure`` on a reachable bind."""
    listen = str(config.get("listen") or "").strip() or DEFAULT_LISTEN
    if settings["https_only"] or _is_loopback_listen(listen):
        return
    log.warning("Web UI session: session.https_only is off while this console listens on %s and is "
                "therefore reachable beyond loopback — the session cookie travels in cleartext and "
                "anyone on that network can replay it. Set session.https_only: true once the "
                "console is reached over TLS, or bind to 127.0.0.1.", listen)


def session_settings(config: dict | None) -> dict:
    """Resolve the effective session/cookie settings from the service config.

    An unset ``session.secret`` gets an ephemeral random key and a warning: sessions are then
    valid until the process restarts. That is a working install, not a silent one — a
    deployment that wants sessions to survive a restart has to configure a secret.

    A configured value always wins over a default; a *bad* configured value is refused loudly
    rather than silently replaced, because a cookie flag nobody can see is a security decision
    taken by accident. ``https_only`` is the one setting with no fixed default: an unset (or empty)
    value is derived from the service's bind address — see :func:`_resolve_https_only`.
    """
    configured = dict((config or {}).get("session") or {})
    settings = dict(DEFAULTS)
    settings.update({k: v for k, v in configured.items() if v is not None})
    service_config = config or {}
    settings["https_only"] = _resolve_https_only(configured, service_config)

    same_site = str(settings["same_site"]).lower()
    if same_site not in _ALLOWED_SAME_SITE:
        raise ValueError(f"session.same_site must be one of {_ALLOWED_SAME_SITE}, got "
                         f"'{settings['same_site']}'")
    settings["same_site"] = same_site
    if same_site == "none" and not settings["https_only"]:
        raise ValueError("session.same_site: none requires session.https_only: true "
                         "(browsers reject a SameSite=None cookie without Secure)")
    if same_site != "lax":
        log.warning("Web UI session cookie has same_site=%s. 'lax' is what lets the OAuth "
                    "callback (a cross-site top-level GET) carry the session cookie; with '%s' "
                    "logins can fail with an empty session.", same_site, same_site)

    settings["max_age"] = int(settings["max_age"])
    if settings["max_age"] <= 0:
        raise ValueError("session.max_age must be a positive number of seconds")
    settings["cookie_name"] = str(settings["cookie_name"])
    settings["https_only"] = bool(settings["https_only"])
    _warn_about_an_insecure_cookie(settings, service_config)

    if not settings["secret"]:
        settings["secret"] = secrets.token_urlsafe(48)
        log.warning("Web UI session: no session.secret configured in services/webservice.yaml - "
                    "using an ephemeral key. Sessions are invalidated on every restart.")
    return settings


def install_session_middleware(app, config: dict | None = None) -> dict:
    """Attach :class:`SessionMiddleware` and return the effective settings.

    Must run before the server starts serving: Starlette refuses ``add_middleware`` once the
    middleware stack has been built ("Cannot add middleware after an application has started"),
    which is why the shell installs in ``WebService.start()`` and never lazily on first request.
    """
    settings = session_settings(config)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings["secret"],
        session_cookie=settings["cookie_name"],
        max_age=settings["max_age"],
        same_site=settings["same_site"],
        https_only=settings["https_only"],
        path="/",
    )
    # the effective settings are what a route has to read when it deletes the cookie (logout must
    # delete the cookie the login actually set, not the module default)
    setattr(app.state, "webui_session_settings", settings)
    log.debug("Web UI session: cookie='%s' max_age=%s same_site=%s https_only=%s",
              settings["cookie_name"], settings["max_age"], settings["same_site"],
              settings["https_only"])
    return settings


def get_csrf_token(request: Request) -> str:
    """The token to render into a form: one per session, minted on first need, then returned.

    There is deliberately no rotation. Nothing in the flow invalidates a token (a rendered page
    keeps posting the same one until the session ends), so a *set* of tokens would only ever hold
    one entry — the ring this used to describe was never appended to, and the ceiling that came
    with it was dead code. If a rotation point is ever added (per login, say), it has to arrive
    with the reason its predecessor is invalidated.
    """
    tokens = request.session.get(_SESSION_KEY)
    if not isinstance(tokens, list) or not tokens:
        tokens = [secrets.token_urlsafe(32)]
        request.session[_SESSION_KEY] = tokens
        return tokens[0]
    return tokens[-1]


def _session_tokens(request: Request) -> list[str]:
    tokens = request.session.get(_SESSION_KEY)
    return [t for t in tokens if isinstance(t, str)] if isinstance(tokens, list) else []


def validate_csrf_token(request: Request, token: str | None) -> bool:
    """Constant-time membership check against the session's token.

    A token that was minted for this session stays valid until the session ends — see the module
    docstring for why it is not spent on use."""
    if not token:
        return False
    return any(secrets.compare_digest(token, known) for known in _session_tokens(request))


async def read_csrf_token(request: Request) -> str | None:
    """The token a write carries, from the header first and the form body second."""
    token = request.headers.get(CSRF_HEADER)
    if token:
        return token
    try:
        form = await request.form()
    except Exception:
        log.debug("CSRF: request body could not be parsed as a form", exc_info=True)
        return None
    value = form.get(CSRF_FIELD)
    return value if isinstance(value, str) else None


async def csrf_protect(request: Request) -> None:
    """Dependency for mutation routes: refuses a write that carries no valid session token."""
    if not validate_csrf_token(request, await read_csrf_token(request)):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")
