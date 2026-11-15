"""The sign-out control in the shell's user chip (writes to ``/auth/logout``).

``services/webservice/auth/routes.py`` already implements the POST logout, but nothing in the UI ever
posted to it, so a signed-in user had no way out. The control is a FORM (never a link): the route only
accepts POST and a logout link is a CSRF hazard, so a GET on the path must never sign anybody out.
Pins: the dashboard renders exactly ONE form (``method="post"``, the module's ``LOGOUT_PATH``, a
hidden CSRF field whose value IS the session's token); a long display name does not push the control
out of the chip; the login page and a shell rendered with ``user=None`` render no control; submitting
the rendered form clears the session, deletes the cookie the login set and leaves the next request
anonymous; and a GET on the path signs nobody out. No request touches Discord, a database or the
network."""
from __future__ import annotations

import re
import types

import pytest
from fastapi import APIRouter, Request
from jinja2 import UndefinedError

from conftest import asgi_client, create_app, install_shell, permissions, session
from services.webservice import templating
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

PASSWORD = "correct horse battery staple"
SESSION_SECRET = "s" * 40
COOKIE_NAME = "dcssb_test_session"
BOT_YAML = "roles:\n  Admin:\n    - specialk\n"

#: the chip's own class, used to slice the control's container out of the page
WHO_CLASS = 'class="who"'
#: one whole sign-out form (its class is the contract the chip and the test share)
FORMS = re.compile(r'<form[^>]*class="signout"[^>]*>.*?</form>', re.S)
#: the hidden CSRF field, by the name the session module owns
HIDDEN = re.compile(r'<input type="hidden" name="([^"]*)" value="([^"]*)">')
ACTION = re.compile(r'action="([^"]*)"')


# --------------------------------------------------------------------------- fixtures / helpers

@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=str(config_dir))


def _hash(password: str = PASSWORD) -> str:
    return local_auth.hash_password(password, iterations=local_auth.MIN_ITERATIONS)


def _config() -> dict:
    return {
        "session": {"secret": SESSION_SECRET, "cookie_name": COOKIE_NAME, "max_age": 600},
        "auth": {"public_base_url": "http://localhost:9876",
                 "local": {"enabled": True, "users": [
                     {"username": "specialk", "password_hash": _hash(), "roles": ["Admin"]}]}},
    }


def _build(config_dir):
    app = create_app()
    registrar = install_shell(app, node=_node(config_dir), config=_config())
    return app, registrar


def _probe_router() -> APIRouter:
    """The two probes: a session dump (the token's source of truth) and a gated page."""
    router = APIRouter()

    @router.get("/probe/session")
    async def session_dump(request: Request):
        return {"session": dict(request.session)}

    @router.get("/probe/admin")
    async def admin():
        return {"ok": "admin"}

    return router


def _register_probes(registrar) -> None:
    permissions.declare_capability("probe.admin", ("Admin",))
    registrar.register_pages("probe", _probe_router(),
                             capabilities={"/probe/session": permissions.PUBLIC,
                                           "/probe/admin": "probe.admin"})


async def _login(client) -> None:
    page = await client.get(auth_routes.LOGIN_PATH)
    token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
    response = await client.post(auth_routes.LOGIN_PATH,
                                 data={"username": "specialk", "password": PASSWORD,
                                       "_csrf_token": token})
    assert response.status_code == 303


def _forms(html: str) -> list[str]:
    return FORMS.findall(html)


def _chip(html: str) -> str:
    """The user chip, sliced out of the page (its nested divs make a whole-page regex fragile).

    The chip moved from the sidebar footer into the TOP BAR, so the slice ends at the top bar's
    closing tag — the container changed, the chip (name, avatar, Sign out form) did not.
    """
    start = html.index(WHO_CLASS)
    return html[start:html.index("</header>", start)]


# ------------------------------------------------------------------- the control on the dashboard

@pytest.mark.asyncio
async def test_the_dashboard_renders_exactly_one_sign_out_form_with_the_path_and_the_session_token(
        config_dir):
    """Acceptance 1: exactly one form, POST, the module's path, and the SESSION's own token.

    The token is compared against the session the browser actually carries (through the probe that
    dumps it) -- a merely non-empty value would pass while an empty one shipped to every user.
    """
    app, registrar = _build(config_dir)
    _register_probes(registrar)
    async with asgi_client(app) as client:
        await _login(client)
        page = await client.get("/")
        assert page.status_code == 200
        session_state = (await client.get("/probe/session")).json()["session"]

    forms = _forms(page.text)
    assert len(forms) == 1, f"exactly one sign-out form, found {len(forms)}: {forms}"
    form = forms[0]
    assert 'method="post"' in form, form
    assert ACTION.search(form).group(1) == auth_routes.LOGOUT_PATH, form
    match = HIDDEN.search(form)
    assert match, f"the form must carry a hidden CSRF field: {form}"
    field, token = match.groups()
    assert field == session.CSRF_FIELD, field
    assert token == session_state["_csrf_tokens"][-1], \
        "the rendered token must be the one the session holds"
    assert form in _chip(page.text), "the control belongs in the user chip"


