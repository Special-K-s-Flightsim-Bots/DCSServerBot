"""The auth routes, the CSRF pin over every write route, and logout.

The set of write routes lacking a CSRF check is pinned to exactly the empty set, enumerated from the
REGISTRAR (never ``app.routes``); logout deletes the cookie the login set and the next request is
anonymous."""
from __future__ import annotations

import logging
import re
import types

import pytest
from fastapi import APIRouter, Request

from conftest import asgi_client, create_app, install_shell, permissions, simple_router, session
from services.webservice import auth
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
"""

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config(users=None, *, cookie_name: str = COOKIE_NAME, **kwargs) -> dict:
    config = {
        "session": {"secret": SESSION_SECRET, "cookie_name": cookie_name, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": users if users is not None else [
                     {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }
    config["auth"].update(kwargs)
    return config


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _build(config_dir, config=None, **kwargs):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=config or _config(**kwargs))
    return app, registrar


async def _login(client, username: str = "specialk", password: str = PASSWORD, **extra):
    token = await _csrf_token(client)
    return await client.post(auth_routes.LOGIN_PATH,
                             data={"username": username, "password": password,
                                   "_csrf_token": token, **extra})


async def _csrf_token(client) -> str:
    response = await client.get(auth_routes.LOGIN_PATH)
    match = re.search(r'name="_csrf_token" value="([^"]*)"', response.text)
    assert match, "the login form must render the CSRF field"
    assert match.group(1), "the rendered CSRF token must not be empty"
    return match.group(1)


# --------------------------------------------------------- the routes exist at fixed constants

def test_the_login_and_logout_paths_are_absolute_constants(config_dir):
    """The paths are module constants shared by the route, and are what the config's
    public_base_url is joined to -- never derived from a request."""
    assert auth_routes.LOGIN_PATH == "/auth/login"
    assert auth_routes.LOGOUT_PATH == "/auth/logout"
    _app, registrar = _build(config_dir)
    paths = {getattr(route, "path", None) for route in registrar.routes}
    assert auth_routes.LOGIN_PATH in paths
    assert auth_routes.LOGOUT_PATH in paths
    assert registrar.capability_for(path=auth_routes.LOGIN_PATH) == permissions.PUBLIC


def test_the_auth_routes_are_declared_public_and_the_reserved_prefix_is_respected(config_dir):
    app, _registrar = _build(config_dir)
 # the auth area's declaration is EXACT: the two password paths plus the Discord OAuth door.
 # A route added under /auth/ without a declaration still fails here (and would be refused by
 # the gate at runtime, since the gate is deny-by-default).
    from services.webservice.auth import discord_oauth
    assert auth_routes.capabilities() == {
        auth_routes.LOGIN_PATH: permissions.PUBLIC,
        auth_routes.LOGOUT_PATH: permissions.PUBLIC,
        auth_routes.LOGIN_ALIAS: permissions.PUBLIC,
        auth_routes.LOGOUT_ALIAS: permissions.PUBLIC,
        discord_oauth.START_PATH: permissions.PUBLIC,
        discord_oauth.CALLBACK_PATH: permissions.PUBLIC}
    assert set(auth_routes.capabilities().values()) == {permissions.PUBLIC}
    assert "/auth/login" not in {getattr(r, "path", None) for r in app.routes}, \
        "app.routes is blind to included routers -- enumerate the registrar instead"


# ------------------------------------------------- the bookmarkable aliases of the two doors
#
# ``mount.FIXED_PATHS`` and the registrar's reserved set both name ``/login`` and ``/logout``: the
# address a bookmark, a document or a hand-typed URL carries. They answer a redirect into the door,
# so the form's own action and its CSRF field stay on ``/auth/*``.

def test_the_aliases_are_registered_at_their_fixed_addresses(config_dir):
    _app, registrar = _build(config_dir)
    paths = {getattr(route, "path", None) for route in registrar.routes}
    assert auth_routes.LOGIN_ALIAS in paths, "/login is reserved but nothing serves it"
    assert auth_routes.LOGOUT_ALIAS in paths, "/logout is reserved but nothing serves it"
    assert registrar.capability_for(path=auth_routes.LOGIN_ALIAS) == permissions.PUBLIC
    assert registrar.capability_for(path=auth_routes.LOGOUT_ALIAS) == permissions.PUBLIC


@pytest.mark.asyncio
async def test_the_login_alias_answers_303_to_the_form_address(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGIN_ALIAS, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == auth_routes.LOGIN_PATH


@pytest.mark.asyncio
async def test_the_login_alias_carries_an_incoming_next_and_surface_unchanged(config_dir):
    """The alias validates nothing; the form's route is the one owner of the return-target rule."""
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get(f"{auth_routes.LOGIN_ALIAS}?next=/players&surface=/group1",
                                    follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{auth_routes.LOGIN_PATH}?next=/players&surface=/group1"
    # and what the door makes of it is the door's own business: an off-site target collapses there
    async with asgi_client(app) as client:
        bounced = await client.get(f"{auth_routes.LOGIN_ALIAS}?next=https://evil.example/x",
                                   follow_redirects=True)
        assert bounced.status_code == 200
        assert "evil.example" not in bounced.text, "an off-site return target reached the form"


@pytest.mark.asyncio
async def test_the_logout_alias_answers_303_to_the_logout_address(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGOUT_ALIAS, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == auth_routes.LOGOUT_PATH


@pytest.mark.asyncio
async def test_the_logout_alias_is_a_get_only_address(config_dir):
    """The sign-out itself stays a CSRF-protected POST: a GET to the alias never signs anyone out."""
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.post(auth_routes.LOGOUT_ALIAS, follow_redirects=False)
    assert response.status_code == 405, "a POST to the alias must not be routed at all"


# ------------------------------------------------------------------------- the login form

@pytest.mark.asyncio
async def test_the_login_form_renders_without_a_session_and_carries_a_csrf_token(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGIN_PATH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert f'action="{auth_routes.LOGIN_PATH}"' in response.text
    assert 'method="post"' in response.text
    assert re.search(r'name="_csrf_token" value="[^"]+"', response.text)
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}="), \
        "the form mints its token into the session, so the cookie must be set here"


@pytest.mark.asyncio
async def test_a_login_post_without_a_token_is_refused(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_login_post_with_a_forged_token_is_refused(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        await client.get(auth_routes.LOGIN_PATH)
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": "not-the-session-token"})
    assert response.status_code == 403


# --------------------------------------------------------------- login / session / capability

@pytest.mark.asyncio
async def test_a_correct_password_signs_in_and_the_capability_matches_the_configured_roles(config_dir):
    """Acceptance 2 end to end: `probe.admin` needs Admin and answers, `probe.dcs` needs DCS Admin
    and is refused -- the capability set is exactly the roles mapped to that user."""
    permissions.declare_capability("probe.admin", ("Admin",))
    permissions.declare_capability("probe.dcs", ("DCS Admin",))
    app, registrar = _build(config_dir)
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/admin": "probe.admin",
                                           "/probe/dcs": "probe.dcs",
                                           "/probe/open": permissions.PUBLIC})

    async with asgi_client(app) as client:
        assert (await client.get("/probe/admin")).status_code == 403
        response = await _login(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert (await client.get("/probe/admin")).status_code == 200
        assert (await client.get("/probe/dcs")).status_code == 403
        assert (await client.get("/probe/open")).status_code == 200


@pytest.mark.asyncio
async def test_a_wrong_password_does_not_sign_in_and_stays_on_the_form(config_dir):
    app, registrar = _build(config_dir)
    permissions.declare_capability("probe.admin", ("Admin",))
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/admin": "probe.admin"})
    async with asgi_client(app) as client:
        response = await _login(client, password="wrong")
        assert response.status_code == 401
        assert "form" in response.text
        assert (await client.get("/probe/admin")).status_code == 403


@pytest.mark.asyncio
async def test_the_login_page_is_reachable_without_a_session(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        assert (await client.get(auth_routes.LOGIN_PATH)).status_code == 200


# ------------------------------------------------- acceptance 3: offered is a subset of authorised

@pytest.mark.asyncio
async def test_every_offered_nav_item_is_authorised_for_the_local_backend(config_dir):
    """`offered ⊆ authorised`, enumerated from the registrar. The assertion is made over the mechanisms
    that will fill the nav: a nav item must name a capability the identity holds, and the route that
    capability gates must answer for that identity."""
    permissions.declare_capability("probe.admin", ("Admin",))
    app, registrar = _build(config_dir)
    from services.webservice.registry import NavItem
    registrar.register_pages("probe", _probe_router(),
                             nav=[NavItem("Probe", "/probe/admin", "probe.admin", "Operations", "grid")],
                             capabilities={"/probe/admin": "probe.admin",
                                           "/probe/whoami": permissions.PUBLIC})
    async with asgi_client(app) as client:
        await _login(client)
        for item in registrar.nav:
            roles = permissions.roles_for_capability(item.capability)
            assert roles, "a nav item must be gated by a capability that names roles"
            assert (await client.get(item.url)).status_code == 200, \
                f"the nav offers '{item.label}' ({item.url}) but the route does not answer"
        assert (await client.get("/probe/whoami")).json()["roles"] == ["Admin"]


@pytest.mark.asyncio
async def test_a_nav_item_the_identity_cannot_reach_is_reported_as_such(config_dir):
    """The other direction: a capability the signed-in identity does NOT hold answers 403, so the
    "offered ⊆ authorised" test above is not vacuous."""
    permissions.declare_capability("probe.other", ("DCS Admin",))
    app, registrar = _build(config_dir)
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/other": "probe.other"})
    async with asgi_client(app) as client:
        await _login(client)
        assert (await client.get("/probe/other")).status_code == 403


# ------------------------------------------------------------- acceptance 5: the CSRF pin

def _csrf_guarded(route) -> bool:
    dependant = getattr(route, "dependant", None)
    calls = [getattr(dep, "call", None) for dep in (getattr(dependant, "dependencies", None) or [])]
 # a route may spell the check as ``csrf_protect`` OR as ``csrf_protect_with(...)`` — the SAME
 # predicate, only the form-parse bound differs; the latter ships a marker (``session.py``) so this
 # pin can SEE it. The pin's purpose is that no write route is unguarded, never that every route
 # writes the dependency the same way.
    return any(call is session.csrf_protect
               or getattr(call, "dcssb_csrf_dependency", False) for call in calls)


@pytest.mark.asyncio
async def test_every_write_route_of_the_shell_requires_a_csrf_token(config_dir):
    """Acceptance 5, pinned to EXACTLY the empty set: a new write route that forgets the check
    fails this test instead of shipping."""
    _app, registrar = _build(config_dir)
    writes = [route for route in registrar.routes
              if UNSAFE_METHODS & set(getattr(route, "methods", None) or ())]
    assert writes, "floor: the shell must own at least the login and logout writes"
    unchecked = {route.path for route in writes if not _csrf_guarded(route)}
    assert unchecked == set(), f"write routes without a CSRF check: {sorted(unchecked)}"


@pytest.mark.asyncio
async def test_the_csrf_pin_reports_an_unchecked_write_route(config_dir):
    """The positive control: the pin must be able to fail. A write route registered without the
    dependency is reported -- otherwise the empty set above proves nothing."""
    _app, registrar = _build(config_dir)
    registrar.register_pages("unguarded", _unguarded_write_router(),
                             capabilities={"/probe/write": permissions.PUBLIC})
    writes = [route for route in registrar.routes
              if UNSAFE_METHODS & set(getattr(route, "methods", None) or ())]
    unchecked = {route.path for route in writes if not _csrf_guarded(route)}
    assert "/probe/write" in unchecked


# ------------------------------------------------------------------ acceptance 6: logout

@pytest.mark.asyncio
async def test_logout_deletes_the_cookie_the_login_set_and_the_next_request_is_anonymous(config_dir):
    permissions.declare_capability("probe.admin", ("Admin",))
    app, registrar = _build(config_dir)
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/admin": "probe.admin",
                                           "/probe/whoami": permissions.PUBLIC})
    async with asgi_client(app) as client:
        login = await _login(client)
        assert client.cookies.get(COOKIE_NAME), "the login must set the configured cookie"
        assert (await client.get("/probe/admin")).status_code == 200

        token = await _csrf_token(client)
        response = await client.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token})

        assert response.status_code == 303
        assert response.headers["location"] == "/", "logout lands on the site root"
        assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}="), \
            "logout must delete the cookie the login set"
        assert "Max-Age=0" in response.headers["set-cookie"] or "1970" in response.headers["set-cookie"]
        assert client.cookies.get(COOKIE_NAME) in (None, ""), "the client must drop the cookie"
        assert (await client.get("/probe/admin")).status_code == 403
        assert (await client.get("/probe/whoami")).json()["ref"] is None


