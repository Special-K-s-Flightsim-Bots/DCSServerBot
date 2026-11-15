"""The RETRYABLE identity refusal must reach a browser as an AUTO-RETRYING page, not raw JSON.

Reproduces Observed behaviour: after restarting the bot, the first page load answered

    {"detail":"Your identity could not be verified right now. Retry shortly - no new sign-in is
    needed."}

and a manual reload fixed it. The gate answers this state on purpose with ``RETRYABLE_STATUS`` +
``Retry-After`` (the console's own poll loop STOPS on a 403, so a restart must not look like one) --
but only 403 had a two-face handler, so the 503 fell through to FastAPI's default JSON.

These tests pin the FIX: the retryable status is registered with the same handler factory, a browser
on a page path gets its own auto-retrying page (never the login door -- the visitor is signed in),
and every non-browser caller keeps the exact JSON body, status and ``Retry-After`` it has today.

The gate path itself (a real unverifiable identity through ``capability_gate``) is covered in
``tests/test_webui_member_transient.py``; here the status is raised directly so the two FACES can be
asserted in isolation.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, HTTPException

from conftest import create_app, install_shell, permissions
from services.webservice import shell
from services.webservice.shell import SHELL_STATIC_DIR

DETAIL = ("Your identity could not be verified right now. "
          "Retry shortly - no new sign-in is needed.")
RETRY_AFTER = 7


def _refusal_app(*, page_path: str = "/probe/admin",
                 api_path: str = "/api/probe/admin") -> object:
    """The real app + two gated routes that raise the gate's exact retryable refusal."""
    app = create_app()
    registrar = install_shell(app, node=types.SimpleNamespace(master=True), config={})
    router = APIRouter()

    def refusal():
        raise HTTPException(status_code=permissions.RETRYABLE_STATUS, detail=DETAIL,
                            headers={"Retry-After": str(RETRY_AFTER)})

    @router.get(page_path)
    async def page_route():
        refusal()

    @router.get(api_path)
    async def api_route():
        refusal()

    registrar.register_pages("probe", router,
                             capabilities={page_path: permissions.PUBLIC,
                                           api_path: permissions.PUBLIC})
    return app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://testserver")


# ------------------------------------------------------------------ the browser gets the page

@pytest.mark.asyncio
async def test_a_browser_gets_the_auto_retrying_page_not_json():
    """``Accept: text/html`` on a page path gets the retrying PAGE -- not the JSON, not a redirect."""
    app = _refusal_app()
    async with _client(app) as client:
        response = await client.get("/probe/admin", headers={"accept": "text/html"})

    assert response.status_code == permissions.RETRYABLE_STATUS
    assert response.headers["content-type"].startswith("text/html"), response.headers
    assert response.headers.get("retry-after") == str(RETRY_AFTER), \
        "the page must carry the same Retry-After the JSON face does"
    assert '"detail"' not in response.text, "the browser must not get the JSON document"
    assert DETAIL not in response.text
    body = response.text
    assert "<html" in body and "<script>" in body
    assert re.search(r"location\.pathname", body), "the page must re-request the path it was asked for"
    assert "reload" in body, "the page must recover by itself once the answer is a 200"


@pytest.mark.asyncio
async def test_the_page_never_sends_the_visitor_to_the_login_door():
    """The visitor is SIGNED IN but not yet verifiable, so the answer must not bounce them to login.

    A bare redirect (3xx + Location, or a page whose only action is the login URL) would contradict
    the message that says no new sign-in is needed.
    """
    app = _refusal_app()
    async with _client(app) as client:
        response = await client.get("/probe/admin", headers={"accept": "text/html"})

    assert response.status_code == permissions.RETRYABLE_STATUS, "not a 3xx redirect"
    assert "location" not in {k.lower() for k in response.headers}
    assert "/auth/login" not in response.text
    assert "/auth/discord" not in response.text
    low = response.text.lower()
    assert "still signed in" in low and "no new sign-in" in low, \
        "the page must say what is actually happening"


