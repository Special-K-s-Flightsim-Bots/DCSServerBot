"""THE ADD PICKER — the options come from the SCHEMA for the scope, minus what is configured.

looks like as if the first node defines which extensions are there and all others can only add these
extensions as a maximum. I also do not see standard extensions like LogAnalyser in there." Plus: "The
description of the plugins can not be read it is cut after some characters. Can we make that
tooltips?" - "the 'Load off' can vanish in that 'Add' tile".

Pinned here, over the REAL app:

* the ``+ Add`` selection of a scope is EXACTLY that scope's SCHEMA extensions
  (``node_extensions_schema`` 8 / ``instance_extensions_schema`` 19) MINUS the ones the scope itself
  configures — NEVER the set another node happens to configure. A scope that configures NOTHING still
  renders an add tile offering its whole schema set (an empty list, not a design, used to hide it);
* an INSTANCE-scoped extension the scope does not configure — ``LogAnalyser`` — is always offered on
  an instance tab, even when NO node in the file configures it;
* the item's description is a TOOLTIP on its NAME (a native ``title`` AND a visually-hidden copy the
  name's ``aria-describedby`` reaches, so it is readable without a mouse) — never a clipped line;
* the redundant STATE WORD ("Load off" / "Not configured") is gone from the add rows — an add choice
  is off by definition, and only the icon remains (still named by title + aria-label);
* choosing an item still writes EXACTLY that block through the SAME save, every other block
  byte-identical;
* the plugin ``+ Add`` list is the DISABLED OPTIONAL plugins, read from the registry.
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice import catalog, plugin_registry
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _login
from test_webui_node_page import (_add_options, _app, _block, _ext_card,  # noqa: F401
                                  _instance_block, _page_url, addable, node_data, node_dir)
from test_webui_settings_node_scope import _csrf, _revision

#: The two EXHAUSTIVE schema sets (the same lists the sibling cards pin).
NODE_EXTENSIONS = ["Lardoon", "LotAtc", "Olympus", "RealWeather", "SkyEye", "Sneaker", "SRS", "Tacview"]
INSTANCE_EXTENSIONS = ["DKS", "DSMC", "GitHub", "gRPC", "HoundTTS", "Lardoon", "LogAnalyser", "LotAtc",
                       "MizEdit", "ModManager", "Olympus", "Persistence", "Pretense", "RealWeather",
                       "SkyEye", "Sneaker", "SRS", "Tacview", "Trackfile", "VoiceChat"]
#: THE NODE-WIDE SET: the node schema's names PLUS the instance schema's — the operator's
#: decision, so an instance-scoped extension (LogAnalyser) can be set once for every instance. Every
#: node-scoped name is also instance-scoped, so the union IS the instance set here (the test pins the
#: union, not a coincidence, so a future schema that breaks it still passes).
NODE_WIDE_EXTENSIONS = sorted(set(NODE_EXTENSIONS) | set(INSTANCE_EXTENSIONS))


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


_ADD_LIST = re.compile(r'<ul class="st-add-list">(.*?)</ul>', re.DOTALL)
#: ONE add row: the name div (with the optional tooltip attributes), the visually-hidden description
#: copy, then the ONE control. The description is the row's TOOLTIP source and is NEVER a visible
#: clipped element inside the row (there is no ``st-add-desc``), so a regression that brings a clipped
#: line back simply stops matching. The control is a POST form when one click can write the block, and
#: a LINK to the extension's own page when the block cannot pass that page's schema from defaults —
#: either way it is ONE control and the row carries no field.
_ADD_ROW = re.compile(
    r'<li class="st-add-item">\s*'
    r'<div class="st-add-name"([^>]*)>([^<]+)</div>\s*'
    r'<span class="st-add-sr"[^>]*>([^<]*)</span>\s*'
    r'(<form class="st-swform" method="post" action="[^"]*">.*?</form>|<a class="st-sw"[^>]*>.*?</a>)\s*'
    r'</li>', re.DOTALL)


def _add_body(page: str) -> str:
    match = _ADD_LIST.search(page)
    return match.group(1) if match else ""


def _add_rows(page: str) -> list[dict]:
    """Every add row: its name, its name-div attributes, its description copy and its control."""
    return [{"attrs": a, "name": n, "description": d, "form": f}
            for a, n, d, f in _ADD_ROW.findall(_add_body(page))]


# =========================================================================== 1. the options source

@pytest.mark.asyncio
async def test_a_node_never_limited_by_another_nodes_extensions(node_dir, node_data):  # noqa: F811
    """``dcs-a-01`` configures RealWeather at the node; ``dcs-b-01`` configures NOTHING. Each ``+ Add``
    selection is ITS OWN scope's schema MINUS what THAT scope configures and MINUS the DEFAULT
    extensions (which are always cards) — so ``dcs-b-01``'s is the WHOLE node-wide set minus the
    defaults, never ``dcs-a-01``'s smaller set."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        a = await _get(client, _page_url("dcs-a-01"))
        b = await _get(client, _page_url("dcs-b-01"))
    assert set(_add_options(a)) == addable(NODE_WIDE_EXTENSIONS) - {"RealWeather"}
    assert set(_add_options(b)) == addable(NODE_WIDE_EXTENSIONS), \
        "the node that configures nothing was limited by another node"


