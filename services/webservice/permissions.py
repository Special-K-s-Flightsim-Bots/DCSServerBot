"""The ONE capability declaration of the DCSServerBot admin web UI, and its access gate.

Capabilities are declared exactly once (:data:`CAPABILITIES`) and referenced from two places
that must never disagree:

* a route, by declaring the capability of its path when it is registered with the registrar;
* a nav item, by naming the capability it needs to be shown.

The gate (:func:`capability_gate`) is attached to the application **at construction time**
(``FastAPI(dependencies=[Depends(capability_gate)])``) and is therefore *deny by default*:
FastAPI copies application-level dependencies into every ``APIRoute`` created afterwards, so a
route that carries no declaration is refused instead of shipping public. A per-route dependency
cannot express that rule — the absence of a dependency is not a check — and a
``StaticFiles``/``Mount`` bypasses the dependency chain entirely, which is why the shell owns
its assets through ``app.frontend()`` and never mounts (see :mod:`services.webservice.registry`).

What the gate can see: ``request.scope['route']`` is the matched ``APIRoute`` (its ``.path`` is
the full path, prefix included) and ``request.scope['path']`` is the requested path. For an
asset served by ``app.frontend`` the route object is ``None``, so the request path is what
resolves the declaration — assets are gated like everything else.

LIVE-ROLE RESOLUTION: which roles a request holds is decided by ONE pluggable resolver per
backend (:func:`set_role_resolver`), so the Discord OAuth backend and the headless backend feed
the same capability map. In this phase no backend exists yet, so the default resolver returns
an empty set and every non-public capability is refused — the safe direction.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Mapping, Sequence

from fastapi import HTTPException, Request
from starlette.routing import Mount

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the module import-cheap
    from .registry import Registrar
    from .scope import Scope

__all__ = [
    "PUBLIC",
    "CAPABILITIES",
    "SCOPE_GRANTS",
    "BASE_CAPABILITIES",
    "BASE_SCOPE_GRANTS",
    "APPLIED_OVERRIDES",
    "MANAGER_SCOPE_NEVER",
    "OverrideRefusal",
    "declare_capability",
    "declared_capabilities",
    "base_declaration",
    "manager_scope_allowed",
    "candidate_from_entries",
    "validate_candidate",
    "apply_role_overrides",
    "reset_role_overrides",
    "reset_to_declared_bases",
    "roles_for_capability",
    "scope_grants",
    "requirement_text",
    "allows",
    "visible_nav",
    "set_role_resolver",
    "role_names_for",
    "set_scope_resolver",
    "scope_for",
    "set_manager_resolver",
    "manages_console",
    "set_request_authenticated",
    "is_self_guarded",
    "undeclared_routes",
    "EXPECTED_MOUNTS",
    "walk_mounts",
    "audit_mounts",
    "capability_gate",
    "API_PREFIXES",
    "is_api_path",
    "wants_html",
    "wants_error_page",
    "ConditionLog",
    "DEFAULT_SUMMARY_SECONDS",
    "mark_identity_unverifiable",
    "identity_unverifiable",
    "RETRYABLE_STATUS",
]

log = logging.getLogger(__name__)

#: The explicit public capability: reachable without a signed-in identity. Never the default —
#: a route must *name* it, which is what keeps "public" a decision instead of an accident.
PUBLIC = "public"

#: capability name -> role names that grant it. An empty tuple means "declared, no role needed"
#: (distinct from :data:`PUBLIC` in intent, identical in effect for now). A capability that is
#: not in this mapping is undeclared and is refused by the gate.
#:
#: Role names are the canonical names of the bot's role model (``bot.roles``); per-backend role
#: resolution is what turns an identity into these names.
CAPABILITIES: dict[str, tuple[str, ...]] = {
    PUBLIC: (),
}

#: capability name -> whether a MANAGING SCOPE also satisfies it (the console's third kind of
#: identity: an identity whose scope holds a server's ``managed_by``). Declared in the SAME call as
#: the roles, for the same reason the roles are declared once: a capability that grows a second way
#: in must say so at the one place its requirement is written, and the gate, the log line, the
#: refusal detail and the sidebar all read it from there.
#:
#: It is deliberately NOT the default: a capability whose roles include a role no member must lose
#: (the Admin-only bot log) stays exactly as declared, and only the console's own pages opt in.
SCOPE_GRANTS: dict[str, bool] = {}

#: capability name -> the roles the CODE declared for it, recorded at the first declaration and never
#: written again. It is the base a configured OVERRIDE is computed from: an override extends and
#: narrows this pair, and recomputing one always starts from here, so a second apply replaces the
#: first instead of stacking on it.
BASE_CAPABILITIES: dict[str, tuple[str, ...]] = {}

#: capability name -> the scope rule the CODE declared for it (the second axis of
#: :data:`BASE_CAPABILITIES`, kept apart for the same reason the effective pair is).
BASE_SCOPE_GRANTS: dict[str, bool] = {}

#: capability name -> the effective pair an OVERRIDE installed, i.e. what :data:`CAPABILITIES` and
#: :data:`SCOPE_GRANTS` hold because of an override rather than because of a declaration. A value
#: that is neither the base nor this is somebody else's declaration, and applying over it is refused.
APPLIED_OVERRIDES: dict[str, tuple[tuple[str, ...], bool]] = {}


def declare_capability(name: str, roles: Iterable[str] = (), *,
                       scope_grants: bool = False) -> None:
    """Declare a capability. Called from module import of a feature (shell or plugin), never per
    request. Re-declaring with a different role set — or a different scope rule — is refused loudly:
    two declarations of one capability means the pair has already diverged.

    The FIRST declaration of a name is also its BASE (:data:`BASE_CAPABILITIES`): the shipped
    behaviour an override layers on top of, recorded once so a later apply can always recompute from
    it.
    """
    existing = CAPABILITIES.get(name)
    new_roles = tuple(roles)
    new_grants = bool(scope_grants)
    if existing is not None and (existing != new_roles or SCOPE_GRANTS.get(name, False) != new_grants):
        raise ValueError(f"capability '{name}' is already declared with roles {existing} "
                         f"(scope_grants={SCOPE_GRANTS.get(name, False)}), refusing to redeclare "
                         f"it with {new_roles} (scope_grants={new_grants})")
    CAPABILITIES[name] = new_roles
    SCOPE_GRANTS[name] = new_grants
    BASE_CAPABILITIES.setdefault(name, new_roles)
    BASE_SCOPE_GRANTS.setdefault(name, new_grants)


def roles_for_capability(name: str) -> tuple[str, ...] | None:
    """The roles required for a capability, or ``None`` when the capability is undeclared."""
    return CAPABILITIES.get(name)


def scope_grants(name: str) -> bool:
    """Whether a MANAGING SCOPE is a second way into *capability* (undeclared -> ``False``)."""
    return bool(SCOPE_GRANTS.get(name, False))


def base_declaration(capability: str) -> tuple[tuple[str, ...], bool] | None:
    """The pair the CODE declared for *capability* (roles, scope grant), or ``None`` when undeclared.

    Read by everything that has to tell an override from the shipped behaviour: the Access page's
    matrix (a cell counts as changed only when it differs from this), and the override engine itself
    (an apply always recomputes from here).
    """
    roles = BASE_CAPABILITIES.get(capability)
    if roles is None:
        return None
    return roles, bool(BASE_SCOPE_GRANTS.get(capability, False))


def declared_capabilities() -> tuple[str, ...]:
    """Every declared capability name except :data:`PUBLIC`, in declaration order.

    The enumeration the Access page renders and a save is checked against — read off the registry, so
    a page added later appears there without a second table to keep in step.
    """
    return tuple(name for name in CAPABILITIES if name != PUBLIC)


# ---------------------------------------------------------------------------------------------
# the configured OVERRIDE of the declarations (the Access page's stored edits)
# ---------------------------------------------------------------------------------------------
#
# A capability's requirement can be edited in the console instead of in code: the stored block
# EXTENDS and NARROWS the declared pair and is applied to a copy of it. Three rules hold the shape:
#
# * the BASE is never written — an apply recomputes every effective pair from the base, so applying
#   the same block twice lands the same values (nothing stacks);
# * ``Admin`` is never removed from a capability, and a ``managed_by`` scope grant is never enabled
#   for a capability that is machine- or installation-wide (:data:`MANAGER_SCOPE_NEVER`) — those two
#   are the cells no edit may produce;
# * the WEBSITE is the only reader. The bot's Discord commands keep their own role decorators, so
#   nothing below is reachable from a decorator path.

#: Capabilities a ``managed_by`` scope grant may never be enabled for, as DATA: an exact name, or a
#: family prefix ending in ``.``. A scope is a set of SERVER names, so a machine (``nodes.*``), an
#: installation-wide setting or report (``settings.*``, ``presets.*``, ``logs.view``,
#: ``statistics.view``), a server's CREATION or REMOVAL (``servers.add``/``servers.delete``), a
#: node's root mods (``mods.manage``), one server's Discord channels (``servers.config.channels``) and
#: this page's own pair have no server for it to narrow.
MANAGER_SCOPE_NEVER: tuple[str, ...] = (
    "nodes.",
    "settings.",
    "presets.",
    "mods.manage",
    "logs.view",
    "statistics.view",
    "servers.add",
    "servers.delete",
    "servers.config.channels",
    "access.view",
    "access.write",
)

#: the refusal kinds :func:`validate_candidate` reports. The WORDING of each sentence belongs to the
#: Access page (it is chrome, translated at render); this module names the rule that was broken.
UNKNOWN_CAPABILITY = "unknown_capability"
UNKNOWN_ROLE = "unknown_role"
LOCKOUT = "lockout"
STRUCTURAL = "structural"
ADMIN_LOCKED = "admin_locked"
MANAGER_LOCKED = "manager_locked"


@dataclass(frozen=True, slots=True)
class OverrideRefusal:
    """One refused edit: the rule that was broken, and what it was about.

    ``capability`` and ``role`` are the values the edit named (either may be empty). The caller turns
    a refusal into a sentence — the Access page for a save's one-shot notice, the loader for its
    logged startup error — so the two describe one rule with one vocabulary.
    """

    kind: str
    capability: str = ""
    role: str = ""


def manager_scope_allowed(capability: str) -> bool:
    """Whether a ``managed_by`` scope grant may exist for *capability* (:data:`MANAGER_SCOPE_NEVER`).

    An exact match, or a match on a family prefix (``nodes.`` covers every ``nodes.*`` name).
    """
    for denied in MANAGER_SCOPE_NEVER:
        if denied.endswith("."):
            if capability.startswith(denied):
                return False
        elif capability == denied:
            return False
    return True


def _role_list(value: object) -> list[object]:
    """One entry part as a list: a YAML sequence, a single value, or nothing at all.

    A hand-written block may state one role without the brackets (``remove: DCS Admin``); reading
    that as a list of characters would grant nothing and refuse nothing, so the shape is normalised
    here rather than silently misread.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def candidate_from_entries(entries: Mapping[str, Mapping[str, object]], *,
                           roles_known: Iterable[str]) -> tuple[
                               dict[str, tuple[tuple[str, ...], bool]], list[OverrideRefusal]]:
    """The effective pair of EVERY declared capability after *entries*, plus the refused edits.

    ``entries`` is the stored block, one key per capability, every part optional::

        music.write: {add: [Media Manager], remove: [DCS Admin]}
        music.control: {manager: true}

    The result is a full candidate — a capability the block does not name keeps its base — because
    an apply installs the whole map (that is what makes a second apply replace the first). A
    capability or a role the installation does not know is REFUSED here and left at its base: a typo
    must never silently grant nothing.
    """
    known_roles = set(roles_known)
    candidate: dict[str, tuple[tuple[str, ...], bool]] = {}
    refusals: list[OverrideRefusal] = []
    for name in declared_capabilities():
        base = base_declaration(name)
        if base is None:                                  # pragma: no cover - declared by definition
            continue
        roles = list(base[0])
        grants = base[1]
        entry = entries.get(name) or {}
        for role in _role_list(entry.get("add")):
            role = str(role)
            if role not in known_roles:
                refusals.append(OverrideRefusal(UNKNOWN_ROLE, name, role))
            elif role not in roles:
                roles.append(role)
        for role in _role_list(entry.get("remove")):
            role = str(role)
            if role not in known_roles:
                refusals.append(OverrideRefusal(UNKNOWN_ROLE, name, role))
            elif role in roles:
                roles.remove(role)
        manager = entry.get("manager")
        if manager is not None:
            grants = bool(manager)
        candidate[name] = (tuple(roles), grants)
    for name in entries:
        if name not in CAPABILITIES or name == PUBLIC:
            refusals.append(OverrideRefusal(UNKNOWN_CAPABILITY, str(name)))
    return candidate, refusals


