"""THE NODE PAGE's EXTENSION CARDS — why they render as cards, and the write on them.

PART 1: the
node page's extensions are the SAME card tiles the Settings landing uses (``_tiles.html`` macros), in
the SAME ``st-grid`` card grid — NOT a stack of full-width rows. Pinned here so the two surfaces
cannot drift apart again.

PART 2 (the ADD form): the card SET comes from the SCHEMA (every extension a scope declares), the card
STATE comes from the FILE. Turning an UNCONFIGURED extension on writes that extension's EMPTY block
into the scope, through the SAME scoped save and the SAME whole-document patch, so the scope guard, the
no-op rule and the byte-identical-elsewhere promise all hold.

PART 3 (the ON/OFF switch): a CONFIGURED extension — default or not — is switched with the plain
``disable``/``enable`` actions, which WRITE ``enabled: false`` / ``enabled: true`` into the selected
scope's block and DELETE nothing. The runtime honours the key for every extension
(``core/extension.py``), and an instance INHERITS the node block's value (``load_extension`` merges the
node block UNDER the instance one). Removing a block is a different, destructive write and is behind
the confirmation dialog (``test_webui_node_page_extension_removal_dialog.py``).

PART 4 (the acknowledgement): a removal's token is minted by that dialog and is bound to the
(node, scope, extension) it showed and to the file revision it displayed. A bare POST, a forged token,
a token minted for another block, a replayed one and one minted before the file changed are all
refused with the file byte-identical.

PART 5 (what no control offers): a ``disable``/``enable`` writes INSIDE a block, so the selected scope
must already carry one for that name — or the name must be a DEFAULT extension, which runs with no
block and whose switch-off therefore CREATES one. A crafted switch action for a non-configured
non-default extension is REFUSED with nothing written.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from test_webui_dashboard import _login
from test_webui_node_page import (_add_options, _app, _block, _extension_block,  # noqa: F401
                                  _instance_block, _page_url, addable, node_data, node_dir)
from test_webui_settings_node_scope import _csrf, _revision

#: the 8 node-scoped extensions ``node_extensions_schema`` declares — an EXHAUSTIVE list.
NODE_EXTENSIONS = ["Lardoon", "LotAtc", "Olympus", "RealWeather", "SkyEye", "Sneaker", "SRS", "Tacview"]
#: the 20 instance-scoped extensions ``instance_extensions_schema`` declares — an EXHAUSTIVE list.
INSTANCE_EXTENSIONS = ["DKS", "DSMC", "GitHub", "gRPC", "HoundTTS", "Lardoon", "LogAnalyser", "LotAtc",
                       "MizEdit", "ModManager", "Olympus", "Persistence", "Pretense", "RealWeather",
                       "SkyEye", "Sneaker", "SRS", "Tacview", "Trackfile", "VoiceChat"]
#: THE NODE-WIDE SET — the union of the two schema sets.
NODE_WIDE_EXTENSIONS = sorted(set(NODE_EXTENSIONS) | set(INSTANCE_EXTENSIONS))


# ------------------------------------------------------------------------------- helpers

async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


_CARD = re.compile(r'<article class="st-tile ext([^"]*)">(.*?)</article>', re.DOTALL)


def _cards(page: str) -> list[dict]:
    """Every extension card on the page, as ``{classes, body}`` in render order."""
    return [{"classes": m.group(1), "body": m.group(2)} for m in _CARD.finditer(page)]


def _card(page: str, name: str) -> dict | None:
    """The card whose ``st-name`` is *name*, or ``None``."""
    for card in _cards(page):
        if f'class="st-name">{name}<' in card["body"]:
            return card
    return None


def _switch_form(page: str, name: str) -> dict:
    """The card's switch form: its action, its posted fields and its button label — or ``{}``."""
    card = _card(page, name)
    assert card is not None, f"no card for {name}"
    form = re.search(r'<form class="st-swform" method="post" action="([^"]*)"[^>]*>(.*?)</form>',
                     card["body"], re.DOTALL)
    if form is None:
        return {}
    fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(2)))
    return {"action": html.unescape(form.group(1)), "fields": fields}


_ADD_TILE = re.compile(r'<article class="st-tile ext add">(.*?)</article>', re.DOTALL)


def _add_body(page: str) -> str:
    """The ONE ``+ Add`` tile's inner body (the ``<details>`` and its list) — or ``""``."""
    match = _ADD_TILE.search(page)
    return match.group(1) if match else ""


def _add_options(page: str) -> list[str]:
    """The extension names offered as choices inside the ``+ Add`` tile, in render order."""
    return re.findall(r'<div class="st-add-name"[^>]*>([^<]+)</div>', _add_body(page))


def _add_option_form(page: str, name: str) -> dict:
    """The ``+ Add`` tile's form for *name*: its action and posted fields — or ``{}``."""
    for item in _add_body(page).split('<li class="st-add-item">')[1:]:
        if re.search(rf'<div class="st-add-name"[^>]*>{re.escape(name)}</div>', item):
            form = re.search(
                r'<form class="st-swform" method="post" action="([^"]*)"[^>]*>(.*?)</form>', item,
                re.DOTALL)
            if form is None:
                return {}
            fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(2)))
            return {"action": html.unescape(form.group(1)), "fields": fields}
    return {}


