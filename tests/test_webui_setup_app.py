"""The setup application: what an unaccepted caller sees, what acceptance costs, and what leaks.

The subject is :func:`services.webservice.setup.app.create_setup_app` driven through a real ASGI
request, so the gate page, the cookie behaviour, the refusal statuses and the route table are the
SHIPPED ones and not a description of them.

WHAT THEY DO NOT COVER: no test here boots ``run.py``, so the boot decision itself
(``main.yaml`` absent ⇒ the console may run) is pinned in ``test_webui_setup_boot``; and no test
here starts a real socket, so "the URL is reachable from a browser" is exercised separately against
a live server.
"""
from __future__ import annotations

import socket

import pytest
from fastapi import APIRouter, Depends, Request

from conftest import asgi_client, permissions
from services.webservice import session
from services.webservice.registry import Registrar
from services.webservice.setup.app import (
    HEALTH_PATH, SETUP_LISTEN, SETUP_PATH, SetupBindRefused, create_setup_app,
    setup_window_dependency,
)
from services.webservice.setup.tour import FLAGS_PATH, LANGUAGE_PATH, STEP_PATH, THEME_PATH
from services.webservice.setup.window import ACCEPTED, SetupWindow

CODE = "ABCD2345"
WRONG_CODE = "ZZZZ9999"


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build(config_dir, *, code: str = CODE, clock=None, ttl: float = 1800.0,
          max_attempts: int = 5, lockout_seconds: float = 300.0):
    """The shipped application, over a window a test can steer."""
    window = SetupWindow(code=code, ttl=ttl, max_attempts=max_attempts,
                         lockout_seconds=lockout_seconds, wrong_delay=0.0,
                         clock=clock or Clock())
    app = create_setup_app(window, config_dir=config_dir, listen=SETUP_LISTEN)
    return app, window


def registrar_of(app) -> Registrar:
    return app.state.webui_registrar


# ------------------------------------------------------------------------- the gate page

@pytest.mark.asyncio
async def test_the_unaccepted_caller_gets_one_static_page_and_no_cookie(tmp_path):
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        response = await client.get(SETUP_PATH)
    assert response.status_code == 200
    assert "DCSServerBot setup" in response.text
    assert "Enter the code printed by DCSServerBot." in response.text
    assert response.text.count("<input") == 1
    assert "<script" not in response.text
    # the session is what produces a cookie, and the gate page deliberately does not touch it
    assert "set-cookie" not in {name.lower() for name in response.headers}


@pytest.mark.asyncio
async def test_the_gate_page_leaks_no_machine_fact(tmp_path):
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        body = (await client.get(SETUP_PATH)).text
    for fact in (str(tmp_path), socket.gethostname(), "/auth", "/api", "/static"):
        assert fact not in body, f"the gate page must not leak {fact!r}"


@pytest.mark.asyncio
async def test_the_health_probe_is_a_constant_and_needs_no_code(tmp_path):
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        response = await client.get(HEALTH_PATH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "set-cookie" not in {name.lower() for name in response.headers}


# ------------------------------------------------------------------------- the blast radius

@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/index.html", "/admin", "/api", "/api/servers",
                                  "/servers", "/nodes", "/auth/login", "/static/shell.css"])
async def test_an_unaccepted_caller_reaches_nothing_else(tmp_path, path):
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        assert (await client.get(path)).status_code == 404


def test_the_route_table_holds_the_gate_the_tour_and_the_probe_only(tmp_path):
    app, _window = build(tmp_path)
    registrar = registrar_of(app)
    paths = {route.path for route in registrar.routes}
    assert paths == {SETUP_PATH, STEP_PATH, LANGUAGE_PATH, THEME_PATH, FLAGS_PATH + "/{name}",
                     HEALTH_PATH}
    assert not [path for path in paths if path.startswith("/api")]
    # the console's own pages are not registered on this application at all
    assert registrar.page_paths == ()


def test_no_identity_layer_is_installed_on_the_setup_path(tmp_path):
    app, _window = build(tmp_path)
    assert getattr(app.state, "webui_auth", None) is None
    # the three resolvers are process-global; install_auth is what sets them, and it is never
    # called here, so they stay at the deny-by-default None
    assert permissions._ROLE_RESOLVER is None
    assert permissions._SCOPE_RESOLVER is None
    assert permissions._MANAGER_RESOLVER is None


def test_the_setup_console_refuses_anything_but_loopback(tmp_path):
    window = SetupWindow(code=CODE, wrong_delay=0.0)
    for listen in ("0.0.0.0", "192.168.0.10", "::", "example.invalid"):
        with pytest.raises(SetupBindRefused):
            create_setup_app(window, config_dir=tmp_path, listen=listen)


# ------------------------------------------------------------------------- the one-time code

