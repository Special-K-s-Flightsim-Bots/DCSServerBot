"""A cancelled or refused Discord sign-in must show the console's REAL login page.

The old page was hand-built HTML: it loaded only ``shell.css``, printed the status code at the person
(``HTTP 400``) and hard-coded a link to the username/password door -- a door an installation can have
turned OFF. The cancel page is now rendered by the same renderer + template the login route uses, so
the status pill, the styling and the DOORS are config-driven; a disabled door is ABSENT; Discord's own
words (``error_description``) and the developer's status code are never echoed. The status codes are
unchanged (400 for a cancel, 404 for a disabled backend). No network: the bot and Discord's HTTP layer
are stubbed."""
from __future__ import annotations

import re
import types

import pytest

from conftest import asgi_client, create_app, input_fields, install_shell, password_fields
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
CLIENT_ID = "123456789012345678"
CLIENT_SECRET = "CLIENT-SECRET-MUST-NOT-LEAK"
PUBLIC_BASE_URL = "http://localhost:9876"
BOT_YAML = "roles:\n  Admin:\n    - specialk\n"

#: what Discord puts in ``error_description`` when the person presses "cancel", and what the
#: cancellation of the flow is asked about. Nothing of this may reach the page.
ERROR_DESCRIPTION = "The+resource+owner+denied+the+request+SENTINEL"
ECHO_SENTINEL = "SENTINEL"

