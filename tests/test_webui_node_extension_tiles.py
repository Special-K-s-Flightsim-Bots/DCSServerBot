"""THE NODE PAGE's EXTENSION TILES — the VERY SAME component the Settings plugins/services use.

the very same way. They have a short description of what they are and some enable / disable slider. A
cogwheel opens the configuration of them."

Pinned here over the REAL app:

* an extension tile is the SAME card the plugin/service tiles are — the ONE shared macro
  (`_tiles.html` `tile_card`), the same st-* classes: a kind head, a name, a two-line description, a
  foot with the SAME `st-sw` switch and a cogwheel;
* the description is the extension's OWN README first prose line (never invented prose);
* the switch is the SAME on/off slider as a plugin's — ON when the scope configures the extension,
  OFF when not — and moves BOTH ways;
* the cogwheel OPENS the extension's configuration (a destination page), which renders the SAME
  schema-driven scoped view a plugin's cogwheel opens.
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice import catalog
from services.webservice.pages import node_settings as node_settings_page
from test_webui_node_page import (_app, _block, _extension_block, _instance_block,  # noqa: F401
                                  node_data, node_dir)
from test_webui_settings_node_scope import _csrf, _revision

_CARD = re.compile(r'<article class="st-tile ext([^"]*)">(.*?)</article>', re.DOTALL)


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


def _page_url(node: str, tab: int | None = None) -> str:
    url = node_settings_page.node_page_url(node)
    return url if tab is None else f"{url}?tab={tab}"


def _card(page: str, name: str) -> str | None:
    for match in _CARD.finditer(page):
        if f'class="st-name">{name}<' in match.group(2):
            return match.group(0)
    return None


def _description_of(name: str) -> str:
    for info in catalog.discover_extensions():
        if info.name == name:
            return info.description
    raise AssertionError(f"no extension {name}")


# =========================================================== 1. the SAME tile, from the SAME macro

@pytest.mark.asyncio
async def test_extension_tiles_use_the_shared_card_classes(node_dir, node_data):  # noqa: F811
    """The extension tile carries the SAME st-* classes a plugin/service tile does — the shared
    `_tiles.html` component, so a future divergence fails here.

    The switch is INSTANCE-tab only (ON/OFF writes the Instance scope, and ``enable()`` runs on a
    Server), so the foot's switch is asserted on the instance tab; the Node-wide card is the same card
    without one.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, _page_url("dcs-a-01"))
        instance = await _get(client, _page_url("dcs-a-01", tab=1))
    card = _card(page, "RealWeather")
    assert card is not None, "no extension card for RealWeather"
    for needle in ('class="st-tile ext', 'class="st-top"', 'class="st-kind"',
                   'class="st-name"', 'class="st-desc clamp2"', 'class="st-foot"'):
        assert needle in card, f"the extension card is missing {needle!r}"
    assert 'class="st-sw' in _card(instance, "Trackfile"), "the instance card has no switch"
    # the configuration is behind the cogwheel, NOT an inline form: no ``f.`` field, no ``st-row``
    assert 'name="f.' not in card, "the extension card still carries inline config fields"
    assert "st-row" not in card, "the extension card still carries inline config rows"
    assert 'class="st-cog"' in card, "the extension card has no cogwheel into its configuration"


async def _login_safe(client):
    from test_webui_dashboard import _login
    await _login(client, "staff")


