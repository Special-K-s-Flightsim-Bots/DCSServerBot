"""The web UI registrar — the single seam through which anything contributes pages.

The shell installs one registrar and hands it out; the shell itself and every plugin register
through it. Nothing else may touch the FastAPI app: a contributor receives the registrar, never
``app``, so it cannot add middleware, mount a path or bypass the access gate.

Why the registrar owns the include
------------------------------------------------------------------
On the pinned FastAPI/Starlette (0.141.1 / 1.6.0) ``app.include_router(r)`` appends **one**
``_IncludedRouter`` wrapper object to ``app.routes``; the router's own ``APIRoute``s stay inside
it. Other FastAPI versions append one copied ``APIRoute`` per route instead — either way the
objects are NOT the router's own routes, so an idiom that matches ``router.routes`` against
``app.routes`` removes **nothing**: the previous handler keeps answering after a "reload",
including its own auth decisions, and nothing warns. The removal is therefore
:func:`include_router_counted` / :func:`exclude_router` — a snapshot of ``app.routes`` before the
include, a diff by identity afterwards, and a removal by identity — and every component that
installs a router on the app (the registrar, ``plugins/restapi``, any later contributor) uses that
pair instead of its own guess at what the include appended. It does not depend on any version's
incidental behaviour.

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
SAME path therefore WINS it: the console can never shadow a peer's route, and the old endpoints
keep answering exactly as they did before the console existed. The console's page is still reached
when nothing else claims its path, and it keeps its own capability gate either way.

The REST API owns ``/servers`` (``plugins/restapi``), so the console's own Servers page lives at
``/fleet`` instead — deliberately off the path the API claims, so no console route shares a path
with it at all. The low-priority mechanism stays the guarantee for ANY future shared path. This is
also why the gate resolves a capability by **route identity** (:meth:`Registrar.owns_route`) and
never by path: two components' routes can share a path, and only ownership can tell them apart (see
:func:`services.webservice.permissions.capability_gate`).

The console's mount is a registration-time fact
-----------------------------------------------
A page declares its path from its own root (``/fleet``, ``/``, ``/actions/server/start``);
:meth:`Registrar.register_pages` serves, claims and advertises the whole registration under one base
prefix (see :mod:`services.webservice.mount`). ``""`` is the console-at-root mode. Nothing below
this seam names a mount, and the installation's fixed surface (``/auth/*``, ``/language``,
``/i18n/messages.js``, ``/theme``, ``/static``) is registered with an empty prefix so its addresses
do not move with the console.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from starlette.routing import compile_path

from . import mount, permissions

if TYPE_CHECKING:  # pragma: no cover
    from jinja2 import Environment

__all__ = ["NavItem", "NavGroup", "NAV_GROUPS", "NAV_GROUPS_BY_NAME", "Registration",
           "RegistrationError", "Registrar", "RESERVED_PATHS", "RESERVED_PREFIXES", "SHELL_OWNERS",
           "exclude_router", "include_router_counted"]

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- router installation

def include_router_counted(app: FastAPI, router: APIRouter, *, prefix: str = "") -> list[Any]:
    """``app.include_router(router, prefix=prefix)``, returning exactly the route object(s) THIS
    include appended.

    The include never appends the router's own routes: on the pinned pair it appends one
    ``_IncludedRouter`` wrapper, on other versions one copied ``APIRoute`` per route. The returned
    list is what :func:`exclude_router` must be given later — the router's own ``routes`` are not.

    *prefix* is the include-time path prefix: every path the router declares is served under it,
    which is how the console's whole page tree is mounted at one address without any page module
    naming that address.
    """
    before = list(app.routes)
    app.include_router(router, prefix=prefix)
    return [route for route in app.routes if not any(route is other for other in before)]


def exclude_router(app: FastAPI, added: Sequence[Any]) -> int:
    """Remove exactly the objects *added* from the app's route table, by identity.

    Returns how many were removed. Identity and not equality: ``_IncludedRouter`` is a dataclass, so
    ``list.remove`` can delete another owner's wrapper that compares equal.
    """
    routes = app.router.routes
    removed = 0
    for route in added:
        for index, existing in enumerate(routes):
            if existing is route:
                del routes[index]
                removed += 1
                break
    return removed


#: Paths the shell owns. A contributor registering one of these is refused: with the pinned
#: FastAPI a second registration on the same path wins silently, so a collision must be loud at
#: registration time (it cannot be detected later). ``/console`` is the console's mount under a
#: public site, reserved in BOTH modes so a site slug or a plugin can never squat the address the
#: console moves to.
RESERVED_PATHS: tuple[str, ...] = ("/", "/login", "/logout", "/console", "/static")

#: Prefixes the shell owns (an ``/auth/*`` route belongs to the login flow, not to a plugin; the
#: whole ``/static`` area is the shell's asset surface).
RESERVED_PREFIXES: tuple[str, ...] = ("/auth/", "/static/")

#: The owner names the shell registers under. They — and only they — may claim the reserved surface
#: (``/auth/*``, ``/static``, the site root and the console's own mount). ``site`` is the public
#: site's page tree (``services/webservice/site_pages.py``): it is shipped by the shell, it owns the
#: site root beside the console's mount, and the slugs it is served under are validated against this
#: module's reserved surface at startup.
SHELL_OWNERS: tuple[str, ...] = ("shell", "installation", "site")


class RegistrationError(RuntimeError):
    """Raised when a contributor tries to claim something that is already owned."""


@dataclass(frozen=True)
class NavGroup:
    """One heading of the sidebar: its name alone.

    ``name`` is the ENGLISH msgid — the group names are chrome, so every catalog owns their words
    (``base.html`` translates it at render). A heading draws nothing: it is a bare label printed on
    the rail's gutter (``shell.css``, ``.nav .grp``), and the entries under it wear the sprite.
    """
    name: str


#: THE GROUP ORDER — one constant, because the order and the set are a decision of the console's own
#: chrome rather than of any page. A page names one of these on its own nav item; :class:`NavItem`
#: refuses a name that is not here, so an unknown group cannot silently create a sixth heading, and
#: ``pages/dashboard.nav_groups`` renders the headings in exactly this order (and drops the ones left
#: with nothing visible under them).
NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup(name="Operations"),
    NavGroup(name="Media"),
    NavGroup(name="Cluster"),
    NavGroup(name="Data"),
    NavGroup(name="Administration"),
)

#: group name -> its declaration, for the one lookup a refusal and a grouping need
NAV_GROUPS_BY_NAME: dict[str, NavGroup] = {group.name: group for group in NAV_GROUPS}


@dataclass(frozen=True)
class NavItem:
    """One navigation entry. ``capability`` is the capability a viewer needs to be offered it —
    the nav is rendered from the same declaration the route is gated by, so a page can never
    advertise a door that answers 403.

    ``group`` and ``glyph`` are declared HERE, by the page that owns the entry, and never looked up
    in a table the shell keeps: one declaration per page cannot drift from the pages it describes.

    * ``group`` must be one of :data:`NAV_GROUPS` — checked at construction, which is the first
      moment a page names a group, so a wrong name fails at import instead of rendering a heading
      nobody declared;
    * ``glyph`` is a name in the nav sprite. An UNKNOWN name is not refused (a page added before its
      drawing exists still renders — it draws nothing), but an EMPTY one is: an entry that can never
      draw anything is a declaration mistake, not a missing drawing.

    ``available`` is an optional test of whether the product behind the entry exists in THIS process
    (its service/plugin is active), as opposed to ``capability``, which is a right. ``None`` means
    always available, so every entry that does not declare it is unchanged. It is evaluated PER
    REQUEST by the sidebar, never at registration: routes and capabilities are registered once at
    boot, while the state a test reads is only reliable later.
    """
    label: str
    url: str
    capability: str
    group: str
    glyph: str
    available: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if self.group not in NAV_GROUPS_BY_NAME:
            raise ValueError(
                f"nav item '{self.label}' ({self.url}) names the group '{self.group}', which the "
                f"console does not have (declared: {', '.join(NAV_GROUPS_BY_NAME)})")
        if not str(self.glyph or "").strip():
            raise ValueError(
                f"nav item '{self.label}' ({self.url}) declares no glyph; every entry names a "
                f"drawing of the nav sprite")


@dataclass
class Registration:
    """What one contributor registered."""
    owner: str
    router: APIRouter
    nav: tuple[NavItem, ...] = ()
    capabilities: dict[str, str] = field(default_factory=dict)
    templates: Path | None = None
    #: the base prefix this owner's routes were mounted under (``""`` when it keeps the root)
    prefix: str = ""
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


def _route_answers(route, path: str) -> bool:
    """Whether *route* answers *path* — by its compiled pattern, or its literal path when it has none."""
    matcher = getattr(route, "path_regex", None)
    if matcher is not None:
        return bool(matcher.match(path))
    return getattr(route, "path", None) == path


def _declares_slash(route) -> bool:
    """Whether *route*'s OWN path ends with a slash — an address that lives at ``/x/``."""
    return str(getattr(route, "path", "") or "").endswith("/")