def validate_candidate(candidate: Mapping[str, tuple[Iterable[str], bool]], *,
                       roles_known: Iterable[str], locked: Sequence[str] = (),
                       page: str = "") -> list[OverrideRefusal]:
    """Every rule a candidate matrix has to satisfy, as :class:`OverrideRefusal` values.

    Checked in this order, and the first non-empty answer is what a caller reports:

    1. names the installation does not know — a capability that is not declared, a role that is not
       in the bot's role model;
    2. LOCKOUT — no role able to open the Access page (*page*), which is the one outcome a save must
       never produce, asked before any cell rule so that a matrix that empties the page is reported
       as what it is;
    3. a STRUCTURAL capability (*locked*) whose pair was changed at all;
    4. ``Admin`` removed from a capability;
    5. a ``managed_by`` change on a capability whose scope axis is structural
       (:data:`MANAGER_SCOPE_NEVER`) — it can neither be enabled nor removed there.

    Rules 3-5 are per capability over the whole matrix, so a caller reports every offending
    capability rather than the first one it happens to walk into.
    """
    known_roles = set(roles_known)
    unknown: list[OverrideRefusal] = []
    for name, (roles, _manager) in candidate.items():
        if name not in BASE_CAPABILITIES or name == PUBLIC:
            unknown.append(OverrideRefusal(UNKNOWN_CAPABILITY, name))
            continue
        for role in roles:
            if str(role) not in known_roles:
                unknown.append(OverrideRefusal(UNKNOWN_ROLE, name, str(role)))
    if unknown:
        return unknown

    if page:
        reachable = candidate.get(page)
        if reachable is None or not (set(reachable[0]) & known_roles):
            return [OverrideRefusal(LOCKOUT, page)]

    refusals: list[OverrideRefusal] = []
    for name, (roles, manager) in candidate.items():
        base = base_declaration(name)
        if base is None:                                  # pragma: no cover - checked above
            continue
        pair = (tuple(roles), bool(manager))
        if name in locked and pair != base:
            refusals.append(OverrideRefusal(STRUCTURAL, name))
            continue
        if "Admin" not in pair[0] and "Admin" in base[0]:
            refusals.append(OverrideRefusal(ADMIN_LOCKED, name))
            continue
        if pair[1] != base[1] and not manager_scope_allowed(name):
            refusals.append(OverrideRefusal(MANAGER_LOCKED, name))
    return refusals


