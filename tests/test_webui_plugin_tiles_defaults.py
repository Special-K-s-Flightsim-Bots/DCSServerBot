"""PLUGIN TILES — a default plugin's state, and the warning before it is switched off.

A DEFAULT plugin (a ``DEFAULT_PLUGINS`` name) is loaded when its name is in ``main.yaml``'s
``plugins`` list — the loader is ``config.get('plugins', DEFAULT_PLUGINS)``
(``core/data/impl/nodeimpl.py``), so an ABSENT key means EVERY default loads and ``opt_plugins`` only
ADDS optional plugins on top. The landing tile now reads a default's on/off from THAT list (not from
``opt_plugins``), marks it more prominently, and shows the operator's inline warning BEFORE it is switched
off. Pinned here:

* the state comes from ``plugins`` (absent key = the default set), an optional's from ``opt_plugins``;
* the warning appears ONLY for defaults, and only when it is being switched OFF;
* switching a default off writes the ``plugins`` list and switching it on restores it;
* an unknown/extra entry survives a sibling tile's toggle;
* an unchanged list is a byte-identical no-op;
* the warning renders in all five catalog languages (and English stays the msgid).
"""
from __future__ import annotations

import gettext
import html
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import plugin_registry
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

#: a ``main.yaml`` whose ``plugins`` list names ONE default (``mission``) and whose ``opt_plugins``
#: names an optional (``motd``) and a legacy entry that maps to no shipped plugin (``serverstats``).
MAIN = """\
guild_id: 112233445566
guild_name: My Server
language: en
plugins:
  - mission
opt_plugins:
  - motd
  - serverstats
"""

#: The same file WITHOUT the ``plugins`` key — every default loads, and a default tile reads ON.
MAIN_NO_PLUGINS_KEY = """\
guild_id: 112233445566
guild_name: My Server
language: en
opt_plugins:
  - motd
"""


def _dir(tmp_path, text: str) -> Path:
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def _app(d, text=None):
    if text is not None:
        (d / "main.yaml").write_text(text, encoding="utf-8")
    return _build(d, users=[{"username": "staff", "password_hash": _hash(),
                             "roles": ["Admin"]}])


async def _landing(client, query: str = "") -> str:
    response = await client.get(settings_page.SETTINGS_PATH + query)
    assert response.status_code == 200, response.text[:300]
    return response.text


def _tile(page: str, name: str) -> str:
    """The markup of ONE plugin tile, found by its ``st-name`` (the row names are unique)."""
    for part in page.split('<article class="st-tile plugin')[1:]:
        if f">{name}</h3>" in part:
            return part.split("</article>")[0]
    raise AssertionError(f"no plugin tile for {name!r}")


def _values(tile: str, field: str) -> list[str]:
    """Every ``f.<field>`` hidden value a tile's switch form carries, in order (sentinel included)."""
    return re.findall(rf'name="f\.{re.escape(field)}" value="([^"]*)"', tile)


def _token(tile: str, field: str) -> str:
    return re.search(rf'name="{re.escape(field)}" value="([^"]*)"', tile).group(1)


def _plugins(path) -> list[str]:
    from core.utils.configwrite import load_config_file
    return list((load_config_file(path) or {}).get("plugins", []))


# ================================================================= 1. the state comes from `plugins`

def test_a_default_reads_its_state_from_the_plugins_list():
    plugins = [plugin_registry.PluginInfo("mission", "d", True),
               plugin_registry.PluginInfo("help", "d", True),
               plugin_registry.PluginInfo("motd", "d", False)]
    # the key is PRESENT and names only `mission`: `mission` is ON, the OTHER default is OFF
    block = plugin_registry.build_tiles(["mission"], True, ["motd"], True, plugins)
    by = {row["name"]: row for row in block["rows"]}
    assert by["mission"]["enabled"] is True and by["mission"]["field"] == plugin_registry.DEFAULT_FIELD
    assert by["help"]["enabled"] is False and by["help"]["field"] == plugin_registry.DEFAULT_FIELD
    # an OPTIONAL plugin reads its state from opt_plugins, and toggles THAT field
    assert by["motd"]["enabled"] is True and by["motd"]["field"] == plugin_registry.OPT_PLUGINS_FIELD


def test_an_absent_plugins_key_turns_every_default_on():
    plugins = [plugin_registry.PluginInfo("mission", "d", True),
               plugin_registry.PluginInfo("help", "d", True)]
    block = plugin_registry.build_tiles(None, False, [], False, plugins)
    by = {row["name"]: row for row in block["rows"]}
    assert by["mission"]["enabled"] is True and by["help"]["enabled"] is True


@pytest.mark.asyncio
async def test_a_default_tile_is_marked_on_and_more_prominently(tmp_path):
    app, _ = _app(_dir(tmp_path, MAIN))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
    # a default wears the stronger "default" badge class …
    assert 'class="st-badge default">default<' in page
    # … and its switch is a LINK to the inline warning (a default being switched OFF)
    assert f'href="{settings_page.SETTINGS_PATH}?warn=mission"' in page
    # an OPTIONAL plugin has no warning step: its switch is a plain form POST
    assert f'{settings_page.SETTINGS_PATH}?warn=motd' not in page


# ============================================================ 2. the warning appears only for defaults