def _join(base: str, path: str) -> str:
    """*path* under *base*: the root page is served at the mount itself, not at the mount plus a slash."""
    if not base:
        return path
    if path == "/":
        return base
    return base + path


def _set_route_path(route, path: str) -> None:
    """Set one route's address and rebuild the compiled matcher it answers by."""
    route.path = path
    if hasattr(route, "path_format"):
        route.path_format = path
    if hasattr(route, "path_regex"):
        route.path_regex, _format, route.param_convertors = compile_path(path)


def _mount_route(route, base: str) -> None:
    """Serve one route under *base*, in place.

    The route keeps its place in its router and in the router's low-priority list; only the path it
    matches moves. The compiled matcher is rebuilt from the new path, so the route answers at its
    mounted address and nowhere else. The installation's fixed surface (``mount.is_fixed``) keeps
    its address: the door and the display switches are not part of the console's page tree.
    """
    path = getattr(route, "path", None)
    if not base or not isinstance(path, str) or not path.startswith("/") or mount.is_fixed(path):
        return
    _set_route_path(route, _join(base, path))


def _unmount_route(route, base: str) -> None:
    """Move one mounted route back to its base-relative address, in place.

    The inverse of :func:`_mount_route`: a fixed path and a path outside *base* keep their address.
    """
    path = getattr(route, "path", None)
    if not base or not isinstance(path, str) or not path.startswith("/") or mount.is_fixed(path):
        return
    if path == base:
        _set_route_path(route, "/")
    elif path.startswith(base + "/"):
        _set_route_path(route, path[len(base):])