def _replacement_refused(name: str, target: tuple[tuple[str, ...], bool]) -> str | None:
    """Why *name* cannot hold *target*, or ``None`` when it can.

    The value currently held must be either the base, an earlier application of this engine, or the
    target itself — anything else is a DECLARATION this module did not make, and it is refused
    instead of being overwritten (the same failure ``declare_capability`` refuses: one capability,
    two pairs). Pure: it reads the tables and changes nothing.
    """
    base = base_declaration(name)
    if base is None:
        return f"capability '{name}' is not declared; nothing can override it"
    current = (CAPABILITIES.get(name), bool(SCOPE_GRANTS.get(name, False)))
    if current == target or current == base or current == APPLIED_OVERRIDES.get(name):
        return None
    return (f"capability '{name}' holds {current}, which is neither its declaration {base} nor an "
            f"applied override; refusing to replace it")


def _install_effective(name: str, target: tuple[tuple[str, ...], bool]) -> None:
    """Install one effective pair, leaving the base untouched. Cannot raise once checked.

    Only called after every member of an apply has passed :func:`_replacement_refused`, which is what
    makes the whole apply atomic: this function has no refusing branch left.
    """
    base = base_declaration(name)
    if base is None:                                      # pragma: no cover - checked by the caller
        raise ValueError(f"capability '{name}' is not declared; nothing can override it")
    if (CAPABILITIES.get(name), bool(SCOPE_GRANTS.get(name, False))) == target:
        return
    # the entry is dropped before the re-declaration so ``declare_capability`` sees a FRESH name: it
    # refuses a differing re-declaration by construction, and the declaration is restored first here
    CAPABILITIES.pop(name, None)
    SCOPE_GRANTS.pop(name, None)
    declare_capability(name, target[0], scope_grants=target[1])
    if target == base:
        APPLIED_OVERRIDES.pop(name, None)
    else:
        APPLIED_OVERRIDES[name] = target


def apply_role_overrides(candidate: Mapping[str, tuple[Iterable[str], bool]]) -> dict[
        str, tuple[tuple[str, ...], bool]]:
    """Install the effective declarations a candidate matrix describes; return what actually differs.

    The WHOLE matrix is applied, not only the names the stored block mentions: a capability the
    candidate leaves at its base is RESTORED to it, which is what makes a save that drops an entry
    take effect, and what makes applying the same block twice land the same values instead of
    stacking.

    ALL-OR-NOTHING: every target is computed and checked first (:func:`_replacement_refused`), and
    only then installed. A refusal therefore raises with the matrix exactly as it was — no
    capability is left holding an override the caller was told did not land.

    Applied on the next request by construction — :func:`allows`, the gate, the sidebar and the tab
    strips read :data:`CAPABILITIES` per call, so nothing is cached anywhere that would need clearing.
    """
    targets: dict[str, tuple[tuple[str, ...], bool]] = {}
    bases: dict[str, tuple[tuple[str, ...], bool]] = {}
    for name in declared_capabilities():
        base = base_declaration(name)
        if base is None:                                  # pragma: no cover - a name that vanished
            continue
        target = candidate.get(name, base)
        target = (tuple(target[0]), bool(target[1]))
        refused = _replacement_refused(name, target)
        if refused is not None:
            raise ValueError(refused)
        targets[name] = target
        bases[name] = base
    applied: dict[str, tuple[tuple[str, ...], bool]] = {}
    for name, target in targets.items():
        _install_effective(name, target)
        if target != bases[name]:
            applied[name] = target
    return applied


def reset_role_overrides() -> dict[str, tuple[tuple[str, ...], bool]]:
    """Drop every override: every capability back to its declared pair. Returns what was reset."""
    applied = dict(APPLIED_OVERRIDES)
    bases = {name: base_declaration(name) for name in declared_capabilities()}
    apply_role_overrides({name: pair for name, pair in bases.items() if pair is not None})
    return applied


