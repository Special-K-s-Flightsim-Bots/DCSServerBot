"""The console must never intercept another component's route, and the REST API owns ``/servers``.

THE PATH RULE. The REST API (``plugins/restapi``) owns ``GET /servers`` — that is a hard
requirement (the plugin's callers cannot be changed). So the console's own Servers page must NOT
live at that path: it lives at ``/fleet`` (``pages.servers.SERVERS_PATH``), a path the API does not
claim. The two surfaces therefore no longer share a path at all.

TWO mechanisms keep the console from ever shadowing a peer, and both are pinned here:

* the console registers its own pages as LOW-PRIORITY routes, so a route another component
  registered on the SAME path WINS it (the console can never shadow a peer);
* the gate resolves a capability BY ROUTE IDENTITY (``Registrar.owns_route``), never by path, so a
  route the console did not register is never handed a console capability — it keeps its OWN auth.

The console's own behaviour is unchanged: its page keeps its capability and its 403 message.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter, Depends, HTTPException

from conftest import asgi_client, permissions
from services.webservice.pages import servers as servers_page

#: the console's own Servers page path — the ONE constant, never a re-typed string.
CONSOLE_SERVERS_PATH = servers_page.SERVERS_PATH
#: the path the REST API owns and the console must stay off.
API_SERVERS_PATH = "/servers"


def _plugin_router(*, path: str = API_SERVERS_PATH, refuse: bool = False) -> APIRouter:
    """A REST surface shaped like ``plugins/restapi``: an endpoint with its OWN auth dependency."""
    router = APIRouter(prefix="")

    def api_auth():
        if refuse:
            raise HTTPException(status_code=401, detail="Missing API key.")

    async def servers():
        return {"who": "restapi", "count": 1}

    router.add_api_route(path, servers, methods=["GET"], dependencies=[Depends(api_auth)])
    return router


def _console_servers_route(registrar):
    """The console's own Servers page route object (there is exactly one), at its OWN path."""
    matches = [r for r in registrar.routes if getattr(r, "path", None) == CONSOLE_SERVERS_PATH]
    assert len(matches) == 1, f"expected one console {CONSOLE_SERVERS_PATH} route, got {matches}"
    return matches[0]


# --------------------------------------------------- the REST API keeps ``/servers``, untouched

@pytest.mark.asyncio
async def test_the_old_endpoint_is_reachable_without_a_console_session(shell_app):
    """GET /servers with the plugin's own auth and NO console session returns the PLUGIN's answer."""
    app, registrar = shell_app
    app.include_router(_plugin_router())
    async with asgi_client(app) as client:
        response = await client.get(API_SERVERS_PATH)
    assert response.status_code == 200, response.text
    assert response.json() == {"who": "restapi", "count": 1}


@pytest.mark.asyncio
async def test_the_endpoints_own_auth_decides_not_the_console_gate(shell_app):
    """The plugin's own refusal (401) survives — the console gate did not intercept the request.

    If the console capability had intercepted the API path, this would be a 403 carrying the
    console's "needs one of" detail. It must be the plugin's own 401.
    """
    app, registrar = shell_app
    app.include_router(_plugin_router(refuse=True))
    async with asgi_client(app) as client:
        response = await client.get(API_SERVERS_PATH)
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing API key."
    assert "needs one of" not in response.text


def test_the_console_registers_no_route_on_the_api_path(shell_app):
    """The console must own NO route at the API's path — that is the whole point of the move.

    A console route on ``/servers`` would be the shadowing this module exists to forbid. The
    console's Servers page is at ``/fleet`` instead, so the API's path is the plugin's alone.
    """
    app, registrar = shell_app
    console_paths = {getattr(route, "path", None) for route in registrar.routes}
    assert API_SERVERS_PATH not in console_paths, \
        "the console must not register a route on the REST API's path"
    assert CONSOLE_SERVERS_PATH in console_paths, "the console's Servers page moved off /servers"
    assert CONSOLE_SERVERS_PATH != API_SERVERS_PATH


def test_no_console_nav_item_points_at_the_api_path(shell_app):
    """Every console link (nav) is off the API's path — no menu entry reaches ``/servers``."""
    app, registrar = shell_app
    urls = [item.url for item in registrar.nav]
    assert API_SERVERS_PATH not in urls, f"a console nav item still points at the API: {urls}"
    assert CONSOLE_SERVERS_PATH in urls, "the Servers menu entry must point at the console's page"