async def _switch(client, node: str, tab: int, name: str, action: str, scope: str, page: str,
                  ack: str | None = None):
    """POST the switch form for *name* on the node page (the SAME save path a plain save uses)."""
    data = {"_csrf_token": _csrf(page), "_revision": _revision(page),
            node_settings_page.NODE_PAGE_SCOPE_FIELD: scope,
            node_settings_page.NODE_PAGE_EXTENSION_FIELD: name,
            node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD: action}
    if ack is not None:
        data[node_settings_page.NODE_PAGE_EXTENSION_ACK_FIELD] = ack
    return await client.post(f"{node_settings_page.node_page_url(node)}?tab={tab}", data=data)


def _trash_link(page: str, name: str) -> str:
    """The configured card's trashcan href (the confirmation dialog's URL)."""
    card = _card(page, name)
    assert card is not None, f"no card for {name}"
    link = re.search(r'<a class="st-trash" href="([^"]*)"', card["body"])
    assert link is not None, f"{name}'s card carries no trashcan"
    return html.unescape(link.group(1))


async def _removal_token(client, node: str, tab: int, name: str) -> str:
    """Open *name*'s confirmation dialog and return the acknowledgement it minted."""
    page = await _get(client, _page_url(node, tab))
    dialog = await _get(client, _trash_link(page, name))
    form = re.search(r'<form id="dlg-form"[^>]*>(.*?)</form>', dialog, re.DOTALL)
    assert form is not None, "the dialog rendered no form"
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', form.group(1)))
    token = fields.get(node_settings_page.NODE_PAGE_EXTENSION_ACK_FIELD, "")
    assert token, "the dialog minted no acknowledgement"
    return token


#: A ``nodes.yaml`` whose node carries Olympus (a NON-default extension) with a real value, and whose
#: Syria instance configures the same extension — so the two scopes can be told apart.
OLYMPUS_NODES = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    Olympus:
      nodejs: /tmp
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        Olympus:
          backend:
            port: 4512
          frontend: {}
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


def _write_nodes(node_dir, text: str) -> None:
    """Replace the fixture's ``nodes.yaml`` with *text* (the page is fetched after this)."""
    (node_dir / "nodes.yaml").write_text(text, encoding="utf-8")


# =============================================================== PART 1: the cards, both scopes

@pytest.mark.asyncio
async def test_every_schema_extension_of_a_scope_is_a_card(node_dir, node_data):  # noqa: F811
    """Every schema extension of a scope is REACHABLE — a CONFIGURED one is a CARD of its own, and the
    ones the scope does NOT configure are the CHOICES of the ONE ``+ Add`` tile. The SET comes from the
    SCHEMA, so an extension ``nodes.yaml`` does not configure is STILL offered (it cannot silently
    vanish) — it is simply offered as an add choice rather than a dimmed card."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    for name in NODE_WIDE_EXTENSIONS:
        assert _card(default, name) is not None or name in _add_options(default), \
            f"node-wide extension {name} is neither a card nor an add choice"
    for name in INSTANCE_EXTENSIONS:
        assert _card(syria, name) is not None or name in _add_options(syria), \
            f"instance-scoped extension {name} is neither a card nor an add choice"
    # the fixture configures RealWeather at the NODE and Trackfile at the INSTANCE (Syria): those are
    # cards, and the selection of the ``+ Add`` tile is EXACTLY the remaining node-wide extensions
    # (MINUS the DEFAULT ones, which are always cards — LogAnalyser, Cloud)
    assert _card(default, "RealWeather") is not None
    assert set(addable(NODE_WIDE_EXTENSIONS)) - {"RealWeather"} == set(_add_options(default))
    assert _card(default, "LogAnalyser") is not None, "a default extension must be a card"
    assert _card(syria, "Trackfile") is not None
    assert set(addable(INSTANCE_EXTENSIONS)) - {"Trackfile"} == set(_add_options(syria))
    # and the CARD MARKUP is the shared tile language (a real card, not a bare row)
    assert '<article class="st-tile ext' in default and '<article class="st-tile ext' in syria


def test_the_extension_grid_is_the_settings_grid_and_is_not_stacked():
    """PART 1 at the SHEET level: the node page's extension cards use the Settings ``.st-grid`` (the
    card grid), and the old full-width ``.st-grid.stack`` rule — the one that made them full-width ROWS
    instead of cards — is GONE. Inside a card the field rows stack so the form fits the card's column."""
    sheet = (Path(__file__).resolve().parent.parent / "services" / "webservice" / "static"
             / "settings-scope.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", sheet, flags=re.DOTALL)
    assert ".st-grid.stack" not in body, "the full-width stack rule is still shipped"
    assert re.search(r"\.st-tile\s+\.st-row\s*\{", body), \
        "the extension card's field rows do not stack inside the card"