def reset_to_declared_bases() -> dict[str, tuple[tuple[str, ...], bool]]:
    """Return every declared capability to the pair the CODE declared, and drop the overrides.

    The three tables are PROCESS state while an APPLICATION is not: ``WebService.stop()`` drops the
    app and ``start()`` builds a new one in the same process (the master/agent switch), so the
    second install re-declares what the first one already holds. With an override still applied, that
    re-declaration looks like two declarations of one capability and :func:`declare_capability`
    refuses it — the rebuild then raises out of the install and the service does not start. Restoring
    the bases first makes declare-then-redeclare idempotent; the stored block is applied again after
    the pages have declared, so the rebuilt app lands on the same matrix.

    Returns what was dropped, for the caller's log line.
    """
    dropped = dict(APPLIED_OVERRIDES)
    for name, roles in BASE_CAPABILITIES.items():
        CAPABILITIES[name] = roles
        SCOPE_GRANTS[name] = bool(BASE_SCOPE_GRANTS.get(name, False))
    APPLIED_OVERRIDES.clear()
    return dropped


def requirement_text(capability: str) -> str:
    """The capability's requirement in PLAIN WORDS — the ONE wording of record.

    Two readers must never disagree about what a page asks for: the access gate logs it, and the
    refusal it raises carries it as the JSON ``detail`` (the wire contract an API caller reads).
    Written once here, so the sentence a person reads in a log line and the sentence an API caller
    reads are the same sentence — including the console's manager clause, which the previous
    wording (a bare ``', '.join(roles)``) could not state at all and which named ``DCS``.
    """
    roles = CAPABILITIES.get(capability)
    if roles is None:
        return "a capability this installation does not declare"
    names = ", ".join(roles)
    if scope_grants(capability):
        return (f"{names}, or a manager of at least one server" if names
                else "a manager of at least one server")
    return names or "any signed-in caller"


def allows(capability: str, roles: Iterable[str], *, manager: bool = False) -> bool:
    """THE capability decision: does this identity satisfy this capability?

    Three readers that must not be allowed to disagree: the access gate calls it for a request, the
    navigation calls it for the sidebar (:func:`visible_nav`), and the dashboard's tab strip calls
    it per tab. A nav item rendered by a second spelling of this rule is the exact failure the
    registrar's ``NavItem`` exists to prevent -- a menu entry whose route answers 403.

    The identity has two dimensions, and this signature states both: *roles* (the flat role names of
    the capability's declaration) and *manager* (whether the resolved SCOPE holds a server's
    ``managed_by`` -- see ``scope.manages_any_server``). A capability declared ``scope_grants`` is
    satisfied by either; the manager flag is resolved by the caller from the live identity, and
    defaults to ``False`` so a caller that cannot resolve a scope (a standalone render, a test
    double) gets the STRICTER answer.

    ``False`` for an UNDECLARED capability (deny by default: a route declaring a capability nobody
    declared is a configuration error, and a nav item doing so would advertise a door that cannot
    open). ``True`` for :data:`PUBLIC` and for a declared capability with an empty role set --
    "declared, no role needed" -- which is what makes those two shapes behave identically here, as
    the gate already did.
    """
    declared = CAPABILITIES.get(capability)
    if declared is None:
        return False
    if capability == PUBLIC or not declared:
        return True
    if set(declared) & set(roles):
        return True
    return manager and scope_grants(capability)


def visible_nav(nav_items: Iterable, roles: Iterable[str], *, manager: bool = False) -> list:
    """The nav items a viewer holding *roles* (and *manager*) may be offered.

    Reads the SAME :func:`allows` the gate uses, so ``offered ⊆ authorised`` holds by construction
    rather than by two implementations happening to agree.
    """
    return [item for item in nav_items
            if allows(getattr(item, "capability", None), roles, manager=manager)]


# ---------------------------------------------------------------------------------------------
# live identity -> role names (one resolver per backend, set at startup)
# ---------------------------------------------------------------------------------------------

_ROLE_RESOLVER: Callable[[Request], Iterable[str]] | None = None


def set_role_resolver(resolver: Callable[[Request], Iterable[str]] | None) -> None:
    """Install the backend's role resolver. The auth backends call this; tests call it to
    prove a declared capability is reachable *through the real gate*."""
    global _ROLE_RESOLVER
    _ROLE_RESOLVER = resolver


def role_names_for(request: Request) -> frozenset[str]:
    """The role names the current request holds, through the installed resolver.

    A failing resolver denies rather than crashes the request, and the reason is logged — an
    authorization check that answers 500 is a check nobody can distinguish from an outage."""
    if _ROLE_RESOLVER is None:
        return frozenset()
    try:
        return frozenset(_ROLE_RESOLVER(request) or ())
    except Exception:
        log.exception("Role resolver failed, denying the request")
        return frozenset()


# ---------------------------------------------------------------------------------------------
# live identity -> per-server scope (the second resolver, same shape, installed beside the first)
# ---------------------------------------------------------------------------------------------

_SCOPE_RESOLVER: Callable[[Request], "Scope"] | None = None


def set_scope_resolver(resolver: Callable[[Request], "Scope"] | None) -> None:
    """Install the backend's per-request SCOPE resolver (``AuthManager.scope_for``).

    The second resolver beside :func:`set_role_resolver`, and installed at the same moment
    (``install_auth``): the roles answer "may this identity reach this page at all", the scope
    answers "which servers may it see and act on". The scope is resolved HERE rather than inside a
    read model for the same reason the roles are — it must not be possible for a read path to skip
    it (spec §10.6), and a failing resolver must deny rather than crash a page.
    """
    global _SCOPE_RESOLVER
    _SCOPE_RESOLVER = resolver


def scope_for(request: Request) -> "Scope":
    """The per-server scope of the current request, through the installed resolver.

    Resolved PER REQUEST and never cached across identities (like the roles): a Discord role change,
    a removed local user or an edited ``managed_by`` lands on the next request, and nothing about the
    scope is ever read from the session cookie or a URL parameter — the resolver takes only the
    request, and what it reads is the live identity.

    FAIL CLOSED at every step: no resolver, a resolver that returned nothing, or a resolver that
    raised, all answer :meth:`services.webservice.scope.Scope.unresolved` — a non-Admin identity is
    then scoped to NOTHING rather than to everything. The failure is logged (it is an incident, not
    an expected refusal: the identity layer already logs the expected ones), and the resolved scope
    is a value, so the reason is visible to whoever renders the empty page.
    """
    if _SCOPE_RESOLVER is None:
        return _no_scope()
    try:
        return _SCOPE_RESOLVER(request) or _no_scope()
    except Exception:
        log.exception("Scope resolver failed, denying the request")
        return _no_scope()


