"""THE ADD TILE NEVER VANISHES, AND THE NODE-WIDE SCOPE OFFERS THE INSTANCE-SCOPED NAMES.

the operator's two answers: "1. ok" — ALWAYS draw the add tile; and "2. I would keep them
also for the node-section as you might override stuff for all instances at a central place."

PART 1 — the tile is ALWAYS drawn. Today the template draws no add tile when a scope's option list is
empty, so on a node that configures EVERY extension its ``+ Add`` simply disappears. Here it is pinned
that a fully-configured scope STILL renders the tile, in an explicit, NON-INTERACTIVE "all configured"
state whose reason is VISIBLE text (never only a tooltip) — for BOTH the node-wide and an instance tab,
and for the Settings plugin add tile.

PART 2 — the node-wide scope offers the instance-scoped names too. The runtime HONOURS a node-level
block for an instance-scoped extension: ``load_extension`` (``core/data/impl/serverimpl.py:786-793``)
resolves an extension's config as the node-level block merged UNDER the instance-level one, for ANY
name (documented in ``extensions/README.md:105-111``). So the node-wide ``+ Add`` selection is the
UNION of the two schema sets minus what the scope configures (``LogAnalyser`` present), an INSTANCE
tab's set is UNCHANGED, and a node-wide write of an instance-scoped extension round-trips and survives
a post-save re-read.
"""
from __future__ import annotations

import re

import pytest

from conftest import asgi_client
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login
from test_webui_node_page import (_add_options, _app, _block, _ext_card,  # noqa: F401
                                  _page_url, addable, node_data, node_dir)

#: The two EXHAUSTIVE schema sets (the same lists the sibling cards pin).
NODE_EXTENSIONS = ["Lardoon", "LotAtc", "Olympus", "RealWeather", "SkyEye", "Sneaker", "SRS", "Tacview"]
INSTANCE_EXTENSIONS = ["DKS", "DSMC", "GitHub", "gRPC", "HoundTTS", "Lardoon", "LogAnalyser", "LotAtc",
                       "MizEdit", "ModManager", "Olympus", "Persistence", "Pretense", "RealWeather",
                       "SkyEye", "Sneaker", "SRS", "Tacview", "Trackfile", "VoiceChat"]
#: THE NODE-WIDE SET — the union of the two schema sets.
NODE_WIDE_EXTENSIONS = sorted(set(NODE_EXTENSIONS) | set(INSTANCE_EXTENSIONS))


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


# ------------------------------------------------------------------ a FULLY-CONFIGURED node fixture

def _fully_configured_nodes() -> str:
    """A ``nodes.yaml`` whose ``dcs-a-01`` configures EVERY node-wide extension AND whose ``Syria``
    instance configures EVERY instance extension — so BOTH scopes' option lists come out EMPTY.

    The blocks are deliberately MINIMAL (``{}``): this test is about the RENDER of the "all configured"
    state, and a render does not require valid block CONTENTS (validity is the SAVE's concern, and it
    is pinned by the switch tests). The names are generated from the schema sets, so the fixture can
    never drift from the schemas.
    """
    node_exts = "\n".join(f"    {name}: {{}}" for name in NODE_WIDE_EXTENSIONS)
    inst_exts = "\n".join(f"        {name}: {{}}" for name in NODE_WIDE_EXTENSIONS)
    return (
        "dcs-a-01:\n"
        "  listen_address: 127.0.0.1\n"
        "  listen_port: 10042\n"
        "  extensions:\n"
        f"{node_exts}\n"
        "  instances:\n"
        "    Syria:\n"
        "      home: /home/a-syria\n"
        "      bot_port: 6666\n"
        "      webgui_port: 8086\n"
        "      dcs_port: 10306\n"
        "      extensions:\n"
        f"{inst_exts}\n"
        "dcs-b-01:\n"
        "  listen_address: 127.0.0.1\n"
        "  listen_port: 10043\n"
        "  instances:\n"
        "    Nevada:\n"
        "      home: /home/b-nevada\n"
        "      bot_port: 6669\n"
        "      webgui_port: 8089\n"
        "      dcs_port: 10309\n"
    )