@pytest.mark.asyncio
async def test_the_warning_appears_only_for_defaults(tmp_path):
    app, _ = _app(_dir(tmp_path, MAIN))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        plain = await _landing(client)
        warned = await _landing(client, "?warn=mission")      # a DEFAULT
        optional = await _landing(client, "?warn=motd")       # an OPTIONAL plugin
    assert settings_page.PLUGIN_DEFAULT_WARNING not in plain
    assert settings_page.PLUGIN_DEFAULT_WARNING in warned
    assert settings_page.PLUGIN_DEFAULT_TURN_OFF in warned
    assert settings_page.PLUGIN_DEFAULT_KEEP in warned
    assert 'class="st-warn"' in warned
    # the warning is for DEFAULTS only — naming an optional opens nothing
    assert settings_page.PLUGIN_DEFAULT_WARNING not in optional
    assert 'class="st-warn"' not in optional


@pytest.mark.asyncio
async def test_a_warned_default_does_not_write_until_the_confirm_button(tmp_path):
    """Opening the warning changes NOTHING — the write happens only on the confirm form."""
    d = _dir(tmp_path, MAIN)
    before = (d / "main.yaml").read_text(encoding="utf-8")
    app, _ = _app(d)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        await _landing(client, "?warn=mission")
    assert (d / "main.yaml").read_text(encoding="utf-8") == before


# ========================================================== 3. switching off writes, on restores

@pytest.mark.asyncio
async def test_switching_a_default_off_writes_the_plugins_list_and_on_restores_it(tmp_path):
    d = _dir(tmp_path, MAIN)
    app, _ = _app(d)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        warned = await _landing(client, "?warn=mission")
        tile = _tile(warned, "mission")
        values = _values(tile, "plugins")
        # the confirm form posts every CURRENT plugins entry EXCEPT mission — and an empty sentinel
        assert [v for v in values if v] == [] and "" in values
        response = await client.post(
            settings_page.SETTINGS_SAVE_PATH,
            data={"f.plugins": values, "_csrf_token": _token(tile, "_csrf_token"),
                  "_revision": _token(tile, "_revision"), "_expert": "0"})
        assert response.status_code == 303
    assert _plugins(d / "main.yaml") == []                     # mission was removed

    # …and turning it back on posts the list WITH mission, restoring the entry
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        tile = _tile(page, "mission")
        values = _values(tile, "plugins")
        assert [v for v in values if v] == ["mission"]
        await client.post(
            settings_page.SETTINGS_SAVE_PATH,
            data={"f.plugins": values, "_csrf_token": _token(tile, "_csrf_token"),
                  "_revision": _token(tile, "_revision"), "_expert": "0"})
    assert _plugins(d / "main.yaml") == ["mission"]


@pytest.mark.asyncio
async def test_an_unknown_extra_entry_survives_a_sibling_toggle(tmp_path):
    text = MAIN.replace("  - serverstats\n", "  - serverstats\n  - legacything\n")
    d = _dir(tmp_path, text)
    app, _ = _app(d)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        tile = _tile(page, "motd")                             # the OPTIONAL, switched OFF
        values = _values(tile, "opt_plugins")
        assert "legacything" in values and "serverstats" in values
        assert "motd" not in [v for v in values if v]
        await client.post(
            settings_page.SETTINGS_SAVE_PATH,
            data={"f.opt_plugins": values, "_csrf_token": _token(tile, "_csrf_token"),
                  "_revision": _token(tile, "_revision"), "_expert": "0"})
    from core.utils.configwrite import load_config_file
    written = list(load_config_file(d / "main.yaml").get("opt_plugins"))
    assert written == ["serverstats", "legacything"]           # the unknown entry survived


# ================================================================== 4. an unchanged save is a no-op

@pytest.mark.asyncio
async def test_an_unchanged_plugins_list_is_byte_identical(tmp_path):
    d = _dir(tmp_path, MAIN)
    before = (d / "main.yaml").read_text(encoding="utf-8")
    app, _ = _app(d)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _landing(client)
        await client.post(
            settings_page.SETTINGS_SAVE_PATH,
            data={"f.plugins": ["mission"], "_csrf_token": _token(page, "_csrf_token"),
                  "_revision": _token(page, "_revision"), "_expert": "0"})
        landed = await _landing(client)
    assert (d / "main.yaml").read_text(encoding="utf-8") == before
    banner = re.search(r'notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_NO_CHANGE


# ========================================================== 5. the warning in every catalog language

def test_the_default_warning_is_translated_in_all_five_catalogs():
    repo = Path(__file__).resolve().parent.parent
    msgs = (settings_page.PLUGIN_DEFAULT_WARNING, settings_page.PLUGIN_DEFAULT_TURN_OFF,
            settings_page.PLUGIN_DEFAULT_KEEP)
    for language in ("de", "es", "ru", "cn"):
        translations = gettext.translation("webui", localedir=str(repo / "locale"),
                                           languages=[language], fallback=True)
        for msgid in msgs:
            assert translations.gettext(msgid) != msgid, f"{language} misses a translation: {msgid!r}"
    # English is the msgid (the base language)
    english = gettext.translation("webui", localedir=str(repo / "locale"),
                                  languages=["en"], fallback=True)
    for msgid in msgs:
        assert english.gettext(msgid) == msgid


@pytest.mark.asyncio
async def test_the_inline_warning_renders_in_german(tmp_path):
    """The warning is CHROME: it renders in the request's language, end to end through the page."""
    from services.webservice import i18n
    app, _ = _app(_dir(tmp_path, MAIN))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _landing(client, "?warn=mission")
    assert "Du schaltest ein Standard-Plugin aus." in page
    assert "Trotzdem ausschalten" in page
    assert settings_page.PLUGIN_DEFAULT_WARNING not in page
