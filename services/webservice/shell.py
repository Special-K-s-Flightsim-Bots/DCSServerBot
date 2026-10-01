"""``install_shell(app, node, config)`` — the DCSServerBot admin web UI substrate.

What this module owns, in one place, so nothing else has to:

* the application factory (:func:`create_app`) that attaches the app-level, deny-by-default
  access gate **at construction time** — FastAPI copies application-level dependencies into
  every ``APIRoute`` created afterwards, which is the only way "no capability declaration =
  refused" can hold for routes added later (a per-route dependency cannot express it, and a
  route added before the app-level dependency never sees it);
* the session/cookie middleware (:mod:`services.webservice.session`);
* the ONE asset owner (:meth:`Registrar.register_assets` over ``app.frontend``);
* the template environment (:mod:`services.webservice.templating`);
* the core router include and the registrar itself.

Idempotency and the app's identity
----------------------------------
``WebService`` builds its FastAPI app in ``__init__`` and **again** in ``start()`` whenever
``self.app`` is falsy (``stop()`` sets it to ``None``), and a master/agent switch stops and
re-starts master-only services — so the app object is replaced at runtime and anything
registered in ``__init__`` only is lost after a takeover. ``install_shell`` is therefore called
from ``start()`` (through ``WebService._create_app``) and is idempotent per app object: a second
call returns the registrar it already installed instead of adding a second shell.

Two things make a *new* app object whole rather than merely present: the plugin pages are replayed
from :mod:`services.webservice.contributors` (registrations are recorded when the plugins load, so
this also covers a WebService that started before the plugins did), and mounts are audited
(:func:`services.webservice.permissions.audit_mounts`) so an ungated surface is loud.

``lifespan="off"`` means FastAPI startup/shutdown events never fire on this app; every piece of
shell initialisation happens here, in ``start()``, and never in a ``@app.on_event("startup")``.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from . import auth, contributors, permissions, session, templating
from .auth import routes as auth_routes
from .registry import Registrar

__all__ = ["SHELL_ASSET_PATH", "SHELL_STATIC_DIR", "REFUSED_STATUSES", "create_app", "install_shell",
           "frontend_enabled", "refusal_response"]

log = logging.getLogger(__name__)

#: the shell's asset path: reserved, owned by exactly one frontend (see the registrar docstring)
SHELL_ASSET_PATH = "/static"
SHELL_STATIC_DIR = Path(__file__).parent / "static"

#: the statuses the access gate refuses with. Registered by STATUS through one factory, so a second
#: status cannot grow its own bespoke answer (and the API face stays byte-identical for every one).
REFUSED_STATUSES: tuple[int, ...] = (403,)

_STATE_ATTR = "webui_registrar"


def frontend_enabled(config: dict | None) -> bool:
    """Whether the admin frontend should be installed for *config* (the service's ``webservice``
    block).

    The switch is ``frontend`` and it defaults to ``false``, so every existing configuration keeps
    the frontend it has always had. ``false`` means the service runs as a REST API only: the shell
    is never installed (no page routes, no ``/auth/*``, no session middleware, no shell assets, no
    UI background work) and the ``auth:`` block is not evaluated at all. The gate lives with the
    shell so the two callers that must agree — the app factory and the UI background tasks — read
    one predicate, never their own copy of the rule.
    """
    return bool((config or {}).get("frontend", False))


def create_app() -> FastAPI:
    """Create the WebService application with the access gate already attached.

    The debug documentation endpoints (``/openapi.json``, ``/docs``, ``/redoc``) are added by
    ``WebService.add_debug_routes`` *after* this call: they are created through the app, so they
    inherit the gate, and they keep their own local-networks-only dependency — which the gate
    recognises as a route that guards itself (``permissions.is_self_guarded``).
    """
    return FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(permissions.capability_gate)],
    )


def install_shell(app: FastAPI, node=None, config: dict | None = None) -> Registrar:
    """Install the shell on *app* and return the registrar. Idempotent per app object.

    ``node`` supplies ``config_dir`` to the identity layer (C2 reads ``bot.yaml`` through it, never
    through a relative path, so a request can never see a different ``bot.yaml`` than the running
    process); ``config`` is the service's own ``webservice.yaml`` block, which carries both the
    session/cookie policy and the auth backends.
    """
    existing = getattr(app.state, _STATE_ATTR, None)
    if existing is not None:
        log.debug("Admin web shell already installed on this application, reusing its registrar.")
        return existing

    registrar = Registrar(app)
    # The service's own config, where a PAGE can read it. The log panel's default level lives in
    # webservice.yaml, and a route has no other way to reach it: the constructor argument is not
    # stored anywhere, and reading the YAML again from a request handler would bypass the schema
    # and the service's own loading (tags, defaults, the lazy-write path).
    app.state.webui_config = dict(config or {})
    session.install_session_middleware(app, config)
    auth.install_auth(app, node, config)
    _install_refusal_handlers(app)
    registrar.register_assets(SHELL_ASSET_PATH, SHELL_STATIC_DIR, permissions.PUBLIC)
    core_router, core_capabilities, core_nav = _core_router()
    registrar.register_pages("shell", core_router, nav=core_nav, capabilities=core_capabilities)
    # The WRITE surface, under its own owner (see pages/actions.py): registered right after the
    # shell's pages so a path collision is refused loudly here rather than silently winning later.
    from .pages import actions as actions_page
    actions_page.register(registrar)
    # THE ACTION REGISTRY, populated in THIS process before anything renders a control: the only
    # other discover_actions caller is services/mcpservice/server.py, so without this the web path
    # would find an empty registry, action_available() would answer False for everything, and every
    # write control would be silently omitted while the suite stayed green.
    _discover_actions(node)
    registrar.environment = templating.build_environment(registrar)

    # A rebuilt application (stop()/start(), a master/agent switch, or a WebService that started
    # after the plugin load) gets a fresh, EMPTY registrar: replay the plugin pages that were
    # recorded when the plugins loaded, or every one of them is silently missing.
    contributors.replay(registrar)
    # An ungated surface cannot be seen by the gate or by a route-table test, so say it out loud.
    permissions.audit_mounts(app, context="shell install")

    # set only after every step succeeded: a half-installed shell must not look installed
    setattr(app.state, _STATE_ATTR, registrar)
    app.state.webui_templates = registrar.environment

    summary = registrar.summary()
    log.info("Admin web shell installed: %d route(s), %d capability declaration(s), owner(s) %s, "
             "asset path '%s'.", summary["routes"], summary["capabilities"],
             ", ".join(summary["owners"]) or "-", SHELL_ASSET_PATH)
    return registrar


def _discover_actions(node=None) -> None:
    """Populate the ACTION REGISTRY for this process — the discovery wiring (review I-4).

    The console renders a write control from ``core.actions.action_available``, which answers out of
    the ``@action`` registry that only ``discover_actions`` fills. The ONLY other caller in the
    tree is ``services/mcpservice/server.py`` — a different service, on a different lifecycle — so
    on the web path the registry would stay empty, ``action_available`` would answer ``False`` for
    every action, and each control would be silently OMITTED while the whole suite stayed green.
    That is the failure mode this repo has hit before (code that looks implemented and never runs),
    so the call lives here, at install, and ``tests/test_webui_write_pause.py`` asserts
    ``action_available("pause_mission")`` through THIS shell's own install.

    The plugin list is MCPServer's own (``PluginManager.plugins``, which falls back to the node's
    configured plugin list before ``load_plugins`` has run) — the same source, so the web and MCP
    surfaces cannot disagree about which plugins exist.

    NEVER FATAL: a console that cannot read the plugin list, or a plugin whose ``actions.py``
    explodes on import, must still install and serve its pages — it simply offers no control, and a
    crafted POST gets the seam's typed "not available" refusal. Both failures are logged loudly.
    """
    try:
        from core.actions import discover_actions
    except Exception:
        log.exception("Admin web shell: the action layer could not be imported; no write control "
                      "will be offered on this installation.")
        return
    plugin_names: list[str] = []
    try:
        from core.plugin_manager import PluginManager

        # ``node=None`` (a shell installed without one) has no plugin list to read, and therefore
        # no cluster to act on: the honest answer is the empty list, not a lookup that cannot work.
        if node is not None:
            plugin_names = list(PluginManager(node).plugins)
    except Exception:
        log.exception("Admin web shell: the loaded-plugin list could not be read; no action will "
                      "be discovered and every write control is omitted (a crafted POST is refused "
                      "with a typed answer, never a 500).")
    try:
        registry = discover_actions(plugin_names)
    except Exception:
        log.exception("Admin web shell: action discovery failed for plugin(s) %s.", plugin_names)
        return
    log.info("Admin web shell: action registry populated from %d plugin(s) - %d action(s) "
             "available in this process: %s.", len(plugin_names), len(registry),
             ", ".join(sorted(registry)) or "-")


def _core_router() -> tuple[APIRouter, dict[str, str], tuple]:
    """The shell's own router, its capability declaration, and its navigation.

    The shell owns the reserved surface: ``/auth/login`` and ``/auth/logout`` (C2) plus the core
    pages (C3) here, all registered under the owner name ``shell`` — because the registrar reserves
    ``/auth/*`` and ``/`` (and the ``/static`` asset area) for the shell, a plugin cannot claim a
    login path or the site root, and it cannot bypass the gate either, since it only ever receives
    the registrar.

    The auth paths are absolute constants (:mod:`services.webservice.auth.routes`) and are declared
    ``PUBLIC`` explicitly: with an app-level deny-by-default gate, an undeclared route is refused,
    so the login page must name its capability or nobody could reach the door.

    A page module owns its own capability, nav item and copy; this function only hands the router
    over, which is what keeps "one owner per page" true while the shell stays the single place that
    knows what the reserved surface consists of.
    """
    from .pages import dashboard as dashboard_page
    from .pages import instances as instances_page
    from .pages import live as live_page
    from .pages import logs as logs_page
    from .pages import nodes as nodes_page
    from .pages import players as players_page
    from .pages import servers as servers_page

    router = APIRouter()
    auth_routes.add_routes(router)
    dashboard_page.add_routes(router)
    logs_page.add_routes(router)
    # the standalone list pages: the FULL inventory behind the dashboard's attention view. One
    # route, one capability and one nav item each, registered flat like the pages above.
    servers_page.add_routes(router)
    nodes_page.add_routes(router)
    instances_page.add_routes(router)
    players_page.add_routes(router)
    live_page.add_routes(router)
    capabilities = dict(auth_routes.capabilities())
    capabilities.update(dashboard_page.capabilities())
    capabilities.update(logs_page.capabilities())
    capabilities.update(servers_page.capabilities())
    capabilities.update(nodes_page.capabilities())
    capabilities.update(instances_page.capabilities())
    capabilities.update(players_page.capabilities())
    capabilities.update(live_page.capabilities())
    return router, capabilities, (dashboard_page.nav_items() + logs_page.nav_items()
                                  + servers_page.nav_items() + nodes_page.nav_items()
                                  + instances_page.nav_items() + players_page.nav_items())


# ---------------------------------------------------------------------------------------------
# the refusal, rendered for the caller that can read it
# ---------------------------------------------------------------------------------------------
#
# One factory, registered by status (see REFUSED_STATUSES). The decision of WHICH face a caller gets
# is ``permissions.wants_error_page`` — never re-derived here, so the two cannot drift.

def _install_refusal_handlers(app: FastAPI) -> None:
    """Register the one handler that gives a refusal its two faces, by STATUS.

    By status and not by exception class: an ``HTTPException`` is what every route raises, and a
    class-level handler would have to re-implement FastAPI's own JSON for the callers that must keep
    it (a body identical to the one they read before this change is the wire contract).
    """
    for status in REFUSED_STATUSES:
        app.add_exception_handler(status, _refusal_handler(status))


def _refusal_handler(status_code: int):
    async def handler(request, exc):
        return refusal_response(request, exc, status_code=status_code)
    return handler


def refusal_response(request, exc, *, status_code: int | None = None):
    """The answer to a refusal: a login door, an HTML page, or the JSON it has always been.

    * a non-browser caller (no ``text/html``, or an API path) gets ``{"detail": ...}`` — the same
      body and status FastAPI's default handler produced, untouched;
    * an anonymous browser on a page path gets a redirect to the login door, carrying the path so
      the visitor lands where they were going (never the query string — it is attacker-controlled);
    * a signed-in browser gets a page carrying the REAL status code.

    Nothing from the request reaches the page: no path, no query, no ``detail`` (the handler is
    reachable from every route, so copy naming one of them is wrong on the next).
    """
    status = status_code or getattr(exc, "status_code", 403)
    if not permissions.wants_error_page(request):
        return JSONResponse({"detail": getattr(exc, "detail", None)}, status_code=status,
                            headers=getattr(exc, "headers", None))
    # a 403 is raised by the gate or by a dependency, both of which run BEFORE the handler — so a
    # refused write really did save nothing, and saying so is the one thing its author needs to read
    wrote = request.method not in ("GET", "HEAD", "OPTIONS")
    if _is_signed_in(request):
        return HTMLResponse(refusal_page(status, wrote=wrote), status_code=status)
    redirect = _login_redirect(request)
    if redirect is None:
        # no identity layer is installed, so there is no login door to send anyone to: a page is
        # the only honest answer (a redirect would land on a 404)
        return HTMLResponse(refusal_page(status, wrote=wrote), status_code=status)
    return redirect


def _is_signed_in(request) -> bool:
    """Whether this request carries a resolvable identity. A failing identity layer means NOT signed
    in — the safe direction, because the visitor is then sent to the login door."""
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    if manager is None:
        return False
    try:
        return manager.authenticate(request) is not None
    except Exception:
        log.exception("Web UI refusal: the identity layer failed while answering a refusal; "
                      "treating the caller as anonymous")
        return False


def _login_redirect(request):
    """A redirect to the door, or ``None`` when no identity layer is installed.

    WHICH door — the login page, or straight into the Discord sign-in flow when Discord is the only
    one enabled — is decided by the auth layer (:func:`services.webservice.auth.routes.
    login_redirect`), which owns the doors value the login page itself renders from. One owner, so
    the page and the bounce cannot disagree; and the bounce goes through the existing start route,
    never a hand-built authorize URL.
    """
    return auth_routes.login_redirect(request)


#: the refusal page's look. Namespaced under ``.refusal`` and kept HERE rather than in the surface
#: sheet: it is a standalone document with no shell chrome (an error page loaded while the shell
#: itself is unavailable must not depend on the shell), and a class this narrow has no business
#: among the shell's layout rules.
_REFUSAL_STYLE = """<style>
.refusal { max-width: 40rem; margin: 12vh auto; padding: 0 1.5rem;
  font: 16px/1.5 system-ui, sans-serif; }
.refusal h1 { font-size: 1.25rem; margin: 0 0 .5rem; }
.refusal p { margin: 0 0 1rem; }
.refusal .refusal-status { opacity: .7; font-variant-numeric: tabular-nums; }
</style>"""


def refusal_page(status_code: int, *, wrote: bool = False) -> str:
    """The HTML a browser is refused with. Takes no request data — see :func:`refusal_response`.

    ``wrote`` swaps the sentence a person reads after SUBMITTING something: "was my work saved?" is
    the only question they have, and a refusal raised before the handler means the answer is no.
    """
    message = ("Your request could not be completed, and nothing was saved."
               if wrote else
               "Your account does not have access to this page.")
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{status_code} - access denied</title>\n"
        f"<link rel=\"stylesheet\" href=\"{SHELL_ASSET_PATH}/shell.css\">\n"
        f"{_REFUSAL_STYLE}\n"
        "</head>\n<body>\n<main class=\"refusal\">\n"
        "<h1>Access denied</h1>\n"
        f"<p>{message}</p>\n"
        f"<p class=\"refusal-status\">HTTP {status_code}</p>\n"
        "<p><a href=\"/\">Return to the dashboard</a></p>\n"
        "</main>\n</body>\n</html>\n"
    )

