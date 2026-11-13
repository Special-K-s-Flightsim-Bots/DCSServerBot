"""The ``/api/v1`` surface: the guard, and the mount built from discovery.

The rows are not written here. Each plugin declares its own in ``api.py``; :func:`install` imports
them (:func:`core.action_surface.discover_endpoints`), filters them for this slice, builds one
FastAPI route per row and appends the whole thing to the running application. Nothing in this module
knows what a server is.

What a transport must decide, and where each decision is made
------------------------------------------------------------

**The prefix is fixed** at :data:`PREFIX`. The legacy surface keeps its configurable ``prefix:``;
v1 does not read it, because a client of the machine API addresses a versioned path and an operator
who moves the legacy stats routes must not move the versioned ones with them.

**Who may call a row** is :class:`TransportGuard`'s business, in this order per request:

1. the row's CLASS FLOOR (:data:`FLOORS`) and the config's own requirement for the caller's
   network (``endpoints.<key>.security``, else ``security.default[<network>]``) are combined with
   ``max`` — a configured policy may RAISE a class floor but can never lower one, so
   ``security.default: {local: none}`` does not make a lifecycle write anonymous;
2. ``disabled`` in config is a 404, decided before any credential is read (the row is normally not
   registered at all — see :func:`install` — and this is the per-network form of the same answer);
3. a requirement of ``none`` runs with no credential examined, which is the READ convenience v1
   keeps from the legacy surface;
4. otherwise :meth:`ApiVerifier.authenticate` names the caller: no credential is a **401** and a
   credential under the effective floor is a **403**. Legacy answers 403 for both; v1 tells a
   machine client the difference, because a client that must fetch a token needs to know it is
   unauthenticated rather than under-privileged.

**What the action receives** is an :class:`ActionContext` carrying :class:`MachineScope` — an
explicitly UNRESTRICTED scope. This is not decoration: ``ActionContext._scope_allows`` answers
``False`` when no scope is set, so a scopeless context makes ``resolve_scoped_server`` refuse every
name and v1 would answer 404 for the whole fleet. v1 authorises by KEY LEVEL, not by a per-server
view; a machine key has no fleet view to build.

**What a row may be** is :data:`SLICE_RISKS`. A MODERATION, CONFIG, NODE or SECRET row is not
mounted even when a plugin declares it: the row stays registered for the surface that will own it,
and this mount names it in the log instead of exposing it silently. Rows handed to :func:`install`
directly are validated there (:func:`core.action_surface.check_endpoints`), so a caller cannot mount
a row the discovery path would have refused.

**Which seam a row runs through** is the ACTION's kind (``core.actions.kind_of``), never the row's
own risk claim: a row that declares READ over a write action still goes through ``call_action``, so
it keeps the in-flight guard and the audit. An action absent from the registry answers the typed
``action_unavailable`` (501) from either seam.

**The audit actor** of a v1 call is ``[api:<scheme>/<label>]`` — the caller the verifier named,
sanitised through ``core.actions._audit_token``. The legacy surface keeps ``"API"``.

**The config key of a row** is ``v1/<relative path>`` — ``v1/servers``, and the whole block under it
(``enabled``, ``security``). The legacy surface keeps its own spelling, so one key never governs
both; a legacy key that happens to spell a v1 row path is reported as a WARNING at mount time.

Outcome → status is ``core.action_surface.status_for`` (200/400 from ``success``, else from the
machine-readable ``code``), and the body is ``body_of`` — the envelope, plus ``code`` when the result
carries one.
"""
from __future__ import annotations

import inspect
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from core.action_surface import (ACTION_UNAVAILABLE, READ, Endpoint, Risk, RouteGuard, build_route,
                                 check_endpoints, discover_endpoints)
from core.actions import (TRANSPORT_PLUGIN, ActionContext, _audit_token, action_registry,
                          call_action, discover_actions, kind_of, read_action)
from services.webservice.api_auth import ApiVerifier, Level, Principal
from services.webservice.registry import include_router_counted

__all__ = ["PREFIX", "FLOORS", "SLICE_RISKS", "MachineScope", "TransportGuard", "config_key",
           "audit_actor", "discovered_rows", "install"]

log = logging.getLogger(__name__)

#: The v1 prefix. Fixed by design: rows are relative to it and the legacy ``prefix:`` never moves it.
PREFIX = "/api/v1"

#: The minimum credential each risk class needs on EVERY network (design §7.2). The effective
#: requirement is ``max(this, the config's requirement for the network)``; a class floor can be
#: raised by configuration and never lowered.
FLOORS: Mapping[Risk, Level] = {
    Risk.READ: Level.NONE,
    Risk.LIFECYCLE: Level.API_KEY,
    Risk.MODERATION: Level.API_KEY,
    Risk.CONFIG: Level.API_KEY,
    Risk.NODE: Level.JWT,
    Risk.SECRET: Level.JWT,
}