@pytest.mark.asyncio
async def test_present_is_on_and_absent_is_off(node_dir, node_data):  # noqa: F811
    """The card STATE comes from the FILE: ``RealWeather`` (configured on the node) is a CARD — no
    ``off`` class, a short description, a cogwheel AND a trashcan into its own removal; an extension the
    scope does NOT configure is not a card at all but an ADD CHOICE in the ``+ Add`` tile.

    THE NODE-WIDE TAB CARRIES NO SWITCH: ON/OFF belongs to the instance tab, so that card is the same
    card without a control — no form, no POST — and the state is the card's own (the ``off`` class).
    """
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    assert "off" not in _card(default, "RealWeather")["classes"].split()
    assert "off" not in _card(syria, "Trackfile")["classes"].split()
    # a configured card carries its short description, a cogwheel and a trashcan into its removal
    assert 'class="st-desc clamp2"' in _card(default, "RealWeather")["body"]
    assert 'class="st-cog"' in _card(default, "RealWeather")["body"]
    assert 'class="st-trash"' in _card(default, "RealWeather")["body"]
    # an UNCONFIGURED extension is an add CHOICE whose form posts the add action — never a dim card
    assert _card(default, "SRS") is None
    assert _add_option_form(default, "SRS")["fields"][node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
        == node_settings_page.NODE_PAGE_EXTENSION_ADD
    # the Node-wide card has NO switch at all; the instance tab's configured card is the turn-OFF form
    node_wide = _card(default, "RealWeather")
    assert node_wide is not None
    assert _switch_form(default, "RealWeather") == {}, "the Node-wide card grew a switch back"
    assert 'class="st-sw' not in node_wide["body"]
    form = _switch_form(syria, "Trackfile")
    assert form["fields"][node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
        == node_settings_page.NODE_PAGE_EXTENSION_DISABLE
    instance_card = _card(syria, "Trackfile")
    assert instance_card is not None
    assert node_settings_page.NODE_PAGE_EXTENSION_ENABLED in html.unescape(instance_card["body"])


@pytest.mark.asyncio
async def test_the_switch_posts_the_existing_scoped_save_path(node_dir, node_data):  # noqa: F811
    """No new route: the ``+ Add`` tile's choice forms post the node page's OWN save path (with its
    tab), and carry the scope carrier so the save knows which block they belong to."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
    form = _add_option_form(default, "SRS")
    assert form["action"] == f'{node_settings_page.node_page_url("dcs-a-01")}?tab=0'
    assert form["fields"][node_settings_page.NODE_PAGE_SCOPE_FIELD] \
        == node_settings_page.NODE_PAGE_SCOPE_DEFAULT


# =============================================================== PART 2: turning ON writes the block

@pytest.mark.asyncio
async def test_turning_a_node_extension_on_writes_exactly_that_block(node_dir, node_data):  # noqa: F811
    """A turn-ON on the Node-wide tab writes that extension's block, SEEDED from the ``default`` values
    its own ``NODE_CONFIG_DICT`` declares (SRS: ``installation``, ``beta``, ``autoupdate``) — never an
    invented value — and leaves every other node, scope and block byte-for-byte what it was.
    """
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "SRS",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    block = _extension_block(after, "dcs-a-01", "SRS")
    assert "DCS-SimpleRadio-Standalone" in block, f"the seeded default was not written: {block}"
    assert "beta: false" in block and "autoupdate: true" in block, block
    # every OTHER node is byte-for-byte what it was
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    # the node's OWN other blocks (RealWeather, and every instance) are untouched
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Syria", "Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"


@pytest.mark.asyncio
async def test_turning_an_instance_extension_on_writes_its_slot(node_dir, node_data):  # noqa: F811
    """An instance ADD writes that extension's block into THAT instance, seeded from its own
    ``CONFIG_DICT`` defaults (Persistence declares ``path``), and touches nothing else."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        reply = await _switch(client, "dcs-a-01", 1, "Persistence",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", page)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "path: Saves" in _instance_block(after, "dcs-a-01", "Syria")
    # the node's own extension block and the other instances are untouched
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    for instance in ("Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance)
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")


# =============================================================== PART 3: the non-destructive switch

@pytest.mark.asyncio
async def test_disabling_a_configured_extension_writes_enabled_false_inside_its_block(node_dir, node_data):  # noqa: F811
    """PIN 1. A node-wide ``disable`` on a CONFIGURED non-default extension writes ``enabled: false``
    INSIDE the existing block: the block's own values survive (``nodejs``), nothing else in the
    document changes, and the re-read shows the card OFF with a switch that posts ``enable``."""
    _write_nodes(node_dir, OLYMPUS_NODES)
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Olympus",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        assert reply.status_code == 303, reply.text
        after = path.read_text(encoding="utf-8")
        off_page = await _get(client, _page_url("dcs-a-01"))
    # the value is written INSIDE the block, and the block keeps its own key
    block = _extension_block(after, "dcs-a-01", "Olympus")
    assert "enabled: false" in block
    assert "nodejs: /tmp" in block, "the block's own value was discarded"
    # nothing else in the document moved: strip the ONE added line and the file is what it was
    assert after.replace("      enabled: false\n", "") == before, "another key of the file changed"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    # the re-read shows the RUNTIME state: OFF, and its block is intact
    card = _card(off_page, "Olympus")
    assert card is not None and "off" in card["classes"].split(), "the card is not shown OFF"
    assert _switch_form(off_page, "Olympus") == {}, "the Node-wide card grew a switch back"


@pytest.mark.asyncio
async def test_enabling_it_again_writes_enabled_true_and_keeps_every_other_key(node_dir, node_data):  # noqa: F811
    """PIN 3. ``enable`` after a ``disable`` writes ``enabled: true`` in place and keeps every other key
    of the block — the switch round-trips without ever deleting the block."""
    _write_nodes(node_dir, OLYMPUS_NODES)
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "Olympus",
                      node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        off_page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Olympus",
                              node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, off_page)
        on_page = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    block = _extension_block(path.read_text(encoding="utf-8"), "dcs-a-01", "Olympus")
    assert "enabled: true" in block
    assert "nodejs: /tmp" in block
    assert "off" not in _card(on_page, "Olympus")["classes"].split()


@pytest.mark.asyncio
async def test_a_disable_that_changes_nothing_leaves_the_file_byte_identical(node_dir, node_data):  # noqa: F811
    """PIN 8. A ``disable`` on an already-disabled block changes nothing: the no-op rule keeps the file
    byte-identical and the operator is told so."""
    _write_nodes(node_dir, OLYMPUS_NODES)
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "Olympus",
                      node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        before = path.read_text(encoding="utf-8")
        off_page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Olympus",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, off_page)
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a no-op disable rewrote the file"


@pytest.mark.asyncio
async def test_an_instance_tab_shows_the_nodes_disable_and_writes_its_own_enable(node_dir, node_data):  # noqa: F811
    """PIN 4. A configured NON-default extension whose NODE block says ``enabled: false`` renders OFF on
    the instance tab (the inherited value — ``load_extension`` merges the node block UNDER the instance
    one), and its switch posts ``enable`` INTO that instance's block, which then wins."""
    _write_nodes(node_dir, OLYMPUS_NODES)
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        await _switch(client, "dcs-a-01", 0, "Olympus",
                      node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                      node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
        card = _card(syria, "Olympus")
        assert card is not None
        assert "off" in card["classes"].split(), "the instance did not inherit the node's disable"
        form = _switch_form(syria, "Olympus")
        assert form["fields"][node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
            == node_settings_page.NODE_PAGE_EXTENSION_ENABLE
        # the switch writes into THIS tab's scope (the save reads the tab it was posted to)
        assert form["action"].endswith("?tab=1"), form["action"]
        reply = await _switch(client, "dcs-a-01", 1, "Olympus",
                              node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                              node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Syria", syria)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    instance_block = _instance_block(after, "dcs-a-01", "Syria")
    assert "enabled: true" in instance_block
    assert "port: 4512" in instance_block, "the instance block lost its own values"
    assert "enabled: false" in _extension_block(after, "dcs-a-01", "Olympus"), \
        "the instance override rewrote the node's own block"


# =============================================== the schema: every node extension accepts ``enabled``

NODE_EXTENSION_ENABLED_DOCS = {
    "Lardoon": "      cmd: C:/lardoon.exe\n      minutes: 5\n",
    "LotAtc": "      autoupdate: true\n",
    "Olympus": "      nodejs: /tmp\n",
    "RealWeather": "      installation: /tmp\n",
    "SkyEye": "      installation: /tmp\n",
    "Sneaker": "      cmd: \"{file}\"\n      bind: 0.0.0.0:8080\n",
    "SRS": "      autoupdate: true\n",
    "Tacview": "      autoupdate: true\n",
}


def _enabled_document(node_dir, name: str, *, with_enabled: bool) -> str:
    """A ``nodes.yaml`` configuring ONE node extension, with or without ``enabled: false``."""
    extra = NODE_EXTENSION_ENABLED_DOCS[name]
    lines = extra.replace("{file}", (node_dir / "sneaker.exe").as_posix())
    body = f"    {name}:\n"
    if lines:
        body += lines
    if with_enabled:
        body += "      enabled: false\n"
    return ("NODE1:\n  listen_address: 127.0.0.1\n  listen_port: 10042\n"
            f"  extensions:\n{body}")


@pytest.mark.asyncio
async def test_every_node_extension_schema_accepts_the_enabled_key(node_dir, node_data):  # noqa: F811
    """PIN 5. ``enabled`` is a declared key of every one of the eight node extension schemas: a
    ``nodes.yaml`` carrying ``extensions: <Name>: {enabled: false}`` (beside the values the schema
    REQUIRES) validates, driven through the bot's OWN validator
    (``core/utils/validators.py:validate``) over ``schemas/nodes_schema.yaml`` plus every
    ``extensions/**/*_schema.yaml``. The CONTROL is the same document WITHOUT ``enabled`` — also valid,
    so the pin cannot pass by refusing everything."""
    from core.utils import validators

    repo = Path(__file__).resolve().parent.parent
    schema_files = ([str(repo / "schemas" / "nodes_schema.yaml")]
                    + sorted(str(path) for path in (repo / "extensions").rglob("*_schema.yaml")))
    (node_dir / "sneaker.exe").write_text("", encoding="utf-8")
    doc = node_dir / "nodes.yaml"
    for name in NODE_EXTENSIONS:
        for with_enabled in (True, False):
            doc.write_text(_enabled_document(node_dir, name, with_enabled=with_enabled),
                           encoding="utf-8")
            try:
                validators.validate(str(doc), schema_files, raise_exception=True)
            except Exception as ex:  # noqa: BLE001 - any refusal is the pinned failure
                pytest.fail(f"{name} (enabled={with_enabled}) was refused: {ex}")


# =============================================================== PART 2: the guard + the no-op rule

@pytest.mark.asyncio
async def test_a_switch_naming_a_foreign_scope_or_extension_is_refused(node_dir, node_data):  # noqa: F811
    """A switch naming something the SELECTED scope cannot write is refused, with NOTHING written:
    (b) a scope carrier naming a tab this node does not have; (c) a name that is not a schema
    extension at all."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        # (b) a scope carrier naming a tab this node does not have
        bad_scope = await _switch(client, "dcs-a-01", 0, "SRS",
                                  node_settings_page.NODE_PAGE_EXTENSION_ADD,
                                  node_settings_page.NODE_PAGE_SCOPE_INSTANCE_PREFIX + "Nope", page)
        # (c) an extension that is not a schema extension at all
        unknown = await _switch(client, "dcs-a-01", 0, "NoSuchExtension",
                                node_settings_page.NODE_PAGE_EXTENSION_ADD,
                                node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert bad_scope.status_code == unknown.status_code == 303
    assert path.read_text(encoding="utf-8") == before, "a refused switch wrote to the file"


@pytest.mark.asyncio
async def test_a_switch_naming_an_instance_scoped_extension_at_the_node_wide_scope_is_accepted(
        node_dir, node_data):  # noqa: F811
    """THE WIDENING. ``Persistence`` is instance-scoped, and the runtime HONOURS a node-level block for
    it (``core/data/impl/serverimpl.py`` merges the node-level block UNDER the instance-level one, for
    any name). So a node-wide switch may name it: the empty block is written to the node's OWN
    ``extensions`` map, and every other node, scope and block is byte-for-byte what it was."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Persistence",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "Persistence: {}" in _block(after, "dcs-a-01"), "the node-wide block was not written"
    assert _extension_block(after, "dcs-a-01", "RealWeather") == \
        _extension_block(before, "dcs-a-01", "RealWeather")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    for instance in ("Syria", "Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"


@pytest.mark.asyncio
async def test_a_noop_switch_leaves_the_file_byte_identical(node_dir, node_data):  # noqa: F811
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        # add an already-present extension, and try to remove an absent one (with no acknowledgement,
        # so it is refused rather than silently dropped) — both leave the file untouched
        add = await _switch(client, "dcs-a-01", 0, "RealWeather",
                            node_settings_page.NODE_PAGE_EXTENSION_ADD,
                            node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        remove = await _switch(client, "dcs-a-01", 0, "SRS",
                               node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                               node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack="")
    assert add.status_code == remove.status_code == 303
    assert path.read_text(encoding="utf-8") == before, "a no-op switch rewrote the file"


@pytest.mark.asyncio
async def test_turning_on_an_extension_whose_empty_block_is_invalid_is_refused(node_dir, node_data):  # noqa: F811
    """The EMPTY block is still governed by the SCHEMA: an extension whose schema REQUIRES a value
    (RealWeather's ``installation``) cannot be turned on with an empty block, so the save is REFUSED
    (visibly) and NOTHING is written — the switch never fabricates a value the schema demands."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-b-01"))
        reply = await _switch(client, "dcs-b-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_ADD,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a refused add wrote to the file"


# =============================================================== the shape-conservative add

CRAFTED_NODES = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  extensions:
    SRS: 7
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
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


def test_the_add_helper_refuses_a_non_mapping_block_slot():
    """``_add_extension_block`` only ever writes into an ABSENT slot; a slot that already holds a
    non-mapping (a scalar, a list, an explicit ``None``) is REFUSED by the helper itself, not left to
    the schema validator downstream — so a value at the block slot can never be replaced first and
    rejected second."""
    from ruamel.yaml.comments import CommentedMap

    from core.utils import configwrite

    # the FINAL slot holds a scalar -> refused, the scalar untouched
    doc = CommentedMap({"n": CommentedMap({"extensions": CommentedMap({"SRS": 7})})})
    with pytest.raises(configwrite.ConfigWriteError):
        node_settings_page._add_extension_block(doc, ("n", "extensions", "SRS"))
    assert doc["n"]["extensions"]["SRS"] == 7, "the helper replaced a non-mapping value"

    # an explicit None is a VALUE too, not an absent key -> refused
    doc_none = CommentedMap({"n": CommentedMap({"extensions": CommentedMap({"SRS": None})})})
    with pytest.raises(configwrite.ConfigWriteError):
        node_settings_page._add_extension_block(doc_none, ("n", "extensions", "SRS"))
    assert doc_none["n"]["extensions"]["SRS"] is None

    # an INTERMEDIATE token that holds a non-mapping -> refused as well
    doc_mid = CommentedMap({"n": CommentedMap({"extensions": 5})})
    with pytest.raises(configwrite.ConfigWriteError):
        node_settings_page._add_extension_block(doc_mid, ("n", "extensions", "SRS"))
    assert doc_mid["n"]["extensions"] == 5

    # the helper's NORMAL job still works: an absent slot is created empty, intermediates included
    doc_new = CommentedMap({"n": CommentedMap()})
    node_settings_page._add_extension_block(doc_new, ("n", "extensions", "SRS"))
    assert doc_new["n"]["extensions"]["SRS"] == {}


@pytest.mark.asyncio
async def test_a_crafted_add_against_a_non_mapping_slot_is_refused(node_dir, node_data):  # noqa: F811
    """END TO END: a ``nodes.yaml`` whose extension slot holds a scalar (``SRS: 7``) is not overwritten
    by the turn-ON switch — the add is REFUSED (the helper's own refusal, before any write) and the
    file stays BYTE-IDENTICAL, the ``7`` intact."""
    from core.utils import configwrite

    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))   # a real CSRF token from the real page
        token = _csrf(page)
        path.write_text(CRAFTED_NODES, encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        reply = await client.post(
            f'{node_settings_page.node_page_url("dcs-a-01")}?tab=0',
            data={"_csrf_token": token,
                  "_revision": configwrite.config_revision(path),
                  node_settings_page.NODE_PAGE_SCOPE_FIELD: node_settings_page.NODE_PAGE_SCOPE_DEFAULT,
                  node_settings_page.NODE_PAGE_EXTENSION_FIELD: "SRS",
                  node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD:
                      node_settings_page.NODE_PAGE_EXTENSION_ADD})
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a crafted add replaced a non-mapping slot"
    assert "SRS: 7" in before
    # the refusal is the HELPER's own sentence (not a downstream schema rejection), and it names the
    # reason: nothing was added because a non-mapping value is in the way
    assert "was not added" in html.unescape(shown)


# =============================================== PART 4: the removal's acknowledgement

@pytest.mark.asyncio
async def test_a_remove_without_the_acknowledgement_is_refused_and_writes_nothing(node_dir, node_data):  # noqa: F811
    """A direct POST (``_ext`` + ``_ext_action=remove``, valid CSRF and revision) that never opened the
    confirmation dialog must NOT remove a value-carrying block. The save REFUSES with nothing written,
    and says so — the "never a silent write" claim is a SERVER rule, not a UI one."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack="")
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a remove with no acknowledgement wrote the file"
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)
    assert "RealWeather" in _extension_block(before, "dcs-a-01", "RealWeather")  # still there


@pytest.mark.asyncio
async def test_a_forged_or_blank_acknowledgement_is_refused(node_dir, node_data):  # noqa: F811
    """A token that the session never minted — a guess, or the dialog form's own field left empty — is
    refused exactly like an absent one. The acknowledgement is server-side and unguessable."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack="not-a-real-token")
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a forged acknowledgement wrote the file"


@pytest.mark.asyncio
async def test_a_token_minted_for_one_block_cannot_remove_another(node_dir, node_data):  # noqa: F811
    """The token is bound to the (node, scope, extension) the dialog showed. A token minted for the
    INSTANCE's block (Trackfile on Syria) cannot be spent on the NODE-wide block (RealWeather): the
    lookup is by binding, so a token for a different block simply is not found — nothing is removed."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        foreign = await _removal_token(client, "dcs-a-01", 1, "Trackfile")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack=foreign)
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a foreign acknowledgement removed a block"
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)


@pytest.mark.asyncio
async def test_the_valid_acknowledgement_is_what_lets_the_removal_through(node_dir, node_data):  # noqa: F811
    """The positive half: with the token the dialog minted for THAT block, the removal goes through and
    removes exactly it. (The sibling test proves the write; this pins that the token was the
    difference — the same POST without it was refused just above.)"""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        token = await _removal_token(client, "dcs-a-01", 0, "RealWeather")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack=token)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "RealWeather" not in _block(after, "dcs-a-01"), "the block was not removed"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")


def _recreate_realweather(text: str, installation: str) -> str:
    """Put a DIFFERENT ``RealWeather`` block back into the node's block (the attacker's step 2).

    Step 1's removal leaves the node's ``extensions`` map EMPTY (``extensions: {}``), so the block is
    re-created by replacing that empty map — inserting a second ``extensions:`` key would be invalid.
    """
    block = ("  extensions:\n    RealWeather:\n"
             f"      installation: {installation}\n      autoupdate: false\n")
    if "  extensions: {}\n" in text:
        return text.replace("  extensions: {}\n", block)
    return text.replace("  listen_port: 10042\n", "  listen_port: 10042\n" + block)


@pytest.mark.asyncio
async def test_the_reviewers_replay_sequence_is_refused_with_the_file_byte_identical(node_dir, node_data):  # noqa: F811
    """THE BLOCKING REPLAY, verified end to end. Step 1 mints the acknowledgement for the block holding
    ``/opt/rw-a`` and SPENDS it (the block is removed). Step 2 re-creates the block on disk with
    DIFFERENT values (``/opt/rw-b``) and the operator opens the node page FRESH, so its ``_revision``
    legitimately matches the new file. Step 3 re-posts the OLD, already-spent token with the fresh
    page's revision. It must now be REFUSED, and the file must be BYTE-IDENTICAL — nobody was ever
    shown ``/opt/rw-b``, so nothing may be discarded."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        # 1. mint the acknowledgement for /opt/rw-a, then SPEND it (the removal goes through)
        spent = await _removal_token(client, "dcs-a-01", 0, "RealWeather")
        page = await _get(client, _page_url("dcs-a-01"))
        first = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack=spent)
        assert first.status_code == 303, first.text
        after_first = path.read_text(encoding="utf-8")
        assert "RealWeather" not in _block(after_first, "dcs-a-01"), "step 1 did not remove its block"
        # 2. the block is RE-CREATED with DIFFERENT values; a fresh page matches the NEW file
        recreated = _recreate_realweather(after_first, "/opt/rw-b")
        path.write_text(recreated, encoding="utf-8")
        before_attack = path.read_text(encoding="utf-8")
        assert "/opt/rw-b" in before_attack
        fresh = await _get(client, _page_url("dcs-a-01"))   # fresh GET: revision == the new file
        # 3. re-post the OLD, already-spent token with the FRESH page's revision
        attack = await _switch(client, "dcs-a-01", 0, "RealWeather",
                               node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                               node_settings_page.NODE_PAGE_SCOPE_DEFAULT, fresh, ack=spent)
        shown = await _get(client, _page_url("dcs-a-01"))
    assert attack.status_code == 303, attack.text
    assert path.read_text(encoding="utf-8") == before_attack, \
        "the replay removed contents nobody was shown"
    assert "/opt/rw-b" in _block(path.read_text(encoding="utf-8"), "dcs-a-01")
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)


@pytest.mark.asyncio
async def test_a_token_minted_before_a_change_cannot_authorise_a_write_after_it(node_dir, node_data):  # noqa: F811
    """The REVISION BINDING specifically — the half the one-time deletion would NOT catch. A token is
    minted for the ``/opt/rw-a`` block but NEVER spent (the operator turned away without confirming);
    the file's contents then change (``/opt/rw-b``). The fresh page's revision matches the new file, so
    the revision guard cannot refuse — only the token's own revision binding can. It must be refused
    with the file byte-identical: the acknowledgement authorises the contents it was DISPLAYED for."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        unspent = await _removal_token(client, "dcs-a-01", 0, "RealWeather")   # NOTE: never spent
        before = path.read_text(encoding="utf-8")
        changed = before.replace("/opt/rw-a", "/opt/rw-b")
        assert changed != before
        path.write_text(changed, encoding="utf-8")
        fresh = await _get(client, _page_url("dcs-a-01"))   # revision matches the NEW file
        attack = await _switch(client, "dcs-a-01", 0, "RealWeather",
                               node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                               node_settings_page.NODE_PAGE_SCOPE_DEFAULT, fresh, ack=unspent)
    assert attack.status_code == 303, attack.text
    assert path.read_text(encoding="utf-8") == changed, \
        "a token minted before the change authorised a write after it"
    assert "/opt/rw-b" in _block(path.read_text(encoding="utf-8"), "dcs-a-01")


@pytest.mark.asyncio
async def test_a_spent_acknowledgement_cannot_be_used_a_second_time(node_dir, node_data):  # noqa: F811
    """The ONE-TIME half: once a removal has been accepted, the same token is dead. An immediate second
    POST of it (against a fresh page, so the revision is current) is REFUSED, and the file does not
    change again."""
    path = node_dir / "nodes.yaml"
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        token = await _removal_token(client, "dcs-a-01", 0, "RealWeather")
        page = await _get(client, _page_url("dcs-a-01"))
        first = await _switch(client, "dcs-a-01", 0, "RealWeather",
                              node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page, ack=token)
        assert first.status_code == 303, first.text
        after_first = path.read_text(encoding="utf-8")
        fresh = await _get(client, _page_url("dcs-a-01"))
        # the SAME token, spent once already
        second = await _switch(client, "dcs-a-01", 0, "RealWeather",
                               node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                               node_settings_page.NODE_PAGE_SCOPE_DEFAULT, fresh, ack=token)
        shown = await _get(client, _page_url("dcs-a-01"))
    assert second.status_code == 303, second.text
    assert path.read_text(encoding="utf-8") == after_first, "a spent token authorised a second write"
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)


def test_the_ack_binding_cannot_collide_on_the_separator():
    """THE DELIMITER NITPICK. Under a bare ``"\\x1f".join`` the node key ``"A\\x1finstance:B"`` in
    DEFAULT scope and node ``"A"`` with instance ``"B\\x1fdefault"`` encode the SAME string — one
    acknowledgement could then be spent on the other's block. Length-prefixing each part makes the two
    tuples encode two DIFFERENT strings, so no two distinct tuples can ever collide."""
    left = node_settings_page._extension_ack_binding("A\x1finstance:B", "default", "E")
    right = node_settings_page._extension_ack_binding("A", "instance:B\x1fdefault", "E")
    assert left != right, "the length-prefixed binding still collides"
    # what the two tuples USED to produce (identical) — the collision this fix closes
    assert "\x1f".join(("A\x1finstance:B", "default", "E")) == \
        "\x1f".join(("A", "instance:B\x1fdefault", "E"))


# ================================== PART 5: the switch never CREATES a non-default extension's block

@pytest.mark.asyncio
async def test_a_disable_of_an_extension_the_scope_does_not_configure_is_refused(node_dir, node_data):  # noqa: F811
    """A ``disable``/``enable`` writes INSIDE a block, so there must BE one: ``Olympus`` is a
    non-default extension the fixture's node does not configure, and no control posts a switch action
    for it (its card is an ``+ Add`` choice). The crafted POST is REFUSED — the file is byte-identical,
    no block appears, and the extension is still offered in ``+ Add`` because nothing was created."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        assert "Olympus" in _add_options(page)
        reply = await _switch(client, "dcs-a-01", 0, "Olympus",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        assert reply.status_code == 303, reply.text
        shown = await _get(client, _page_url("dcs-a-01"))
        after = path.read_text(encoding="utf-8")
    assert after == before, "a refused switch created the extension's block"
    assert "Olympus" not in _block(after, "dcs-a-01"), "an empty block was written"
    assert "Olympus" in _add_options(shown), "the extension stopped being offered as a choice"
    assert html.unescape(node_settings_page.NODE_PAGE_EXTENSION_UNCONFIGURED) in html.unescape(shown)


@pytest.mark.asyncio
async def test_a_disable_of_a_name_configured_on_ANOTHER_scope_is_refused(node_dir, node_data):  # noqa: F811
    """The guard reads the SELECTED scope's ``extensions`` map, never any scope's: ``Trackfile`` is
    configured on the Syria INSTANCE, so a NODE-wide ``disable`` of it is a write nothing offers —
    refused, and the instance's own block is untouched."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await _switch(client, "dcs-a-01", 0, "Trackfile",
                              node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                              node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before, "a refused switch wrote to the file"


@pytest.mark.asyncio
async def test_CONTROL_a_default_extension_with_no_block_still_gets_its_disable_written(
        node_dir, node_data):  # noqa: F811
    """CONTROL. ``LogAnalyser`` is a DEFAULT extension: it runs with NO block, so it has no ``extensions``
    entry to write into and the guard above must NOT catch it — its switch-off CREATES the block with
    ``enabled: false`` (the write the loader reads), and its switch-on writes ``enabled: true`` back.
    This is the primary caller of :func:`_set_extension_enabled`."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        off = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                            node_settings_page.NODE_PAGE_EXTENSION_DISABLE,
                            node_settings_page.NODE_PAGE_SCOPE_DEFAULT, page)
        assert off.status_code == 303, off.text
        after_off = path.read_text(encoding="utf-8")
        off_page = await _get(client, _page_url("dcs-a-01"))
        on = await _switch(client, "dcs-a-01", 0, "LogAnalyser",
                           node_settings_page.NODE_PAGE_EXTENSION_ENABLE,
                           node_settings_page.NODE_PAGE_SCOPE_DEFAULT, off_page)
        assert on.status_code == 303, on.text
    assert "LogAnalyser:" in _block(after_off, "dcs-a-01"), "the default's switch-off wrote no block"
    assert "enabled: false" in _extension_block(after_off, "dcs-a-01", "LogAnalyser")
    # the operator was told the write went through, never the unconfigured refusal
    shown = html.unescape(off_page)
    assert node_settings_page.NODE_PAGE_EXTENSION_TURNED_OFF % {"name": "LogAnalyser"} in shown
    assert node_settings_page.NODE_PAGE_EXTENSION_UNCONFIGURED not in shown
    assert "enabled: true" in _extension_block(path.read_text(encoding="utf-8"),
                                               "dcs-a-01", "LogAnalyser")
