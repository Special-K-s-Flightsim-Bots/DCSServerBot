"""``auth.login_page``: the switch between the animated sign-in page and the plain one.

The two variants are ONE page with two shells: the same doors, form, CSRF token, theme and language
controls, rendered either over the DCS scene (``fancy``, the default) or on a plain background
(``simple``). The switch is a config key, so the installation-level choice is made in
``config/services/webservice.yaml`` and its Settings page. The doors themselves are shared through
``_login_doors.html``; a page that renders the wrong doors would be a second, divergent login page.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from conftest import asgi_client, create_app, install_shell, password_fields
from services.webservice.auth import local as local_auth
from services.webservice.auth import routes as auth_routes

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "services" / "webservice" / "schemas" / "webservice_schema.yaml"
SAMPLE_PATH = REPO_ROOT / "samples" / "services" / "webservice.yaml"

SESSION_SECRET = "s" * 40
PUBLIC_BASE_URL = "http://localhost:9876"
BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: the scene's markers: the canvas element and the script that animates it. A plain page must ship
#: neither, and the default page must ship both.
SCENE_MARKERS = ("dcs-tactical-canvas", "login-stars.js")
#: the plain page's own marker
SIMPLE_MARKER = "login-simple"


def _node(config_dir):
    return types.SimpleNamespace(master=True, config_dir=config_dir)


def _config(config_dir, *, login_page=None):
    """A webservice.yaml block with a real local door, plus the optional page switch."""
    auth = {
        "public_base_url": PUBLIC_BASE_URL,
        "local": {
            "enabled": True,
            "users": [{"username": "specialk",
                       "password_hash": local_auth.hash_password(
                           "correct horse battery staple",
                           iterations=local_auth.MIN_ITERATIONS),
                       "roles": ["Admin"]}],
        },
        "discord": {"enabled": False},
    }
    if login_page is not None:
        auth["login_page"] = login_page
    return {"session": {"secret": SESSION_SECRET}, "auth": auth}


def _build(config_dir, config):
    app = create_app()
    install_shell(app, node=_node(config_dir), config=config)
    return app


async def _page(app) -> str:
    async with asgi_client(app) as client:
        response = await client.get(auth_routes.LOGIN_PATH)
    assert response.status_code == 200, response.text
    return response.text


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------------- the default page


@pytest.mark.asyncio
async def test_an_unset_key_renders_the_fancy_page(config_dir):
    """Nothing configured means the shipped page: the switch must not change an existing install."""
    html = await _page(_build(config_dir, _config(config_dir)))
    for marker in SCENE_MARKERS:
        assert marker in html, f"the default page lost its scene marker {marker!r}"
    assert SIMPLE_MARKER not in html


# -------------------------------------------------------------------------- the plain page


@pytest.mark.asyncio
async def test_simple_renders_the_plain_shell_without_the_scene(config_dir):
    html = await _page(_build(config_dir, _config(config_dir, login_page="simple")))
    assert SIMPLE_MARKER in html
    for marker in SCENE_MARKERS:
        assert marker not in html, f"the plain page still ships the scene marker {marker!r}"
    # the plain page is the SAME page: the door and its form are still there
    assert password_fields(html), "the plain page rendered no password field"
    assert f'action="{auth_routes.LOGIN_PATH}"' in html
    assert "Local account" in html


@pytest.mark.asyncio
async def test_the_plain_page_keeps_the_theme_and_language_controls(config_dir):
    """The chrome is shared, not re-implemented: the bar and its controls come from the partial."""
    html = await _page(_build(config_dir, _config(config_dir, login_page="simple")))
    assert "shell-bar" in html
    assert "data-theme" in html


# ------------------------------------------------------------------------------ the fallback


def test_login_page_style_reads_the_config_and_falls_back_to_fancy(config_dir):
    app = _build(config_dir, _config(config_dir, login_page="simple"))
    assert auth_routes.login_page_style(app.state) == "simple"

    app = _build(config_dir, _config(config_dir))
    assert auth_routes.login_page_style(app.state) == "fancy"

    # the schema refuses these at startup; the renderer must still land on a page, never a 500
    for value in ("", "  ", "plain", "FANCY!"):
        app = _build(config_dir, _config(config_dir, login_page=value))
        assert auth_routes.login_page_style(app.state) == "fancy", value


# -------------------------------------------------------------------- the schema and the sample


def _validate(text: str, tmp_path):
    """Validate *text* against the service schema, with the unrelated ``unique_port`` stubbed."""
    from pykwalify.core import Core
    extension = tmp_path / "ext.py"
    extension.write_text("def unique_port(value, rule_obj, path):\n    return True\n",
                         encoding="utf-8")
    source = tmp_path / "webservice.yaml"
    source.write_text(text, encoding="utf-8")
    Core(source_file=str(source), schema_files=[str(SCHEMA_PATH)],
         extensions=[str(extension)]).validate()


@pytest.mark.parametrize("value", ["fancy", "simple"])
def test_the_schema_accepts_both_page_styles(value, tmp_path):
    _validate(f"DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n"
              f"  auth:\n    login_page: {value}\n", tmp_path)


def test_the_schema_refuses_an_unknown_page_style(tmp_path):
    from pykwalify.errors import SchemaError
    with pytest.raises(SchemaError):
        _validate("DEFAULT:\n  listen: 127.0.0.1\n  port: 9876\n"
                  "  auth:\n    login_page: plain\n", tmp_path)


def test_the_sample_documents_the_switch(tmp_path):
    _validate(SAMPLE_PATH.read_text(encoding="utf-8"), tmp_path)
    assert "login_page: fancy" in SAMPLE_PATH.read_text(encoding="utf-8")