#: The risk classes this mount serves: reads, and actions that stop or start a SERVER and its
#: mission inside this process. Everything above LIFECYCLE — a person's record, a file that outlives
#: the process, a whole machine, a credential — needs its own contract before it is reachable.
SLICE_RISKS = frozenset({Risk.READ, Risk.LIFECYCLE})

#: The two refusal sentences of this surface. There is one wording for each so a client can match
#: on the status and a human on the sentence, and no route invents a third.
UNAUTHENTICATED = "Not authenticated"
INSUFFICIENT_LEVEL = ("Invalid authentication level: '{presented}' cannot run a {risk} action, "
                      "which requires '{required}'.")

#: The audit token's scheme word per credential scheme: the api key is spelled ``key``, matching the
#: ``[mcp:key/<label>]`` shape. Every other scheme is spelled as itself (``jwt``, ``none``).
ACTOR_SCHEMES: Mapping[str, str] = {"api_key": "key"}


def config_key(path: str) -> str:
    """The ``endpoints.<key>`` key of a v1 row: ``v1/<relative path>``.

    The namespace is what keeps the two surfaces apart — the legacy key for ``/servers`` is
    ``servers``, and without this prefix disabling either one would disable both.
    """
    return f"v1/{path.lstrip('/')}"


def audit_actor(principal: Principal) -> str:
    """The audit actor of a v1 call: ``[api:<scheme>/<label>]``, the label sanitised.

    The label is caller-influenced (a JWT subject), so it is neutralised by
    ``core.actions._audit_token`` exactly as ``from_web`` neutralises its subject: a bracket or a
    newline must not be able to close the token or break the entry line.
    """
    scheme = ACTOR_SCHEMES.get(principal.scheme, _audit_token(principal.scheme))
    return f"[api:{scheme}/{_audit_token(principal.label)}]"


class MachineScope:
    """The scope of a machine caller: every server, by construction.

    ``ActionContext`` reads exactly two facts off a scope — ``allows(managed_by)`` and the
    ``unrestricted`` flag — and a MISSING scope denies everything. A machine key is authorised by
    its level, so its view is the fleet; nothing here is derived from the caller.
    """

    #: seen every server
    unrestricted = True
    #: the scope is resolved (nothing is left to look up)
    resolved = True

    def allows(self, managed_by: Any = ()) -> bool:
        """Every server is inside this scope, whatever its ``managed_by`` says."""
        return True

    def __repr__(self) -> str:
        return "MachineScope(unrestricted)"


#: the one scope instance — it carries no per-caller state, so one is enough
MACHINE_SCOPE = MachineScope()


@dataclass(frozen=True)
class _Identity:
    """What the guard stamped on the current request: which row, and who is calling.

    It travels through a ``ContextVar`` because a FastAPI dependency and the route handler share one
    execution context and the handler's signature is the ACTION's — there is no room in it for a
    parameter the transport would add.
    """

    row: Endpoint
    principal: Principal


#: the caller of the request being served: set by the guard's dependency, cleared by its teardown
_CURRENT: ContextVar[_Identity | None] = ContextVar("dcsb_api_v1_identity", default=None)