def _unmounted_nav(item: NavItem, base: str) -> NavItem:
    """*item* with *base* removed from its URL (the inverse of :func:`_mounted_nav`)."""
    if not base or not str(item.url or "").startswith("/"):
        return item
    if item.url == base:
        return replace(item, url="/")
    if item.url.startswith(base + "/"):
        return replace(item, url=item.url[len(base):])
    return item


def _mark_routes(router) -> None:
    """Invalidate a router's cached effective route lists after its routes moved in place."""
    mark = getattr(router, "_mark_routes_changed", None)
    if callable(mark):
        mark()


def _staged_include(router: APIRouter, app: FastAPI) -> Any:
    """Build the include wrapper for *router* WITHOUT touching the application's route table.

    ``include_router`` appends its wrapper to the router it is called on. Calling it on a staging
    router with the application's own include attributes produces the same wrapper — same prefix,
    dependencies, response class and override provider — while the live table stays untouched. The
    wrapper is what a swap installs, so a failed build leaves the previous table serving.
    """
    parent = app.router
    # typed loosely: ``include_router_counted`` declares a FastAPI but only needs ``.routes`` and
    # ``.include_router``, which an APIRouter also has
    staging: Any = APIRouter(
        prefix=parent.prefix,
        tags=parent.tags,
        dependencies=parent.dependencies,
        default_response_class=parent.default_response_class,
        responses=parent.responses,
        callbacks=parent.callbacks,
        deprecated=parent.deprecated,
        include_in_schema=parent.include_in_schema,
        generate_unique_id_function=parent.generate_unique_id_function,
        strict_content_type=parent.strict_content_type,
        dependency_overrides_provider=app,
    )
    added = include_router_counted(staging, router)
    return added[0] if added else None


