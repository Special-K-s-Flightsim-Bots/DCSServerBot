"""A browser refusal is a page (or a trip to the login door); every other caller keeps the JSON.

An unauthenticated or under-privileged BROWSER request to a page is answered an HTML page carrying the
REAL status code (or a redirect to the login route when the visitor is anonymous); an API path gets
JSON whatever it asked for; and a caller that never asked for HTML (bot, curl, XHR -- no ``Accept``
or ``application/json``) gets the JSON body it has always had, byte for byte."""
from __future__ import annotations

import re

import pytest
from fastapi import APIRouter, Depends

from conftest import stub_identity, asgi_client, create_app, install_shell, permissions, session

from services.webservice import auth as auth_layer
from services.webservice.auth import routes as auth_routes
from services.webservice.pages import dashboard as dashboard_page

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
JSON_ACCEPT = {"accept": "application/json"}
NO_ACCEPT = {}  # httpx sends `*/*` when no Accept header is given - the bot/curl shape


class _StubManager:
    """A signed-in identity, without a backend: install_shell(config={}) has no backend enabled."""

    def __init__(self, identity):
        self._identity = identity

    def authenticate(self, request):
        return self._identity


def _refused_route(router: APIRouter, path: str) -> APIRouter:
    """A one-route contributor gated by a capability an anonymous caller cannot satisfy."""

    @router.get(path)
    async def _handler():
        return {"body": "never reached"}

    return router


def _app_with_a_refused_api_path():
    app = create_app()
    registrar = install_shell(app, node=None, config={})
    permissions.declare_capability("probe.api", ("Admin",))
    registrar.register_pages("probe", _refused_route(APIRouter(), "/api/probe"),
                             capabilities={"/api/probe": "probe.api"})
    return app


# ----------------------------------------------------------------- browser: the login door

@pytest.mark.asyncio
async def test_an_anonymous_browser_is_sent_to_the_login_page(shell_app):
    """An anonymous visitor opening the console gets a door, not a JSON body."""
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get(dashboard_page.DASHBOARD_PATH, headers=HTML)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert location.startswith(auth_routes.LOGIN_PATH), location
    assert "next=" in location, "the login door must know where to come back to"


@pytest.mark.asyncio
async def test_the_login_redirect_carries_the_path_but_never_echoes_the_query(shell_app):
    app, _registrar = shell_app
    payload = "PAYLOAD_SENTINEL"
    async with asgi_client(app) as client:
        response = await client.get(f"{dashboard_page.DASHBOARD_PATH}?probe={payload}", headers=HTML)
    assert response.status_code == 303
    assert payload not in response.headers["location"], \
        "the query string is attacker-controlled and must not be reflected"


# ------------------------------------------------------- browser, signed in: an HTML refusal

@pytest.mark.asyncio
async def test_a_signed_in_browser_without_the_role_gets_an_html_page_with_the_real_code(shell_app):
    """Under-privileged, not anonymous: a page is the answer, and it carries 403."""
    app, _registrar = shell_app
    stub_identity(["GameMaster"])
    app.state.webui_auth = _StubManager(auth_layer.Identity(subject="nobody", backend="local",
                                                            roles=frozenset({"GameMaster"})))
    async with asgi_client(app) as client:
        response = await client.get(dashboard_page.DASHBOARD_PATH, headers=HTML)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html"), response.headers["content-type"]
    assert "403" in response.text
    assert "Not authorized" not in response.text, "the JSON body must not be what a browser renders"


@pytest.mark.asyncio
async def test_the_refusal_page_never_echoes_the_requested_path_or_query(shell_app):
    """The handler is reachable from every route, so nothing from the request may reach the page."""
    app, _registrar = shell_app
    stub_identity(["GameMaster"])
    app.state.webui_auth = _StubManager(auth_layer.Identity(subject="nobody", backend="local",
                                                            roles=frozenset({"GameMaster"})))
    payload = "REFLECT_SENTINEL"
    async with asgi_client(app) as client:
        response = await client.get(f"/?probe={payload}", headers=HTML)
    assert response.status_code == 403
    assert payload not in response.text
    assert "<script" not in response.text.lower()


# --------------------------------------------------------------- every non-browser caller

@pytest.mark.asyncio
async def test_a_json_accept_keeps_the_json_403_it_always_had(shell_app):
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get(dashboard_page.DASHBOARD_PATH, headers=JSON_ACCEPT)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
 # the ONE wording of record: the console's requirement now carries the manager clause, so the
 # expected sentence is read from the same function the gate reads — never re-spelled here (a
 # hand-written copy pins the old "Admin, DCS Admin, DCS" text and reds out on the rule change).
    assert response.json() == {
        "detail": f"Not authorized (needs one of: "
                  f"{permissions.requirement_text(dashboard_page.DASHBOARD_CAPABILITY)})."}