@pytest.mark.asyncio
async def test_logout_without_a_csrf_token_is_refused(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        await _login(client)
        response = await client.post(auth_routes.LOGOUT_PATH)
    assert response.status_code == 403
    assert "CSRF" in response.json()["detail"]


@pytest.mark.asyncio
async def test_logout_uses_the_configured_cookie_name_not_the_default(config_dir):
    app, _registrar = _build(config_dir, _config(cookie_name="custom_cookie"))
    async with asgi_client(app) as client:
        await _login(client)
        token = await _csrf_token(client)
        response = await client.post(auth_routes.LOGOUT_PATH, data={"_csrf_token": token})
    assert response.headers["set-cookie"].startswith("custom_cookie=")
    assert session.SESSION_COOKIE != "custom_cookie"


# -------------------------------------------------------------------- the redirect target

@pytest.mark.asyncio
async def test_a_next_target_is_honoured_and_an_open_redirect_is_refused(config_dir):
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        good = await client.post(auth_routes.LOGIN_PATH,
                                 data={"username": "specialk", "password": PASSWORD,
                                       "_csrf_token": token, "next": "/probe/admin"})
        assert good.headers["location"] == "/probe/admin"

        for bad in ("//evil.example.com", "https://evil.example.com", "", "evil"):
 # a FRESH token per attempt: a successful login starts a new session, so the token the
 # previous login's form minted is gone with it (that is the fixation fix, not a flaw)
            token = await _csrf_token(client)
            response = await client.post(auth_routes.LOGIN_PATH,
                                         data={"username": "specialk", "password": PASSWORD,
                                               "_csrf_token": token, "next": bad})
            assert response.headers["location"] == "/", f"{bad!r} must not redirect off-site"


# ---------------------------------------------------- the redirect target carries no control chars
#
# The value is attacker-controlled and ends up in a ``Location`` header (and in the login form's
# hidden field). Control characters belong to no path: CR/LF are the two that turn a value into
# something a header cannot hold, so ``_safe_next`` removes them -- the rule the view layer already
# applies to every URL-shaped input (``readmodels.views._clean``). This is a REFUSAL of hygiene,
# not an escape: what survives is a plain local path or the site root.

@pytest.mark.parametrize("value,expected", [
    ("/probe/admin\r\nX-Injected: 1", "/probe/adminX-Injected: 1"),
    ("/probe/admin\n", "/probe/admin"),
    ("\r/probe/admin", "/probe/admin"),
    ("/a\x00b", "/ab"),
    ("/a\x7fb", "/ab"),
    ("/a\u0085b", "/ab"),
    ("\r\n", "/"),
    ("//evil.example.com\r\n", "/"),
])
def test_safe_next_strips_control_characters(value, expected):
    """No control character survives, and the value is re-validated AFTER the strip (so a CR/LF
    cannot be used to smuggle a protocol-relative target past the ``//`` check)."""
    assert auth_routes._safe_next(value) == expected


@pytest.mark.asyncio
async def test_a_next_target_with_control_characters_never_reaches_the_location_header(config_dir):
    """The end-to-end half: a login carrying ``next`` with CR/LF redirects to the STRIPPED path.

    Starlette percent-encodes a ``Location`` before it becomes a header (verified on the pinned
    version: ``/probe/admin%0D%0AX-Injected:%201``), so the unit test above is the one that catches
    a regression in ``_safe_next`` itself -- this one pins that the route uses it at all.
    """
    app, _ = _build(config_dir)
    async with asgi_client(app) as client:
        token = await _csrf_token(client)
        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token,
                                           "next": "/probe/admin\r\nX-Injected: 1"})
        assert response.status_code == 303
        location = response.headers["location"]
 # the CR/LF is GONE, and the path it was smuggled into is the stripped one
        assert location.startswith("/probe/adminX-Injected:")
        for forbidden in ("\r", "\n", "%0D", "%0A"):
            assert forbidden not in location, location


