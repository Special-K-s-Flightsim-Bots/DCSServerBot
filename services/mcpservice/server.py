"""The MCP transport: config, caller identity, and the mount the web application carries.

Three things live here, in order of dependence:

* the CONFIG (:class:`McpKey`, :func:`keys_from_config`, :class:`McpTransportConfig`) — parsed once,
  every mistake a startup ERROR instead of a request-time refusal;
* the IDENTITY (:class:`McpPrincipal`, :func:`current_principal`, :class:`McpVerifier`,
  :class:`_Bearer`) — one verified principal per request, carried into the tool call by a
  ``ContextVar``;
* the MOUNT (:func:`streamable_app`, :class:`_ExactMount`, :func:`mount`) — the Starlette app a
  level's tool list is served from, and the mount that answers exactly on its own path.

A mount answers BEFORE the application's dependency chain runs, so ``_Bearer`` is the only gate on
``/mcp``: an unauthenticated request is refused with 401 there, before any MCP session exists.
"""
from __future__ import annotations

import logging
import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Match, Mount

from core.actions import _audit_token
from services.webservice.api_auth import ApiAuthPolicy, ApiVerifier

log = logging.getLogger(__name__)

__all__ = [
    "LEVEL_READ", "LEVEL_CONTROL", "LEVELS", "VISIBLE_RISKS",
    "McpConfigError", "McpKey", "McpTransportConfig", "McpPrincipal",
    "keys_from_config", "transport_from_config", "current_principal",
    "McpVerifier", "streamable_app", "mount", "unmount", "find_mount",
]

#: The two key levels. ``read`` sees READ rows only; ``control`` sees every class slice 1 exposes.
LEVEL_READ = "read"
LEVEL_CONTROL = "control"
#: Every level, in ascending authority — the order a tool list is built in.
LEVELS: tuple[str, ...] = (LEVEL_READ, LEVEL_CONTROL)

#: The audit-token shape a label must have. A label ends up inside ``[mcp:key/<label>]``, so it may
#: not carry a bracket, a space or a newline: a crafted label could otherwise forge a second token
#: or break the line the entry is written on.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The risk classes each level's tool list may carry. NODE and SECRET are never exposed at any
#: level: one takes a machine out of service, the other carries a credential.
VISIBLE_RISKS: dict[str, frozenset[str]] = {
    LEVEL_READ: frozenset({"read"}),
    LEVEL_CONTROL: frozenset({"read", "lifecycle", "moderation", "config"}),
}


class McpConfigError(Exception):
    """A configuration mistake that must stop the service, with the consequence in the sentence."""


@dataclass(frozen=True)
class McpKey:
    """One configured client key: what it is called, what it may see, and how it proves itself.

    ``secret`` is a static bearer token (compared in constant time) and ``verifier`` is a JWT
    verifier for an entry configured with a ``jwt:`` block — exactly one of the two is set.
    """

    label: str
    level: str
    secret: str | None = None
    verifier: ApiVerifier | None = None


@dataclass(frozen=True)
class McpTransportConfig:
    """The transport's DNS-rebinding allow-lists."""

    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...] = ()

    def settings(self) -> TransportSecuritySettings:
        """The SDK's settings object for this configuration, with protection ENABLED."""
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(self.allowed_hosts),
            allowed_origins=list(self.allowed_origins),
        )


def _entries(config: Mapping[str, Any]) -> Sequence[Any]:
    """The raw ``keys`` list, or a config error naming what is missing."""
    raw = config.get("keys")
    if raw is None:
        raise McpConfigError(
            "MCP is enabled but declares no 'keys': every request would be answered 401 and no "
            "client could call a tool. Add at least one entry (label, key, level) to "
            "services/mcpservice.yaml.")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise McpConfigError(f"MCP 'keys' must be a non-empty list of entries, not {raw!r}.")
    return raw