@pytest.mark.asyncio
async def test_a_default_extension_is_a_card_not_an_add_option(node_dir, node_data):  # noqa: F811
    """``LogAnalyser`` is a DEFAULT extension: the runtime loads it even with no ``nodes.yaml`` block,
    so it is a CARD (ON, "default" badge) — NEVER an add choice that implies it is off. ``Cloud`` too
    (it has no schema at all, yet it runs). An instance tab and the node-wide tab agree."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        nevada = await _get(client, _page_url("dcs-b-01", tab=1))
        node_wide = await _get(client, _page_url("dcs-b-01"))
    for page in (nevada, node_wide):
        assert _ext_card(page, "LogAnalyser") is not None, "a default extension is not a card"
        assert "LogAnalyser" not in _add_options(page), "a default extension was offered as addable"
        assert "Cloud" not in _add_options(page), "a schema-less default was offered as addable"
    assert set(_add_options(nevada)) == addable(INSTANCE_EXTENSIONS)


@pytest.mark.asyncio
async def test_the_node_wide_tab_offers_the_instance_scoped_names_too(node_dir, node_data):  # noqa: F811
    """the operator's decision: the NODE-wide tab OFFERS the instance-scoped names too — an
    operator can set one once for every instance. ``Persistence`` (instance-scoped, not a default) is an
    add choice on the node-wide tab; the DEFAULT names (``LogAnalyser``) are cards, not choices. An
    INSTANCE tab still offers EXACTLY the instance schema minus what it configures and its defaults."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        node_tab = await _get(client, _page_url("dcs-a-01"))
        inst_tab = await _get(client, _page_url("dcs-a-01", tab=1))
    # the node-wide tab offers the instance-scoped names (widened) ...
    assert "Persistence" in _add_options(node_tab), "the node-wide tab must offer instance-scoped names"
    assert "Trackfile" in _add_options(node_tab)
    assert set(_add_options(node_tab)) == addable(NODE_WIDE_EXTENSIONS) - {"RealWeather"}
    # ... and the INSTANCE tab's set is UNCHANGED: the instance schema minus its own and the defaults
    assert "Persistence" in _add_options(inst_tab)
    assert set(_add_options(inst_tab)) == addable(INSTANCE_EXTENSIONS) - {"Trackfile"}


# =========================================== 2. a scope that configures nothing still has a tile

@pytest.mark.asyncio
async def test_the_scope_that_configures_nothing_renders_an_add_tile(node_dir, node_data):  # noqa: F811
    """An EMPTY list — not a design — used to hide the tile. ``dcs-b-01`` configures no node
    extension, so it has no CONFIGURED extension card, yet its ``+ Add`` tile is rendered with the
    whole (non-default) schema set inside it."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        b = await _get(client, _page_url("dcs-b-01"))
    assert _ext_card(b, "RealWeather") is None, "dcs-b-01 must not carry a RealWeather card"
    assert '<article class="st-tile ext add">' in b, "the add tile was hidden by an empty list"
    assert len(_add_rows(b)) == len(addable(NODE_WIDE_EXTENSIONS))


# ===================================== 3. the description is the tooltip, never a clipped line

@pytest.mark.asyncio
async def test_the_description_is_the_tooltip_on_the_name_not_a_clipped_line(node_dir, node_data):  # noqa: F811
    """Each row's description is the TOOLTIP on its NAME: the name carries a ``title`` AND an
    ``aria-describedby`` pointing at a visually-hidden copy with the SAME text (reachable without a
    mouse — not a bare title). No clipped/truncated description element sits in the row."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    summaries = {info.name.lower(): info.description for info in catalog.discover_extensions()}
    rows = _add_rows(page)
    assert rows, "no add rows rendered"
    for row in rows:
        expected = summaries[row["name"].lower()]
        assert expected, f"{row['name']} has no README description in the fixture"
        # the description lives as the native tooltip AND is the accessible copy's text
        title = re.search(r'title="([^"]*)"', row["attrs"])
        assert title is not None and html.unescape(title.group(1)) == expected, row["attrs"]
        assert "tabindex=" in row["attrs"], "the tooltip name is not keyboard reachable"
        assert "aria-describedby=" in row["attrs"], "the tooltip is not reachable without a mouse"
        assert html.unescape(row["description"]) == expected, "the accessible copy is not the source"
    # no truncated element (the old clamp) survives anywhere in the add list
    body = _add_body(page)
    assert "st-add-desc" not in body and "clamp2" not in body, "a clipped line is back in the row"