LOCAL_CARD_TITLE = "Local account"


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _node(config_dir) -> types.SimpleNamespace:
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(*, local: bool = False, discord: bool = False) -> dict:
    """The webservice.yaml block for the door combinations this card names."""
    auth: dict = {"public_base_url": PUBLIC_BASE_URL,
                  "local": ({"enabled": True, "users": [
                      {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]}
                      if local else {"enabled": False, "users": []})}
    auth["discord"] = ({"enabled": True, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}
                       if discord else {"enabled": False})
    return {"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
            "auth": auth}


def _build(config_dir, config):
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    return app


async def _cancel(client, *, state: str | None = None, query: str = ""):
    """The URL Discord calls when the person presses "cancel" on the authorize page."""
    parameters = [f"error=access_denied&error_description={ERROR_DESCRIPTION}"]
    if state is not None:
        parameters.append(f"state={state}")
    if query:
        parameters.append(query)
    return await client.get(f"{discord_oauth.CALLBACK_PATH}?{'&'.join(parameters)}")


async def _start(client):
    return await client.get(discord_oauth.START_PATH)


def _state(response) -> str:
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


# --------------------------------------------------------- the page IS the console's login page

@pytest.mark.asyncio
async def test_a_cancelled_login_is_rendered_by_the_real_login_template(config_dir):
    """The marker is the template's own chrome (the status pill) — not a sentence this module
    happens to contain. A hand-built page cannot carry it."""
    app = _build(config_dir, _config(local=True, discord=True))
    async with asgi_client(app) as client:
        response = await _cancel(client)
    assert response.status_code == 400
    html = response.text
    assert '<span class="pill">' in html, "the login page's top bar/pill is missing"
    assert auth_routes.login_doors(app.state).status_text in html, \
        "the pill must state something TRUE of this configuration"
    assert '<title>Sign in — DCSServerBot</title>' in html
    assert "login-wrap" in html, "the real login page's layout is missing"


@pytest.mark.asyncio
async def test_the_cancel_page_prints_no_status_code_and_does_not_echo_discord_s_words(config_dir):
    """The status is for the developer, not for the person; and Discord's own sentence about WHY is
    never reflected — a query anybody can type must not be able to write into the page."""
    app = _build(config_dir, _config(local=True, discord=True))
    async with asgi_client(app) as client:
        response = await _cancel(client, state="not-a-real-state")
    html = response.text
    assert "HTTP 400" not in html, "the developer's status code is on the page"
    assert "HTTP" not in html, html[:400]
    assert ECHO_SENTINEL not in html, "the query text was reflected into the page"
    assert discord_oauth.CANCELLED_MESSAGE in html, "the page must SAY the sign-in was cancelled"


# ------------------------------------------------------------------------ the doors it respects

@pytest.mark.asyncio
async def test_with_local_disabled_the_cancel_page_offers_only_the_discord_door(config_dir):
    """With username/password disabled the page must not advertise it -- not as a link and not as a
    field."""
    app = _build(config_dir, _config(discord=True))
    async with asgi_client(app) as client:
        response = await _cancel(client)
    assert response.status_code == 400
    html = response.text
    assert discord_oauth.CONTROL_LABEL in html, "the enabled Discord door must be offered"
    assert f'href="{discord_oauth.START_PATH}?next=/"' in html
    assert f'href="{auth_routes.LOGIN_PATH}"' not in html, \
        "the disabled local door must not be linked"
    # the cancel page embeds the login stylesheet, whose rules select these fields: the ELEMENTS
    # are parsed, so CSS text cannot stand in for a rendered door
    assert not [field for field in input_fields(html) if field.get("name") == "username"], \
        "the disabled local door rendered its username field"
    assert not password_fields(html), "the disabled local door rendered its password field"
    assert LOCAL_CARD_TITLE not in html
    assert "<form" not in html, "a form with no door to post to must not ship at all"
    assert discord_oauth.CANCELLED_MESSAGE in html


@pytest.mark.asyncio
async def test_with_both_doors_enabled_the_cancel_page_offers_both(config_dir):
    app = _build(config_dir, _config(local=True, discord=True))
    async with asgi_client(app) as client:
        response = await _cancel(client)
    html = response.text
    assert discord_oauth.CONTROL_LABEL in html
    assert 'name="username"' in html and 'type="password"' in html
    assert LOCAL_CARD_TITLE in html
    assert discord_oauth.CANCELLED_MESSAGE in html


@pytest.mark.asyncio
async def test_with_discord_disabled_the_frontend_page_offers_only_the_local_door(config_dir):
    """The mirror case, through the path a disabled backend answers (the start route): the local
    door is offered, and there is no Discord button — because there is no Discord backend."""
    app = _build(config_dir, _config(local=True))
    async with asgi_client(app) as client:
        response = await client.get(discord_oauth.START_PATH)
    assert response.status_code == 404, "a disabled backend keeps its status"
    html = response.text
    assert 'name="username"' in html and 'type="password"' in html
    assert LOCAL_CARD_TITLE in html
    assert discord_oauth.CONTROL_LABEL not in html
    assert discord_oauth.START_PATH not in html
    assert discord_oauth.DISABLED_MESSAGE in html, "the page must say why the door is not there"
    assert "HTTP 404" not in html


@pytest.mark.asyncio
async def test_a_disabled_discord_backend_on_a_discord_only_install_still_renders_the_real_page(
        config_dir):
    """No door at all + a refused OAuth path: the page must be the console's own \"no sign-in method\"
    page, never the leftover standalone document."""
    app = _build(config_dir, _config())
    async with asgi_client(app) as client:
        response = await client.get(f"{discord_oauth.CALLBACK_PATH}?error=access_denied")
    assert response.status_code == 404
    html = response.text
    assert "no sign-in method" in html.lower()
    assert "<form" not in html
    assert "HTTP 404" not in html
    assert ECHO_SENTINEL not in html


@pytest.mark.asyncio
async def test_a_cancelled_flow_still_drops_the_pending_state(config_dir):
    """The behaviour the exchange already had must survive the new page: a judged state is spent."""
    app = _build(config_dir, _config(local=True, discord=True))
    async with asgi_client(app) as client:
        start = await _start(client)
        state = _state(start)
        cancelled = await _cancel(client, state=state)
        assert cancelled.status_code == 400
 # the state was consumed: the same one cannot finish a login
        replay = await client.get(f"{discord_oauth.CALLBACK_PATH}"
                                  f"?code=AUTH-CODE&state={state}")
        assert replay.status_code == 400


def test_the_hand_built_page_is_gone():
    """The source assertion behind the page assertions: no hard-coded status line and no hard-coded
    door link survive in the module that serves the OAuth routes."""
    from services.webservice.auth import discord_oauth as module
    source = open(module.__file__, encoding="utf-8").read()
    assert "Sign in with a username and password" not in source, \
        "a hard-coded door link is still in the module"
    assert "oauth-status" not in source, "the developer status line is still built by hand"


def test_the_error_page_api_renders_through_the_login_renderer():
    """The one renderer, stated as a source fact: ``error_page`` must not build markup of its own."""
    from services.webservice.auth import discord_oauth as module
    source = open(module.__file__, encoding="utf-8").read()
    body = source.split("def error_page(")[1]
    assert "render_login_page" in body, "error_page does not call the login renderer"
    assert re.search(r"<!doctype", body, re.IGNORECASE) is None, \
        "error_page still builds a document by hand"