# ------------------------------------------------- session fixation: the login starts fresh

@pytest.mark.asyncio
async def test_a_local_login_starts_a_fresh_session(config_dir):
    """SESSION FIXATION. Whatever the visitor's browser carried BEFORE the login must not survive into the
    authenticated session: on a shared machine (or after a cookie handoff) the fixed value would be
    authenticated after the victim signs in.

    The concrete pre-login entries here are the CSRF token the login form itself mints, plus
    ``pre_login_marker`` standing in for any other pre-login content.
    """
    app, registrar = _build(config_dir)
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/session": permissions.PUBLIC,
                                           "/probe/seed": permissions.PUBLIC})
    async with asgi_client(app) as client:
 # the attacker-controlled starting point: a session that already holds something
        assert (await client.get("/probe/seed")).status_code == 200
        token = await _csrf_token(client)
        before = (await client.get("/probe/session")).json()["session"]
        assert "pre_login_marker" in before, before
        assert "_csrf_tokens" in before, before

        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "specialk", "password": PASSWORD,
                                           "_csrf_token": token, "next": "/probe/admin"})
        assert response.status_code == 303
        assert response.headers["location"] == "/probe/admin", "the landing still works"
        after = (await client.get("/probe/session")).json()["session"]

    assert after == {auth.SESSION_IDENTITY_KEY: {"backend": "local", "subject": "specialk"}}, \
        f"pre-login session content survived the login: {after}"


