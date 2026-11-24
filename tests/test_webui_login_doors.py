"""The login page's doors: which sign-in methods an installation offers, decided by config.

The page and the server must agree, and both read ONE value built from the enabled backends
(``services/webservice/auth/routes.py::login_doors``), covering the four configurations a real
``services/webservice.yaml`` can produce (both / discord only / local only / neither). A disabled door
must be ABSENT FROM THE HTML (not hidden with CSS), and the server must refuse it too: the page never
offers a door the server would refuse, and the server never accepts one the configuration turned off."""
from __future__ import annotations

import logging
import re
import types

import jinja2
import pytest
from fastapi import APIRouter, Request

from conftest import asgi_client, create_app, install_shell, password_fields
from services.webservice import auth
from services.webservice.auth import discord_oauth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
PUBLIC_BASE_URL = "http://localhost:9876"
CLIENT_ID = "1234567890"
CLIENT_SECRET = "shhh"
BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: the status pill's sentence per case — the page must state something TRUE about the config
PILL = {
    "both": "Discord and local sign-in enabled",
    "discord": "Discord sign-in enabled",
    "local": "Local sign-in enabled",
    "none": "No sign-in method enabled",
}

#: the footer hint exists ONLY where both doors do
FOOTER_HINT = "not sure which to use?"

LOCAL_CARD_TITLE = "Local account"
NO_METHOD_MESSAGE = "no sign-in method"


def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(*, local: bool = False, discord: bool = False, breakglass: bool = False,
            auto_start: bool = False) -> dict:
    """A webservice.yaml block for one of the four cases, with the REAL config keys."""
    auth: dict = {"public_base_url": PUBLIC_BASE_URL}
    if local:
        auth["local"] = {"enabled": True,
                         "users": [{"username": "specialk", "password_hash": _hash(),
                                    "roles": ["Admin"]}]}
    else:
        auth["local"] = {"enabled": False, "users": []}
    if breakglass:
        auth["breakglass"] = {"enabled": True, "username": "emergency",
                              "password_hash": _hash("letmein")}
    auth["discord"] = ({"enabled": True, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                        "auto_start": auto_start}
                       if discord else {"enabled": False})
    return {"session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
            "auth": auth}


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _build(config_dir, config):
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    return app


async def _page(app) -> str:
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGIN_PATH)
    assert response.status_code == 200, response.text
    return response.text


# ------------------------------------------------------------------ the value and its owner

def test_the_doors_value_is_built_from_the_enabled_backends(config_dir):
    """ONE value describes the doors, and it is built from what the server will enforce."""
    both = auth_routes.login_doors(_build(config_dir, _config(local=True, discord=True)).state)
    assert (both.discord, both.password, both.count, both.both) == (True, True, 2, True)
    assert both.status_text == PILL["both"]

    local = auth_routes.login_doors(_build(config_dir, _config(local=True)).state)
    assert (local.discord, local.password, local.count) == (False, True, 1)
    assert local.status_text == PILL["local"]

    discord = auth_routes.login_doors(_build(config_dir, _config(discord=True)).state)
    assert (discord.discord, discord.password, discord.count) == (True, False, 1)
    assert discord.status_text == PILL["discord"]

    none = auth_routes.login_doors(_build(config_dir, _config()).state)
    assert (none.discord, none.password, none.count, none.none) == (False, False, 0, True)
    assert none.status_text == PILL["none"]


def test_a_password_door_that_is_break_glass_only_still_counts(config_dir):
    """`auth.breakglass.enabled` is a real password door on the same form: the page must offer it,
    because the server accepts it (pinned end to end by the break-glass session test)."""
    doors = auth_routes.login_doors(
        _build(config_dir, _config(local=False, breakglass=True)).state)
    assert (doors.discord, doors.password) == (False, True)