def _no_scope() -> "Scope":
    """The empty scope. Imported lazily so this module stays free of the scope module's imports."""
    from .scope import Scope
    return Scope.unresolved()


# ---------------------------------------------------------------------------------------------
# live identity -> "does this identity MANAGE a server" (the third resolver, same shape)
# ---------------------------------------------------------------------------------------------

_MANAGER_RESOLVER: Callable[[Request], bool] | None = None


def set_manager_resolver(resolver: Callable[[Request], bool] | None) -> None:
    """Install the backend's MANAGER resolver (``AuthManager.manages_any_server``).

    The third and last half of an identity, installed at the same moment as the other two
    (``install_auth``): the roles answer "may this identity reach this page at all", the scope
    answers "which servers may it see and act on", and this one answers the question the console's
    access rule needs — "is the scope what makes this identity a manager of at least one server".
    It is resolved HERE, through the permissions module, for the same reason the roles and the scope
    are: a page cannot forget it and a route cannot re-derive it (see :func:`allows`).
    """
    global _MANAGER_RESOLVER
    _MANAGER_RESOLVER = resolver


#: the request attribute the answer is remembered on, so one render's gate, sidebar, tab strip and
#: links all read ONE answer (and a request that asks twice does not read the cluster twice)
_MANAGER_STATE_ATTR = "webui_manager"


def manages_console(request: Request) -> bool:
    """Whether the current request's identity is a MANAGER of at least one server.

    DYNAMIC and per request, like the roles and the scope: the resolver reads the live identity and
    the live cluster on every request, so a ``managed_by`` rename or a config edit lands on the
    next one and nothing is remembered in the session cookie.

    FAIL CLOSED at every step, exactly like :func:`scope_for` and :func:`role_names_for`: no
    resolver, a resolver that returned nothing, or a resolver that raised all answer ``False`` — a
    manager is then refused rather than shown the cluster. The failure is logged (it is an incident,
    not an expected refusal).

    The answer is memoized ON THE REQUEST (:data:`_MANAGER_STATE_ATTR`), because several readers
    within one render legitimately ask the same question (the access gate, the sidebar, the tab
    strip, the links) and they must all get the same answer for one request: a fact re-resolved
    mid-render could otherwise let the gate refuse what the sidebar offered.
    """
    state = getattr(request, "state", None)
    if state is not None:
        remembered = getattr(state, _MANAGER_STATE_ATTR, None)
        if remembered is not None:
            return bool(remembered)
    value = _resolve_manager(request)
    if state is not None:
        try:
            setattr(state, _MANAGER_STATE_ATTR, value)
        except Exception:  # pragma: no cover - a request whose state refuses writes
            pass
    return value


def _resolve_manager(request: Request) -> bool:
    """Ask the installed resolver, fail closed, and log the failure once per request."""
    if _MANAGER_RESOLVER is None:
        return False
    try:
        return bool(_MANAGER_RESOLVER(request))
    except Exception:
        log.exception("Manager resolver failed, denying the request")
        return False


# ---------------------------------------------------------------------------------------------
# expected refusals vs. incidents: the identity flag, and logging a CONDITION once
# ---------------------------------------------------------------------------------------------

def set_request_authenticated(request: Request, authenticated: bool) -> None:
    """Record, on the request, whether the identity layer RESOLVED an identity for it.

    The auth manager's role resolver calls this — it is the one thing that knows the difference
    between "anonymous or an expired/unresolvable session" and "signed in but not authorised" — so
    the gate can tell an EXPECTED refusal (not an incident, not worth a log line per request) from a
    genuine authorization refusal (worth exactly one). It is never trusted for authorization: it
    only chooses *how loudly* a refusal is recorded.
    """
    state = getattr(request, "state", None)
    if state is not None:
        state.webui_authenticated = bool(authenticated)


def _is_authenticated(request: Request) -> bool:
    """Whether an identity was resolved for this request (see :func:`set_request_authenticated`).

    Falls back to the session reference when no identity layer reported one: a request with no
    session reference cannot have been resolved, so the fallback answers the safe way.
    """
    state = getattr(request, "state", None)
    reported = getattr(state, "webui_authenticated", None)
    if reported is not None:
        return bool(reported)
    return _session_ref_present(request)


def _session_ref_present(request: Request) -> bool:
    """Whether the request carries an identity reference at all (a session that names somebody)."""
    try:
        from .auth import session_identity_ref
    except Exception:  # pragma: no cover - the auth package is importable wherever this is
        return False
    try:
        return session_identity_ref(request) is not None
    except Exception:  # pragma: no cover - a broken session must not break the gate
        return False


#: how long a CONDITION may stay quiet before its suppressed repeats are surfaced as ONE line
DEFAULT_SUMMARY_SECONDS = 300.0


class ConditionLog:
    """A standing CONDITION, logged once — never once per request.

    A refusal caused by something that does not change between requests (the bot has no member list,
    a role set cannot satisfy a capability, a route declares no capability) is ONE fact, not N
    incidents: a dashboard left open on an expired session would otherwise append a paragraph of
    explanation on every poll. The first occurrence is logged IN FULL — the diagnosis survives —
    while later ones are counted; when the summary interval elapses, ONE line names the count and
    when the condition was first seen, and :meth:`cleared` surfaces the count when it stops holding.

    Keys are the condition's IDENTITY (a capability, a route path, a reason bucket) and never
    request-varying data — a key that changed per request would be a per-request log again. A
    repeating line carries no subject id.
    """

    def __init__(self, logger=None, *, interval: float = DEFAULT_SUMMARY_SECONDS, clock=None):
        self._logger = logger or log
        self._interval = interval
        self._clock = clock or time.monotonic
        #: key -> [first wall-clock time, suppressed count, last summary monotonic time]
        self._state: dict[object, list] = {}

    def hit(self, key, message, *args, level=logging.WARNING, summary=None,
            summary_level=None) -> bool:
        """Record one occurrence of *key*; returns whether a record was emitted.

        * the first occurrence logs *message* with *args* at *level*;
        * repeats increment the count and emit nothing until *interval* seconds have passed since
          the last summary, at which point ``summary(count, since)`` is logged at *summary_level*
          (defaulting to *level*) and the count resets. ``summary=None`` suppresses silently — for
          a condition whose repeats are expected and whose first line is the whole story.
        """
        now = self._clock()
        entry = self._state.get(key)
        if entry is None:
            self._state[key] = [time.time(), 0, now]
            self._logger.log(level, message, *args)
            return True
        entry[1] += 1
        if summary is None or now - entry[2] < self._interval:
            return False
        entry[2] = now
        count = entry[1]
        entry[1] = 0
        since = time.strftime("%H:%M", time.localtime(entry[0]))
        self._logger.log(summary_level or level, "%s", summary(count, since))
        return True

    def cleared(self, key, message, *args, level=logging.DEBUG) -> bool:
        """Drop *key*; when it had suppressed repeats, surface the count once.

        The suppressed count is the FIRST format argument of *message* (``"%d ..."``), so a caller
        does not have to know it to write the sentence. Called when the condition stops holding, so
        a reader learns both that it ended and how many requests were quieted while it lasted.
        """
        entry = self._state.pop(key, None)
        if entry is None or entry[1] <= 0:
            return False
        self._logger.log(level, message, entry[1], *args)
        return True