def keys_from_config(config: Mapping[str, Any]) -> tuple[McpKey, ...]:
    """Every configured key, validated; any mistake raises :class:`McpConfigError`.

    Each entry carries a unique ``label`` (the audit actor's tail), a ``level`` and exactly one
    credential: a static ``key``, or a ``jwt`` block with the shared ``key`` and the ``jwks_url`` the
    token's signing key is fetched from.
    """
    keys: list[McpKey] = []
    labels: set[str] = set()
    for index, entry in enumerate(_entries(config)):
        where = f"MCP keys[{index}]"
        if not isinstance(entry, Mapping):
            raise McpConfigError(f"{where} must be a mapping with label/level and a credential.")
        label = str(entry.get("label") or "")
        if not _LABEL.match(label):
            raise McpConfigError(
                f"{where}: label {label!r} is not usable - a label is part of the audit token "
                "'[mcp:key/<label>]', so it must be 1-64 characters of letters, digits, '.', '_' "
                "or '-'. The actor of every audited MCP call is built from it.")
        if label in labels:
            raise McpConfigError(f"{where}: the label {label!r} is declared twice; an actor tail "
                                 "must name one client.")
        labels.add(label)
        level = str(entry.get("level") or "")
        if level not in LEVELS:
            raise McpConfigError(f"{where} ({label}): level {level!r} is not one of {list(LEVELS)}.")
        secret = entry.get("key")
        jwt_info = entry.get("jwt")
        if secret is not None and jwt_info is not None:
            raise McpConfigError(f"{where} ({label}): a key entry carries either 'key' or 'jwt', "
                                 "not both.")
        if secret is not None:
            if not isinstance(secret, str) or not secret.strip():
                raise McpConfigError(f"{where} ({label}): 'key' must be a non-empty string.")
            keys.append(McpKey(label=label, level=level, secret=secret))
            continue
        if jwt_info is None:
            raise McpConfigError(f"{where} ({label}): no credential - add a static 'key' or a "
                                 "'jwt' block.")
        if (not isinstance(jwt_info, Mapping) or not jwt_info.get("key")
                or not jwt_info.get("jwks_url")):
            raise McpConfigError(f"{where} ({label}): a 'jwt' block needs both 'key' and "
                                 "'jwks_url'.")
        import jwt as pyjwt

        keys.append(McpKey(
            label=label, level=level,
            verifier=ApiVerifier(ApiAuthPolicy(
                jwt_key=str(jwt_info["key"]),
                jwks_client=pyjwt.PyJWKClient(str(jwt_info["jwks_url"])))),
        ))
    return tuple(keys)


def transport_from_config(config: Mapping[str, Any]) -> McpTransportConfig:
    """The transport allow-lists; an empty ``allowed_hosts`` is a STARTUP error.

    The SDK's default is ``enable_dns_rebinding_protection=True`` with an EMPTY ``allowed_hosts``,
    and its middleware then answers ``421 Invalid Host header`` to every request — a mount that
    looks installed and refuses all traffic. That is unreadable from a request log, so it is refused
    here, at start, naming the consequence.
    """
    transport = config.get("transport") or {}
    if not isinstance(transport, Mapping):
        raise McpConfigError(f"MCP 'transport' must be a mapping, not {transport!r}.")
    hosts = transport.get("allowed_hosts")
    origins = transport.get("allowed_origins") or ()
    if not isinstance(hosts, Sequence) or isinstance(hosts, (str, bytes)):
        raise McpConfigError(
            "MCP 'transport.allowed_hosts' must be a list of Host header values. It is not "
            "optional: the SDK enables DNS-rebinding protection by default, and with no allowed "
            "host every request is refused with 421 'Invalid Host header'.")
    cleaned = tuple(str(host).strip() for host in hosts if str(host).strip())
    if not cleaned:
        raise McpConfigError(
            "MCP 'transport.allowed_hosts' is empty: DNS-rebinding protection is enabled by "
            "default and an empty allow-list refuses EVERY request with 421 'Invalid Host "
            "header'. List the host names MCP clients reach this install by, e.g. "
            "[\"dcs.example.com\", \"100.64.0.5:*\"], in services/mcpservice.yaml.")
    if not isinstance(origins, Sequence) or isinstance(origins, (str, bytes)):
        raise McpConfigError("MCP 'transport.allowed_origins' must be a list of Origin values.")
    return McpTransportConfig(
        allowed_hosts=cleaned,
        allowed_origins=tuple(str(origin).strip() for origin in origins if str(origin).strip()))