def test_the_template_fails_loudly_when_the_doors_value_is_not_passed(config_dir):
    """StrictUndefined discipline: a name nobody passes is a hard error, never an empty string."""
    app = _build(config_dir, _config(local=True))
    environment = app.state.webui_templates
    with pytest.raises(jinja2.UndefinedError):
        environment.get_template(auth_routes.LOGIN_TEMPLATE).render(
            login_path=auth_routes.LOGIN_PATH, csrf_field="x", csrf_token="t", next_url="/",
            error=None, discord_url=None, discord_label=discord_oauth.CONTROL_LABEL,
            discord_scope=discord_oauth.SCOPE)


# ------------------------------------------------------------------------------ both doors

@pytest.mark.asyncio
async def test_both_enabled_renders_both_cards_and_the_hint(config_dir):
    html = await _page(_build(config_dir, _config(local=True, discord=True)))
    assert discord_oauth.CONTROL_LABEL in html
    assert f'href="{discord_oauth.START_PATH}?next=/"' in html
    assert 'name="username"' in html and 'type="password"' in html
    # the stylesheet alone satisfies the substring above, so the FIELD is asserted separately
    assert password_fields(html), "the local door rendered no password field at all"
    assert f'action="{auth_routes.LOGIN_PATH}"' in html
    assert LOCAL_CARD_TITLE in html
    assert FOOTER_HINT in html
    assert PILL["both"] in html


# ---------------------------------------------------------------------------- discord only

@pytest.mark.asyncio
async def test_discord_only_renders_a_single_discord_card_and_no_local_form(config_dir):
    html = await _page(_build(config_dir, _config(discord=True)))
    assert discord_oauth.CONTROL_LABEL in html
    assert f'href="{discord_oauth.START_PATH}?next=/"' in html
    # the disabled door is ABSENT, not hidden
    assert 'name="username"' not in html
    assert not password_fields(html), (
        "the local form's password field is rendered under a door that is off")
    assert LOCAL_CARD_TITLE not in html
    assert "<form" not in html, "a form with no door to post to must not ship at all"
    assert "<hr" not in html, "no either/or divider without two doors"
    assert FOOTER_HINT not in html
    assert PILL["discord"] in html


# ------------------------------------------------------------------------------ local only

@pytest.mark.asyncio
async def test_local_only_renders_a_single_local_card_and_no_discord_control(config_dir):
    html = await _page(_build(config_dir, _config(local=True)))
    assert 'name="username"' in html and 'type="password"' in html
    assert LOCAL_CARD_TITLE in html
    assert discord_oauth.CONTROL_LABEL not in html
    assert discord_oauth.START_PATH not in html
    assert "Discord" not in html, "no hint may point at a door that does not exist"
    assert FOOTER_HINT not in html
    assert PILL["local"] in html
    assert 'autofocus' in html, "with one door, the cursor belongs on its first field"


# --------------------------------------------------------------------------------- neither

@pytest.mark.asyncio
async def test_neither_enabled_renders_an_explicit_page_and_no_form(config_dir):
    html = await _page(_build(config_dir, _config()))
    assert NO_METHOD_MESSAGE.lower() in html.lower()
    assert "<form" not in html
    assert 'name="username"' not in html
    assert discord_oauth.CONTROL_LABEL not in html
    assert discord_oauth.START_PATH not in html
    assert FOOTER_HINT not in html
    assert PILL["none"] in html


def test_neither_enabled_is_logged_critically_at_startup(config_dir, caplog):
    """Mid-configuration is a legitimate state — the service must NOT crash, and an operator must
    find out from the log."""
    with caplog.at_level(logging.CRITICAL, logger="services.webservice.auth"):
        app = _build(config_dir, _config())
    assert app is not None, "a console with no door must still start (it also serves API traffic)"
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert critical, "no CRITICAL log line for a console with no sign-in method"
    assert any("sign-in method" in record.getMessage() for record in critical), \
        [record.getMessage() for record in critical]


# ------------------------------------------------------------- the server enforces the same thing