@pytest.fixture
def full_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(_fully_configured_nodes(), encoding="utf-8")
    (tmp_path / "servers.yaml").write_text("SRS-1:\n  node: dcs-a-01\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def full_data(full_dir, monkeypatch):
    """The same validator binding the sibling ``node_data`` fixture makes, but pointed at ``full_dir``.

    Replicated (not reused) so this module's crafted ``nodes.yaml`` — not the sibling fixture's — is the
    cluster the bot's validator sees, and the two fixtures' ``tmp_path`` uses cannot clash.
    """
    import types

    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(full_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


_EXT_ADD_TILE = re.compile(r'<article class="st-tile ext add">(.*?)</article>', re.DOTALL)


def _ext_add_tile(page: str) -> str:
    """The ONE extension ``+ Add`` tile's body, or ``""`` when it was not drawn."""
    match = _EXT_ADD_TILE.search(page)
    return match.group(1) if match else ""


# ===================================================== PART 1: a fully-configured scope keeps its tile

@pytest.mark.asyncio
async def test_a_fully_configured_node_wide_scope_still_draws_the_add_tile(full_dir, full_data):  # noqa: F811
    """``dcs-a-01`` configures EVERY node-wide extension, so there is nothing left to add — yet the
    ``+ Add`` tile is STILL drawn, in its explicit "all configured" state: NON-INTERACTIVE (no
    ``<details>``, no add control), and the reason is VISIBLE text (``st-add-none``), never only a
    tooltip."""
    app = _app(full_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    tile = _ext_add_tile(page)
    assert tile, "the add tile VANISHED on a fully-configured node-wide scope"
    assert _add_options(page) == [], "a fully-configured scope still offered add choices"
    assert "st-add-all" in tile, "the tile is not in its all-configured state"
    assert 'class="st-add-none"' in tile, "the reason is not visible text"
    assert node_settings_page.NODE_PAGE_EXTENSION_ALL_CONFIGURED in tile, \
        "the all-configured reason sentence is missing"
    # NON-INTERACTIVE: no expandable list and no add control in the tile
    assert "<details" not in tile and "st-swform" not in tile
    # every configured extension is still a CARD (the section is not empty)
    for name in NODE_WIDE_EXTENSIONS:
        assert _ext_card(page, name) is not None, f"{name} is not a card"


@pytest.mark.asyncio
async def test_a_fully_configured_instance_scope_still_draws_the_add_tile(full_dir, full_data):  # noqa: F811
    """The SAME for an INSTANCE tab: Syria configures every instance extension, so its ``+ Add`` tile
    is drawn in the "all configured" state too — the tile does not vanish silently on either scope."""
    app = _app(full_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01", tab=1))
    tile = _ext_add_tile(page)
    assert tile, "the add tile VANISHED on a fully-configured instance scope"
    assert _add_options(page) == []
    assert "st-add-all" in tile and 'class="st-add-none"' in tile
    assert node_settings_page.NODE_PAGE_EXTENSION_ALL_CONFIGURED in tile


# ===================================================== PART 1: the Settings plugin add tile, same rule

def _main_with_all_optionals() -> str:
    """A ``main.yaml`` that enables EVERY optional plugin, so the plugin add list comes out EMPTY."""
    from services.webservice import plugin_registry

    optionals = sorted(info.name for info in plugin_registry.discover_plugins() if not info.default)
    lines = "\n".join(f"  - {name}" for name in optionals)
    return ("guild_id: 112233445566\nguild_name: My Server\nlanguage: en\n"
            f"opt_plugins:\n{lines}\n")


@pytest.fixture
def all_plugins_dir(tmp_path):
    (tmp_path / "services").mkdir(exist_ok=True)
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(_main_with_all_optionals(), encoding="utf-8")
    return tmp_path


_PLUGIN_ADD_TILE = re.compile(r'<article class="st-tile plugin add">(.*?)</article>', re.DOTALL)


@pytest.mark.asyncio
async def test_an_empty_plugin_add_list_still_draws_the_add_tile(all_plugins_dir):
    """With EVERY optional plugin enabled, the plugin ``+ Add`` list is empty — the tile is STILL
    drawn, in the same explicit "all configured" state, with the reason as visible text."""
    app = _build(all_plugins_dir,
                 users=[{"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}])[0]
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
    match = _PLUGIN_ADD_TILE.search(page)
    assert match is not None, "the plugin add tile VANISHED when nothing was left to add"
    tile = match.group(1)
    assert "st-add-all" in tile and 'class="st-add-none"' in tile
    assert settings_page.PLUGIN_ADD_ALL_CONFIGURED in tile
    assert "<details" not in tile and "st-swform" not in tile
    assert re.findall(r'<div class="st-add-name"', tile) == [], "an add choice survived"


# ===================================================== PART 2: the node-wide union, and the round trip

@pytest.mark.asyncio
async def test_the_node_wide_add_list_is_the_union_minus_configured(full_dir, full_data):  # noqa: F811
    """``dcs-b-01`` configures NOTHING, so its node-wide ``+ Add`` list is the WHOLE union (19 names)
    MINUS the DEFAULT extensions — ``LogAnalyser`` and ``Cloud`` are ALWAYS cards, never add choices
    (they run regardless of the file). The other instance-scoped names stay addable,
    so ``Persistence`` (instance-scoped, not a default) IS offered. An INSTANCE tab keeps its own set:
    the instance schema minus that instance's own configured extensions and its defaults."""
    app = _app(full_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        node_wide = await _get(client, _page_url("dcs-b-01"))
        nevada = await _get(client, _page_url("dcs-b-01", tab=1))
    assert set(_add_options(node_wide)) == addable(NODE_WIDE_EXTENSIONS)
    assert "Persistence" in _add_options(node_wide), "the node-wide tab must offer instance-scoped names"
    assert "LogAnalyser" not in _add_options(node_wide), "a DEFAULT extension is a card, not an add choice"
    # the instance tab is UNCHANGED: exactly the instance schema minus the defaults (nothing configured)
    assert set(_add_options(nevada)) == addable(INSTANCE_EXTENSIONS)


@pytest.mark.asyncio
async def test_a_node_wide_write_of_an_instance_scoped_extension_round_trips(node_dir, node_data):  # noqa: F811
    """END TO END. Add ``Persistence`` (instance-scoped, NOT a default) on the NODE-WIDE tab of
    ``dcs-a-01`` through the add tile's OWN form; the empty block lands in the node's ``extensions``
    map; the file re-reads; and a POST-SAVE re-read shows it as a CONFIGURED CARD and NO LONGER an add
    choice — it did not silently vanish, and it did not stay offerable. Every other node/scope is
    byte-for-byte what it was.

    (The sibling ``full_dir`` fixture is deliberately schema-MINIMAL for the RENDER tests; a WRITE
    validates the whole document, so this test uses the standard, valid fixture.)
    """
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        # the add tile's OWN form for Persistence (never a hand-built one)
        body = _ext_add_tile(page)
        item = next(chunk for chunk in body.split('<li class="st-add-item">')[1:]
                    if re.search(r'<div class="st-add-name"[^>]*>Persistence</div>', chunk))
        form = re.search(r'<form class="st-swform" method="post" action="([^"]*)">(.*?)</form>',
                         item, re.DOTALL)
        assert form is not None, "the add tile offered no form for Persistence"
        import html as _html
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(2)))
        reply = await client.post(_html.unescape(form.group(1)), data=fields)
        assert reply.status_code == 303, reply.text
        # POST-SAVE RE-READ
        again = await _get(client, _page_url("dcs-a-01"))
    after = path.read_text(encoding="utf-8")
    assert "Persistence: {}" in _block(after, "dcs-a-01"), "the node-wide block was not written"
    # survives the re-read: a card now, and no longer an add choice
    assert _ext_card(again, "Persistence") is not None, "the written block vanished on re-read"
    assert "Persistence" not in _add_options(again), "the written extension was still offered"
    # the configured node extension and the other node are byte-for-byte their old selves
    assert "RealWeather" in _block(after, "dcs-a-01")
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