_GATE_CONDITIONS_ATTR = "webui_gate_conditions"


def _gate_conditions(app) -> ConditionLog:
    """The gate's condition log, held on the application (one per app object, like every other
    piece of shell state). A fresh app — a stop/start or a takeover — starts with a clean log, which
    is correct: nothing is known about the previous app object's conditions."""
    state = getattr(app, "state", None)
    if state is None:  # pragma: no cover - every FastAPI app has state
        return ConditionLog(log)
    conditions = getattr(state, _GATE_CONDITIONS_ATTR, None)
    if conditions is None:
        conditions = ConditionLog(log)
        setattr(state, _GATE_CONDITIONS_ATTR, conditions)
    return conditions


# ---------------------------------------------------------------------------------------------
# "could not be verified" vs. "is not allowed": the retryable refusal
# ---------------------------------------------------------------------------------------------
#
# An identity backend can fail to resolve an identity for a reason that PASSES — the bot is
# restarting, it has no member list, its lookup raised or rate limited. That is not the same thing
# as an identity that is not allowed in, and answering it with 401/403 tells the client to STOP
# (which is exactly what the console's poll loop does with a refusal). The backend records the
# distinction on the REQUEST, and the gate turns it into a RETRYABLE answer with ``Retry-After``:
# an open dashboard then recovers by itself once the backend is ready, with no new sign-in.

_UNVERIFIABLE_ATTR = "webui_identity_unverifiable"

#: the status a RETRYABLE refusal is answered with. Named here, next to the marker, because the
#: answer is built in the gate and described in the identity backends' log lines: one constant is
#: what keeps the number in the log and the number on the wire from drifting apart (they did: the
#: backend logged ``"retryable (HTTP %d)" % Retry-After`` -- "HTTP 5" -- while answering 503).
RETRYABLE_STATUS = 503


def mark_identity_unverifiable(request: Request, *, retry_after: int) -> None:
    """Record, on the request, that the identity could not be VERIFIED *right now*.

    Called by the identity backend when the member it was asked about could not be resolved because
    the lookup did not answer (never when the lookup answered "not a member"). It changes nothing
    about authorization — the unverifiable identity holds no capability, exactly like an anonymous
    one — it chooses only HOW the refusal is answered: a retryable 503 instead of a 401/403 the
    client would stop on. ``retry_after`` is the seconds the answer carries.
    """
    state = getattr(request, "state", None)
    if state is not None:
        try:
            setattr(state, _UNVERIFIABLE_ATTR, int(retry_after))
        except Exception:  # pragma: no cover - a request whose state refuses writes
            pass


def identity_unverifiable(request: Request) -> int | None:
    """The ``Retry-After`` seconds when the identity was UNVERIFIABLE for this request, else None."""
    value = getattr(getattr(request, "state", None), _UNVERIFIABLE_ATTR, None)
    return value if isinstance(value, int) else None


# ---------------------------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------------------------

def _own_dependencies(route, app=None) -> list:
    """Dependencies a route carries *besides* this gate (FastAPI copies the gate into every
    route added after it was attached, so the gate itself is never an "own" dependency).

    Two shapes have to be covered, because FastAPI stores the two kinds differently on this
    version (verified against 0.141.1): a route added through ``app.add_api_route(..., 
    dependencies=[...])`` keeps them in ``route.dependant.dependencies``, while a router included
    with ``app.include_router(router, dependencies=[...])`` keeps them in the wrapper's
    ``include_context.dependencies`` (merged with the app-level ones at include time, which is why
    the app-level dependencies are subtracted again here).

    If a future version moves them elsewhere this returns ``[]`` — i.e. the route counts as
    *not* self-guarded and is refused. The failure direction is a loud 403, never a silent hole.
    """
    dependant = getattr(route, "dependant", None)
    own = [dep for dep in (getattr(dependant, "dependencies", None) or ())
           if _dependency_callable(dep) is not capability_gate]
    if own or app is None:
        return own

    app_dependencies = {_dependency_key(dep)
                        for dep in (getattr(getattr(app, "router", None), "dependencies", None) or ())}
    for wrapper in (getattr(getattr(app, "router", None), "routes", None) or ()):
        context = getattr(wrapper, "include_context", None)
        if context is None:
            continue
        inner = getattr(context, "included_router", None) or getattr(wrapper, "original_router", None)
        if not any(candidate is route for candidate in (getattr(inner, "routes", None) or ())):
            continue
        return [dep for dep in (getattr(context, "dependencies", None) or ())
                if _dependency_callable(dep) is not capability_gate
                and _dependency_key(dep) not in app_dependencies]
    return []


def _dependency_callable(dependant_param):
    """The callable behind a dependency, in either shape FastAPI uses on this version: a
    ``Dependant`` (route-level, ``.call``) or a ``Depends`` (app/router-level, ``.dependency``)."""
    return getattr(dependant_param, "call", None) or getattr(dependant_param, "dependency", None)


def _dependency_key(dependant_param) -> tuple:
    return (_dependency_callable(dependant_param),
            getattr(dependant_param, "use_cache", True),
            getattr(dependant_param, "scope", None))


def is_self_guarded(route, app=None) -> bool:
    """True when a route brings its own guard instead of a capability declaration.

    This is the documented legacy surface, not a feature: ``plugins/restapi`` (and the
    WebService's own debug endpoints) install their routes with their own auth or local-network
    dependency and must keep answering exactly as before. Such a route is *allowed by the gate*
    and logged; a route with neither a declaration nor an own guard is refused. Removal
    condition: when every surface registers its routes with the registrar, this exemption goes
    away and the gate becomes a pure deny-by-default floor.
    """
    return bool(_own_dependencies(route, app))