@pytest.mark.asyncio
async def test_a_local_login_post_is_refused_while_the_password_door_is_off(config_dir):
    """A direct POST must be a REFUSAL, not a login attempt — and it must never authenticate."""
    app = _build(config_dir, _config(discord=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
 # the token comes from the session the way a page with a form mints it; the login page has
 # no form in this configuration, so nothing is read out of it
        token = (await client.get("/probe/csrf")).json()["token"]
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token})
        assert response.status_code == 403, response.text
        assert "set-cookie" not in response.headers, "a refusal sets no cookie"
        session_state = (await client.get("/probe/session")).json()["session"]
    assert auth.SESSION_IDENTITY_KEY not in session_state, "the POST must not have authenticated"
    assert app.state.webui_auth.backend("local") is None


@pytest.mark.asyncio
async def test_a_browser_post_to_the_disabled_password_door_never_authenticates(config_dir):
    """The browser face of the same refusal, pinned: it does not reach a credential check either
    (no identity is written), whatever it is answered with."""
    app = _build(config_dir, _config(discord=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        token = (await client.get("/probe/csrf")).json()["token"]
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token}, headers=HTML)
        assert response.status_code in (302, 303), response.text
        session_state = (await client.get("/probe/session")).json()["session"]
    assert auth.SESSION_IDENTITY_KEY not in session_state


@pytest.mark.asyncio
async def test_the_discord_start_route_is_refused_while_the_discord_door_is_off(config_dir):
    """The mirror direction, pinning the existing behaviour: a disabled door refuses a direct GET."""
    app = _build(config_dir, _config(local=True))
    async with asgi_client(app) as client:
        response = await client.get(discord_oauth.START_PATH)
    assert response.status_code == 404
    assert discord_oauth.DISABLED_MESSAGE in response.text


@pytest.mark.asyncio
async def test_a_local_login_still_works_while_the_password_door_is_on(config_dir):
    """The positive control for the refusal above: the same POST succeeds when the door is on."""
    app = _build(config_dir, _config(local=True))
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token})
        assert response.status_code == 303, response.text


# ---------------------------------------------------------- what already worked keeps working

@pytest.mark.asyncio
async def test_a_refused_login_still_shows_the_generic_sentence(config_dir):
    app = _build(config_dir, _config(local=True))
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": "wrong",
                                           "_csrf_token": token})
    assert response.status_code == 401
    assert auth_routes.FAILED_LOGIN_MESSAGE in response.text
    for leak in ("unknown user", "bad password", "hash"):
        assert leak.lower() not in response.text.lower(), leak


@pytest.mark.asyncio
async def test_the_next_value_cannot_become_an_open_redirect(config_dir):
    app = _build(config_dir, _config(local=True))
    async with asgi_client(app) as client:
        for bad in ("//evil.example.com", "https://evil.example.com", "evil", ""):
            page = await client.get(f"{auth_routes.LOGIN_PATH}?next={bad}")
            assert 'name="next" value="/"' in page.text, bad
            token = re.search(r'name="_csrf_token" value="([^"]+)"', page.text).group(1)
            response = await client.post(auth_routes.LOGIN_PATH,
                                         data={"username": "specialk", "password": PASSWORD,
                                               "_csrf_token": token, "next": bad})
            assert response.status_code == 303
            assert response.headers["location"] == "/", bad


# ------------------------------------- discord-only: the login page is the door
#
# When Discord is the ONLY enabled door, an anonymous request to a guarded PAGE still gets the
# login page: it carries the path and the surface the visitor was refused on, renders the Discord
# control (whose own href is the start route, so the state nonce and its cookie are minted by the
# one code path that owns them) and no username/password form. ``auth.discord.auto_start: true``
# is the one way back to the immediate jump: a 302 into the start route, no page in between.

HTML = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
JSON_ACCEPT = {"accept": "application/json"}


def _probe_router():
    """A gated page, a gated API path and the two session probes (all in-process, no Discord).

    ``Request`` is imported at module level on purpose: this module uses
    ``from __future__ import annotations``, so the annotation is resolved against the MODULE's
    globals — a function-local import makes FastAPI read ``request`` as a missing QUERY parameter
    and answer 422.
    """
    router = APIRouter()

    @router.get("/probe/admin")
    async def admin():
        return {"ok": "admin"}

    @router.get("/api/probe")
    async def api():
        return {"ok": "api"}

    @router.get("/probe/session")
    async def session_dump(request: Request):
        return {"session": dict(request.session)}

    @router.get("/probe/csrf")
    async def csrf(request: Request):
        """The session's token, the way a page that renders a form mints it (the sign-out form on
        the dashboard does this for a discord-only install, where the login page has no form)."""
        from conftest import session as session_module
        return {"token": session_module.get_csrf_token(request)}

    return router