@dataclass(frozen=True)
class McpPrincipal:
    """A verified MCP caller: one configured key, its level, and the actor token it audits as."""

    scheme: str      # "key" | "jwt"
    label: str       # the actor token's tail
    level: str       # LEVEL_READ | LEVEL_CONTROL

    @property
    def actor(self) -> str:
        """The audit actor of a call this principal makes — ``[mcp:key/<label>]``.

        The label is the token's only caller-influenced part, so it is neutralised through
        ``core.actions._audit_token`` — the same rule ``from_web`` applies to its subject — before
        the token is assembled. Sanitising the assembled token would neutralise its own brackets.
        """
        return f"[mcp:{self.scheme}/{_audit_token(self.label)}]"

    def sees(self, risk: Any) -> bool:
        """Whether this principal's level offers a tool of that risk class."""
        return str(getattr(risk, "value", risk)) in VISIBLE_RISKS.get(self.level, frozenset())


#: The verified principal of the request being served. A tool reads it; ``_Bearer`` sets it for the
#: duration of the request and resets it. It is a ``ContextVar`` and not a ``Context``-typed tool
#: parameter because mcp 2.3.0 finds the injected context parameter with ``typing.get_type_hints``,
#: which does NOT consult ``__signature__`` — such a parameter would land in the tool's input schema.
_CURRENT_PRINCIPAL: ContextVar[McpPrincipal | None] = ContextVar("mcp_principal", default=None)


def current_principal() -> McpPrincipal | None:
    """The principal of the call being served, or ``None`` — which must run nothing."""
    return _CURRENT_PRINCIPAL.get()


class McpVerifier:
    """Answers who a bearer token is, or ``None``. Raises nothing and answers no HTTP question."""

    def __init__(self, keys: Sequence[McpKey]):
        self._keys = tuple(keys)

    @property
    def keys(self) -> tuple[McpKey, ...]:
        return self._keys

    async def authenticate(self, request: Request) -> McpPrincipal | None:
        """The principal behind the request's ``Authorization: Bearer`` header, or ``None``."""
        scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            return None
        presented = token.encode("utf-8")
        for entry in self._keys:
            if entry.secret is not None:
                if secrets.compare_digest(presented, entry.secret.encode("utf-8")):
                    return McpPrincipal(scheme="key", label=entry.label, level=entry.level)
                continue
            if entry.verifier is not None:
                principal = await entry.verifier.authenticate(request)
                if principal is not None:
                    # a JWT subject is externally supplied: neutralise it with the core rule rather
                    # than refuse, so a mangled actor is visible in the trail
                    return McpPrincipal(scheme="jwt",
                                        label=_audit_token(principal.subject) or entry.label,
                                        level=entry.level)
        return None