def _mounted_nav(item: NavItem, base: str) -> NavItem:
    """*item* with its URL moved under *base* (an empty base returns the item unchanged).

    The site root is the console's own root once a prefix exists: ``/`` becomes ``<base>`` and not
    ``<base>/``, so the entry names the mounted page exactly.
    """
    if not base or not str(item.url or "").startswith("/"):
        return item
    return replace(item, url=_join(base, item.url))


class Registrar:
    """Bookkeeping for everything the admin web UI serves.

    *console_prefix* is the base prefix the console's own pages are mounted under (``""`` at the
    site root, ``"/console"`` while a public site owns ``/``). It is passed at construction, from
    the configuration, and applied in :meth:`register_pages` only — the single place a path, its
    capability and its nav entry are claimed, so a page module keeps declaring its own path and
    never names a mount.
    """

    def __init__(self, app: FastAPI, *, console_prefix: str = ""):
        self.app = app
        self.console_prefix = console_prefix or ""
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
        #: ask "is this route one of ours" — a path cannot answer that (two components' routes can
        #: share a path; see :func:`services.webservice.permissions.capability_gate`).
        self._owned_route_ids: set[int] = set()
        #: route id -> the base prefix its owner was mounted under (diagnostics and lookups only)
        self._route_bases: dict[int, str] = {}

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
        self._claim(_normalise_prefix(path) + "*", capability, owner,
                    reserved_ok=(owner in SHELL_OWNERS))
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
                       templates: Path | str | None = None,
                       prefix: str | None = None) -> Registration:
        """Register a contributor's pages (a router with its routes).

        ``capabilities`` maps each route path (full path, prefix included; a key ending in ``*``
        declares a prefix) to the capability it needs. Every declared path is checked against the
        capability declaration and against the paths other owners already hold — a collision or a
        missing capability raises :class:`RegistrationError` instead of producing a route that
        silently wins or ships public.

        *prefix* is the base prefix the routes are served under; ``None`` means the console's own
        (``self.console_prefix``) and ``""`` means the installation's fixed surface, which is never
        moved. The prefix is applied HERE, to the whole registration at once: every route's path,
        its capability declaration and every nav URL are moved under it, so a page declares
        ``/fleet`` and is served, gated and advertised at ``<prefix>/fleet`` without knowing that a
        prefix exists.
        """
        base = (self.console_prefix if prefix is None else prefix) or ""
        owner = (owner or "").strip()
        if not owner:
            raise RegistrationError("a registration needs a non-empty owner name")
        if owner in self._registrations:
            raise RegistrationError(f"owner '{owner}' is already registered; unregister it first "
                                    f"(registering twice would leave two sets of live routes)")
        nav_items = tuple(_mounted_nav(item, base) for item in (nav or ()))
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
            claimed = path if mount.is_fixed(path) else _join(base, path)
            self._claim(claimed, capability, owner, reserved_ok=(owner in SHELL_OWNERS))

        template_dir: Path | None = None
        if templates is not None:
            template_dir = Path(templates)
            if not template_dir.is_dir():
                raise RegistrationError(
                    f"owner '{owner}' registered templates that do not exist: {template_dir}")

        routes = list(getattr(router, "routes", []) or [])
        # the whole page tree moves under the registration's base, in place: the routes keep their
        # identity (which the gate resolves capabilities by) and only the address they match changes
        for route in routes:
            _mount_route(route, base)
        # THE CONSOLE REGISTERS ITS OWN PAGES AS LOW-PRIORITY ROUTES. A route another component
        # registered on the SAME path therefore WINS it, so the console can never shadow a peer's
        # route — the old endpoints keep answering exactly as they did before the console existed.
        # The console's page is still reached when nothing else claims its path, and it keeps its own
        # capability gate either way. ``_low_priority_routes`` is the mechanism ``app.frontend``
        # already uses on the pinned FastAPI (0.141.1); if a future version drops it we fall back to
        # a normal include and SAY SO, rather than silently shadowing another component's route.
        #
        # KNOWN EDGE, PINNED (fail-closed): low priority only applies when NO normal route matches
        # the request at all. If another component claims the SAME path with a method the console
        # page does NOT accept (a POST-only route on the console's own GET page), its NORMAL route is
        # a PARTIAL match (path yes, method no) and the pinned FastAPI router serves that partial
        # BEFORE this fallback - so a request using the console page's OWN method answers 405 Method
        # Not Allowed and the page is not reached. The direction is DENY (nothing is served, no gate
        # is bypassed into content) and it is deterministic across restarts; no shipping component
        # collides this way (the console's Servers page lives at ``/fleet``, off the REST API's
        # ``/servers``). It is pinned by
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
        # exactly the object(s) OUR include appended: the include appends its OWN objects, never
        # the router's routes, so only a snapshot/diff can name what to remove later
        included = include_router_counted(self.app, router)

        registration = Registration(owner=owner, router=router, nav=nav_items,
                                    capabilities=declared, templates=template_dir,
                                    included=included, routes=routes, prefix=base)
        self._registrations[owner] = registration
        self._owned_route_ids.update(id(route) for route in routes)
        for route in routes:
            self._route_bases[id(route)] = base
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
        removed = exclude_router(self.app, registration.included)
        for route in registration.routes:
            self._owned_route_ids.discard(id(route))
            self._route_bases.pop(id(route), None)
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

    def _claimed_by(self, claimed: str):
        """The ``(owner, capability)`` holding *claimed*, or ``None``."""
        if _is_prefix(claimed):
            return self._prefixes.get(_normalise_prefix(claimed))
        return self._capabilities.get(claimed)

    def _check_claim(self, claimed: str, owner: str, *, reserved_ok: bool) -> None:
        """Refuse a claim that another owner already holds, or a reserved path, BEFORE mutating."""
        existing = self._claimed_by(claimed)
        if existing is not None and existing[0] != owner:
            raise RegistrationError(f"'{claimed}' is already claimed by '{existing[0]}', not by "
                                    f"'{owner}'")
        if not reserved_ok and (claimed in RESERVED_PATHS
                               or any(claimed.startswith(p) for p in RESERVED_PREFIXES)):
            raise RegistrationError(f"'{claimed}' is reserved by the shell")

    def _release_registration(self, registration: Registration) -> None:
        """Drop ONE registration's own capability declarations; leave its asset prefixes alone."""
        for path in registration.capabilities:
            claimed = path if mount.is_fixed(path) else _join(registration.prefix, path)
            if _is_prefix(claimed):
                key = _normalise_prefix(claimed)
                if self._prefixes.get(key, (None,))[0] == registration.owner:
                    del self._prefixes[key]
            elif self._capabilities.get(claimed, (None,))[0] == registration.owner:
                del self._capabilities[claimed]

    def replace_owner(self, owner: str, router: APIRouter | None,
                      capabilities: dict[str, str] | None = None,
                      nav: Iterable[NavItem] | None = None,
                      prefix: str | None = None,
                      templates: Path | str | None = None) -> Registration | None:
        """Build a replacement registration for *owner* and swap it in with ONE assignment.

        The new router's routes are mounted and its include wrapper is built BEFORE anything live is
        touched; the application's route table is then replaced in a single assignment. A failure
        while building leaves the previous registration serving. ``router=None`` removes the owner's
        table without registering a new one (an empty site catalog).

        The old registration's capability declarations are released and the new ones claimed in the
        same step, so a path the new table does not serve cannot keep a stale declaration and a path
        it does serve is declared before the table answers.
        """
        owner = (owner or "").strip()
        if not owner:
            raise RegistrationError("a registration needs a non-empty owner name")
        base = (self.console_prefix if prefix is None else prefix) or ""
        declared = dict(capabilities or {})
        for path, capability in declared.items():
            if permissions.roles_for_capability(capability) is None:
                raise RegistrationError(
                    f"owner '{owner}' declares unknown capability '{capability}' for '{path}'")
            claimed = path if mount.is_fixed(path) else _join(base, path)
            self._check_claim(claimed, owner, reserved_ok=(owner in SHELL_OWNERS))
        nav_items = tuple(_mounted_nav(item, base) for item in (nav or ()))
        for item in nav_items:
            if permissions.roles_for_capability(item.capability) is None:
                raise RegistrationError(
                    f"owner '{owner}' declares nav item '{item.label}' with unknown capability "
                    f"'{item.capability}'")
        template_dir = Path(templates) if templates is not None else None
        if template_dir is not None and not template_dir.is_dir():
            raise RegistrationError(
                f"owner '{owner}' registered templates that do not exist: {template_dir}")

        routes: list[Any] = []
        wrapper = None
        if router is not None:
            routes = list(getattr(router, "routes", []) or [])
            for route in routes:
                _mount_route(route, base)
            low_priority = getattr(router, "_low_priority_routes", None)
            if low_priority is None:
                log.warning("Registrar: '%s' replacement registered %d page route(s) as NORMAL "
                            "routes - this FastAPI does not expose low-priority routes.", owner,
                            len(routes))
            else:
                setattr(router, "_low_priority_routes", list(low_priority) + routes)
                router.routes = []
            _mark_routes(router)
            wrapper = _staged_include(router, self.app)

        old = self._registrations.get(owner)
        if old is None and router is None:
            # nothing to remove and nothing to install: an apply that leaves this owner absent
            return None
        live = list(self.app.router.routes)
        old_included = tuple(old.included) if old is not None else ()
        table = [route for route in live
                 if not any(route is included for included in old_included)]
        if wrapper is not None:
            table.append(wrapper)

        if old is not None:
            self._release_registration(old)
            for route in old.routes:
                self._owned_route_ids.discard(id(route))
                self._route_bases.pop(id(route), None)
            self._template_dirs.pop(owner, None)
            if router is None:
                # ``router=None`` retires the owner's table without replacing it: nothing of the
                # owner stays reachable and a later registration starts from a clean slate.
                del self._registrations[owner]
                self.app.router.routes = table
                self.clear_template_cache()
                log.info("Registrar: '%s' removed (0 route(s) registered in its place).", owner)
                return None
        for path, capability in declared.items():
            claimed = path if mount.is_fixed(path) else _join(base, path)
            self._claim(claimed, capability, owner, reserved_ok=(owner in SHELL_OWNERS))

        if router is None:  # unreachable: the two branches above retired the only such case
            return None
        registration = Registration(owner=owner, router=router, nav=nav_items,
                                    capabilities=declared, templates=template_dir,
                                    prefix=base,
                                    included=[wrapper] if wrapper is not None else [],
                                    routes=routes)
        self._registrations[owner] = registration
        self._owned_route_ids.update(id(route) for route in routes)
        for route in routes:
            self._route_bases[id(route)] = base
        if template_dir is not None:
            self._template_dirs[owner] = template_dir
        # THE SWAP IS ONE ASSIGNMENT: a request already walking the old list finishes on it, and a
        # request that starts afterwards sees the complete new table — never a half-built one.
        self.app.router.routes = table
        _mark_routes(self.app.router)
        self.clear_template_cache()
        log.info("Registrar: '%s' replaced (%d route(s), %d capability declaration(s)).",
                 owner, len(routes), len(declared))
        return registration

    def move_console(self, new_prefix: str) -> str:
        """Move every console registration from the current mount to *new_prefix*.

        The console's page tree is registered at one base; when an enabled root site appears or
        disappears that base changes. The routes are moved IN PLACE (their own path and compiled
        matcher), the capability declarations are re-keyed under the new base and the nav URLs are
        re-mounted. The site's own registration is never moved: its routes keep their site prefixes.
        Fixed installation paths (``mount.is_fixed``) never move.

        Every new claim is checked before the first mutation, so a refused claim leaves the table as
        it was. Returns the new prefix.
        """
        old = self.console_prefix
        new = new_prefix or ""
        if new == old:
            return old
        moving: list[Registration] = []
        for owner, registration in list(self._registrations.items()):
            if owner in ("site", "installation") or registration.prefix != old:
                continue
            for path, capability in registration.capabilities.items():
                claimed = path if mount.is_fixed(path) else _join(new, path)
                self._check_claim(claimed, owner, reserved_ok=(owner in SHELL_OWNERS))
            moving.append(registration)
        for registration in moving:
            # DECLARE BEFORE SERVING: the new paths are claimed before their routes are moved there,
            # so a path that appears is never reachable undeclared. The old paths are released first,
            # so the window is a DENY (nothing served) and never an unclaimed PUBLIC route.
            self._release_registration(registration)
            for path, capability in registration.capabilities.items():
                claimed = path if mount.is_fixed(path) else _join(new, path)
                self._claim(claimed, capability, registration.owner,
                            reserved_ok=(registration.owner in SHELL_OWNERS))
            for route in registration.routes:
                _unmount_route(route, old)
                _mount_route(route, new)
            registration.nav = tuple(_mounted_nav(_unmounted_nav(item, old), new)
                                     for item in registration.nav)
            registration.prefix = new
            for route in registration.routes:
                self._route_bases[id(route)] = new
            _mark_routes(registration.router)
        self.console_prefix = new
        log.info("Registrar: the console moved from '%s' to '%s' (%d registration(s)).",
                 old or "/", new or "/", len(moving))
        return new

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

    def serves(self, path: str) -> bool:
        """Whether a route this registrar registered matches *path* — pages AND dynamic paths.

        Matched with each route's own compiled ``path_regex``, by the registration's stored route
        objects (``self.routes``), which is the only enumeration that sees mounted page paths. A
        static comparison against ``route.path`` would answer ``False`` for ``/player/<ucid>``.
        """
        text = str(path or "")
        if not text.startswith("/"):
            return False
        for route in self.routes:
            if _route_answers(route, text):
                return True
        return False

    def slash_target(self, path: str) -> str | None:
        """The unslashed address *path* should be redirected to, or ``None``.

        ``None`` in three cases, each of them a rule rather than a defensive check:

        * the path is not a trailing-slash form (the site root included — stripping its slash names
          no path at all);
        * the unslashed form is served by no route, so a redirect would invent an address;
        * a route DECLARED at ``/x/`` — its own path ends with the slash — answers the slashed form,
          so that form is an address of its own and is never redirected away from. A parameterised
          route does not count: ``/fleet/{name:path}`` matches ``/fleet/`` with an empty name, which
          is nobody's address, and ``/fleet`` is what a pasted link was aiming at.
        """
        text = str(path or "")
        if len(text) < 2 or not text.endswith("/") or text == "//":
            return None
        unslashed = text[:-1]
        if not unslashed.startswith("/") or not self.serves(unslashed):
            return None
        for route in self.routes:
            if _declares_slash(route) and _route_answers(route, text) \
                    and not _route_answers(route, unslashed):
                return None
        return unslashed

    def owns_route(self, route) -> bool:
        """Whether *route* is a route THIS registrar registered (a page or an asset).

        THE route-identity test the gate uses. A route object the console did not register —
        ``plugins/restapi``'s own ``APIRoute``s, the WebService's debug endpoints, anything another
        component added straight to the app — is NOT the console's, whatever its path: two
        components' routes can share a PATH while being different ROUTES, and only ownership can
        tell them apart.
        """
        if route is None:
            return False
        return id(route) in self._owned_route_ids

    def capability_for(self, route=None, path: str | None = None) -> str | None:
        """The capability declared for a matched route / requested path, or ``None``.

        Two shapes reach the gate:

        * an ``APIRoute`` the console registered (``route`` is set). It resolves BY ROUTE IDENTITY:
          only a route this registrar owns may take a capability, and it is looked up by the route's
          OWN path — never by the requested path, because a path can be shared with another
          component's route. A route the console does not own answers ``None`` here, so the gate
          falls through to the route's own guard
          (:func:`services.webservice.permissions.is_self_guarded`) and the old endpoints keep their
          own auth.
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
