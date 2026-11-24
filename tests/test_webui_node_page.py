"""THE NODE PAGE (``/nodes/<node>``) — The design: a node's block of ``nodes.yaml`` shown as
tabs, DEFAULT first (the node's own settings AND its node-scoped extensions), then one tab per
INSTANCE the node declares (that instance's settings AND its instance-scoped extensions).

Pinned here over the REAL app:

* the Nodes LIST still renders, and the node page renders DEFAULT + one tab per instance (1 + N);
* each tab shows only ITS OWN scope's settings and only ITS OWN extensions — an extension under
  DEFAULT is not shown under an instance, and vice versa;
* editing an extension in a tab writes into ``nodes.yaml`` in the right slot and leaves every OTHER
  node and every other scope byte-identical;
* the Expert Mode editor is reachable from the node page and its slice holds ONLY the selected node.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import catalog
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login
from test_webui_settings_node_scope import (_block, _chips, _csrf, _get, _panel, _revision,
                                            _tab_labels)

BOT_YAML = """\
roles:
  Admin:
    - specialk
"""

#: TWO nodes. ``dcs-a-01`` declares THREE instances (DEFAULT + 3 = 4 tabs); ``dcs-b-01`` ONE. The
#: node's own ``extensions`` block configures ``RealWeather`` (a node/instance extension); the Syria
#: instance configures ``Trackfile`` (an instance-only extension) — so a scope's own extensions can be
#: told apart from the other scope's.
NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    RealWeather:
      installation: /opt/rw-a
      autoupdate: true
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        Trackfile:
          enabled: true
          target: /opt/track
    Caucasus:
      home: /home/a-caucasus
      bot_port: 6667
      webgui_port: 8087
      dcs_port: 10307
    PersianGulf:
      home: /home/a-pg
      bot_port: 6668
      webgui_port: 8088
      dcs_port: 10308
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
      bot_port: 6669
      webgui_port: 8089
      dcs_port: 10309
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""


@pytest.fixture
def node_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(node_dir, monkeypatch):
    """Make the bot's own validator see THIS install's cluster (the same binding the sibling tests use)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(node_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


def _app(node_dir):
    app = _build(node_dir, users=[{"username": "staff", "password_hash": _hash(),
                                   "roles": ["Admin"]}])[0]
    app.state.webui_source_provider = _fleet_source
    return app


def _fleet_source():
    """A scoped source that knows both nodes, so the node page can resolve them."""
    from test_webui_dashboard import _source, node

    nodes = {"dcs-a-01": node(name="dcs-a-01", master=True),
             "dcs-b-01": node(name="dcs-b-01", master=False)}
    return _source(nodes=nodes, node_obj=nodes["dcs-a-01"])


def _labels(page: str) -> list[str]:
    """The tab strip's labels (the text before the ``<span class="n">`` note)."""
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.DOTALL)
    assert nav is not None, "the tab strip was not rendered"
    return [re.sub(r"<[^>]+>", "", whole.split('<span class="n">')[0]).strip()
            for whole in re.findall(r'<a class="t[^\"]*"[^>]*>(.*?)</a>', nav.group(0), re.DOTALL)]


def _page_url(node: str, tab: int | None = None) -> str:
    url = node_settings_page.node_page_url(node)
    return url if tab is None else f"{url}?tab={tab}"


_EXT_CARD = re.compile(r'<article class="st-tile ext([^"]*)">(.*?)</article>', re.DOTALL)


def _ext_card(page: str, name: str) -> tuple[str, str] | None:
    """One extension card's ``(classes, body)`` by its ``st-name`` — or ``None`` when it is absent."""
    for match in _EXT_CARD.finditer(page):
        if f'class="st-name">{name}<' in match.group(2):
            return match.group(1), match.group(2)
    return None


_EXT_ADD = re.compile(r'<article class="st-tile ext add">(.*?)</article>', re.DOTALL)


def _add_options(page: str) -> list[str]:
    """The extension names offered as choices inside the ONE ``+ Add`` tile, in render order."""
    match = _EXT_ADD.search(page)
    if match is None:
        return []
    return re.findall(r'<div class="st-add-name"[^>]*>([^<]+)</div>', match.group(1))