class TransportGuard(RouteGuard):
    """:class:`core.action_surface.RouteGuard` for REST v1 — authenticate, authorise, dispatch.

    One instance serves every row: per-request state reaches :meth:`context` through :data:`_CURRENT`,
    which the dependency sets and its teardown clears on every exit.
    """

    def __init__(self, verifier: ApiVerifier, plugin: Any, *,
                 logger: logging.Logger | None = None):
        self.verifier = verifier
        self.plugin = plugin
        self.log = logger or log

    # ---------------------------------------------------------------- 1-3: the decision order

    def required_level(self, row: Endpoint, network: str) -> Level:
        """The credential *row* needs on *network*: the class floor, or the config's own demand.

        ``max`` and not the configured value: ``security.default`` describes the legacy surface's
        convenience (a loopback request may present nothing at all), and reading it as a CEILING
        would let that convenience authorise a write on v1, which is the one thing the floors exist
        to prevent.
        """
        configured = self.verifier.level_for(path=config_key(row.path), network=network)
        if configured is Level.DISABLED:
            return Level.DISABLED
        return max(FLOORS[Risk(row.risk)], configured)

    async def _authorise(self, row: Endpoint, request: Request) -> Principal:
        """Authenticate the request and authorise *row*'s class, or raise the refusal."""
        network = self.verifier.policy.network_for(
            request.client.host if request.client else None)
        required = self.required_level(row, network)

        if required is Level.DISABLED:
            raise HTTPException(status_code=404, detail="Not found.")

        # the same stamps the historical surface writes, so a route of another plugin reading
        # request.state on this app sees one contract whichever surface answered
        request.state.auth_scope = network
        request.state.required_auth_level = required.wire

        if required is Level.NONE:
            # no credential is examined — a READ on a network the operator left open
            request.state.auth_scheme = "none"
            return Principal(level=Level.NONE, network=network, scheme="none", label="anonymous")

        principal = await self.verifier.authenticate(request)
        if principal is None:
            raise HTTPException(status_code=401, detail=UNAUTHENTICATED,
                                headers={"WWW-Authenticate": "Bearer"})
        if principal.level < required:
            raise HTTPException(
                status_code=403,
                detail=INSUFFICIENT_LEVEL.format(presented=principal.level.wire,
                                                 risk=Risk(row.risk).value,
                                                 required=required.wire))
        return principal

    # ---------------------------------------------------------------- the RouteGuard contract

    def dependency(self, row: Endpoint) -> Callable[[Request], Any]:
        """The FastAPI dependency of *row*: authenticate and authorise, then stamp the identity.

        A refusal raised here ends the request before the handler runs, so no action ever sees an
        unauthorised call and no route has to repeat the check.

        A YIELD dependency, so its teardown runs on every exit of the request — including the route
        builder's 404-before-dispatch return, which never reaches :meth:`dispatch` — and
        :data:`_CURRENT` is never left set for whatever runs next in this task.
        """

        async def guard(request: Request):
            principal = await self._authorise(row, request)
            request.state.v1_principal = principal
            _CURRENT.set(_Identity(row=row, principal=principal))
            try:
                yield
            finally:
                _CURRENT.set(None)

        return guard

    def context(self, row: Endpoint, params: Mapping[str, Any]) -> ActionContext:
        """The ``ActionContext`` this request's action runs against — fleet-wide, by key level."""
        identity = _CURRENT.get()
        if identity is None or identity.row is not row:
            # fail closed: a handler reached without the guard's stamp runs nothing
            raise HTTPException(status_code=401, detail=UNAUTHENTICATED)
        ctx = ActionContext(node=self.plugin.node, bus=self.plugin.bus)
        ctx._transport = TRANSPORT_PLUGIN
        ctx._audit_user = audit_actor(identity.principal)
        ctx._scope = MACHINE_SCOPE
        return ctx

    async def dispatch(self, row: Endpoint, ctx: ActionContext,
                       params: Mapping[str, Any]) -> Any:
        """Run the row's action through the seam, with the RESOLVED server name.

        The seam is chosen by the ACTION's own kind (``kind_of``), never by the row's risk claim: a
        row that says READ over a write action would otherwise skip the in-flight guard and the
        audit. An action absent from the registry answers the typed 501 from either seam, because
        ``kind_of`` reports an unknown action as a write.

        The name the action receives is the one the server registry answers to, not the caller's
        spelling of it: an alias or a display name must reach the action as the object it names.
        """
        kwargs = dict(params)
        name = kwargs.get("server_name")
        if isinstance(name, str):
            server = ctx.resolve_server(name)
            if server is not None:
                kwargs["server_name"] = server.name
        try:
            if kind_of(row.action) == READ:
                return await read_action(row.action, ctx, **kwargs)
            return await call_action(row.action, ctx, **kwargs)
        finally:
            # the guard's dependency clears this too; a direct call to dispatch clears it itself
            _CURRENT.set(None)


# ------------------------------------------------------------------------ discovery and the mount

def _path_params(path: str) -> tuple[str, ...]:
    """The ``{name}`` segments of *path*, in order."""
    names: list[str] = []
    for piece in path.split("{")[1:]:
        name = piece.split("}", 1)[0]
        if name:
            names.append(name)
    return tuple(names)


def _placeholder(row: Endpoint) -> Callable[..., Any]:
    """A stand-in for a row whose action is not registered in this process.

    The route must still exist: its answer is the seam's typed 501, and a route that was never
    installed could only answer 404 — telling a client its URL is wrong when the installation is what
    is missing. Only the path segments are declared, because they are the one part of the wire shape
    the row itself states; with no action there is no signature to derive a body from.
    """
    parameters = [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str)
                  for name in _path_params(row.path)]

    async def placeholder(**kwargs: Any) -> None:
        """The action this row names is not registered in this process."""

    placeholder.__name__ = placeholder.__qualname__ = f"v1_{row.action}_unavailable"
    placeholder.__signature__ = inspect.Signature(parameters=parameters)  # type: ignore[attr-defined]
    return placeholder