@pytest.mark.asyncio
async def test_a_caller_that_never_asked_for_html_keeps_the_json(shell_app):
    """The bot / ``curl`` / XHR shape: no Accept at all. A document here would be rendered in an
    alert box or an image tag, so the JSON body is the wire contract."""
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get(dashboard_page.DASHBOARD_PATH, headers=NO_ACCEPT)
    assert response.status_code == 403
    assert response.json()["detail"].startswith("Not authorized")


@pytest.mark.asyncio
async def test_an_api_path_stays_json_even_for_a_browser():
    """The second signal: a browser that wandered onto an API path must not be handed a page."""
    app = _app_with_a_refused_api_path()
    async with asgi_client(app) as client:
        response = await client.get("/api/probe", headers=HTML)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json"), \
        "an API path is JSON whatever the Accept header says"


# ------------------------------------------------------------------- read vs write: the notice

def _app_with_a_refused_write():
    """A PUBLIC write route carrying the CSRF dependency, so a browser POST is refused by it."""
    app = create_app()
    registrar = install_shell(app, node=None, config={})
    router = APIRouter()

    @router.post("/probe/write", dependencies=[Depends(session.csrf_protect)])
    async def _write():
        return {"ok": True}

    registrar.register_pages("probe", router, capabilities={"/probe/write": permissions.PUBLIC})
    app.state.webui_auth = _StubManager(auth_layer.Identity(subject="nobody", backend="local",
                                                            roles=frozenset({"Admin"})))
    return app


@pytest.mark.asyncio
async def test_a_refused_write_says_that_nothing_was_saved():
    """The only question the person who submitted the form has. A 403 is raised by the gate or by a
    dependency, so the handler never ran and the claim is true."""
    app = _app_with_a_refused_write()
    async with asgi_client(app) as client:
        response = await client.post("/probe/write", data={"field": "value"}, headers=HTML)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "nothing was saved" in response.text.lower(), response.text


@pytest.mark.asyncio
async def test_a_refused_read_does_not_claim_a_write(shell_app):
    """The other direction, on the same vocabulary: a refused GET must not tell the visitor their
    work was not saved - nobody submitted anything."""
    app, _registrar = shell_app
    stub_identity(["GameMaster"])
    app.state.webui_auth = _StubManager(auth_layer.Identity(subject="nobody", backend="local",
                                                            roles=frozenset({"GameMaster"})))
    async with asgi_client(app) as client:
        response = await client.get(dashboard_page.DASHBOARD_PATH, headers=HTML)
    assert response.status_code == 403
    assert "nothing was saved" not in response.text.lower()


# ----------------------------------------------------------- a missing address: a page, never a door

@pytest.mark.asyncio
async def test_a_dead_link_is_a_page_for_a_browser_and_never_a_login_bounce(shell_app):
    """A visitor following a dead link is owed a page — not JSON, and not a login door.

    The 403 path sends an anonymous browser to sign in; a 404 must not, because nothing is being
    denied: the address is simply not served, so a reader of a public site has nothing to sign in for.
    """
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get("/no-such-page", headers=HTML)
    assert response.status_code == 404, "a missing page must not be turned into a login bounce"
    assert "location" not in response.headers
    assert response.headers["content-type"].startswith("text/html"), response.headers["content-type"]
    assert "Page not found" in response.text
    assert '"detail"' not in response.text, "the JSON body must not be what a browser renders"


@pytest.mark.asyncio
async def test_a_dead_link_keeps_the_json_body_for_a_caller_that_reads_it(shell_app):
    """The wire contract for everything that is not a browser: the body it has always read."""
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get("/no-such-page", headers=JSON_ACCEPT)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_a_missing_api_path_stays_json_even_for_a_browser(shell_app):
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get("/api/no-such-thing", headers=HTML)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json"), \
        "an API path is JSON whatever the Accept header says"


@pytest.mark.asyncio
async def test_a_dead_link_offers_the_start_page_when_the_console_owns_the_root(shell_app):
    """With no site at ``/`` that address IS the console's start page, so the offer is the dashboard."""
    app, _registrar = shell_app
    async with asgi_client(app) as client:
        response = await client.get("/no-such-page", headers=HTML)
    assert response.status_code == 404
    assert 'href="/"' in response.text
    assert "Return to the dashboard" in response.text