# --------------------------------------------------- the console page, on its own path, still gates

@pytest.mark.asyncio
async def test_the_console_page_on_its_own_path_still_enforces_its_capability(shell_app):
    """The console's Servers page is the matched route on ``/fleet`` and answers its own 403."""
    app, registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get(CONSOLE_SERVERS_PATH)
    assert response.status_code == 403
    assert "needs one of" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_console_page_is_reachable_behind_its_gate_with_the_api_present(shell_app):
    """With the API router present, ``/servers`` is the plugin's AND ``/fleet`` is the console's.

    The two surfaces live on different paths, so installing the API router changes nothing about
    the console page: ``GET /fleet`` still reaches the console page and still runs its gate (403
    for a session-less caller). ``GET /servers`` is the plugin's.
    """
    app, registrar = shell_app
    app.include_router(_plugin_router())
    async with asgi_client(app) as client:
        api = await client.get(API_SERVERS_PATH)
        page = await client.get(CONSOLE_SERVERS_PATH)
    assert api.status_code == 200 and api.json() == {"who": "restapi", "count": 1}
    assert page.status_code == 403
    assert "needs one of" in page.json()["detail"]


def test_the_gate_capability_is_resolved_by_route_identity_not_by_path(shell_app):
    """The mechanism: only a route the console OWNS resolves a console capability, whatever the
    requested path says. A foreign route on the console's own path answers ``None``.
    """
    app, registrar = shell_app
    console_route = _console_servers_route(registrar)

    plugin_router = _plugin_router(path=CONSOLE_SERVERS_PATH)
    app.include_router(plugin_router)
    plugin_route = next(r for r in plugin_router.routes
                        if getattr(r, "path", None) == CONSOLE_SERVERS_PATH)

 # same path...
    assert getattr(console_route, "path") == getattr(plugin_route, "path") == CONSOLE_SERVERS_PATH
 #... different identities
    assert registrar.owns_route(console_route) is True
    assert registrar.owns_route(plugin_route) is False
    assert registrar.capability_for(route=console_route, path=CONSOLE_SERVERS_PATH) == "servers.view"
    assert registrar.capability_for(route=plugin_route, path=CONSOLE_SERVERS_PATH) is None
 # the gate's route-identity rule: a foreign route never resolves a capability, even by path
    assert registrar.capability_for(route=object(), path=CONSOLE_SERVERS_PATH) is None


# ----------------------------- the low-priority guarantee (a synthetic shared path, fail-closed)

def _post_only_plugin_router(path: str) -> APIRouter:
    """A REST surface accepting ONLY ``POST`` on *path* — the 405 partial-match shape.

    The console page accepts only ``GET``, so the plugin's ``POST``-only route can never FULL-match
    a console method. Its ``PARTIAL`` match (path matches, method does not) is, however, a NORMAL
    route, and the pinned FastAPI router serves a normal route's partial BEFORE it ever falls
    through to the console's LOW-PRIORITY page (see ``Registrar.register_pages``). So a request
    with a method the plugin does NOT accept (``GET``) is answered by the plugin route with a 405 —
    and the console page is never reached. This is the mismatch this module pins.
    """
    router = APIRouter(prefix="")

    def api_auth():
        raise HTTPException(status_code=401, detail="Missing API key.")

    async def servers():
        return {"who": "restapi", "count": 1}

    router.add_api_route(path, servers, methods=["POST"], dependencies=[Depends(api_auth)])
    return router


