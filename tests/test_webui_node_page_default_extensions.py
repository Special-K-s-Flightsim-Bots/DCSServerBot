"""THE NODE PAGE TELLS THE TRUTH ABOUT DEFAULT EXTENSIONS.

``DEFAULT_EXTENSIONS = {'LogAnalyser': {}, 'Cloud': {}}``". His presentation decision: "I would display
it like with the (default) plugins. Just mark it default and show it. People can still disable them."

WHY THIS MODULE EXISTS. The node page's extension cards read their state from the FILE, so a default
extension that ``nodes.yaml`` does not configure used to show as an "add choice" — the operator was
told the OPPOSITE of the truth, because the runtime loads ``LogAnalyser`` and ``Cloud`` even with no
block (``core/data/impl/serverimpl.py``'s ``load_extension``/``init_extensions`` merge
``DEFAULT_EXTENSIONS`` UNDER the file's own blocks). This module pins the honest behaviour, over the
REAL app:

* an UNCONFIGURED default extension renders as a CARD, ON, wearing the SAME "default" badge a default
  plugin tile wears — never as an off/not-configured add choice;
* a CONFIGURED one renders as configured;
* DISABLING IS SUPPORTED and the switch writes EXACTLY the value the loader reads — ``enabled: false``
  into the correct scope's block (node-wide tab → the node block, instance tab → that instance's block)
  — NOT a removal (an absent block means "default on", so a removal would silently re-enable it); ON
  writes ``enabled: true``; the two round-trip, and a no-op save stays byte-identical;
* a DEFAULT extension has NO trashcan/removal warning — removing its block does not stop it, so offering
  a removal would imply the opposite of what it does;
* the add picker's "not configured" list never contradicts the runtime: a default name is never in it;
* ``disable``/``enable`` are refused for a NON-default extension (those are switched off by removal).
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import (_add_options, _app, _block, _extension_block,  # noqa: F401
                                  _instance_block, _page_url, node_data, node_dir)
from test_webui_node_page_extension_switch import _card, _get, _switch, _switch_form


# ===================================================== 1. an unconfigured default is ON, "default", no add

@pytest.mark.asyncio
async def test_an_unconfigured_default_extension_renders_on_with_the_default_badge(node_dir, node_data):  # noqa: F811
    """``LogAnalyser`` (instance-scoped, no block anywhere) and ``Cloud`` (no schema at all, no block)
    are DEFAULT extensions: they RUN regardless of the file, so both render as CARDS — ON (no ``off``
    class), carrying the SAME ``st-badge default`` marker a default plugin tile wears and the
    ``is-default`` state class — and NEITHER is an add choice (which would imply it is off)."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        node_wide = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    for page in (node_wide, syria):
        for name in ("LogAnalyser", "Cloud"):
            card = _card(page, name)
            assert card is not None, f"{name} is not a card on this tab"
            classes = card["classes"].split()
            assert "off" not in classes, f"{name} rendered OFF while it is running"
            assert "is-default" in classes, f"{name} does not carry the is-default state"
            assert 'class="st-badge default"' in card["body"], f"{name} has no default badge"
            assert name not in _add_options(page), f"{name} is offered as an add choice"


