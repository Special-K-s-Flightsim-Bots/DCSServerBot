"""THE SETTINGS LANDING PAGE — the tile grid (plugins, services).

Slice 1 of the approved Settings redesign: ``/settings`` stops being a list of YAML filenames and
becomes a grid of TILES, one per plugin, one per service. Each tile carries the item's name, its own
one-line description, an on/off control, and a cogwheel into its configuration. Pinned here, through
the REAL application:

* the two kinds render DISTINCTLY (two rails/words/scopes, never colour alone);
* EXTENSIONS and the ``nodes.yaml`` tile have LEFT SETTINGS (this card): extension and node config are
  reached from the NODES page (``/nodes/<node>``), so the landing offers neither;
* a service held off by its PLUGIN dependency SAYS SO, and turning it off moves NOTHING;
* a plugin's on/off posts the SAME ``f.opt_plugins`` list through the SAME save route;
* a service's on/off MOVES its config file aside and back (the file IS the service);
* the main config form now lives at ``/settings/main/main`` — ``/settings`` is the grid.
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

MAIN_YAML = """\
guild_id: 112233445566
guild_name: My Server
language: en
plugins:
  - mission
opt_plugins:
  - motd
"""

NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    SRS:
      installation: /opt/srs
  instances:
    Host-A-Instance:
      home: /home/dcs-a
      extensions:
        MizEdit: {}
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  extensions:
    SRS:
      installation: /opt/srs
"""