@pytest.mark.asyncio
async def test_a_plugin_route_with_a_method_the_console_page_lacks_flips_the_path_to_405(shell_app):
    """A ``POST``-only plugin route on the console's ``GET`` page path turns ``GET`` into a 405.

    WHAT IS PINNED, exactly:

 * the status is a REFUSAL (405), with ``Allow: POST`` — the method the plugin route does accept;
 * it is NOT the console page: not its 403, and its "needs one of" gate sentence is absent;
 * it is NOT the plugin's own answer either: not its 401, and its ``{"who": "restapi"}`` body is
      absent.

    A future routing change that turns this 405 into the console page answering, into a different
    status, or into anything that serves content is a REGRESSION this test fails on.
    """
    app, _ = shell_app
    app.include_router(_post_only_plugin_router(CONSOLE_SERVERS_PATH))
    async with asgi_client(app) as client:
        response = await client.get(CONSOLE_SERVERS_PATH)
    assert response.status_code == 405, response.text
    assert response.headers.get("allow") == "POST"
    assert response.json()["detail"] == "Method Not Allowed"
 # not the console page (the gate did NOT fall open into the page)...
    assert "needs one of" not in response.text
    assert response.status_code != 403
 #... and not the plugin's route answering for a method it does not accept
    assert response.json() != {"who": "restapi", "count": 1}
    assert response.json().get("detail") != "Missing API key."


@pytest.mark.asyncio
async def test_the_flipping_plugin_route_is_never_served_for_a_method_it_does_not_accept(shell_app):
    """On the flipped path the plugin handler is served for ``POST`` ALONE.

    ``POST`` reaches the plugin's route (its own 401 auth decides). ``GET`` — the console page's
    method, which the plugin route does not accept — and ``PUT`` both answer 405: the plugin handler
    is NOT served for a method it does not accept, and the console page is not served either.
    """
    app, _ = shell_app
    app.include_router(_post_only_plugin_router(CONSOLE_SERVERS_PATH))
    async with asgi_client(app) as client:
        post = await client.post(CONSOLE_SERVERS_PATH)
        get = await client.get(CONSOLE_SERVERS_PATH)
        put = await client.put(CONSOLE_SERVERS_PATH)
 # POST is the one method the plugin accepts: its own auth decides (no console involvement)
    assert post.status_code == 401
    assert post.json()["detail"] == "Missing API key."
 # the methods it does NOT accept are refused, never handed to the plugin handler
    for response in (get, put):
        assert response.status_code == 405, response.text
        assert response.json().get("detail") != "Missing API key."
        assert response.json() != {"who": "restapi", "count": 1}


@pytest.mark.asyncio
async def test_the_console_page_still_runs_its_gate_when_nothing_foreign_claims_the_path(shell_app):
    """The flip is a property of the SHARED path, not of the console page: with no foreign route on
    ``/fleet`` the console page is the matched route and its gate still runs — ``GET`` answers the
    console's 403, unchanged.
    """
    app, _ = shell_app
    async with asgi_client(app) as client:
        response = await client.get(CONSOLE_SERVERS_PATH)
    assert response.status_code == 403
    assert "needs one of" in response.json()["detail"]


def test_the_flip_is_the_plugin_route_not_the_console_gate(shell_app):
    """The mechanism, by route identity: on a shared path the console page owns the capability and
    the plugin's ``POST``-only route resolves NO capability (it keeps its own auth).
    """
    app, registrar = shell_app
    console_route = _console_servers_route(registrar)
    plugin_router = _post_only_plugin_router(CONSOLE_SERVERS_PATH)
    app.include_router(plugin_router)
    plugin_route = next(r for r in plugin_router.routes
                        if getattr(r, "path", None) == CONSOLE_SERVERS_PATH)

    assert getattr(console_route, "path") == getattr(plugin_route, "path") == CONSOLE_SERVERS_PATH
    assert registrar.capability_for(route=console_route, path=CONSOLE_SERVERS_PATH) == "servers.view"
    assert registrar.capability_for(route=plugin_route, path=CONSOLE_SERVERS_PATH) is None


def test_the_console_registers_its_pages_as_low_priority_routes(shell_app):
    """The routing half: the console's page route is a LOW-PRIORITY route, which is what lets a
    route another component registered win a shared path (see ``Registrar.register_pages``)."""
    app, registrar = shell_app
    console_route = _console_servers_route(registrar)
    routers = [registration.router for registration in registrar.registrations.values()]
 # the page is NOT in any wrapper's NORMAL routes...
    assert not any(r is console_route
                   for router in routers
                   for r in (getattr(router, "routes", []) or [])), \
        "a console page must not sit in the wrapper's NORMAL routes, or it would shadow a peer"
 #... it IS a LOW-PRIORITY route
    assert any(r is console_route
               for router in routers
               for r in (getattr(router, "_low_priority_routes", []) or [])), \
        "the console page must be a LOW-PRIORITY route"