def discovered_rows(plugin: Any) -> tuple[Endpoint, ...]:
    """Every validated endpoint row of this installation, discovered the one way that exists.

    The action registry is filled where the plugins load; the call here is the fallback for a
    process without that load, and it is idempotent. A row whose action is absent is DROPPED by the
    validator and logged — the absences that survive to the router are the ones a test or a later
    registry change makes.
    """
    from core.plugin_manager import PluginManager

    plugin_names = list(PluginManager(plugin.node).plugins)
    discover_actions(plugin_names)
    return discover_endpoints(plugin_names, registry=action_registry(), kind_of=kind_of)


def _surviving_rows(rows: Sequence[Endpoint], *, registry: Mapping[str, Callable],
                    kind: Callable[[str], str]) -> tuple[Endpoint, ...]:
    """The rows the static proof accepts, each refusal logged at ERROR with its row and action.

    The one problem that does NOT drop a row is ``V1`` (the action is absent from this process):
    that row still gets its route, and the seam answers ``action_unavailable`` (501) for it, so the
    client is told the installation is missing the action rather than that its URL is wrong.
    """
    problems = check_endpoints(rows, registry=registry, kind_of=kind)
    refused: set[int] = set()
    for problem in problems:
        if problem.code == "V1":
            log.warning("API v1: row %s %s names the action '%s', which is not registered in this "
                        "process - the row stays routed and answers '%s' (501).",
                        problem.row.method, problem.row.path, problem.row.action,
                        ACTION_UNAVAILABLE)
            continue
        refused.add(problem.index)
        log.error("API v1: row %s %s (action '%s') refused - %s", problem.row.method,
                  problem.row.path, problem.row.action, problem.message)
    return tuple(row for index, row in enumerate(rows) if index not in refused)


def _warn_shared_config_keys(verifier: ApiVerifier, rows: Sequence[Endpoint]) -> None:
    """Warn about legacy ``endpoints`` keys that also spell a v1 row's path.

    The two surfaces key apart (``servers`` vs ``v1/servers``), so such a key now governs the legacy
    route alone. It is reported because an operator who wrote it expected it to cover both.
    """
    v1_paths = {row.path.lstrip("/") for row in rows}
    for key in sorted(verifier.policy.endpoints):
        relative = key.lstrip("/")
        if relative in v1_paths:
            log.warning("API v1: config key 'endpoints.%s' names BOTH the legacy route '/%s' and "
                        "the v1 row '%s/%s'; it governs the LEGACY surface only - the v1 row reads "
                        "'endpoints.%s'.", relative, relative, PREFIX.lstrip("/"), relative,
                        config_key("/" + relative))


def install(app: Any, *, plugin: Any, verifier: ApiVerifier,
            rows: Sequence[Endpoint] | None = None) -> list[Any]:
    """Build the v1 router and append it to *app*; return what the include appended.

    *rows* defaults to :func:`discovered_rows`; a caller that already holds validated rows (a test,
    a re-mount) passes them to skip a second discovery.

    The rows are validated here whether they were discovered or handed in
    (:func:`_surviving_rows`), and four filters run per row, none of them silent:

    * a row the static proof refuses is dropped and logged at ERROR — the action being absent from
      the registry is the one exception, and that row keeps its typed 501;
    * the risk class must be in :data:`SLICE_RISKS` — otherwise ERROR, the row is not mounted;
    * ``endpoints.v1/<path>.enabled`` must not be false — the config's 404, decided at registration
      time exactly as the legacy surface decides it;
    * the action must be in this process's registry — otherwise the row is still routed, and the
      seam answers ``action_unavailable`` (501) for it.

    An empty router is not included at all: an install with no rows has nothing to serve, and an
    include would only add a wrapper to the route table for a reload to clean up.
    """
    wanted = tuple(rows) if rows is not None else discovered_rows(plugin)
    guard = TransportGuard(verifier, plugin)
    registry = action_registry()
    wanted = _surviving_rows(wanted, registry=registry, kind=kind_of)
    _warn_shared_config_keys(verifier, wanted)
    router = APIRouter()
    for row in wanted:
        risk = Risk(row.risk)
        if risk not in SLICE_RISKS:
            log.error("API v1: row %s %s (%s) is a %s action - not mounted. This slice serves %s.",
                      row.method, row.path, row.action, risk.value,
                      " and ".join(sorted(entry.value for entry in SLICE_RISKS)))
            continue
        if not verifier.policy.endpoint_enabled(config_key(row.path)):
            log.info("API v1: row %s %s is disabled in config ('endpoints.%s') - not mounted.",
                     row.method, row.path, config_key(row.path))
            continue
        fn = registry.get(row.action)
        build_route(router, row, fn=fn if fn is not None else _placeholder(row),
                    guard=guard, prefix=PREFIX)
    if not router.routes:
        log.warning("API v1: no row to mount - %s answers nothing.", PREFIX)
        return []
    log.info("API v1: mounted %d row(s) under %s.", len(router.routes), PREFIX)
    return include_router_counted(app, router)
