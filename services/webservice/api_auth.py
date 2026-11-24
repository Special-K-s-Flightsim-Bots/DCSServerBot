"""The api_key / JWT verifier, shared by every transport that answers on the app.

One verification path, three translations. :meth:`ApiVerifier.authenticate` reads the credential
and answers a :class:`Principal` or ``None`` — it raises nothing and answers no HTTP question.
:meth:`ApiVerifier.legacy_dependency` is the historical surface's answer (403 for anything
insufficient, 404 for an endpoint that is not there); :meth:`ApiVerifier.v1_dependency` is the
newer one (401 for a missing credential, 403 below the floor). A third transport translates the
same ``Principal | None`` itself.

The decision table (per request)
--------------------------------
``network`` comes from the peer address: loopback is ``local``, an address inside
``security.trusted`` is ``trusted``, anything else — including a request with no peer at all — is
``remote``. The requirement is ``endpoints.<key>.security[network]``, else
``security.default[network]``, else ``jwt``; a requirement that is not one of the four level
words falls back to ``jwt`` with a warning rather than opening the endpoint.

=====================  ===========================================================
requirement            answer
=====================  ===========================================================
``disabled``           404 — checked before any credential and before any stamp
``none``               allowed, and no credential is looked at (even a valid one)
``api_key``            allowed for an API key or a JWT, refused otherwise
``jwt``                allowed for a JWT only
=====================  ===========================================================

``endpoints.<key>.enabled`` is a different axis: it is decided once, at registration time. The
legacy closure only runs for a route that was registered, so :meth:`legacy_dependency` does not
re-check it; a transport that registers routes asks :meth:`ApiAuthPolicy.endpoint_enabled` and
skips the route, which is where the 404 comes from.

The ``request.state`` stamps
----------------------------
``auth_scope`` and ``required_auth_level`` are written once the requirement is known and is not
``disabled``. ``auth_scheme``, ``jwt_token`` and ``jwt_payload`` are written only where a
credential was actually verified — an endpoint that requires ``none`` stamps ``auth_scheme`` as
``"none"`` and never verifies anything. Routes in other plugins read ``jwt_payload`` off the same
app, so the stamp set is a contract, not a debugging aid.

What is deliberately not here
-----------------------------
Authorization (what a level may do), the response envelope, and the status mapping of an action's
outcome. Those belong to the transports; this module only answers who the caller is.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Mapping

import jwt
from fastapi import HTTPException
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import Request

__all__ = ["Level", "Principal", "ApiAuthPolicy", "ApiVerifier",
           "NETWORKS", "DEFAULT_SECURITY", "DEFAULT_TRUSTED"]

#: the three network scopes, in the config's own words
NETWORKS = ("local", "trusted", "remote")

#: ``security.default`` when the config states none — the historical fallback
DEFAULT_SECURITY = {"local": "none", "trusted": "api_key", "remote": "jwt"}

#: ``security.trusted`` when the config states none — the historical fallback
DEFAULT_TRUSTED = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")


class Level(IntEnum):
    """The credential a caller presented, ranked, in the historical wire vocabulary.

    ``DISABLED`` is not an attained level — it means "this endpoint is not there", and it ranks
    highest only because it always wins. Code that compares a requirement against a presented
    level must handle ``DISABLED`` before the comparison; :class:`Principal` is never
    ``DISABLED``.
    """

    NONE = 0
    API_KEY = 1
    JWT = 2
    DISABLED = 3

    @classmethod
    def from_config(cls, value: Any) -> "Level | None":
        """The level a config word names, or ``None`` for a word that is not a level."""
        return _CONFIG_TO_LEVEL.get(value)

    @property
    def wire(self) -> str:
        """The config word for this level — the value ``required_auth_level`` is stamped with."""
        return _WIRE[self]


_CONFIG_TO_LEVEL = {
    "none": Level.NONE,
    "api_key": Level.API_KEY,
    "jwt": Level.JWT,
    "disabled": Level.DISABLED,
}

_WIRE = {
    Level.NONE: "none",
    Level.API_KEY: "api_key",
    Level.JWT: "jwt",
    Level.DISABLED: "disabled",
}


@dataclass(frozen=True)
class Principal:
    """A verified caller. Built by :meth:`ApiVerifier.authenticate`, consumed by a transport."""

    level: Level
    network: str                       # "local" | "trusted" | "remote"
    scheme: str                        # "none" | "api_key" | "jwt"
    subject: str = ""                  # the JWT subject, or "" for a static key
    label: str = ""                    # what an audit trail shows for this caller
    payload: dict[str, Any] = field(default_factory=dict)   # the verified JWT claims


@dataclass(frozen=True)
class ApiAuthPolicy:
    """The auth part of one plugin config block, parsed once.

    Two surfaces may be given different policies from different config files; the verification
    code below is the same for both.
    """

    api_key: str | None = None
    jwt_key: str | None = None
    jwks_client: Any = None            # jwt.PyJWKClient for ``jwt.jwks_url``, built once
    default_security: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_SECURITY))
    trusted: tuple[Any, ...] = ()       # ipaddress networks
    endpoints: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_plugin_config(cls, config: Mapping[str, Any] | None) -> "ApiAuthPolicy":
        """Build the policy from a plugin's ``DEFAULT`` block.

        ``auth.api_key``, ``auth.jwt.{key,jwks_url}``, ``security.{trusted,default}`` and
        ``endpoints`` are the only keys read.
        """
        config = config or {}
        auth = config.get("auth") or {}
        jwt_info = auth.get("jwt") or None
        security = config.get("security") or {}
        return cls(
            api_key=auth.get("api_key"),
            jwt_key=jwt_info.get("key") if jwt_info else None,
            jwks_client=jwt.PyJWKClient(jwt_info["jwks_url"]) if jwt_info else None,
            # presence-preserving: a `default` block that is present but empty names no level for
            # any network, so every lookup in `configured_level` falls to `jwt`. Only a MISSING
            # key means the built-in default — a falsy block must not widen the surface.
            default_security=security.get("default", DEFAULT_SECURITY),
            trusted=tuple(ipaddress.ip_network(network)
                          for network in security.get("trusted", DEFAULT_TRUSTED)),
            endpoints=config.get("endpoints") or {},
        )

    # ------------------------------------------------------------------ the network scope

    def network_for(self, host: str | None) -> str:
        """The scope of a peer address: loopback, a trusted network, or anything else.

        An address that cannot be parsed and a request with no peer at all are both ``remote``.
        """
        if not host:
            return "remote"
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return "remote"
        if address.is_loopback:
            return "local"
        if any(address in network for network in self.trusted):
            return "trusted"
        return "remote"

    # ------------------------------------------------------------------ the endpoint config

    def endpoint_config(self, key: str) -> Mapping[str, Any]:
        """The ``endpoints.<key>`` block; the key is the route path without its leading slash."""
        return self.endpoints.get(_config_key(key)) or {}

    def endpoint_enabled(self, key: str) -> bool:
        """Whether the endpoint is registered at all — ``enabled`` defaults to true."""
        return bool(self.endpoint_config(key).get("enabled", True))

    def configured_level(self, key: str, network: str) -> Any:
        """The raw requirement for ``key`` on ``network``, before validation.

        An endpoint may override per network; otherwise the network's default applies, and a
        network the default does not name requires ``jwt``.
        """
        security = self.endpoint_config(key).get("security") or {}
        return security.get(network, self.default_security.get(network, "jwt"))


class ApiVerifier:
    """Answers who a request is, or refuses it, for one policy."""

    def __init__(self, policy: ApiAuthPolicy, log: logging.Logger | None = None):
        self.policy = policy
        self.log = log or logging.getLogger(__name__)
        # the same extractors the legacy closure used through Depends, so header parsing —
        # including its exact-case "Bearer" scheme check — is not re-derived here
        self._bearer = HTTPBearer(auto_error=False)
        self._api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    # ------------------------------------------------------------------ the requirement

    def level_for(self, *, path: str, network: str) -> Level:
        """The requirement for an endpoint config key on a network.

        An unrecognised word is not an open door: it names ``jwt`` and is logged.
        """
        configured = self.policy.configured_level(path, network)
        level = Level.from_config(configured)
        if level is None:
            self.log.warning(
                f"Invalid REST API auth level '{configured}' for endpoint "
                f"'{_config_key(path)}' and scope '{network}'. Falling back to 'jwt'.")
            return Level.JWT
        return level

    # ------------------------------------------------------------------ the one verification

    async def authenticate(self, request: Request) -> Principal | None:
        """Verify the credential a request carries.

        Returns a :class:`Principal` for a verified credential and ``None`` for a missing or
        invalid one — it never answers an HTTP question. The credential-derived ``request.state``
        stamps are written only when a credential verified.
        """
        credentials: HTTPAuthorizationCredentials | None = await self._bearer(request)
        provided_api_key: str | None = await self._api_key_header(request)
        network = self.policy.network_for(request.client.host if request.client else None)

        if self.policy.jwt_key and credentials and credentials.scheme == "Bearer":
            try:
                payload = await self._verify_token(credentials.credentials)
            except jwt.InvalidTokenError as ex:
                # one line, no traceback, never the credential itself or a slice of it
                self.log.warning(
                    f"RestAPI: refused a malformed {credentials.scheme} credential from "
                    f"{_peer(request)} on {request.url.path} ({type(ex).__name__}).")
                payload = None
            if payload:
                request.state.auth_scheme = "jwt"
                request.state.jwt_token = credentials.credentials
                request.state.jwt_payload = payload
                subject = str(payload.get("sub", ""))
                return Principal(level=Level.JWT, network=network, scheme="jwt",
                                 subject=subject, label=subject, payload=payload)

        # plain equality: the legacy key is a config literal, compared as the legacy surface does
        if self.policy.api_key and provided_api_key == str(self.policy.api_key):
            request.state.auth_scheme = "api_key"
            return Principal(level=Level.API_KEY, network=network, scheme="api_key",
                             subject="", label="api_key")

        return None

    async def _verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify a bearer token against the configured JWKS, audience and shared key claim.

        Raises :class:`jwt.InvalidTokenError` for a token that is not usable — a bearer string that
        is not a JWT, a bad signature, an expired or mis-audienced claim. The caller answers that
        one, because naming the request path and the peer needs the request. Every other failure
        is logged here, with its traceback, and answers ``None``.
        """
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")

            if not kid:
                return None

            signing_key = await asyncio.to_thread(self.policy.jwks_client.get_signing_key, kid)

            if not signing_key:
                self.log.error("RestAPI: No signing key provided!")
                return None

            data = jwt.decode(
                token,
                signing_key.key,
                algorithms=[signing_key.algorithm_name],
                audience="dcssb",
                leeway=10,
            )

            if data.get("key") != self.policy.jwt_key:
                self.log.error("RestAPI: Signing key does not match!")
                return None

            return data

        except jwt.InvalidTokenError:
            raise
        except Exception as ex:
            self.log.exception(ex)
            return None

    # ------------------------------------------------------------------ the transport shims

    def legacy_dependency(self, *, path: str) -> Callable[[Request], Any]:
        """The historical surface's auth dependency for one endpoint config key.

        403 for a missing or insufficient credential (this surface never answers 401), 404 for a
        ``disabled`` requirement. The ``request.state`` stamps are written exactly where the
        historical closure wrote them.
        """
        key = _config_key(path)

        async def auth(request: Request) -> None:
            network = self.policy.network_for(request.client.host if request.client else None)
            required = self.level_for(path=key, network=network)

            if required is Level.DISABLED:
                raise HTTPException(status_code=404, detail="Not found.")

            request.state.auth_scope = network
            request.state.required_auth_level = required.wire

            if required is Level.NONE:
                # no credential is examined, so a presented JWT leaves no jwt stamp either
                request.state.auth_scheme = "none"
                return

            principal = await self.authenticate(request)

            # DISABLED was answered above, so both sides are at most JWT and the rank compares
            if principal is not None and principal.level >= required:
                return

            raise HTTPException(status_code=403, detail="Invalid authentication")

        return auth

    def v1_dependency(self, *, floor: Level) -> Callable[[Request], Any]:
        """The newer surface's auth dependency for a risk-class floor.

        401 for a missing or bad credential, 403 for a credential below the floor. The refusal
        body of that surface is the transport's envelope, not this module's.
        """
        if floor is Level.DISABLED:
            raise ValueError("a disabled endpoint is not registered, so it has no floor")

        async def auth(request: Request) -> None:
            network = self.policy.network_for(request.client.host if request.client else None)
            request.state.auth_scope = network
            request.state.required_auth_level = floor.wire

            if floor is Level.NONE:
                request.state.auth_scheme = "none"
                return

            principal = await self.authenticate(request)

            if principal is None:
                raise HTTPException(status_code=401, detail="Not authenticated",
                                    headers={"WWW-Authenticate": "Bearer"})

            if principal.level < floor:
                raise HTTPException(status_code=403, detail="Invalid authentication level")

        return auth


def _config_key(path: str) -> str:
    """The ``endpoints.<key>`` key for a route path (the legacy spelling drops leading slashes)."""
    return path.lstrip("/")


def _peer(request: Request) -> str:
    """The client address of a request, or ``no peer`` when the transport reports none."""
    return request.client.host if request.client else "no peer"