@pytest.mark.asyncio
async def test_the_default_switch_says_enabled_and_posts_the_disable_write(node_dir, node_data):  # noqa: F811
    """The switch is HONEST about the runtime: its state word is "Enabled" (never the file's "Not
    configured"), and its form posts the ``disable`` action through the node page's OWN save path —
    the same route a plugin tile's switch uses, not a second control.

    Read on an INSTANCE tab: ON/OFF belongs there (``enable()`` runs on a Server and the action writes
    the Instance scope), and the Node-wide card carries no switch at all.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        node_wide = await _get(client, _page_url("dcs-a-01"))
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    node_wide_card = _card(node_wide, "LogAnalyser")
    assert node_wide_card is not None
    assert "st-sw" not in node_wide_card["body"], "the Node-wide card grew a switch back"
    body = html.unescape(_card(page, "LogAnalyser")["body"])
    assert node_settings_page.NODE_PAGE_EXTENSION_ENABLED in body
    assert node_settings_page.NODE_PAGE_EXTENSION_OFF not in body, "the file's word leaked in"
    form = _switch_form(page, "LogAnalyser")
    assert form["action"] == f'{node_settings_page.node_page_url("dcs-a-01")}?tab=1'
    assert form["fields"][node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] == \
        node_settings_page.NODE_PAGE_EXTENSION_DISABLE


@pytest.mark.asyncio
async def test_a_default_extension_has_no_trashcan_and_no_removal_warning(node_dir, node_data):  # noqa: F811
    """REMOVING a default extension's block does NOT stop it — an absent block means "default on" — so
    the card offers NO trashcan (its removal would imply the opposite of what it does) and carries no
    inline removal warning either."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    card = _card(page, "LogAnalyser")
    assert 'class="st-trash"' not in card["body"], "a default has a trashcan"
    assert "st-warn" not in card["body"], "a default rendered a removal warning"


# ===================================================== 2. the disable/enable write, and the round trip

@pytest.mark.asyncio
async def test_disabling_a_default_extension_writes_enabled_false_and_round_trips(node_dir, node_data):  # noqa: F811
    """The node-wide tab's OFF writes ``enabled: false`` into the NODE's block (created for the write),
    leaving every other node/scope byte-identical; the re-read shows the card OFF; turning it back ON
    writes ``enabled: true``. The value written is EXACTLY the one the loader reads
    (``core/extension.py``)."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        assert reply.status_code == 303, reply.text
        after_disable = path.read_text(encoding="utf-8")
        off_page = await _get(client, _page_url("dcs-a-01"))
        back = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                             node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                             node_settings_page.NODE_PAGE_SCOPE_DEFAULT, off_page)
        assert back.status_code == 303, back.text
    # 1. the disable wrote enabled: false into the NODE block, and nothing else moved
    assert "LogAnalyser:" in _block(after_disable, "dcs-a-01"), "no block was written"
    assert "enabled: false" in _extension_block(after_disable, "dcs-a-01", "LogAnalyser")
    assert _block(after_disable, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _extension_block(after_disable, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Syria", "Caucasus", "PersianGulf"):
        assert _instance_block(after_disable, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"
    # 2. the re-read shows it OFF (the runtime's answer), still a "default" card, no add choice
    off_card = _card(off_page, "LogAnalyser")
    assert "off" in off_card["classes"].split(), "the disabled default is not shown OFF"
    assert "is-default" in off_card["classes"].split()
    assert "LogAnalyser" not in _add_options(off_page)
    # 3. the enable wrote enabled: true back
    after_enable = path.read_text(encoding="utf-8")
    assert "enabled: true" in _extension_block(after_enable, "dcs-a-01", "LogAnalyser")
    assert _block(after_enable, "dcs-b-01") == _block(before, "dcs-b-01")


@pytest.mark.asyncio
async def test_disabling_a_default_extension_on_an_instance_writes_that_instances_block(node_dir, node_data):  # noqa: F811
    """The INSTANCE tab's OFF writes ``enabled: false`` into THAT instance's block — the node's own
    block and the sibling instances stay byte-identical."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await _switch(client, "dcs-a-01", 1, "LogAnalyser",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "enabled: false" in _instance_block(after, "dcs-a-01", "Syria")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance)