@pytest.mark.asyncio
async def test_a_long_display_name_does_not_push_the_control_out_of_the_chip(config_dir):
    """Acceptance 5: the chip tolerates a long name without overflowing or losing the control.

    Two halves, because neither alone is evidence: the RENDER keeps the name and the control in the
    same chip (the name column is the shrinkable one), and the STYLESHEET gives that column
    ``min-width: 0`` with the control pinned at ``flex: 0 0 auto`` -- so the name ellipsizes instead
    of shoving the button out of the sidebar. Naming the rule means deleting it fails here.
    """
    long_name = "wolfeschlegelsteinhausenbergerdorff"
    config = _config()
    config["auth"]["local"]["users"] = [
        {"username": long_name, "password_hash": _hash(), "roles": ["Admin"]}]
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    async with asgi_client(app) as client:
        page = await client.get(auth_routes.LOGIN_PATH)
        token = re.search(r'name="_csrf_token" value="([^"]*)"', page.text).group(1)
        assert (await client.post(auth_routes.LOGIN_PATH,
                                  data={"username": long_name, "password": PASSWORD,
                                        "_csrf_token": token})).status_code == 303
        html = (await client.get("/")).text

    chip = _chip(html)
    assert f"<b>{long_name}</b>" in chip, chip
    assert 'class="who-name"' in chip, "the name column is the shrinkable one"
    assert _forms(chip), "the control is still rendered beside the long name"

    from pathlib import Path
    sheet = Path(__file__).resolve().parent.parent / "services" / "webservice" / "static" / "shell.css"
    css = sheet.read_text(encoding="utf-8")
    assert re.search(r"\.signout\s*\{[^}]*flex:\s*0 0 auto", css, re.S), css
    assert re.search(r"\.who \.who-name\s*\{[^}]*min-width:\s*0", css, re.S), css
    assert re.search(r"\.signout button:focus-visible\s*\{[^}]*outline", css, re.S), \
        "the control's focus ring is visible (acceptance 6)"


# ---------------------------------------------------------------- the control is only for the signed-in

@pytest.mark.asyncio
async def test_the_login_page_renders_no_sign_out_control(config_dir):
    """Acceptance 3, the login page: nobody is signed in at the door."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        page = await client.get(auth_routes.LOGIN_PATH)
    assert page.status_code == 200
    assert "signout" not in page.text, "the login page must render no sign-out control"
    assert f'action="{auth_routes.LOGOUT_PATH}"' not in page.text


def test_the_shell_renders_no_sign_out_control_without_an_identity():
    """Acceptance 3, the other direction: no resolvable identity -> no control.

    The standalone render (no ``user`` at all) is what the login page's extends does, and
    ``user=None`` is what the dashboard passes for an unresolvable identity. Neither may render a
    control -- and neither may raise, which is what the shell's optional names are for.
    """
    registrar = install_shell(create_app(), node=None, config={})
    assert "signout" not in templating.render(registrar, "base.html")
    assert "signout" not in templating.render(registrar, "base.html", user=None)


def test_a_user_card_without_the_sign_out_data_fails_loudly():
    """StrictUndefined, applied to the control: a page that supplies a ``user`` card but not the
    sign-out data must raise, not render a control with an empty action or a blank token.

    This is the reason the template reads ``user.sign_out`` unguarded: silently rendering nothing
    would ship a chip without the one way out, and the missing name has an owner
    (``pages/dashboard.sign_out_context``) whose absence should be a failure, not a detail.
    """
    registrar = install_shell(create_app(), node=None, config={})
    card = {"name": "Special K", "initials": "SK", "avatar": "", "subtitle": "local · Admin"}
    with pytest.raises(UndefinedError):
        templating.render(registrar, "base.html", user=card)


# ------------------------------------------------------------------- the control and the route agree

@pytest.mark.asyncio
async def test_submitting_the_rendered_form_signs_the_user_out_and_deletes_the_cookie(config_dir):
    """Acceptance 3 end to end: the PAGE's token is accepted, the session is cleared, the cookie the
    login set is deleted, and the follow-up request is anonymous."""
    app, registrar = _build(config_dir)
    _register_probes(registrar)
    async with asgi_client(app) as client:
        await _login(client)
        assert client.cookies.get(COOKIE_NAME), "the login must set the configured cookie"
        assert (await client.get("/probe/admin")).status_code == 200

        page = await client.get("/")
        form = _forms(page.text)[0]
        action = ACTION.search(form).group(1)
        field, token = HIDDEN.search(form).groups()

        response = await client.post(action, data={field: token})

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}="), \
            "logout must delete the cookie the login set"
        assert "Max-Age=0" in response.headers["set-cookie"] or \
            "1970" in response.headers["set-cookie"]
        assert client.cookies.get(COOKIE_NAME) in (None, "")
        assert (await client.get("/probe/admin")).status_code == 403
        assert (await client.get("/probe/session")).json()["session"].get("webui_identity") is None


@pytest.mark.asyncio
async def test_a_get_on_the_logout_path_does_not_sign_anyone_out(config_dir):
    """Acceptance 4: the route accepts POST only, and the shell links to it from nowhere.

    Pinned so nobody later "fixes" the control into an anchor: a GET that logged the user out would
    be a CSRF-free logout any page could trigger with one image tag.
    """
    app, registrar = _build(config_dir)
    _register_probes(registrar)
    async with asgi_client(app) as client:
        await _login(client)
        page = await client.get("/")
        assert f'href="{auth_routes.LOGOUT_PATH}"' not in page.text, \
            "the sign-out control must be a form, never a link"

        response = await client.get(auth_routes.LOGOUT_PATH)
        assert response.status_code == 405, "a GET on the logout path is refused"
        assert (await client.get("/probe/admin")).status_code == 200, \
            "the GET must not have signed the user out"


@pytest.mark.asyncio
async def test_the_sign_out_control_needs_no_javascript(config_dir):
    """Acceptance 4: a plain form submit. The control is in the server-rendered HTML and carries no
    script hook, so with JavaScript off the user can still sign out."""
    app, _registrar = _build(config_dir)
    async with asgi_client(app) as client:
        await _login(client)
        page = await client.get("/")
    form = _forms(page.text)[0]
    assert "<button" in form and "type=\"submit\"" in form, form
    assert "onclick" not in form and "onsubmit" not in form and "<script" not in form, form