@pytest.mark.asyncio
async def test_a_break_glass_login_starts_a_fresh_session_too(config_dir):
    """The break-glass credential signs in through the SAME ``/auth/login`` route (there is no
    second login path), so it must be fixed by the same clear -- asserted separately, with the
    local backend disabled, so a future split of the two cannot regress this silently."""
    config = _config(users=None)
    config["auth"]["local"] = {"enabled": False, "users": []}
    config["auth"]["breakglass"] = {"enabled": True, "username": "emergency",
                                    "password_hash": _hash("letmein")}
    app, registrar = _build(config_dir, config)
    assert app.state.webui_auth.backend("local") is None
    assert app.state.webui_auth.backend("breakglass") is not None
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/session": permissions.PUBLIC,
                                           "/probe/seed": permissions.PUBLIC})
    async with asgi_client(app) as client:
        assert (await client.get("/probe/seed")).status_code == 200
        token = await _csrf_token(client)
        before = (await client.get("/probe/session")).json()["session"]
        assert "pre_login_marker" in before and "_csrf_tokens" in before, before

        response = await client.post(auth_routes.LOGIN_PATH,
                                     data={"username": "emergency", "password": "letmein",
                                           "_csrf_token": token})
        assert response.status_code == 303
        after = (await client.get("/probe/session")).json()["session"]

    assert after == {auth.SESSION_IDENTITY_KEY: {"backend": "breakglass",
                                                 "subject": "emergency"}}, \
        f"pre-login session content survived the break-glass login: {after}"