@pytest.mark.asyncio
async def test_the_code_in_the_url_is_exchanged_and_leaves_the_address_bar(tmp_path):
    app, window = build(tmp_path)
    async with asgi_client(app) as client:
        response = await client.get(SETUP_PATH, params={"code": "abcd-2345"})
        assert response.status_code == 303
        assert response.headers["location"] == SETUP_PATH
        assert window.spent is True
        # the cookie starts here, and only here
        assert "set-cookie" in {name.lower() for name in response.headers}
        tour = await client.get(SETUP_PATH)
    assert tour.status_code == 200
    # acceptance leads into the tour: GET /setup renders its current (first) step
    assert "Which language do you speak?" in tour.text
    assert CODE not in tour.text, "the code is not repeated once it is spent"


@pytest.mark.asyncio
async def test_a_second_client_cannot_reuse_the_spent_code(tmp_path):
    app, _window = build(tmp_path)
    async with asgi_client(app) as client:
        assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
    async with asgi_client(app) as other:
        response = await other.get(SETUP_PATH, params={"code": CODE})
        assert response.status_code == 403
        assert "already been used" in response.text
        assert "set-cookie" not in {name.lower() for name in response.headers}
        # and it is still just the gate page for this client
        assert (await other.get(SETUP_PATH)).status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_code_from_the_form_is_refused_without_spending_it(tmp_path):
    app, window = build(tmp_path)
    async with asgi_client(app) as client:
        response = await client.post(SETUP_PATH, data={"code": WRONG_CODE})
        assert response.status_code == 403
        assert "not correct" in response.text
        assert "set-cookie" not in {name.lower() for name in response.headers}
        assert window.spent is False
        # the real code still works for this client
        assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303


@pytest.mark.asyncio
async def test_an_expired_code_is_refused_and_mints_no_session(tmp_path):
    clock = Clock()
    app, window = build(tmp_path, clock=clock, ttl=30.0)
    clock.advance(31.0)
    async with asgi_client(app) as client:
        response = await client.get(SETUP_PATH, params={"code": CODE})
        assert response.status_code == 403
        assert "expired" in response.text
        assert "set-cookie" not in {name.lower() for name in response.headers}
    assert window.spent is False


@pytest.mark.asyncio
async def test_a_flood_is_locked_out_with_429_while_the_code_survives(tmp_path):
    app, window = build(tmp_path, max_attempts=3, lockout_seconds=60.0)
    async with asgi_client(app) as client:
        for _ in range(3):
            assert (await client.post(SETUP_PATH, data={"code": WRONG_CODE})).status_code == 403
        locked = await client.post(SETUP_PATH, data={"code": WRONG_CODE})
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0
        assert "Too many incorrect codes" in locked.text
    assert window.spent is False
    # the operator is not locked out: the code still works from another address
    assert await window.exchange(CODE, "10.0.0.1") == ACCEPTED


# ------------------------------------------------------- the dependency every tour route declares

def _tour_router(window, registrar: Registrar) -> None:
    """A stand-in for the tour: a read route and a write route, both behind the two guards."""
    router = APIRouter(dependencies=[Depends(setup_window_dependency(window))])

    @router.get("/probe/tour")
    async def tour_page(request: Request):
        return {"csrf": session.get_csrf_token(request)}

    @router.post("/probe/tour", dependencies=[Depends(session.csrf_protect)])
    async def tour_write():
        return {"saved": True}

    registrar.register_pages("probe", router, capabilities={"/probe/tour": permissions.PUBLIC})


@pytest.mark.asyncio
async def test_the_tour_route_is_unreachable_until_the_code_is_accepted(tmp_path):
    app, window = build(tmp_path)
    _tour_router(window, registrar_of(app))
    async with asgi_client(app) as client:
        assert (await client.get("/probe/tour")).status_code == 404
        assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
        assert (await client.get("/probe/tour")).status_code == 200


@pytest.mark.asyncio
async def test_a_tour_write_needs_the_session_token(tmp_path):
    app, window = build(tmp_path)
    _tour_router(window, registrar_of(app))
    async with asgi_client(app) as client:
        assert (await client.post("/probe/tour", data={})).status_code == 404
        await client.get(SETUP_PATH, params={"code": CODE})
        assert (await client.post("/probe/tour", data={})).status_code == 403
        token = (await client.get("/probe/tour")).json()["csrf"]
        assert token
        ok = await client.post("/probe/tour", data={session.CSRF_FIELD: token})
        assert ok.status_code == 200
        assert ok.json() == {"saved": True}


@pytest.mark.asyncio
async def test_an_acceptance_from_another_window_does_not_satisfy_this_one(tmp_path):
    """The session stores the accepting window's fingerprint, so acceptance cannot be inherited."""
    first_app, _first = build(tmp_path)
    async with asgi_client(first_app) as client:
        assert (await client.get(SETUP_PATH, params={"code": CODE})).status_code == 303
        cookie = client.cookies
    second_app, _second = build(tmp_path)
    _tour_router(_second, registrar_of(second_app))
    async with asgi_client(second_app) as client:
        client.cookies = cookie
        assert (await client.get("/probe/tour")).status_code == 404