def undeclared_routes(app, registrar: "Registrar") -> list[str]:
    """Audit helper: routes reachable on *app* that carry no declaration.

    Walks the real app (``app.routes`` plus the ``_IncludedRouter`` wrappers and the frontend
    routes inside them) because a walk of ``app.routes`` alone misses both. Returns the paths
    of undeclared self-guarded routes — the legacy surface the gate lets through. This exists
    so a test can assert the legacy set explicitly instead of trusting that it is empty.
    """
    out: list[str] = []
    for route in _walk_routes(app):
        path = getattr(route, "path", None)
        if path is None:
            continue
        if registrar.capability_for(route=route, path=path) is not None:
            continue
        if is_self_guarded(route, app):
            out.append(path)
    return sorted(set(out))


def _walk_routes(app) -> list:
    """Every APIRoute of the application, recursing into include wrappers and frontend routes.

    ``include_router`` appends a single ``_IncludedRouter`` wrapper to ``app.routes`` and keeps
    the router's own ``APIRoute``s inside it, so both containers have to be opened here. A router
    may also hold LOW-PRIORITY routes (``_low_priority_routes`` — how the console registers its
    pages, so a route another component registered on the same path wins), and those are yielded
    too, or a walk would silently miss every console page.
    """
    found: list = []
    containers = list(getattr(getattr(app, "router", app), "routes", []) or [])
    containers.extend(list(getattr(getattr(app, "router", app), "_low_priority_routes", []) or []))
    seen: set[int] = set()
    while containers:
        route = containers.pop(0)
        if id(route) in seen:
            continue
        seen.add(id(route))
        inner = getattr(route, "original_router", None)
        if inner is not None:
            containers.extend(getattr(inner, "routes", []) or [])
            containers.extend(getattr(inner, "_low_priority_routes", []) or [])
            continue
        inner = getattr(route, "routes", None)          # e.g. the frontend route group
        if inner is not None and not hasattr(route, "endpoint"):
            containers.extend(inner)
            continue
        found.append(route)
    frontend = getattr(getattr(app, "router", None), "_frontend_routes", None)
    found.extend(list(getattr(frontend, "routes", []) or []))
    return found


def walk_mounts(app) -> list:
    """Every ``Mount`` reachable on *app*, opening the include wrappers it may sit behind.

    A ``Mount`` is not a gated surface at all: it answers before the dependency chain runs, so a
    request to it never reaches :func:`capability_gate` (this is the documented hole that made the
    shell serve its assets through ``app.frontend`` instead). It is also *invisible* to
    :func:`_walk_routes` — a mount carries no ``endpoint`` and no ``original_router``, and whether
    it exposes ``.routes`` depends on the Starlette version — so this is its own walk rather than a
    filter over that one.
    """
    found: list = []
    containers = list(getattr(getattr(app, "router", app), "routes", []) or [])
    seen: set[int] = set()
    while containers:
        route = containers.pop(0)
        if id(route) in seen:
            continue
        seen.add(id(route))
        if isinstance(route, Mount):
            found.append(route)
            continue
        inner = (getattr(route, "original_router", None)
                 or getattr(route, "included_router", None))
        if inner is not None:
            containers.extend(getattr(inner, "routes", []) or [])
            continue
        inner = getattr(route, "routes", None)
        if inner is not None and not hasattr(route, "endpoint"):
            containers.extend(inner)
    return found


#: Mounts the shell expects. EMPTY, and that is the point: the registrar serves assets through
#: ``app.frontend`` (which runs the dependency chain), so a mount is always somebody's mistake.
EXPECTED_MOUNTS: tuple[str, ...] = ()


def audit_mounts(app, *, context: str = "startup") -> list[str]:
    """Report every ``Mount`` on *app* outside :data:`EXPECTED_MOUNTS`; return their paths.

    Logged at ERROR, because the consequence is silent by construction: an ungated surface that
    answers an anonymous request and cannot be seen in any declaration, any test of the route table,
    or the access gate's own logs. Called from ``install_shell`` (every application the service
    builds, including the one rebuilt after a stop/start) and from the gate whenever the application's
    route table has changed since the last audit, which is what catches a mount added after the
    install — including one added after the console is already serving.
    """
    offenders = [getattr(mount, "path", "") or "/" for mount in walk_mounts(app)]
    offenders = [path for path in offenders if path not in EXPECTED_MOUNTS]
    for path in offenders:
        log.error("Access gate: the application mounts '%s', a surface that BYPASSES the web UI "
                  "access gate (%s audit). Serve assets through the registrar's "
                  "``register_assets``/``app.frontend`` instead, or declare it here if it is "
                  "genuinely intended.", path, context)
    return offenders


def _audit_mounts_once(app) -> None:
    """The per-application audit, run whenever the route table has CHANGED since the last one.

    Cheap (one length comparison per request and, only when the table grew, one route-table walk) and
    it covers the window a startup-only audit cannot — but keyed on the route-table size rather than
    a one-shot "first request" flag, because registration does not end with the first request: the
    plugin that holds the app object touches it *after* the service starts (``plugins/restapi``
    waits for the bot to be ready before registering its routes), so a mount can appear while the
    console is already serving. With a one-shot flag that mount is silently reachable forever; with
    a size key, the next request after the table grew re-audits.
    """
    state = getattr(app, "state", None)
    if state is None:
        return
    routes = getattr(getattr(app, "router", None), "routes", None)
    if routes is None:  # pragma: no cover - every FastAPI app has a router with routes
        return
    size = len(routes)
    if getattr(state, "_webui_mounts_audited", None) == size:
        return
    setattr(state, "_webui_mounts_audited", size)
    audit_mounts(app, context="request audit")


