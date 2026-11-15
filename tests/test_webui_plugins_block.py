"""The PLUGIN BLOCK on the (old) Settings page — ``main.yaml``'s ``opt_plugins`` — and its retirement.

The item 4: ``opt_plugins`` was a bare list of strings, so nobody could tell what a name does or
which plugins are already default. The block used to render a DEDICATED set of rows beside the
schema-generated form: one row per plugin that EXISTS on disk (discovered, never hard-coded), each with
the plugin's OWN one-line description, a default/optional marker and a checkbox that posts the SAME
``opt_plugins`` list the config holds.

THE BLOCK MOVED. ``main.yaml``'s cogwheel now opens the SAME scoped editor every
other config file uses, and that editor OMITS ``plugins``/``opt_plugins`` entirely — those lists are
managed by the plugin TILES on the Settings landing (each tile's switch posts the same list through the
SAME save route). So the old dedicated block is no longer rendered anywhere.

Pinned here: the discovery reads the tree (not a list); a description comes from the plugin's own
files; the default marker comes from ``DEFAULT_PLUGINS``; the main.yaml editor renders NO plugin-list
control of any shape; a save ROUND-TRIPS (enable one, disable one, keep an unknown entry — never
silently dropped); and an UNTOUCHED save is byte-identical. Driven in-process through the real
application.
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice import plugin_registry
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login
from test_webui_settings_page import _csrf_and_revision, _open

#: A ``main.yaml`` whose ``opt_plugins`` carries a REAL optional plugin (``motd``) and a LEGACY entry
#: that maps to no shipped plugin (``serverstats`` — it was folded into ``monitoring`` years ago).
PLUGIN_MAIN = """\
# main.yaml — an operator's own file.
guild_id: 112233445566    # the Discord server
guild_name: My Server
autoupdate: true
use_dashboard: false
chat_command_prefix: .
language: en
validation: none
database:
  url: postgres://user:***@localhost:5432/db   # the pool
  pool_min: 5
  pool_max: 10
  max_reties: 1
  max_idle: 0
logging:
  loglevel: DEBUG           # the level
  logrotate_count: 5
  logrotate_size: 10485760
  utc: false
filter:
  server_name: ^Special K -
  mission_name: ^Operation
  tag: "'^[JDS]'"
plugins:
  - mission
opt_plugins:
  - motd
  - serverstats
"""

#: The same file WITHOUT the ``opt_plugins`` key — the block must not CREATE it on an untouched save.
NO_KEY_MAIN = PLUGIN_MAIN.replace("opt_plugins:\n  - motd\n  - serverstats\n", "")

#: The scalar half of the form — identical to the settings card's fixture for the fields the block
#: does not touch. ``opt_plugins`` is posted SEPARATELY (its rows are the block's checkboxes).
_BASE_FIELDS: list[tuple[str, str]] = [
    ("f.guild_id", "112233445566"), ("f.guild_name", "My Server"), ("f.autoupdate", "1"),
    ("f.use_dashboard", "0"), ("f.chat_command_prefix", "."),
    ("f.language", "en"), ("f.validation", "none"),
    ("f.database.url", "postgres://user:***@localhost:5432/db"),
    ("f.database.pool_min", "5"), ("f.database.pool_max", "10"), ("f.database.max_reties", "1"),
    ("f.database.max_idle", "0"),
    ("f.logging.loglevel", "DEBUG"), ("f.logging.logrotate_count", "5"),
    ("f.logging.logrotate_size", "10485760"), ("f.logging.utc", "0"),
    ("f.filter.server_name", "^Special K -"), ("f.filter.mission_name", "^Operation"),
    ("f.filter.tag", "'^[JDS]'"),
    ("f.plugins", "mission"),
]


def _form(enabled: list[str], *, sentinel: bool = True) -> dict:
    """The block as it posts: the hidden empty sentinel (only when the key exists) plus one
    ``f.opt_plugins`` value per CHECKED row. Order is the block's row order (file entries first)."""
    form: dict = dict(_BASE_FIELDS)
    values = ([""] if sentinel else []) + list(enabled)
    if values:
        form["f.opt_plugins"] = values
    return form


def _with_csrf(fields: dict, csrf: str, revision: str) -> dict:
    return {**fields, "_csrf_token": csrf, "_revision": revision}


def _opt_plugins(path) -> list[str]:
    """The ``opt_plugins`` list the file holds, read back through the bot's own round-trip loader."""
    from core.utils.configwrite import load_config_file
    return list((load_config_file(path) or {}).get("opt_plugins", []))


@pytest.fixture
def plugin_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(PLUGIN_MAIN, encoding="utf-8")
    return tmp_path


def _app(plugin_dir, text=None):
    if text is not None:
        (plugin_dir / "main.yaml").write_text(text, encoding="utf-8")
    return _build(plugin_dir, users=[{"username": "staff", "password_hash": _hash(),
                                      "roles": ["Admin"]}])


# ============================================================ 1. the discovery (no hard-coded list)

def test_discovery_reads_the_tree_not_a_list(tmp_path):
    """A plugin is a folder the bot can load (it carries ``commands.py``).

    Pinned on a tree this test OWNS: a checkout may carry extra folders beside the shipped plugins
    (a legacy install, a private plugin), so the rule cannot be read off the real ``plugins/``
    directory — only the shipped names it must keep finding can.
    """
    root = tmp_path / "plugins"
    for name in ("with_commands", "without_commands", "__pycache__"):
        (root / name).mkdir(parents=True)
    (root / "with_commands" / plugin_registry.COMMANDS_FILE).write_text("", encoding="utf-8")
    (root / "without_commands" / "__init__.py").write_text("", encoding="utf-8")

    assert [info.name for info in plugin_registry.discover_plugins(root)] == ["with_commands"]

    # a real default plugin and a real optional one are both found in the shipped tree
    names = [info.name for info in plugin_registry.discover_plugins()]
    assert "mission" in names and "motd" in names