@pytest.mark.asyncio
async def test_the_page_carries_the_retry_behaviour_and_an_honest_bound():
    """It retries itself, honours Retry-After, and is bounded -- a page that spins forever is the
    failure mode the bound avoids."""
    app = _refusal_app()
    async with _client(app) as client:
        body = (await client.get("/probe/admin", headers={"accept": "text/html"})).text

    assert f"var RETRY_MS = {RETRY_AFTER * 1000};" in body, \
        "the pause must come from the server's Retry-After"
    assert "retry-after" in body.lower(), "the page must honour a Retry-After on the retry too"
    assert f"var MAX_ATTEMPTS = {shell.RETRYABLE_PAGE_MAX_ATTEMPTS};" in body
    assert f"var BUDGET_MS = {shell.RETRYABLE_PAGE_BUDGET_S * 1000};" in body
    assert "Retry now" in body, "a manual retry must be offered once auto-retrying stops"


# ------------------------------------------------- the wire contract: JSON untouched for the rest

@pytest.mark.asyncio
async def test_an_api_path_keeps_the_json_body_status_and_retry_after():
    app = _refusal_app()
    async with _client(app) as client:
        response = await client.get("/api/probe/admin", headers={"accept": "text/html"})

    assert response.status_code == permissions.RETRYABLE_STATUS
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers.get("retry-after") == str(RETRY_AFTER)
    assert response.json() == {"detail": DETAIL}


@pytest.mark.asyncio
async def test_a_star_wildcard_caller_keeps_the_json_body():
    """``fetch``/XHR send ``*/*`` -- a page would be useless to them, so they keep JSON."""
    app = _refusal_app()
    async with _client(app) as client:
        response = await client.get("/probe/admin", headers={"accept": "*/*"})

    assert response.status_code == permissions.RETRYABLE_STATUS
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers.get("retry-after") == str(RETRY_AFTER)
    assert response.json() == {"detail": DETAIL}


# ------------------------------------------------------------------- registration pin / unit

def test_the_retryable_status_is_registered_with_the_two_face_handler():
    """Pin the registration so a future edit of the status tuple cannot silently drop the page face
."""
    assert permissions.RETRYABLE_STATUS in shell.REFUSED_STATUSES, shell.REFUSED_STATUSES
    assert 403 in shell.REFUSED_STATUSES, shell.REFUSED_STATUSES

    app = create_app()
    install_shell(app, node=types.SimpleNamespace(master=True), config={})
    registered = set(app.exception_handlers)
    assert permissions.RETRYABLE_STATUS in registered, registered
    assert 403 in registered, registered


def test_retry_after_seconds_reads_a_valid_header_and_falls_back_safely():
    exc = HTTPException(status_code=503, detail="x", headers={"Retry-After": "12"})
    assert shell._retry_after_seconds(exc) == 12
    assert shell._retry_after_seconds(HTTPException(status_code=503, detail="x")) > 0, \
        "a missing header must not drop the pause to zero"
    assert shell._retry_after_seconds(
        HTTPException(status_code=503, detail="x", headers={"Retry-After": "soon"})) > 0
    assert shell._retry_after_seconds(
        HTTPException(status_code=503, detail="x", headers={"Retry-After": "0"})) > 0


# ------------------------------------------------------------------------- the client half

def test_the_client_half_retries_the_retryable_status_and_never_stops_on_it():
    """SOURCE finding (no JS harness in this repo): the console's own poll loop already RETRIES a
    503 with backoff and STOPS only on 401/403 -- so an already-open dashboard recovers by itself.
    The gap was a FULL-PAGE navigation, which never runs shell.js: that is why the page face above
    carries its own retry. Pin both halves so neither can regress.
    """
    js = (Path(SHELL_STATIC_DIR) / "shell.js").read_text(encoding="utf-8")
    assert "response.status === 503" in js, "the poll loop must retry the retryable status"
    assert "status === 401 || response.status === 403" in js, \
        "401/403 still stop the loop; only the retryable status is waited out"
    assert "retry-after" in js.lower(), "the retry must honour Retry-After"

    page = shell.retryable_page(permissions.RETRYABLE_STATUS, retry_after=RETRY_AFTER)
    assert "response.status === 503" in page, "the page face retries the same status"
    assert "setInterval" in page and "location.reload" in page, \
        "the page must re-request by itself and render the recovered document"