# ------------------------------------------------------------------------- helpers

def _probe_router() -> APIRouter:
    router = APIRouter()

    @router.get("/probe/session")
    async def session_dump(request: Request):
        """The whole session, so a test can assert what the authenticated session carries."""
        return {"session": dict(request.session)}

    @router.get("/probe/seed")
    async def seed(request: Request):
        """Plant something in the session the way a pre-login visit would."""
        request.session.setdefault("pre_login_marker", "planted-before-login")
        return {"seeded": True}

    @router.get("/probe/admin")
    async def admin():
        return {"ok": "admin"}

    @router.get("/probe/dcs")
    async def dcs():
        return {"ok": "dcs"}

    @router.get("/probe/other")
    async def other():
        return {"ok": "other"}

    @router.get("/probe/open")
    async def open_():
        return {"ok": "open"}

    @router.get("/probe/whoami")
    async def whoami(request: Request):
        return {"roles": sorted(permissions.role_names_for(request)),
                "ref": auth.session_identity_ref(request)}

    return router


# ------------------------------------------------------------ one line per sign-in

@pytest.mark.asyncio
async def test_a_local_sign_in_is_logged_exactly_once(config_dir, caplog):
    """A sign-in is ONE line. The password backend that verified the credentials and the route that
    created the session both know the same fact, so only the route records it -- a backend success
    line would duplicate it word for word."""
    app, _ = _build(config_dir)
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            assert (await _login(client)).status_code == 303
    signed_in = [record.getMessage() for record in caplog.records
                 if "signed in" in record.getMessage()]
    assert len(signed_in) == 1, signed_in
    assert signed_in[0] == "auth: 'specialk' signed in via the local backend"


@pytest.mark.asyncio
async def test_a_break_glass_sign_in_is_one_line_plus_its_own_notice(config_dir, caplog):
    """The break-glass door logs through the same route line, so its own record must be the
    NOTICE that the credential is in use -- not a second statement that somebody signed in."""
    config = _config()
    config["auth"]["local"] = {"enabled": False, "users": []}
    config["auth"]["breakglass"] = {"enabled": True, "username": "emergency",
                                    "password_hash": _hash("letmein")}
    app, _ = _build(config_dir, config)
    with caplog.at_level(logging.INFO):
        async with asgi_client(app) as client:
            token = await _csrf_token(client)
            response = await client.post(auth_routes.LOGIN_PATH,
                                         data={"username": "emergency", "password": "letmein",
                                               "_csrf_token": token})
            assert response.status_code == 303
    signed_in = [record.getMessage() for record in caplog.records
                 if "signed in" in record.getMessage()]
    assert signed_in == ["auth: 'emergency' signed in via the breakglass backend"], signed_in
    notices = [record for record in caplog.records
               if "BREAK-GLASS credential was used" in record.getMessage()]
    assert len(notices) == 1 and notices[0].levelno == logging.WARNING, \
        [(r.levelname, r.getMessage()) for r in caplog.records]


def _unguarded_write_router() -> APIRouter:
    router = APIRouter()

    @router.post("/probe/write")
    async def write():
        return {"ok": True}

    return router