@pytest.mark.asyncio
async def test_the_extension_switch_is_literally_the_plugin_switch_markup(node_dir, node_data):  # noqa: F811
    """The extension slider and the plugin slider are the SAME control markup — the ONE
    `switch_control` macro — so a future divergence fails here.

    Read on an INSTANCE tab: that is the tab whose cards carry an on/off switch (ON/OFF writes the
    Instance scope). The Node-wide tab's cards carry none by design.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        landing = await _get(client, "/settings")
        node_page = await _get(client, _page_url("dcs-a-01", tab=1))
    control = '<span class="bx" aria-hidden="true"><i></i></span>'
    assert control in landing and control in node_page, "the two switches are not the same control"
    assert 'class="st-swform" method="post"' in landing and \
        'class="st-swform" method="post"' in node_page
    assert re.search(r'class="st-sw on"', landing) and re.search(r'class="st-sw on"', node_page)


# =========================================================== 2. the description comes from the README

@pytest.mark.asyncio
async def test_the_description_renders_from_the_extension_readme(node_dir, node_data):  # noqa: F811
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, _page_url("dcs-a-01"))
    card = _card(page, "RealWeather")
    description = _description_of("RealWeather")
    assert description, "the fixture extension has no README description"
    assert html.escape(description, quote=True) in card, "the description is not on the tile"


# ==================================================== 3. the slider is the SAME control both ways

@pytest.mark.asyncio
async def test_the_switch_is_the_plugin_slider_and_moves_both_ways(node_dir, node_data):  # noqa: F811
    """A configured extension shows the SAME track and the plain ``disable`` FORM (the non-destructive
    switch); an extension the scope does NOT configure is a CHOICE in the ``+ Add`` tile, whose control
    is the very SAME ``switch_control`` form — never a one-way 'disable' control.

    Read on an INSTANCE tab, where the cards carry the switch.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    on_card = _card(page, "Trackfile")             # configured on the Syria instance
    assert 'class="st-sw on"' in on_card and "<span class=\"bx\"" in on_card
    # the ON switch is a FORM posting the disable write — it never removes the block
    assert re.search(r'<form class="st-swform" method="post" action="[^"]*">'
                     r'(?:(?!</form>).)*name="_ext_action" value="disable"', on_card, re.DOTALL), \
        on_card
    # an UNCONFIGURED extension (SRS) is an add CHOICE: the SAME slider markup, a FORM posting the
    # add action through this page's own save
    add = re.search(r'<article class="st-tile ext add">(.*?)</article>', page, re.DOTALL)
    assert add is not None, "the + Add tile was not rendered"
    assert 'class="st-sw"' in add.group(1) and "<span class=\"bx\"" in add.group(1)
    assert 'name="_ext" value="SRS"' in add.group(1) and 'value="add"' in add.group(1)


# =========================================================== 4. the cogwheel OPENS the config

@pytest.mark.asyncio
async def test_the_cogwheel_opens_the_extension_configuration(node_dir, node_data):  # noqa: F811
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, _page_url("dcs-a-01"))
        card = _card(page, "RealWeather")
        link = re.search(r'<a class="st-cog" href="([^"]*)"', card)
        assert link is not None, "the extension card has no cogwheel"
        href = html.unescape(link.group(1))
        assert href == node_settings_page.node_extension_url("dcs-a-01", "RealWeather"), href
        # the destination renders the SAME schema-driven scoped view a plugin's cogwheel opens, with
        # the extension's OWN fields for this node
        config = await _get(client, href)
    assert "f.dcs-a-01.extensions.RealWeather.installation" in config
    assert 'class="st-tabs"' in config and "st-row" in config


# ==================================================== 5. ON adds the block, OFF removes it (warning)

@pytest.mark.asyncio
async def test_switching_an_extension_on_from_the_node_page_writes_the_block(node_dir, node_data):  # noqa: F811
    """A turn-ON writes that extension's block SEEDED FROM ITS OWN DICT'S DEFAULTS (SRS's node dict
    declares ``installation``, ``beta`` and ``autoupdate``), so one click yields a card whose values are
    editable in place — and every other block of the file stays byte-for-byte what it was.
    """
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  node_settings_page.NODE_PAGE_SCOPE_FIELD: node_settings_page.NODE_PAGE_SCOPE_DEFAULT,
                  node_settings_page.NODE_PAGE_EXTENSION_FIELD: "SRS",
                  node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD: node_settings_page.NODE_PAGE_EXTENSION_ADD})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    block = _extension_block(after, "dcs-a-01", "SRS")
    assert "installation:" in block and "DCS-SimpleRadio-Standalone" in block, block
    assert "beta: false" in block and "autoupdate: true" in block, block
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _instance_block(after, "dcs-a-01", "Syria") == _instance_block(before, "dcs-a-01", "Syria")


# ==================================================== 6. the config page SAVES that node's block only

@pytest.mark.asyncio
async def test_saving_the_extension_config_writes_only_that_node(node_dir, node_data):  # noqa: F811
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    url = node_settings_page.node_extension_url("dcs-a-01", "RealWeather")
    async with asgi_client(app) as client:
        await _login_safe(client)
        page = await _get(client, url)
        reply = await client.post(
            url,
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "f.dcs-a-01.extensions.RealWeather.installation": "/opt/rw-new"})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "/opt/rw-new" in _extension_block(after, "dcs-a-01", "RealWeather")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _instance_block(after, "dcs-a-01", "Syria") == _instance_block(before, "dcs-a-01", "Syria")


# ==================================================== 7. the destination renders in the reader's language

@pytest.mark.asyncio
async def test_the_extension_config_page_renders_in_german(node_dir, node_data):  # noqa: F811
    from services.webservice import i18n

    app = _app(node_dir)
    url = node_settings_page.node_extension_url("dcs-a-01", "RealWeather")
    async with asgi_client(app) as client:
        await _login_safe(client)
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _get(client, url)
    body = html.unescape(page)
    assert node_settings_page.NODE_EXTENSION_SCOPE_NOTE not in body
    assert "der Block dieser Erweiterung für einen Bereich" in body
