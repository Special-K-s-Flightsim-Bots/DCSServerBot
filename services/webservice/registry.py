"""The web UI registrar — the single seam through which anything contributes pages.

The shell installs one registrar and hands it out; the shell itself and every plugin register
through it. Nothing else may touch the FastAPI app: a contributor receives the registrar, never
``app``, so it cannot add middleware, mount a path or bypass the access gate.

Why the registrar owns the include (risk F1 in the phase-1 review)
------------------------------------------------------------------
On the pinned FastAPI/Starlette (0.141.1 / 1.6.0) ``app.include_router(r)`` appends **one**
``_IncludedRouter`` wrapper object to ``app.routes``; the router's own ``APIRoute``s stay inside
it. The removal idiom used elsewhere in the tree
(``for route in r.routes: if route in app.routes: app.routes.remove(route)``,
``plugins/restapi/commands.py``) therefore removes **nothing** on this version — the previous
handler keeps answering after a "reload", including its own auth decisions, and nothing warns.
An older FastAPI pair behaves the other way round. So the registrar snapshots ``app.routes``
before its own include and diffs by identity afterwards, and unregistration removes exactly the
objects it added. It does not depend on either version's incidental behaviour.

Deny by default at registration time
------------------------------------
Registration is the one place where the contributor's claim can be checked, so it is checked
loudly:

* a reserved path (``/``, ``/login``, ``/logout``, ``/auth/*``, ``/static``) is refused;
* a path another owner already claims is refused — never first-wins-silently;
* an owner registering twice is refused (unregister first);
* a capability that is not declared in :mod:`services.webservice.permissions` is refused.

The registrar is also the enumeration source of truth for tests: ``app.routes`` is blind to
included routers and ``app.router._frontend_routes`` is blind to both, so a ratchet that walks
either can pass having examined nothing.

The console's pages are LOW-PRIORITY routes
-------------------------------------------
Every page the registrar registers is added as a **low-priority** route (the mechanism
``app.frontend`` already uses on the pinned FastAPI). A route another component registered on the
SAME path — the restapi plugin's API ``/servers`` — therefore WINS it: the console can never shadow
the REST API, and the old endpoints keep answering exactly as they did before the console existed.
The console's page is still reached when nothing else claims its path, and it keeps its own
capability gate either way. This is also why the gate resolves a capability by **route identity**
(:meth:`Registrar.owns_route`) and never by path: the console page ``/servers`` and the plugin's
API ``/servers`` are the same path but different routes (see
:func:`services.webservice.permissions.capability_gate`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

from fastapi import APIRouter, FastAPI

from . import permissions

if TYPE_CHECKING:  # pragma: no cover
    from jinja2 import Environment

__all__ = ["NavItem", "Registration", "RegistrationError", "Registrar",
           "RESERVED_PATHS", "RESERVED_PREFIXES"]

log = logging.getLogger(__name__)

#: Paths the shell owns. A contributor registering one of these is refused: with the pinned
#: FastAPI a second registration on the same path wins silently, so a collision must be loud at
#: registration time (it cannot be detected later).
RESERVED_PATHS: tuple[str, ...] = ("/", "/login", "/logout", "/static")

#: Prefixes the shell owns (an ``/auth/*`` route belongs to the login flow, not to a plugin; the
#: whole ``/static`` area is the shell's asset surface).
RESERVED_PREFIXES: tuple[str, ...] = ("/auth/", "/static/")


class RegistrationError(RuntimeError):
    """Raised when a contributor tries to claim something that is already owned."""


@dataclass(frozen=True)
class NavItem:
    """One navigation entry. ``capability`` is the capability a viewer needs to be offered it —
    the nav is rendered from the same declaration the route is gated by, so a page can never
    advertise a door that answers 403."""
    label: str
    url: str
    capability: str


@dataclass
class Registration:
    """What one contributor registered."""
    owner: str
    router: APIRouter
    nav: tuple[NavItem, ...] = ()
    capabilities: dict[str, str] = field(default_factory=dict)
    templates: Path | None = None
    #: route objects appended to ``app.routes`` by this owner's include (the wrapper)
    included: list[Any] = field(default_factory=list)
    #: the owner's own ``APIRoute`` objects. The console registers them as LOW-PRIORITY routes
    #: (see :meth:`Registrar.register_pages`), so they no longer live in ``router.routes`` —
    #: this is the registrar's copy, used for bookkeeping and route-identity lookups.
    routes: list[Any] = field(default_factory=list)


def _is_prefix(path: str) -> bool:
    return path.endswith("*")


def _normalise_prefix(path: str) -> str:
    """The prefix behind a ``...*`` declaration (the star is the declaration's marker only)."""
    if path.endswith("*"):
        path = path[:-1]
    return path.rstrip("/")


def _matches_prefix(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


class Registrar:
    """Bookkeeping for everything the admin web UI serves."""

    def __init__(self, app: FastAPI):
        self.app = app
        self.environment: "Environment | None" = None
        self._registrations: dict[str, Registration] = {}
        # path (exact) or prefix (without the trailing '*') -> (owner, capability)
        self._capabilities: dict[str, tuple[str, str]] = {}
        self._prefixes: dict[str, tuple[str, str]] = {}
        self._template_dirs: dict[str, Path] = {}
        self._asset_routes: list[Any] = []
        self._asset_owner: str | None = None
        #: ids of every route object the console owns (each registration's ``APIRoute``s plus the
        #: asset routes). The gate resolves a capability by ROUTE IDENTITY, so it must be able to
        #: ask "is this route one of ours" — a path cannot answer that (the console page ``/servers``
        #: and the restapi plugin's API ``/servers`` share the path; see
        #: :func:`services.webservice.permissions.capability_gate`).
        self._owned_route_ids: set[int] = set()

    # ---------------------------------------------------------------------------------- assets

    def register_assets(self, path: str, directory: Path | str,
                        capability: str = permissions.PUBLIC, owner: str = "shell") -> None:
        """Become the ONE owner of an asset path, through the public ``app.frontend()`` API.

        ``StaticFiles`` mounted with ``app.mount()`` bypasses every dependency — the app-level
        access gate never runs for it — and a second mount on the same path is silently dead
        (the first one matches and 404s the second one's files). ``app.frontend()`` does run the
        dependency chain for every asset, so assets are gated exactly like pages; the cost is
        that it records its route outside ``app.routes`` (``app.router._frontend_routes``).
        """
        if _is_prefix(path):
            raise RegistrationError(f"asset path '{path}' must be a concrete path, not a prefix")
        # the asset path is claimed as a PREFIX: everything under it is served from this directory,
        # so an asset request (for which there is no route object) resolves through the prefix
        self._claim(_normalise_prefix(path) + "*", capability, owner, reserved_ok=(owner == "shell"))
        if self._asset_owner is not None and self._asset_owner != owner:
            raise RegistrationError(f"asset path '{path}' is already owned by "
                                    f"'{self._asset_owner}'; the shell is the only asset owner")
        directory = Path(directory)
        if not directory.is_dir():
            raise RegistrationError(f"asset directory '{directory}' does not exist")
        self.app.frontend(path, directory=str(directory))
        group = getattr(self.app.router, "_frontend_routes", None)
        self._asset_routes = list(getattr(group, "routes", []) or [])
        self._asset_owner = owner
        self._owned_route_ids.update(id(route) for route in self._asset_routes)
        log.debug("Registrar: '%s' owns the asset path '%s' (%d route(s)).",
                  owner, path, len(self._asset_routes))

    # ---------------------------------------------------------------------------- registration

    def register_pages(self, owner: str, router: APIRouter, nav: Iterable[NavItem] | None = None,
                       capabilities: dict[str, str] | None = None,
                       templates: Path | str | None = None) -> Registration:
        """Register a contributor's pages (a router with its routes).

        ``capabilities`` maps each route path (full path, prefix included; a key ending in ``*``
        declares a prefix) to the capability it needs. Every declared path is checked against the
        capability declaration and against the paths other owners already hold — a collision or a
        missing capability raises :class:`RegistrationError` instead of producing a route that
        silently wins or ships public.
        """
        owner = (owner or "").strip()
        if not owner:
            raise RegistrationError("a registration needs a non-empty owner name")
        if owner in self._registrations:
            raise RegistrationError(f"owner '{owner}' is already registered; unregister it first "
                                    f"(registering twice would leave two sets of routes live)")
        nav_items = tuple(nav or ())
        for item in nav_items:
            if permissions.roles_for_capability(item.capability) is None:
                raise RegistrationError(
                    f"owner '{owner}' declares nav item '{item.label}' with unknown capability "
                    f"'{item.capability}'")
        declared = dict(capabilities or {})
        for path, capability in declared.items():
            if permissions.roles_for_capability(capability) is None:
                raise RegistrationError(
                    f"owner '{owner}' declares unknown capability '{capability}' for '{path}'")
            self._claim(path, capability, owner, reserved_ok=(owner == "shell"))

        template_dir: Path | None = None
        if templates is not None:
            template_dir = Path(templates)
            if not template_dir.is_dir():
                raise RegistrationError(
                    f"owner '{owner}' registered templates that do not exist: {template_dir}")

        before = list(self.app.routes)
        routes = list(getattr(router, "routes", []) or [])
        # THE CONSOLE REGISTERS ITS OWN PAGES AS LOW-PRIORITY ROUTES. A route another component
        # registered on the SAME path (the restapi plugin's ``/servers``) therefore WINS it, so the
        # console can never shadow the REST API — the old endpoints keep answering exactly as they
        # did before the console existed. The console's page is still reached when nothing else
        # claims its path, and it keeps its own capability gate either way. ``_low_priority_routes``
        # is the mechanism ``app.frontend`` already uses on the pinned FastAPI (0.141.1); if a
        # future version drops it we fall back to a normal include and SAY SO, rather than silently
        # shadowing another component's route.
        #
        # KNOWN EDGE, PINNED (fail-closed): low priority only applies when NO normal route matches
        # the request at all. If another component claims the SAME path with a method the console
        # page does NOT accept (a POST-only route on the console's GET /servers), its NORMAL route is
        # a PARTIAL match (path yes, method no) and the pinned FastAPI router serves that partial
        # BEFORE this fallback - so a request using the console page's OWN method answers 405 Method
        # Not Allowed and the page is not reached. The direction is DENY (nothing is served, no gate
        # is bypassed into content) and it is deterministic across restarts; no shipping component
        # collides this way (the restapi plugin's /servers is GET, which wins the path cleanly). It
        # is pinned by
        # ``tests/test_webui_rest_api_isolation.py::test_a_plugin_route_with_a_method_the_console_page_lacks_flips_the_path_to_405``.
        low_priority = getattr(router, "_low_priority_routes", None)
        if low_priority is None:
            log.warning("Registrar: '%s' registered %d page route(s) as NORMAL routes - this "
                        "FastAPI does not expose low-priority routes, so a page sharing a path "
                        "with another component's route (e.g. the REST API's /servers) would "
                        "shadow it.", owner, len(routes))
        else:
            setattr(router, "_low_priority_routes", list(low_priority) + routes)
            router.routes = []
        self.app.include_router(router)
        # exactly the object(s) OUR include appended — see the module docstring for why this is
        # a snapshot/diff and not `for route in router.routes: app.routes.remove(route)`
        included = [route for route in self.app.routes if not any(route is b for b in before)]

        registration = Registration(owner=owner, router=router, nav=nav_items,
                                    capabilities=declared, templates=template_dir,
                                    included=included, routes=routes)
        self._registrations[owner] = registration
        self._owned_route_ids.update(id(route) for route in routes)
        if template_dir is not None:
            self._template_dirs[owner] = template_dir
        log.info("Registrar: '%s' registered %d route(s), %d capability declaration(s)%s.",
                 owner, len(routes), len(declared),
                 f", templates '{template_dir}'" if template_dir else "")
        return registration

    def unregister_owner(self, owner: str) -> bool:
        """Remove everything an owner registered: its included route object(s), its capability
        declarations and its template namespace. Returns whether the owner existed."""
        registration = self._registrations.pop(owner, None)
        if registration is None:
            return False
        removed = 0
        for route in registration.included:
            # remove BY IDENTITY: ``_IncludedRouter`` is a dataclass, so ``list.remove`` matches by
            # EQUALITY and can delete a different owner's wrapper that compares equal. The point of
            # the snapshot/diff in ``register_pages`` is identity, so the removal is identity too.
            for index, existing in enumerate(self.app.router.routes):
                if existing is route:
                    del self.app.router.routes[index]
                    removed += 1
                    break
        for route in registration.routes:
            self._owned_route_ids.discard(id(route))
        for path in list(self._capabilities):
            if self._capabilities[path][0] == owner:
                del self._capabilities[path]
        for prefix in list(self._prefixes):
            if self._prefixes[prefix][0] == owner:
                del self._prefixes[prefix]
        self._template_dirs.pop(owner, None)
        self.clear_template_cache()
        log.info("Registrar: '%s' unregistered (%d route object(s) removed, %d route(s) no longer "
                 "answering).", owner, removed, len(registration.routes))
        return True

    def clear_template_cache(self) -> None:
        """Drop Jinja's compiled-template cache.

        Jinja caches templates by name, so without this a removed owner's template would still be
        served from the cache — the same class of staleness the route removal above exists for."""
        environment = self.environment
        if environment is not None and environment.cache is not None:
            environment.cache.clear()

    def _claim(self, path: str, capability: str, owner: str, *, reserved_ok: bool) -> None:
        """Claim a path/prefix for an owner, refusing collisions and reserved paths."""
        if not path.startswith("/"):
            raise RegistrationError(f"'{path}' is not an absolute route path")
        if not reserved_ok:
            if path in RESERVED_PATHS or any(path.startswith(p) for p in RESERVED_PREFIXES):
                raise RegistrationError(f"'{path}' is reserved by the shell "
                                        f"(reserved: {', '.join(RESERVED_PATHS + RESERVED_PREFIXES)})")
        if _is_prefix(path):
            key, table = _normalise_prefix(path), self._prefixes
            if not key:
                raise RegistrationError("'*' is not a registrable prefix")
        else:
            key, table = path, self._capabilities
        existing = table.get(key)
        if existing is not None:
            other_owner, other_capability = existing
            if other_owner == owner and other_capability == capability:
                return  # idempotent re-declaration by the same owner
            raise RegistrationError(f"'{path}' is already claimed by '{other_owner}' "
                                    f"(capability '{other_capability}'), not by '{owner}'")
        table[key] = (owner, capability)

    # --------------------------------------------------------------------------------- lookup

    def owns_route(self, route) -> bool:
        """Whether *route* is a route THIS registrar registered (a page or an asset).

        THE route-identity test the gate uses. A route object the console did not register —
        ``plugins/restapi``'s own ``APIRoute``s, the WebService's debug endpoints, anything another
        component added straight to the app — is NOT the console's, whatever its path: the console
        page ``/servers`` and the restapi plugin's API ``/servers`` are the same PATH but different
        ROUTES, and only ownership can tell them apart.
        """
        if route is None:
            return False
        return id(route) in self._owned_route_ids

    def capability_for(self, route=None, path: str | None = None) -> str | None:
        """The capability declared for a matched route / requested path, or ``None``.

        Two shapes reach the gate:

        * an ``APIRoute`` the console registered (``route`` is set). It resolves BY ROUTE IDENTITY:
          only a route this registrar owns may take a capability, and it is looked up by the route's
          OWN path — never by the requested path, because the paths genuinely collide with other
          components' routes (the console page ``/servers`` vs the restapi plugin's API ``/servers``).
          A route the console does not own answers ``None`` here, so the gate falls through to the
          route's own guard (:func:`services.webservice.permissions.is_self_guarded`) and the old
          endpoints keep their own auth.
        * an asset served by ``app.frontend`` (``route`` is ``None``), where only the requested path
          identifies what is being served. Here — and only here — the requested path resolves the
          declaration.
        """
        candidates = []
        if route is not None:
            if not self.owns_route(route):
                return None
            route_path = getattr(route, "path", None)
            if route_path:
                candidates.append(route_path)
        elif path:
            candidates.append(path)
        for candidate in candidates:
            exact = self._capabilities.get(candidate)
            if exact is not None:
                return exact[1]
            for prefix, (_, capability) in self._prefixes.items():
                if _matches_prefix(prefix, candidate):
                    return capability
        return None

    # ---------------------------------------------------------------------------------- views

    @property
    def registrations(self) -> dict[str, Registration]:
        return dict(self._registrations)

    @property
    def owners(self) -> list[str]:
        return sorted(self._registrations)

    @property
    def nav(self) -> list[NavItem]:
        """Every registered nav item, in registration order."""
        return [item for registration in self._registrations.values() for item in registration.nav]

    @property
    def page_paths(self) -> tuple[str, ...]:
        """The paths of every registered PAGE — the console's own registry of known pages.

        Read off the nav items the pages THEMSELVES declared (a page contributes its route, its
        capability and its nav entry as one registration), so this is not a second mapping table
        that could drift from the routes: a page that is registered is here, and a path that is not
        a page (an action route, the ``/api`` streams) is not — which is exactly what makes it
        usable as an allow-list. Its one caller is ``pages/actions.origin_path``, which resolves the
        page a write returns to by LOOKUP against this set and never by trusting a request value.
        """
        return tuple(item.url for item in self.nav)

    @property
    def routes(self) -> list[Any]:
        """Every route this registrar owns: each registered router's routes plus the asset routes
        it installed. THE enumeration the tests and the startup log read — ``app.routes`` is not
        (it hides included routers and frontend routes), and ``router.routes`` is not either (the
        console registers its pages as low-priority routes, so they live on the registration)."""
        out: list[Any] = []
        for registration in self._registrations.values():
            out.extend(list(registration.routes or []))
        out.extend(self._asset_routes)
        return out

    @property
    def route_count(self) -> int:
        return len(self.routes)

    @property
    def capability_count(self) -> int:
        """Number of path/prefix capability declarations the registrar holds."""
        return len(self._capabilities) + len(self._prefixes)

    @property
    def template_dirs(self) -> dict[str, Path]:
        """Namespace -> directory, live: the template loader reads this mapping per lookup, so a
        plugin registering later is picked up without rebuilding the Jinja environment."""
        return self._template_dirs

    def summary(self) -> dict[str, Any]:
        return {
            "owners": self.owners,
            "routes": self.route_count,
            "capabilities": self.capability_count,
            "nav_items": len(self.nav),
            "templates": sorted(self._template_dirs),
        }