@pytest.fixture
def landing_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    # music.yaml EXISTS, but the music plugin is NOT in opt_plugins — the held-off case.
    (tmp_path / "services" / "music.yaml").write_text("DEFAULT: {}\n", encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    return tmp_path


def _app(landing_dir, roles=("Admin",)):
    return _build(landing_dir, users=[{"username": "staff", "password_hash": _hash(),
                                       "roles": list(roles)}])


async def _landing(client) -> str:
    response = await client.get(settings_page.SETTINGS_PATH)
    assert response.status_code == 200, response.text[:400]
    return response.text


def _csrf(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


# ------------------------------------------------------- 1. the grid renders, and two kinds differ

@pytest.mark.asyncio
async def test_the_landing_is_a_tile_grid_not_a_file_list(landing_dir):
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
    assert 'class="st-grid"' in page, "the landing is not a tile grid"
    assert 'class="st-tile plugin' in page
    assert 'class="st-tile service' in page
    # EXTENSIONS LEFT SETTINGS: no extension tile is built any more, and the
    # ``nodes.yaml`` "This install" row is gone — both are reached from the NODES page now.
    assert 'class="st-tile ext' not in page, "an EXTENSIONS tile is still on the Settings landing"
    assert "nodes.yaml" not in page, "the nodes.yaml tile is still on the Settings landing"


@pytest.mark.asyncio
async def test_the_two_kinds_are_distinguishable_at_a_glance(landing_dir):
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
    # the kind WORD and the SCOPE line, per kind (never colour alone)
    assert ">Plugin<" in page and ">Service<" in page
    assert settings_page.SERVICE_RUNS_ON in page            # a service states where it runs
    # the plugin rail legend carries the opt_plugins fact
    assert "opt_plugins" in page
    # the ex-EXTENSIONS rail's words are gone with it
    assert ">Extension<" not in page
    assert "Extensions" not in page


@pytest.mark.asyncio
async def test_extensions_are_no_longer_configurable_from_settings(landing_dir):
    """The EXTENSIONS rail AND the per-extension cogwheel are GONE.

    lives inside ``nodes.yaml`` and is edited from the NODE page (``/nodes/<node>``, one scope per
    tab) — pinned by ``test_webui_node_page``. So Settings must offer no extension tile, no
    ``/settings/extension/<name>`` link, and no scope line.
    """
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
    assert 'class="st-tile ext' not in page
    assert "/settings/extension/" not in page, "a link to the removed extension view was rendered"
    assert "config scope" not in page, "the extension scope line is still rendered"


# ----------------------------------------------------------- 2. a service held off by its plugin

@pytest.mark.asyncio
async def test_a_service_held_off_by_its_plugin_says_so_and_moves_nothing(landing_dir):
    path = landing_dir / "services" / "music.yaml"
    before = path.read_bytes()
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        # the Music tile is present and SAYS the plugin holds it off
        assert settings_page.SERVICE_HELD_BY_PLUGIN.split("%")[0].strip() in page
        assert "music" in page
        token = _csrf(page)
        response = await client.post(
            settings_page.SETTINGS_SERVICE_TOGGLE_PATH.format(name="Music"),
            data={"_csrf_token": token, "action": "disable"})
        landed = await _landing(client)
    assert response.status_code == 303
    assert path.read_bytes() == before, "a plugin-held service's file was moved"
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert banner and html.unescape(banner.group(1)) == settings_page.SERVICE_TOGGLE_HELD


# ------------------------------------------------------------------ 3. the plugin on/off write

@pytest.mark.asyncio
async def test_a_plugin_toggle_posts_the_existing_opt_plugins_save_path(landing_dir):
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        # every plugin tile's switch posts to the EXISTING main.yaml save route (no new route)
        assert f'action="{settings_page.SETTINGS_SAVE_PATH}"' in page
        token = _csrf(page)
        revision = re.search(r'name="_revision" value="([^"]*)"', page).group(1)
        # enable greenieboard on top of the already-listed motd (the tile posts the full list)
        response = await client.post(settings_page.SETTINGS_SAVE_PATH,
                                     data={"f.opt_plugins": ["", "motd", "greenieboard"],
                                           "_csrf_token": token, "_revision": revision, "_expert": "0"})
    assert response.status_code == 303
    from core.utils.configwrite import load_config_file
    assert list(load_config_file(landing_dir / "main.yaml").get("opt_plugins")) == ["motd", "greenieboard"]


# ---------------------------------------------------------------- 4. the service on/off (file move)

@pytest.mark.asyncio
async def test_turning_a_configured_service_off_moves_its_file_aside(landing_dir):
    # make Bot configured (bot.yaml exists) and NOT plugin-bound → the switch can fire
    path = landing_dir / "services" / "bot.yaml"
    assert path.is_file()
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        token = _csrf(page)
        response = await client.post(
            settings_page.SETTINGS_SERVICE_TOGGLE_PATH.format(name="Bot"),
            data={"_csrf_token": token, "action": "disable"})
        landed = await _landing(client)
    assert response.status_code == 303
    assert not path.is_file(), "the service file was not moved aside"
    assert (landing_dir / "services" / "bot.yaml.disabled").is_file()
    # and turning it back on moves it home
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        token = _csrf(page)
        await client.post(settings_page.SETTINGS_SERVICE_TOGGLE_PATH.format(name="Bot"),
                          data={"_csrf_token": token, "action": "enable"})
    assert path.is_file() and not (landing_dir / "services" / "bot.yaml.disabled").is_file()


# ----------------------------------------------------------------------- 5. the main form moved

@pytest.mark.asyncio
async def test_the_main_config_form_lives_at_its_own_url(landing_dir):
    app, _ = _app(landing_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(settings_page.SETTINGS_PATH)).status_code == 200
        response = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200, response.text[:300]
    page = response.text
    assert 'name="f.guild_id"' in page
    assert "The file, as YAML" in page


def test_the_main_path_constant_is_the_generic_file_url():
    assert settings_page.SETTINGS_MAIN_PATH == "/settings/main/main"


# ----------------------------------------------------------------------- 6. the capability gate

@pytest.mark.asyncio
async def test_a_caller_without_the_capability_cannot_open_the_landing_or_toggle(landing_dir):
    app, _ = _app(landing_dir, roles=("DCS Admin",))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        assert (await client.get(settings_page.SETTINGS_PATH)).status_code == 403
        response = await client.post(
            settings_page.SETTINGS_SERVICE_TOGGLE_PATH.format(name="Bot"),
            data={"action": "disable"})
    assert response.status_code == 403