def _register_probes(registrar):
    from conftest import permissions
    permissions.declare_capability("probe.admin", ("Admin",))
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/admin": "probe.admin",
                                           "/api/probe": "probe.admin",
                                           "/probe/session": permissions.PUBLIC,
                                           "/probe/csrf": permissions.PUBLIC})


def _state(authorize_url: str) -> str:
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(authorize_url).query)["state"][0]


@pytest.mark.asyncio
async def test_discord_only_shows_the_login_page_including_at_the_site_root(config_dir):
    """DEFAULT: a refused page answers the LOGIN PAGE, for the root and for a guarded page.

    The login page is where every door the installation has is offered; here that is the Discord
    control alone. Following the redirect renders the form (200, no further redirect), so no
    visitor is ever thrown into a provider without seeing where they are signing in.
    """
    app = _build(config_dir, _config(discord=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        for path in ("/", "/probe/admin"):
            response = await client.get(path, headers=HTML)
            assert response.status_code == 303, f"{path}: {response.text[:200]}"
            assert response.headers["location"] == (
                f"{auth_routes.LOGIN_PATH}?next={path}&surface=/"), path

        page = await client.get(f"{auth_routes.LOGIN_PATH}?next=/&surface=/", headers=HTML)
    assert page.status_code == 200, page.text[:300]
    assert "location" not in page.headers, "the login page redirected again - a loop"
    assert discord_oauth.CONTROL_LABEL in page.text
    assert f'href="{discord_oauth.START_PATH}?next=/' in page.text
    assert password_fields(page.text) == [], "a door this installation does not have was offered"


@pytest.mark.asyncio
async def test_discord_only_auto_start_restores_the_immediate_bounce(config_dir):
    """``auth.discord.auto_start: true``: the old behaviour, through the existing start route.

    Nothing is hand-built: the bounce targets ``discord_oauth.START_PATH``, which mints the state
    nonce and its cookie and then answers Discord's authorize URL.
    """
    app = _build(config_dir, _config(discord=True, auto_start=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        response = await client.get("/probe/admin", headers=HTML)
        assert response.status_code == 302, response.text
        assert response.headers["location"] == f"{discord_oauth.START_PATH}?next=/probe/admin"
        start = await client.get(response.headers["location"])
        assert start.status_code == 302
        assert start.headers["location"].startswith(discord_oauth.AUTHORIZE_URL)
        assert client.cookies.get(COOKIE_NAME), "the start route mints the state into the session"
        #... and the target survives the trip
        session_response = await client.get("/probe/session")
        assert session_response.status_code == 200, session_response.text
        stored = session_response.json()["session"]
        assert stored[discord_oauth.NEXT_SESSION_KEY] == "/probe/admin"
        assert _state(start.headers["location"]) == stored[discord_oauth.STATE_SESSION_KEY]


@pytest.mark.asyncio
async def test_auto_start_has_no_effect_when_a_password_door_exists(config_dir):
    """The key only speaks for a Discord-ONLY install: with a password door the login page is where
    the visitor picks, and the console never auto-picks one for them."""
    app = _build(config_dir, _config(local=True, discord=True, auto_start=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        response = await client.get("/probe/admin", headers=HTML)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == (
        f"{auth_routes.LOGIN_PATH}?next=/probe/admin&surface=/")


@pytest.mark.asyncio
async def test_local_only_and_both_still_open_the_login_page_never_an_auto_picked_door(config_dir):
    for config in (_config(local=True), _config(local=True, discord=True)):
        app = _build(config_dir, config)
        _register_probes(app.state.webui_registrar)
        async with asgi_client(app) as client:
            response = await client.get("/probe/admin", headers=HTML)
        assert response.status_code == 303, response.text
        # the door carries the surface the refusal happened on, so the sign-in returns into THAT
        # surface (the probes are mounted at the site root, whose surface is ``/``)
        assert response.headers["location"] == (
            f"{auth_routes.LOGIN_PATH}?next=/probe/admin&surface=/")


@pytest.mark.asyncio
async def test_the_bounce_never_touches_api_json_or_asset_requests(config_dir):
    app = _build(config_dir, _config(discord=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        api = await client.get("/api/probe", headers=HTML)
        json_ = await client.get("/probe/admin", headers=JSON_ACCEPT)
        asset = await client.get("/static/shell.css")
    assert api.status_code == 403 and "location" not in api.headers
    assert api.headers["content-type"].startswith("application/json")
    assert json_.status_code == 403 and "location" not in json_.headers
    assert asset.status_code == 200, "the asset surface is public and untouched"


@pytest.mark.asyncio
async def test_sign_out_in_a_discord_only_install_lands_on_the_site_root(config_dir):
    """DEFAULT: signing out lands on the site root, as on any other install.

    That root is a guarded page, so the anonymous visitor is refused ONE more hop into the login
    page, which answers on its own — a finite chain, never a loop back into the provider.
    """
    app = _build(config_dir, _config(discord=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        token = (await client.get("/probe/csrf")).json()["token"]
        response = await client.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token})
        assert response.status_code == 303
        assert response.headers["location"] == auth_routes.SITE_ROOT
        bounced = await client.get(response.headers["location"], headers=HTML)
        assert bounced.status_code == 303, bounced.text[:200]
        assert bounced.headers["location"] == (
            f"{auth_routes.LOGIN_PATH}?next=/&surface=/")
        page = await client.get(bounced.headers["location"], headers=HTML)
    assert page.status_code == 200, "sign-out ended in a redirect loop"
    assert "location" not in page.headers


@pytest.mark.asyncio
async def test_sign_out_with_auto_start_lands_on_the_login_page(config_dir):
    """``auth.discord.auto_start: true``: the root would bounce straight back into the flow, so
    signing out lands on the login page instead — which answers on its own (no loop)."""
    app = _build(config_dir, _config(discord=True, auto_start=True))
    _register_probes(app.state.webui_registrar)
    async with asgi_client(app) as client:
        token = (await client.get("/probe/csrf")).json()["token"]
        response = await client.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token})
        assert response.status_code == 303
        assert response.headers["location"] == auth_routes.LOGIN_PATH
        page = await client.get(response.headers["location"], headers=HTML)
    assert page.status_code == 200, "the login page must not redirect into the flow"
    assert "location" not in page.headers


@pytest.mark.asyncio
async def test_a_failed_discord_sign_in_lands_on_the_real_login_page(config_dir):
    """RE-POINTED: the page a failed/cancelled Discord sign-in lands on is the
    console's REAL login page, so it offers the doors this installation actually has. Discord is the
    only door here, so the page shows the Discord control — and NOT the username/password door
    (there is no such backend, and the old hand-built page linked to it anyway)."""
    app = _build(config_dir, _config(discord=True))
    async with asgi_client(app) as client:
        response = await client.get(f"{discord_oauth.CALLBACK_PATH}?error=access_denied")
        assert response.status_code == 400
        assert discord_oauth.CONTROL_LABEL in response.text
        assert f'href="{auth_routes.LOGIN_PATH}"' not in response.text, \
            "a door this installation does not have must not be offered"
        assert 'name="username"' not in response.text
        assert "HTTP 400" not in response.text, "the developer's status code is not on the page"
        follow = await client.get(auth_routes.LOGIN_PATH)
    assert follow.status_code == 200, "no infinite loop: the door page answers on its own"


async def _csrf_token(client) -> str:
    response = await client.get(auth_routes.LOGIN_PATH)
    match = re.search(r'name="_csrf_token" value="([^"]*)"', response.text)
    assert match, "the login page must render the CSRF field where a form exists"
    assert match.group(1), "the rendered CSRF token must not be empty"
    return match.group(1)