def addable(names) -> set[str]:
    """*names* minus the bot's DEFAULT extensions — a default is ALWAYS a card, never an add choice.

    A DEFAULT extension (``DEFAULT_EXTENSIONS``) runs even when ``nodes.yaml`` configures nothing, so
    it is shown ON with the "default" badge rather than offered as a not-yet-configured add choice.
    """
    defaults = catalog.default_extensions()
    return {name for name in names if name.lower() not in defaults}


# ============================================================= the list and the 1 + N tabs

@pytest.mark.asyncio
async def test_the_nodes_list_renders_and_the_node_page_has_default_plus_instances(node_dir,
                                                                                    node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        listing = await _get(client, "/nodes")
        page = await _get(client, _page_url("dcs-a-01"))
    assert 'href="/nodes"' in listing  # the list itself still renders
    assert _labels(page) == ["Node-wide", "Instance Syria", "Instance Caucasus",
                             "Instance PersianGulf"], _labels(page)


@pytest.mark.asyncio
async def test_a_single_instance_node_has_default_plus_one(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-b-01"))
    assert _labels(page) == ["Node-wide", "Instance Nevada"], _labels(page)


# ============================================================= each tab shows only its own scope

@pytest.mark.asyncio
async def test_the_default_tab_shows_the_nodes_own_settings_not_the_instances(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    assert 'name="f.dcs-a-01.listen_address"' in page
    assert 'name="f.dcs-a-01.instances.Syria.bot_port"' not in page, \
        "the DEFAULT tab rendered an instance's settings"


@pytest.mark.asyncio
async def test_an_instance_tab_shows_only_that_instances_settings(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    assert 'name="f.dcs-a-01.instances.Syria.bot_port"' in page
    # the node's own settings and the OTHER instances are not on this tab
    assert 'name="f.dcs-a-01.listen_address"' not in page
    assert 'name="f.dcs-a-01.instances.Caucasus.bot_port"' not in page


@pytest.mark.asyncio
async def test_default_and_instance_extensions_are_not_mixed(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    # a NODE-scoped extension (RealWeather, configured) is a CARD on the Node-wide tab; it is not on
    # the instance tab (there it is an ADD choice, since the Syria scope has not configured it)
    assert _ext_card(default, "RealWeather") is not None
    assert _ext_card(syria, "RealWeather") is None and "RealWeather" in _add_options(syria)
    # an INSTANCE-scoped extension (Trackfile): configured on Syria → a card there; on the Node-wide
    # tab it is an ADD choice — the node-wide scope offers the instance-scoped names too — never a
    # card (the node does not configure it)
    assert _ext_card(syria, "Trackfile") is not None
    assert _ext_card(default, "Trackfile") is None
    assert "Trackfile" in _add_options(default)


# ============================================================= editing an extension in a tab

@pytest.mark.asyncio
async def test_editing_a_node_extension_writes_its_slot_and_leaves_the_others_byte_identical(
        node_dir, node_data):
    path: Path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "f.dcs-a-01.extensions.RealWeather.autoupdate": "0"})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "autoupdate: false" in _block(after, "dcs-a-01"), "the node extension was not written"
    # EVERY other node's block is byte-for-byte what it was
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    # and the node's OWN instances were not touched
    for instance in ("Syria", "Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"


@pytest.mark.asyncio
async def test_editing_an_instance_extension_writes_its_slot_and_leaves_the_others_byte_identical(
        node_dir, node_data):
    path: Path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await client.post(
            _page_url("dcs-a-01", tab=1),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "f.dcs-a-01.instances.Syria.extensions.Trackfile.enabled": "0"})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "enabled: false" in _instance_block(after, "dcs-a-01", "Syria")
    # the node's own extension block and the other instances are untouched
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance)
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")


# ============================================== the full round trip the card names

@pytest.mark.asyncio
async def test_a_full_round_trip_edits_a_default_setting_and_an_instance_extension(node_dir, node_data):
    """The card's round trip: edit a node's DEFAULT setting AND an instance's extension from the node
    page, then RE-READ the file — both landed, and the other node is byte-for-byte its old self."""
    path: Path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        # 1. the node's OWN (DEFAULT) setting
        page = await _get(client, _page_url("dcs-a-01"))
        first = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "f.dcs-a-01.listen_port": "10099"})
        assert first.status_code == 303, first.text
        # 2. an INSTANCE's extension — re-read the page first: the file changed under us
        page2 = await _get(client, _page_url("dcs-a-01", tab=1))
        second = await client.post(
            _page_url("dcs-a-01", tab=1),
            data={"_csrf_token": _csrf(page2), "_revision": _revision(page2),
                  "f.dcs-a-01.instances.Syria.extensions.Trackfile.enabled": "0"})
        assert second.status_code == 303, second.text
    after = path.read_text(encoding="utf-8")
    assert "listen_port: 10099" in _block(after, "dcs-a-01"), "the node's DEFAULT setting was not written"
    assert "enabled: false" in _instance_block(after, "dcs-a-01", "Syria"), \
        "the instance's extension was not written"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), "the other node was rewritten"