class _Bearer:
    """The only gate on the mount: verify, pick the level's application, stamp the principal.

    A mount bypasses the application's dependency chain, so this wrapper is what authenticates. A
    request with no (or a bad) credential is answered ``401`` here, before ``inner`` runs — no MCP
    session is created for an unauthenticated request.
    """

    def __init__(self, apps: Mapping[str, Any], verifier: McpVerifier):
        self._apps = dict(apps)
        self._verifier = verifier

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":          # pragma: no cover - a Mount sends http/websocket only
            return await self._deny(scope, receive, send)
        principal = await self._verifier.authenticate(Request(scope))
        if principal is None:
            return await self._deny(scope, receive, send)
        app = self._apps.get(principal.level)
        if app is None:    # pragma: no cover - one application is built per configured level
            return await self._deny(scope, receive, send)
        token = _CURRENT_PRINCIPAL.set(principal)
        try:
            await app(scope, receive, send)
        finally:
            _CURRENT_PRINCIPAL.reset(token)

    @staticmethod
    async def _deny(scope, receive, send) -> None:
        """Answer 401 with the scheme a client should present."""
        response = JSONResponse({"detail": "Not authenticated"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        await response(scope, receive, send)


class _ExactMount(Mount):
    """A ``Mount`` that answers its OWN path, with no trailing-slash redirect.

    ``Mount`` compiles ``<path>/{path:path}``, whose regex does not match ``/mcp`` itself: Starlette
    then answers that request with ``307 /mcp/``. The hop happens in the outer router, BEFORE the
    mount's ``_Bearer`` runs — so the first thing an unauthenticated client would see is a redirect,
    and the mount would only ever answer one path away from the path it declares. Matching the bare
    path (and treating ``/mcp/`` as the same) keeps the gate, the answer and the declaration equal.
    """

    def __init__(self, path: str, app: Any):
        super().__init__(path, app=app)

    def matches(self, scope) -> tuple[Match, Any]:
        if scope["type"] not in ("http", "websocket"):
            return Match.NONE, {}
        root_path = scope.get("root_path", "") or ""
        path = scope.get("path", "/") or "/"
        # the same rule Starlette's own Mount applies to the request path it matches on
        route_path = path[len(root_path):] if root_path and path.startswith(root_path) else path
        if not route_path.startswith("/"):
            route_path = "/" + route_path
        if route_path != self.path and not route_path.startswith(self.path + "/"):
            return Match.NONE, {}
        prefix = root_path + self.path
        child_scope: dict[str, Any] = {
            "path_params": dict(scope.get("path_params", {})),
            "app_root_path": scope.get("app_root_path", root_path),
            "root_path": prefix,
            "endpoint": self.app,
        }
        if route_path == self.path:
            # The child app routes on "/": Starlette derives a child's path by subtracting its
            # root_path, and an exact hit would otherwise leave it "" — its own router would then
            # answer 307 to the slashed form, which is the hop this class exists to remove.
            child_scope["path"] = prefix + "/"
        return Match.FULL, child_scope

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(path={self.path!r})"


def streamable_app(server: MCPServer, transport: McpTransportConfig) -> Any:
    """The Starlette app one level's tools are served from.

    ``streamable_http_path="/"`` is load-bearing: the SDK's own default is ``/mcp``, so mounting
    that under ``/mcp`` would answer on ``/mcp/mcp``. ``stateless_http=True`` gives every request a
    fresh transport with no session outliving the POST that carried its identity — which is what
    lets the ``ContextVar`` reach the tool, and it is right for v1 besides: no tool sends client
    notifications.

    The app's own lifespan starts the session manager, and a MOUNTED app's lifespan never runs
    (Starlette does not enter it), so the service enters ``server.session_manager.run()`` itself.
    """
    return server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=transport.settings(),
    )


def find_mount(app: Any, path: str = "/mcp") -> Any | None:
    """The mount at *path* on *app*, or ``None``."""
    for route in getattr(getattr(app, "router", None), "routes", ()) or ():
        if isinstance(route, Mount) and getattr(route, "path", None) == path:
            return route
    return None


def mount(app: Any, apps: Mapping[str, Any], verifier: McpVerifier,
          *, path: str = "/mcp") -> _ExactMount:
    """Append the ``_Bearer`` gate at *path* to *app* and return the mount.

    A ``Mount`` is used, not a route: the application-level capability gate is a ROUTE dependency,
    and a mount is the one surface that answers without it — which is why
    ``permissions.EXPECTED_MOUNTS`` declares this path and ``audit_mounts`` stays quiet.
    """
    unmount(app, find_mount(app, path), path=path)
    route = _ExactMount(path, app=_Bearer(apps, verifier))
    app.router.routes.append(route)
    return route


def unmount(app: Any, route: Any, *, path: str = "/mcp") -> bool:
    """Remove *route* (or the mount at *path*) from *app* by IDENTITY; ``True`` if one was removed."""
    routes = getattr(getattr(app, "router", None), "routes", None)
    if not routes:
        return False
    target = route if route is not None else find_mount(app, path)
    if target is None:
        return False
    for index, existing in enumerate(routes):
        if existing is target:
            del routes[index]
            return True
    return False