@pytest.mark.asyncio
async def test_an_instance_inherits_the_nodes_disable(node_dir, node_data):  # noqa: F811
    """The NODE-wide disable is the DEFAULT for every instance: with ``LogAnalyser: {enabled: false}``
    on the node and NOTHING on Syria, the instance tab shows it OFF (``load_extension`` merges the node
    block UNDER the instance block — ``core/data/impl/serverimpl.py``). Turning it ON there writes the
    instance's own block, which then wins."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                      node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
        back = await _switch(client, "dcs-a-01", 1, "LogAnalyser",
                             node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                             node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", syria)
    assert back.status_code == 303, back.text
    # the node-wide disable is INHERITED on the instance tab before the override ...
    assert "off" in _card(syria, "LogAnalyser")["classes"].split(), \
        "the instance did not inherit the node's disable"
    # ... and the instance's own enabled: true is written into ITS block
    after = path.read_text(encoding="utf-8")
    assert "enabled: true" in _instance_block(after, "dcs-a-01", "Syria")
    assert "enabled: false" in _extension_block(after, "dcs-a-01", "LogAnalyser")


@pytest.mark.asyncio
async def test_disabling_the_schema_less_default_cloud_writes_enabled_false(node_dir, node_data):  # noqa: F811
    """``Cloud`` has NO schema at all, yet it is a default extension the runtime loads: its switch
    writes exactly the same ``enabled: false`` the schema-known default does, and the write is ACCEPTED
    (the node schema allows an extension block the extension schema does not name)."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Cloud",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        off_page = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert "enabled: false" in _extension_block(path.read_text(encoding="utf-8"),
                                                "dcs-a-01", "Cloud")
    assert "off" in _card(off_page, "Cloud")["classes"].split()


@pytest.mark.asyncio
async def test_a_noop_disable_leaves_the_file_byte_identical(node_dir, node_data):  # noqa: F811
    """A ``disable`` on an already-disabled default changes nothing: the file is byte-identical (the
    no-op rule), and the operator is told so (not a fabricated "turned off")."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                      node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        before = path.read_text(encoding="utf-8")
        off_page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, off_page)
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a no-op disable rewrote the file"
    assert html.unescape(node_settings_page.NODE_PAGE_EXTENSION_TURNED_OFF) not in html.unescape(shown)


# ===================================================== 3. the switch is the same for every extension

@pytest.mark.asyncio
async def test_a_disable_names_a_non_default_extension_the_same_way(node_dir, node_data):  # noqa: F811
    """``RealWeather`` is configured but NOT a default: its switch is the SAME ``disable`` write the
    default's is — the block keeps every value it holds (``installation``) and gains ``enabled: false``.
    There is no separate "removal" switch: switching off never discards a value."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        off_page = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    block = _extension_block(path.read_text(encoding="utf-8"), "dcs-a-01", "RealWeather")
    assert "enabled: false" in block
    assert "installation: /opt/rw-a" in block, "the block's own value was discarded"
    assert "off" in _card(off_page, "RealWeather")["classes"].split()


@pytest.mark.asyncio
async def test_a_configured_default_renders_as_configured_and_can_be_disabled(node_dir, node_data):  # noqa: F811
    """A default extension that IS configured (a real block) still renders as a "default" card, ON —
    the marker is about the runtime set, not the file — and its disable keeps the block (writing only
    ``enabled: false``), so the configured values survive."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        # configure LogAnalyser's block first (a plain Expert-Mode-free write: the extension's own form)
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                      node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        configured = await _get(client, _page_url("dcs-a-01"))
        card = _card(configured, "LogAnalyser")
        assert card is not None and "off" not in card["classes"].split()
        assert "is-default" in card["classes"].split()
        reply = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, configured)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    # the block survives (only enabled was written) — a removal would have deleted it
    assert "LogAnalyser:" in _block(after, "dcs-a-01")
    assert "enabled: false" in _extension_block(after, "dcs-a-01", "LogAnalyser")


# ===================================================== 4. the switch renders translated

@pytest.mark.asyncio
async def test_the_default_switch_and_its_notice_render_in_german(node_dir, node_data):  # noqa: F811
    """The two new state words are real msgids: with German chosen the card's switch reads the catalog's
    German, not the English msgid — and a disable's notice does too."""
    from services.webservice import i18n

    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        assert reply.status_code == 303, reply.text
        shown = await _get(client, _page_url("dcs-a-01"))
    body = html.unescape(shown)
    # the notice is the CATALOG's German (the extension was turned off)
    assert "wurde für diesen Bereich ausgeschaltet" in body, body[:400]
    assert node_settings_page.NODE_PAGE_EXTENSION_TURNED_OFF not in body