# ============================================================= the Expert Mode slice

@pytest.mark.asyncio
async def test_the_expert_panel_holds_only_the_selected_node(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    assert "data-cfg-expert-toggle" in page, "Expert Mode is not reachable from the node page"
    import html as _html

    assert settings_page.SCOPE_SLICE_NOTE in _html.unescape(page)
    panel = _panel(page)
    assert "dcs-a-01:" in panel
    assert "dcs-b-01" not in panel, "the expert editor was handed more than the selected node's slice"


@pytest.mark.asyncio
async def test_switching_node_changes_the_slice(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        a = _panel(await _get(client, _page_url("dcs-a-01")))
        b = _panel(await _get(client, _page_url("dcs-b-01")))
    assert "dcs-a-01:" in a and "dcs-b-01" not in a
    assert "dcs-b-01:" in b and "dcs-a-01" not in b


@pytest.mark.asyncio
async def test_an_expert_slice_save_lands_on_that_node_and_leaves_the_other_byte_identical(
        node_dir, node_data):
    path: Path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        edited = _panel(page).replace("/opt/rw-a", "/opt/rw-a-NEW")
        reply = await client.post(
            _page_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  "_expert": "1", "_yaml": edited})
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "/opt/rw-a-NEW" in after
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01"), "another node was rewritten"


@pytest.mark.asyncio
async def test_the_validate_route_checks_the_node_slice(node_dir, node_data):
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        good = await client.post(
            node_settings_page._node_page_validate_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_yaml": _panel(page)})
        broken = _panel(page).replace("listen_port: 10042", "listen_port: not-a-number")
        bad = await client.post(
            node_settings_page._node_page_validate_url("dcs-a-01"),
            data={"_csrf_token": _csrf(page), "_yaml": broken})
    assert good.status_code == 200 and good.json()["ok"] is True, good.text
    assert bad.status_code == 200 and bad.json()["ok"] is False, bad.text


# ============================================================= every extension is still reachable

@pytest.mark.asyncio
async def test_an_extension_not_configured_yet_is_still_offered_on_the_node_page(node_dir, node_data):
    """NO SILENT LOSS when the EXTENSIONS rail left Settings: every extension of a scope stays
    REACHABLE from the node page EVEN WHEN ``nodes.yaml`` does not configure it yet — a configured
    one is a TILE, and the ones the scope does not configure are the CHOICES of the ``+ Add`` tile.
    The SET is the schema (``node_extensions_schema`` / ``instance_extensions_schema``), not the
    document, so the node page remains a SUPERSET of what Settings' extension tiles used to reach.

    The path a user takes after this card: Nodes list → the row's cogwheel → ``/nodes/<node>``; the
    node-scoped extension tiles and their ``+ Add`` choices live on the DEFAULT tab, an instance's on
    its instance tab.
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        # dcs-b-01 carries NO node-level extensions block in the fixture ... so it has no extension
        # TILE, but the node-scoped extensions are STILL offered — as ``+ Add`` choices
        default = await _get(client, _page_url("dcs-b-01"))
        assert _ext_card(default, "RealWeather") is None
        assert "RealWeather" in _add_options(default), \
            "a node-scoped extension with no block in nodes.yaml is not offered any more"
        # an instance with no extension block likewise offers its instance-scoped extensions
        nevada = await _get(client, _page_url("dcs-b-01", tab=1))
        assert _ext_card(nevada, "Trackfile") is None
        assert "Trackfile" in _add_options(nevada), \
            "an instance-scoped extension with no block in nodes.yaml is not offered any more"


# ============================================================= the tab NAMES (the rename)

@pytest.mark.asyncio
async def test_the_tabs_are_named_node_wide_and_instance_x(node_dir, node_data):
    """the operator's rename: the first tab reads "Node-wide", each instance tab "Instance <name>" — the
    instance's OWN name, never "<name> override". The ORDER and the ``?tab=`` links are untouched."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    labels = _labels(page)
    assert labels == ["Node-wide", "Instance Syria", "Instance Caucasus", "Instance PersianGulf"], labels
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.DOTALL).group(0)
    # the old wording is GONE: no tab reads "override", and the first tab is not "DEFAULT"
    assert "override" not in nav.lower(), nav
    assert "DEFAULT" not in nav, nav
    # the ORDER and the ?tab= links are EXACTLY what they were (a label change, not a routing change)
    assert re.findall(r'href="([^"]+)"', nav) == \
        [f"/nodes/dcs-a-01?tab={index}" for index in range(4)], nav


@pytest.mark.asyncio
async def test_the_tabs_render_in_german(node_dir, node_data):
    """End to end: the two labels are real msgids — the page renders them in the request's language,
    while the instance NAME stays exactly as the operator typed it."""
    from services.webservice import i18n

    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _get(client, _page_url("dcs-a-01"))
    assert _labels(page) == ["Knotenweit", "Instanz Syria", "Instanz Caucasus",
                             "Instanz PersianGulf"], _labels(page)
    assert "Node-wide" not in page and "DEFAULT" not in page


# ============================================================= the extensions are CARD TILES

@pytest.mark.asyncio
async def test_extensions_render_as_card_tiles_scoped_to_their_tab(node_dir, node_data):
    """The extensions are drawn with the SETTINGS page's tile language (the shared `_tiles.html`
    macros): one ``st-tile ext`` card per CONFIGURED extension, plus the ONE ``+ Add`` tile holding
    the scope's not-yet-configured extensions — each scoped to the tab it is on, none crossing over.
    A configured card carries the SAME short description, slider, cogwheel and TRASHCAN a plugin tile
    does; the configuration itself lives behind the cogwheel, never inline."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    # the SHARED tile grid + the card shell + the kind pill come from the Settings page's macros
    assert '<div class="st-grid">' in default, "the extension cards are not the shared grid"
    assert '<div class="st-grid stack">' not in default, \
        "the extension cards are still stacked full-width instead of sharing the card grid"
    assert '<article class="st-tile ext' in default and '<article class="st-tile ext' in syria
    assert 'class="st-kind"' in default and 'class="st-name"' in default
    # a configured extension is a card with a cogwheel AND a trashcan (into its acknowledged removal)
    card = _ext_card(default, "RealWeather")
    assert card is not None
    assert 'class="st-cog"' in card[1]
    assert 'class="st-trash"' in card[1]
    # an INSTANCE-only extension's CARD is on that instance's tab only — never the node-wide tab
    assert _ext_card(syria, "Trackfile") is not None
    assert _ext_card(default, "Trackfile") is None
    # the ONE ``+ Add`` tile lists the not-yet-configured extensions of the tab's scope: the node-wide
    # tab offers the instance-scoped names too, and RealWeather is
    # an add choice on Syria
    assert "Trackfile" in _add_options(default)
    assert "RealWeather" in _add_options(syria)
    assert _add_options(default), "the node-wide tab offered no way to add an extension"


# ============================================================= helpers

def _instance_block(text: str, node: str, instance: str) -> str:
    """The raw text of one instance's block inside a node's ``instances:`` mapping."""
    body = _block(text, node)
    found = re.search(rf"(?m)^[ ]{{4}}{re.escape(instance)}:\n(?:[ ]{{6,}}.*\n|\n)*", body)
    assert found is not None, f"no block for {node}.{instance}"
    return found.group(0)


def _extension_block(text: str, node: str, extension: str) -> str:
    """The raw text of one extension's block inside a node's ``extensions:`` mapping."""
    body = _block(text, node)
    found = re.search(rf"(?m)^[ ]{{4}}{re.escape(extension)}:\n(?:[ ]{{6,}}.*\n|\n)*", body)
    assert found is not None, f"no extension block for {node}.{extension}"
    return found.group(0)