async def capability_gate(request: Request) -> None:
    """The application-level, deny-by-default capability gate (one dependency for the whole app)."""
    _audit_mounts_once(request.app)
    registrar = getattr(getattr(request.app, "state", None), "webui_registrar", None)
    path = request.scope.get("path", "")
    route = request.scope.get("route")
    if registrar is None:
        # No shell was installed. That is the LEGITIMATE state when the service runs with the
        # frontend off (`frontend: false`): there are no declared routes to authorise, and the
        # legacy surface — a route that brings its OWN guard, e.g. ``plugins/restapi`` — must keep
        # answering exactly as it does when the shell IS installed. Anything else still fails
        # closed: an undeclared route with no shell cannot be authorised at all (this is also the
        # loud guard for a shell that failed to install).
        if is_self_guarded(route, request.app):
            log.debug("Access gate: %s %s is not declared but carries its own guard, allowing it "
                      "(no admin shell installed).", request.method, path)
            return
        log.error("Access gate: no web UI registrar on the application, refusing %s %s",
                  request.method, path)
        raise HTTPException(status_code=403, detail="The admin web UI is not installed.")

    capability = registrar.capability_for(route=route, path=path)
    if capability is None:
        if is_self_guarded(route, request.app):
            log.debug("Access gate: %s %s is not declared but carries its own guard, allowing it "
                      "(legacy surface).", request.method, path)
            return
        # An undeclared route repeats on every request, and the set of offending paths is finite:
        # the WARNING is the CONDITION (a route nobody declared), logged once per path.
        _gate_conditions(request.app).hit(
            ("undeclared", path),
            "Access gate: refusing %s %s - no capability declared (deny by default).",
            request.method, path, level=logging.WARNING)
        raise HTTPException(status_code=403,
                            detail="This route declares no web UI capability (deny by default).")

    if capability == PUBLIC:
        return

    roles = CAPABILITIES.get(capability)
    if roles is None:
        # A route declared a capability nobody declared in CAPABILITIES: a configuration error.
        log.error("Access gate: %s %s declares unknown capability '%s', refusing.",
                  request.method, path, capability)
        raise HTTPException(status_code=403, detail="This route declares an unknown capability.")

    if not roles:
        return

    # The identity has two dimensions and the declaration decides which apply. The MANAGER fact is
    # resolved LAZILY: a role that already satisfies the capability never consults it, so the
    # cluster is not read for the staff who reach every page through their roles (and the lookups
    # for the ones who do not are memoized on the request — see permissions.manages_console).
    roles_held = role_names_for(request)
    granted = allows(capability, roles_held)
    if not granted and scope_grants(capability):
        granted = allows(capability, roles_held, manager=manages_console(request))

    if not granted:
        requirement = requirement_text(capability)
        retry_after = identity_unverifiable(request)
        if retry_after is not None:
            # The identity could not be VERIFIED (a bot restart, no member list, a lookup that did
            # not answer). Refusing with 403 would tell the client to STOP — the console's poll loop
            # does exactly that — so the answer is RETRYABLE: 503 + Retry-After, still with NO data
            # (fail closed is unchanged). A CONDITION, logged once through the shared helper.
            _gate_conditions(request.app).hit(
                ("identity-unverifiable",),
                "Access gate: answering %s %s with a retryable %d - the caller's identity could "
                "not be verified right now (no data is served; the session is kept and the client "
                "is told to retry).", request.method, path, RETRYABLE_STATUS,
                level=logging.INFO)
            raise HTTPException(
                status_code=RETRYABLE_STATUS,
                detail="Your identity could not be verified right now. Retry shortly - no new "
                       "sign-in is needed.",
                headers={"Retry-After": str(retry_after)})
        if _is_authenticated(request):
            # a signed-in caller the declaration does not cover: worth ONE line per capability
            # (never per request — a dashboard polls). The wording is the requirement's, so the log
            # and the refusal below cannot describe the same rule differently.
            _gate_conditions(request.app).hit(
                ("capability", capability),
                "Access gate: refusing %s %s - capability '%s' needs one of %s.",
                request.method, path, capability, requirement, level=logging.INFO)
        else:
            # an EXPECTED refusal — anonymous, or a session the identity layer could not resolve
            # (an expired cookie, a bot that cannot see its members). A dashboard on such a session
            # polls forever, so anything above DEBUG here would flood the log.
            log.debug("Access gate: refusing %s %s - capability '%s' needs a signed-in session "
                      "(no identity resolved).", request.method, path, capability)
        raise HTTPException(status_code=403,
                            detail=f"Not authorized (needs one of: {requirement}).")


# ---------------------------------------------------------------------------------------------
# the refusal's two faces: a page for a browser, the JSON it has always had for everyone else
# ---------------------------------------------------------------------------------------------
#
# The gate refuses with an ``HTTPException``, and FastAPI's default handler renders it as
# ``application/json {"detail": ...}`` for everyone. That is the right answer for an API caller and
# a useless one for a person: a browser navigation answered with a JSON document tells the visitor
# nothing they can act on. This is the ONE predicate that decides which of the two they get —
# declared here, beside the refusal it describes, and read by the single handler factory in
# :mod:`services.webservice.shell` (never re-implemented per route).

#: Path prefixes owned by an API surface by convention: a caller there gets JSON whatever it asked
#: for. The second signal exists because ``Accept`` alone would hand a page to a browser that
#: wandered onto an API path — a document it would render into an ``<img>`` or an alert box.
API_PREFIXES: tuple[str, ...] = ("/api",)


def is_api_path(path: str) -> bool:
    """Whether *path* belongs to an API surface (:data:`API_PREFIXES`)."""
    path = path or ""
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/")
               for prefix in API_PREFIXES)


def _accept_quality(accept: str) -> dict[str, float]:
    """Media type -> its q-value, the best one when it is listed twice."""
    out: dict[str, float] = {}
    for item in str(accept or "").split(","):
        bits = item.split(";")
        media = bits[0].strip().lower()
        if not media:
            continue
        quality = 1.0
        for parameter in bits[1:]:
            parameter = parameter.strip().lower()
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        out[media] = max(out.get(media, 0.0), quality)
    return out


def wants_html(request: Request) -> bool:
    """Whether the caller asked for a document rather than data.

    A wildcard is deliberately NOT a browser: ``fetch``/XHR callers send ``*/*`` and must keep the
    JSON body they would otherwise render as text, so only an explicit ``text/html`` counts. When a
    caller names both, the one it ranks higher wins (``text/html;q=0.5, application/json`` is a
    JSON consumer).
    """
    ranks = _accept_quality(request.headers.get("accept", ""))
    html = ranks.get("text/html", 0.0)
    if html <= 0.0:
        return False
    return html >= ranks.get("application/json", 0.0)


def wants_error_page(request: Request) -> bool:
    """THE two-face decision: a browser navigation on a page path gets a page, the rest keep JSON."""
    if is_api_path(request.scope.get("path", "")):
        return False
    return wants_html(request)