# ============================================================ 4. the state word is dropped

@pytest.mark.asyncio
async def test_the_add_rows_carry_no_state_word(node_dir, node_data):  # noqa: F811
    """An add choice is OFF by definition, so the redundant state word ("Load off" / "Not configured")
    is gone from the add rows — only the icon remains, still named by title + aria-label."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    body = _add_body(page)
    assert "Load off" not in body and "Not configured" not in body, "the state word is back"
    for row in _add_rows(page):
        assert ">Load off<" not in row["form"] and ">Not configured<" not in row["form"]
        # the control is still NAMED, and still never by a state: an extension's add control says
        # "Add" in its own visible word (it writes a block and enables nothing), a plugin's stays
        # icon-only and is named by title + aria-label
        assert 'title="' in row["form"], row["form"]
        assert 'aria-label="' in row["form"] or ">Add<" in row["form"], row["form"]


# ==================================== 5. adding writes exactly that block, everything else identical

@pytest.mark.asyncio
async def test_adding_an_instance_extension_writes_exactly_that_instance_block(node_dir, node_data):  # noqa: F811
    """Adding ``Persistence`` (instance-scoped, not a default) to an instance writes EXACTLY its block
    through the SAME save — seeded from its own ``CONFIG_DICT`` defaults — and the node's own
    extension, the other instances and the other node stay byte-for-byte what they were."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
        row = next(r for r in _add_rows(page) if r["name"] == "Persistence")
        form = re.search(r'action="([^"]*)"', row["form"])
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', row["form"]))
        reply = await client.post(html.unescape(form.group(1)), data=fields)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "path: Saves" in _instance_block(after, "dcs-a-01", "Syria"), "the block was not written"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _instance_block(after, "dcs-a-01", "Caucasus") == \
        _instance_block(before, "dcs-a-01", "Caucasus")
    assert _instance_block(after, "dcs-a-01", "PersianGulf") == \
        _instance_block(before, "dcs-a-01", "PersianGulf")


# ================================= 6. the plugin add list is exactly the disabled optionals

MAIN = """\
guild_id: 112233445566
guild_name: My Server
language: en
opt_plugins:
  - motd
"""


@pytest.fixture
def settings_dir(tmp_path):
    (tmp_path / "services").mkdir(exist_ok=True)
    from test_webui_dashboard import BOT_YAML
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN, encoding="utf-8")
    return tmp_path


def _settings_app(d):
    from test_webui_dashboard import _build, _hash
    return _build(d, users=[{"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}])[0]


_PLUGIN_ADD = re.compile(r'<article class="st-tile plugin add">(.*?)</article>', re.DOTALL)


@pytest.mark.asyncio
async def test_the_plugin_add_list_is_exactly_the_disabled_optionals(settings_dir):
    """The plugin ``+ Add`` selection is the shipped plugins that are OPTIONAL and NOT enabled — read
    from the registry, never a hand-kept list; motd is enabled, so it is a tile, not a choice."""
    app = _settings_app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
    shipped = plugin_registry.discover_plugins()
    optionals = {info.name for info in shipped if not info.default}
    match = _PLUGIN_ADD.search(page)
    assert match is not None, "the plugin + Add tile was not rendered"
    choices = set(re.findall(r'<div class="st-add-name"[^>]*>([^<]+)</div>', match.group(1)))
    assert choices == optionals - {"motd"}, "the plugin add list is not the disabled optionals"
    # the SAME component: descriptions are tooltips here too, and the state word is gone
    body = match.group(1)
    assert "Load off" not in body and "st-add-desc" not in body, "the plugin add row differs"
    summaries = {info.name.lower(): info.description for info in shipped}
    for row in _add_rows(page):
        summary = summaries.get(row["name"].lower(), "")
        if summary:
            assert "aria-describedby=" in row["attrs"], row["attrs"]
            title = re.search(r'title="([^"]*)"', row["attrs"])
            assert title is not None and html.unescape(title.group(1)) == html.unescape(row["description"])
        else:
            assert row["description"] == "", "an undescribed plugin row invented a description"