def test_a_description_comes_from_the_plugins_own_files():
    """The one-line description is the plugin's OWN prose, never a table kept in the registry."""
    by_name = {info.name: info for info in plugin_registry.discover_plugins()}
    assert by_name["motd"].description.startswith("This plugin adds a message of the day")
    assert by_name["mission"].description.startswith("The mission plugin adds commands")
    # a plugin with NEITHER a README nor a docstring yields the empty string (the template then draws
    # no description line) — the block never invents one
    assert by_name["community"].description == ""


def test_the_default_marker_comes_from_the_bots_default_set():
    """``default`` is read from ``DEFAULT_PLUGINS`` — the bot's own knowledge, not a copy here."""
    by_name = {info.name: info for info in plugin_registry.discover_plugins()}
    assert "mission" in plugin_registry.default_plugins()
    assert by_name["mission"].default is True          # a default plugin
    assert by_name["motd"].default is False            # an optional one


def test_build_block_lists_existing_rows_first_and_reports_unknown():
    plugins = [plugin_registry.PluginInfo("motd", "d", False),
               plugin_registry.PluginInfo("mission", "d", True)]
    block = plugin_registry.build_block(["serverstats", "motd"], True, plugins)
    names = [row["name"] for row in block["rows"]]
    assert names[:2] == ["serverstats", "motd"]        # the file's own order, untouched
    assert block["rows"][0]["installed"] is False      # serverstats maps to no shipped plugin
    assert block["rows"][0]["enabled"] is True         # …but it is listed, so its box is checked
    assert block["unknown"] == ["serverstats"]
    assert block["has_key"] is True
    assert names[2] == "mission"                       # the rest appended, default-set first
    assert block["rows"][2]["enabled"] is False


def test_build_block_reports_an_absent_key():
    block = plugin_registry.build_block(None, False, [])
    assert block["has_key"] is False
    assert block["rows"] == [] and block["unknown"] == []


# ============================================================ 2. the page renders the block

@pytest.mark.asyncio
async def test_the_main_editor_omits_the_plugin_lists(plugin_dir):
    """The main.yaml editor shows neither plugin list: both are managed by the LANDING's tiles.

    ``opt_plugins`` used to render as a dedicated checkbox block on the (old) main page; that page is
    no longer ``main.yaml``'s — its cogwheel opens the SAME scoped editor every other config file uses,
    and that editor OMITS ``plugins``/``opt_plugins`` entirely (``MAIN_SUPPRESSED_FIELDS``). The lists
    survive a save untouched (see ``test_webui_settings_page``); the plugin TILES on ``/settings`` own
    the switches (``test_webui_settings_tiles`` / ``test_webui_plugin_tiles_defaults``).
    """
    app, _ = _app(plugin_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    assert 'class="st-layout"' in page and 'class="st-tabs"' in page
    # no plugin-list control of ANY shape is rendered on the main editor
    assert 'name="f.opt_plugins"' not in page
    assert 'name="f.plugins"' not in page
    assert 'class="cfg-sec cfg-plugins"' not in page
    assert 'data-cfg-list="opt_plugins"' not in page and 'data-cfg-list="plugins"' not in page
    # the file keeps those values; the landing tiles read them


# ============================================================ 3. the round-trip

@pytest.mark.asyncio
async def test_a_save_enables_one_disables_one_and_keeps_the_unknown(plugin_dir):
    """Enable ``greenieboard``, disable ``motd``, keep the unknown ``serverstats`` — all preserved."""
    app, _ = _app(plugin_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        # the operator's boxes: serverstats (kept) and greenieboard (newly ticked); motd cleared
        form = _with_csrf(_form(["serverstats", "greenieboard"]), csrf, revision)
        response = await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    assert response.status_code == 303
    assert _opt_plugins(plugin_dir / "main.yaml") == ["serverstats", "greenieboard"]


@pytest.mark.asyncio
async def test_an_untouched_save_is_byte_identical(plugin_dir):
    """The block reproduces the file's OWN order, so posting it unchanged changes nothing."""
    app, _ = _app(plugin_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _with_csrf(_form(["motd", "serverstats"]), csrf, revision)
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
        landed = await _open(client)
    assert (plugin_dir / "main.yaml").read_text(encoding="utf-8") == PLUGIN_MAIN
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_NO_CHANGE


@pytest.mark.asyncio
async def test_clearing_every_plugin_writes_an_empty_list(plugin_dir):
    """Unticking them all must CLEAR the list — the empty sentinel is what makes that land."""
    app, _ = _app(plugin_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _with_csrf(_form([]), csrf, revision)          # nothing checked, sentinel only
        response = await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    assert response.status_code == 303
    assert _opt_plugins(plugin_dir / "main.yaml") == []


@pytest.mark.asyncio
async def test_an_absent_opt_plugins_key_is_not_created_by_an_untouched_save(plugin_dir):
    """With no ``opt_plugins`` key the block posts no sentinel, so a save must not invent one."""
    app, _ = _app(plugin_dir, text=NO_KEY_MAIN)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _with_csrf(_form([], sentinel=False), csrf, revision)
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    assert (plugin_dir / "main.yaml").read_text(encoding="utf-8") == NO_KEY_MAIN
