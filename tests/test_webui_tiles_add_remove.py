"""THE TILES THAT CAN ADD AND REMOVE — the "+ Add" tile and the trashcan, on the ONE tile component.

some selection to select which one? And have a trashcan icon on the extensions also, to delete them
again? On the Settings page, I would also only show the optional plugins that are enabled. There we can
also have that '+' tile to select new optional plugins to be enabled. Can we also order the plugins
alphabetically?"

Pinned here, over the REAL application:

NODE PAGE
* each scope's extensions show as TILES only for what the scope CONFIGURES, plus ONE "+ Add" tile whose
  SELECTION is EXACTLY the scope's schema extensions that are not configured yet (``node_extensions_
  schema`` 8 / ``instance_extensions_schema`` 19);
* choosing one from the add tile writes EXACTLY that extension's empty block through the SAME save and
  the SAME scope guard the switch always used — every other node, scope and block byte-identical;
* the trashcan on a configured tile points at the SAME acknowledged removal path (the warning step);
  completing that path removes the block, and a removal WITHOUT the acknowledgement is still refused.

SETTINGS PAGE
* the DEFAULT plugins are always listed, the OPTIONAL ones only when ENABLED;
* the not-enabled optional plugins are the choices of the "+ Add" tile, and enabling one from it posts
  the SAME ``f.opt_plugins`` write the switch does;
* all plugin TILES are ordered ALPHABETICALLY by name, CASE-INSENSITIVELY (the TILE LIST, not the file).
"""
from __future__ import annotations

import html
import re

import pytest

from conftest import asgi_client
from services.webservice import plugin_registry
from services.webservice.pages import node_settings as node_settings_page
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login
from test_webui_node_page import (_add_options, _app, _block, _ext_card, _extension_block,  # noqa: F401
                                  _instance_block, _page_url, addable, node_data, node_dir)
from test_webui_node_page_extension_switch import _add_option_form
from test_webui_settings_node_scope import _csrf, _revision

#: the two EXHAUSTIVE schema sets (the same lists the sibling card pins).
NODE_EXTENSIONS = ["Lardoon", "LotAtc", "Olympus", "RealWeather", "SkyEye", "Sneaker", "SRS", "Tacview"]
INSTANCE_EXTENSIONS = ["DKS", "DSMC", "GitHub", "gRPC", "HoundTTS", "Lardoon", "LogAnalyser", "LotAtc",
                       "MizEdit", "ModManager", "Olympus", "Persistence", "Pretense", "RealWeather",
                       "SkyEye", "Sneaker", "SRS", "Tacview", "Trackfile", "VoiceChat"]
#: THE NODE-WIDE SET — the union of the two schema sets (every node name is also an
#: instance name, so it IS the instance set; the union is written explicitly so the test states the rule).
NODE_WIDE_EXTENSIONS = sorted(set(NODE_EXTENSIONS) | set(INSTANCE_EXTENSIONS))


async def _get(client, path):
    response = await client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    return response.text


# =============================================================== 1. the node add tile's SELECTION

@pytest.mark.asyncio
async def test_the_node_add_tile_lists_exactly_the_scopes_addable_set(node_dir, node_data):  # noqa: F811
    """Each scope's ``+ Add`` selection is EXACTLY the schema's set for that scope MINUS what the scope
    already configures. The fixture configures RealWeather at the node and Trackfile at Syria. The
    node-wide set is the UNION."""
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        default = await _get(client, _page_url("dcs-a-01"))
        syria = await _get(client, _page_url("dcs-a-01", tab=1))
    assert set(_add_options(default)) == addable(NODE_WIDE_EXTENSIONS) - {"RealWeather"}
    assert set(_add_options(syria)) == addable(INSTANCE_EXTENSIONS) - {"Trackfile"}
    # the add tile is the SAME component: an ``st-tile ext add`` card whose undiscovert choice is the
    # SAME ``st-sw`` switch form, posting the add action — never a second control
    assert '<article class="st-tile ext add">' in default
    assert 'name="_ext" value="SRS"' in default and 'value="add"' in default


# =============================================================== 2. adding writes exactly the block

@pytest.mark.asyncio
async def test_choosing_from_the_add_tile_writes_exactly_that_block(node_dir, node_data):  # noqa: F811
    """Posting the add tile's OWN choice form (not a hand-built one) writes that extension's block —
    seeded from its own dict's declared defaults — and leaves every other node and block byte-for-byte
    what it was."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        form = _add_option_form(page, "SRS")
        assert form, "the add tile offered no form for SRS"
        reply = await client.post(form["action"], data=form["fields"])
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "DCS-SimpleRadio-Standalone" in _extension_block(after, "dcs-a-01", "SRS"), \
        "the block was not written with the dict's defaults"
    assert "RealWeather" in _block(after, "dcs-a-01")            # the configured block is untouched
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    for instance in ("Syria", "Caucasus", "PersianGulf"):
        assert _instance_block(after, "dcs-a-01", instance) == \
            _instance_block(before, "dcs-a-01", instance), f"{instance} was rewritten"


# =============================================================== 3. the trashcan and the ack path

@pytest.mark.asyncio
async def test_the_trashcan_points_at_the_acknowledged_removal_and_removes(node_dir, node_data):  # noqa: F811
    """The trashcan on a configured tile links to its OWN confirmation dialog (never the switch's URL);
    the dialog mints the acknowledgement, and confirming removes exactly that block — nothing else
    changes."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        card = _ext_card(page, "RealWeather")
        assert card is not None
        link = re.search(r'<a class="st-trash" href="([^"]*)"', card[1])
        assert link is not None, "the configured tile carries no trashcan"
        href = html.unescape(link.group(1))
        assert href == node_settings_page.node_extension_remove_url("dcs-a-01", "RealWeather", 0), href
        # the dialog is a GET that writes nothing and mints the acknowledgement
        dialog = await _get(client, href)
        assert path.read_text(encoding="utf-8") == before
        form = re.search(r'<form id="dlg-form"[^>]*action="([^"]*)"[^>]*>(.*?)</form>',
                         dialog, re.DOTALL)
        fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                                 form.group(2)))
        assert fields[node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD] \
            == node_settings_page.NODE_PAGE_EXTENSION_REMOVE
        assert fields[node_settings_page.NODE_PAGE_EXTENSION_ACK_FIELD], "no acknowledgement minted"
        reply = await client.post(html.unescape(form.group(1)), data=fields)
    assert reply.status_code == 303, reply.text
    after = path.read_text(encoding="utf-8")
    assert "RealWeather" not in _block(after, "dcs-a-01"), "the block was not removed"
    assert _block(after, "dcs-b-01") == _block(before, "dcs-b-01")
    assert _instance_block(after, "dcs-a-01", "Syria") == _instance_block(before, "dcs-a-01", "Syria")


@pytest.mark.asyncio
async def test_a_removal_without_the_acknowledgement_is_refused(node_dir, node_data):  # noqa: F811
    """The trashcan's path is the SASME acknowledged one: a bare POST of the remove action (no warning
    rendered, no token) is REFUSED with nothing written."""
    path = node_dir / "nodes.yaml"
    before = path.read_text(encoding="utf-8")
    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
        reply = await client.post(
            f'{node_settings_page.node_page_url("dcs-a-01")}?tab=0',
            data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                  node_settings_page.NODE_PAGE_SCOPE_FIELD: node_settings_page.NODE_PAGE_SCOPE_DEFAULT,
                  node_settings_page.NODE_PAGE_EXTENSION_FIELD: "RealWeather",
                  node_settings_page.NODE_PAGE_EXTENSION_ACTION_FIELD:
                      node_settings_page.NODE_PAGE_EXTENSION_REMOVE,
                  node_settings_page.NODE_PAGE_EXTENSION_ACK_FIELD: ""})
        shown = await _get(client, _page_url("dcs-a-01"))
    assert reply.status_code == 303, reply.text
    assert path.read_text(encoding="utf-8") == before
    assert html.unescape(node_settings_page.SAVE_REFUSED_EXTENSION_ACK) in html.unescape(shown)


# =============================================================== 4. the Settings plugin tiles

MAIN = """\
guild_id: 112233445566
guild_name: My Server
language: en
opt_plugins:
  - motd
"""


@pytest.fixture
def settings_dir(tmp_path):
    # ``exist_ok``: a test may also request the NODE fixtures, which build the SAME ``tmp_path``'s
    # ``services/`` directory (with the very same ``BOT_YAML``); the two must coexist, not clash.
    (tmp_path / "services").mkdir(exist_ok=True)
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN, encoding="utf-8")
    return tmp_path


def _settings_app(d):
    return _build(d, users=[{"username": "staff", "password_hash": _hash(), "roles": ["Admin"]}])[0]


_PLUGIN_CARD = re.compile(r'<article class="st-tile plugin([^"]*)">(.*?)</article>', re.DOTALL)


def _plugin_tile_names(page: str) -> list[str]:
    """The names of the PLUGIN TILES in render order (the ``+ Add`` tile carries no ``st-name``)."""
    names = []
    for match in _PLUGIN_CARD.finditer(page):
        name = re.search(r'class="st-name">([^<]+)<', match.group(2))
        if name:
            names.append(name.group(1))
    return names


@pytest.mark.asyncio
async def test_settings_hides_disabled_optionals_and_offers_them_as_add_choices(settings_dir):
    """A DEFAULT plugin is always a tile; an OPTIONAL plugin is a tile only when ENABLED. The optional
    plugins that are not enabled are the choices of the ``+ Add`` tile, not tiles of their own."""
    app = _settings_app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
    shipped = plugin_registry.discover_plugins()
    defaults = {info.name for info in shipped if info.default}
    optionals = {info.name for info in shipped if not info.default}
    tiles = set(_plugin_tile_names(page))
    # every DEFAULT is a tile; motd (an ENABLED optional) is a tile
    assert defaults <= tiles, f"a default plugin is missing a tile: {defaults - tiles}"
    assert "motd" in tiles
    # no DISABLED optional is a tile ...
    assert not (optionals - {"motd"}) & tiles, "a disabled optional plugin still has a tile"
    # ... they are the add tile's choices instead
    add = re.search(r'<article class="st-tile plugin add">(.*?)</article>', page, re.DOTALL)
    assert add is not None, "the plugin + Add tile was not rendered"
    choices = set(re.findall(r'<div class="st-add-name"[^>]*>([^<]+)</div>', add.group(1)))
    assert choices == optionals - {"motd"}, "the add tile's selection is not the disabled optionals"


@pytest.mark.asyncio
async def test_settings_plugin_tiles_are_alphabetical_case_insensitively(settings_dir):
    """The TILE LIST — never the file — is ordered alphabetically by name, case-insensitively."""
    app = _settings_app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
    names = _plugin_tile_names(page)
    assert names == sorted(names, key=str.lower), names


@pytest.mark.asyncio
async def test_enabling_an_optional_from_the_add_tile_writes_opt_plugins(settings_dir):
    """A choice in the plugin ``+ Add`` tile enables its plugin exactly as the switch does: the SAME
    ``f.opt_plugins`` write through the SAME save route (no new route)."""
    app = _settings_app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
        add = re.search(r'<article class="st-tile plugin add">(.*?)</article>', page, re.DOTALL)
        assert add is not None
        # pick one disabled optional and post ITS choice form
        block = add.group(1)
        item = block.split('<li class="st-add-item">')[1]
        name = re.search(r'<div class="st-add-name"[^>]*>([^<]+)</div>', item).group(1)
        form = re.search(r'<form class="st-swform" method="post" action="([^"]*)">(.*?)</form>',
                         item, re.DOTALL)
        pairs = re.findall(r'name="([^"]+)" value="([^"]*)"', form.group(2))
        assert html.unescape(form.group(1)) == settings_page.SETTINGS_SAVE_PATH
        # the choice posts the SAME f.opt_plugins list the switch does (repeated fields, sentinel and
        # every current entry), so hand it through as httpx's list-valued mapping
        data = {k: v for k, v in pairs if k != "f.opt_plugins"}
        data["f.opt_plugins"] = [v for k, v in pairs if k == "f.opt_plugins"]
        reply = await client.post(settings_page.SETTINGS_SAVE_PATH, data=data)
    assert reply.status_code == 303, reply.text
    from core.utils.configwrite import load_config_file
    written = list(load_config_file(settings_dir / "main.yaml").get("opt_plugins"))
    assert name in written and "motd" in written, written


# =============================================================== 5. the add tile is translated

@pytest.mark.asyncio
async def test_the_add_tile_renders_in_german(node_dir, node_data):  # noqa: F811
    """The ``+ Add`` door is the console's OWN chrome and reuses the ``+ Add`` msgid every catalog
    already carries — so it renders in the request's language, end to end (no new catalog entry)."""
    from services.webservice import i18n

    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        client.cookies.set(i18n.COOKIE_NAME, "de")
        page = await _get(client, _page_url("dcs-a-01"))
    assert "+ Hinzufügen" in page
    assert "+ Add" not in page


# =============================================================== 6. the add list is a COMPACT list

#: The ONE ``+ Add`` list body — the SAME shape on the Settings plugin list and the node extension
#: list (both are the ONE ``add_tile`` macro).
_ADD_LIST = re.compile(r'<ul class="st-add-list">(.*?)</ul>', re.DOTALL)

#: ONE COMPACT row: the item's NAME (which carries the description as a TOOLTIP, see ``title`` /
#: ``aria-describedby``), the visually-hidden accessible copy of that description (empty when the
#: item has none — never invented text), then the item's OWN existing add form. No tile shell of any
#: kind between the name and the form, so a regression that brings back a tile-shaped row (extra
#: markup) simply stops matching and the row COUNT equality below fails.
_ADD_ROW = re.compile(
    r'<li class="st-add-item">\s*'
    r'<div class="st-add-name"[^>]*>([^<]+)</div>\s*'
    r'<span class="st-add-sr"[^>]*>([^<]*)</span>\s*'
    r'(<form class="st-swform" method="post" action="[^"]*">.*?</form>)\s*'
    r'</li>', re.DOTALL)


def _add_rows(page: str) -> list[tuple[str, str, str]]:
    """``(name, description-or-'', add form)`` for every row of the ONE ``+ Add`` list, in order.

    The description is the row's TOOLTIP source: the text of the visually-hidden ``st-add-sr`` copy
    the name's ``aria-describedby`` points at (the very text the native ``title`` also carries).
    """
    match = _ADD_LIST.search(page)
    return _ADD_ROW.findall(match.group(1)) if match else []


def _row_skeleton(row_html: str) -> str:
    """A row's markup with the OPERATOR DATA and the form BODY blanked — the pure row SHAPE.

    Blanking the form's body (not its shell) is exactly what makes the two kinds comparable: a plugin
    add form posts a whole ``f.opt_plugins`` list and an extension add form three fields, but the ROW
    they sit in must be the very same component. The name's attributes (``title``/``aria-describedby``
    appear only when an item has a description) are blanked too, so a described and an undescribed row
    compare as the SAME shape.
    """
    shape = re.sub(r'<div class="st-add-name"[^>]*>.*?</div>',
                   '<div class="st-add-name"></div>', row_html, flags=re.DOTALL)
    shape = re.sub(r'<span class="st-add-sr"[^>]*>.*?</span>',
                   '<span class="st-add-sr"></span>', shape, flags=re.DOTALL)
    shape = re.sub(r'(<form class="st-swform)"[^>]*>.*?(</form>)', r'\1">\2', shape, flags=re.DOTALL)
    # the template's indentation must not decide the shape: collapse the whitespace BETWEEN tags
    return re.sub(r">\s+<", "><", shape).strip()


@pytest.mark.asyncio
async def test_the_node_add_list_is_one_compact_row_per_extension(node_dir, node_data):  # noqa: F811
    """Every addable extension is ONE compact row: its name and, on the SAME line, its OWN README first
    line — the very source the extension TILES use — then the existing add form. Nothing else."""
    from services.webservice import catalog

    app = _app(node_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, _page_url("dcs-a-01"))
    rows = _add_rows(page)
    assert len(rows) == len(_add_options(page)), "an add choice did not render as a compact row"
    assert {name for name, _, _ in rows} == addable(NODE_WIDE_EXTENSIONS) - {"RealWeather"}
    summaries = {info.name.lower(): info.description for info in catalog.discover_extensions()}
    for name, description, _form in rows:
        # the row is operator data, ESCAPED by the template — unescape before comparing the source
        assert html.unescape(description) == summaries[name.lower()], \
            f"{name}'s description is not its README line"


@pytest.mark.asyncio
async def test_the_settings_add_list_pairs_each_plugin_with_its_own_summary(settings_dir):
    """Every not-enabled optional plugin is ONE compact row: its name plus the registry's OWN one-line
    summary (the SAME source the plugin TILES use). A plugin with NO summary shows none — never text."""
    app = _settings_app(settings_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _get(client, settings_page.SETTINGS_PATH)
    rows = _add_rows(page)
    shipped = plugin_registry.discover_plugins()
    optionals = {info.name for info in shipped if not info.default}
    assert {name for name, _, _ in rows} == optionals - {"motd"}
    summaries = {info.name.lower(): info.description for info in shipped}
    for name, description, _ in rows:
        assert html.unescape(description) == summaries[name.lower()], \
            f"{name}'s summary is not the registry's"
    # an EMPTY summary means NO description text (nothing invented), and the row is still compact
    community = next(row for row in rows if row[0] == "community")
    assert community[1] == ""
    assert "st-add-desc" not in page, "a clipped description line lives in the add row"
    assert _row_skeleton(_raw_row(page, "community")) == \
        '<li class="st-add-item"><div class="st-add-name"></div><span class="st-add-sr"></span><form class="st-swform"></form></li>'


def _raw_row(page: str, name: str) -> str:
    """The RAW ``<li>`` markup of the row whose name is *name* — name, description copy, form, close."""
    match = _ADD_LIST.search(page)
    assert match is not None, "the + Add list did not render"
    body = match.group(1)
    for chunk in body.split('<li class="st-add-item">')[1:]:
        row = '<li class="st-add-item">' + chunk.split("</li>")[0] + "</li>"
        if re.search(rf'<div class="st-add-name"[^>]*>{re.escape(name)}</div>', row):
            return row
    raise AssertionError(f"no add row for {name}")


@pytest.mark.asyncio
async def test_the_add_list_carries_no_tile_shell(node_dir, node_data, settings_dir):  # noqa: F811
    """The ``+ Add`` list is a LIST, never a wall of tiles: NO tile shell markup lives inside it — no
    ``st-tile`` shell, no kind head, no name/desc card classes, no cogwheel/trashcan control."""
    def list_body(page: str) -> str:
        match = _ADD_LIST.search(page)
        assert match is not None, "the + Add list did not render"
        return match.group(1)

    settings_app = _settings_app(settings_dir)
    async with asgi_client(settings_app) as client:
        await _login(client, "staff")
        settings_page_html = await _get(client, settings_page.SETTINGS_PATH)
    node_app = _app(node_dir)
    async with asgi_client(node_app) as client:
        await _login(client, "staff")
        node_page_html = await _get(client, _page_url("dcs-a-01"))

    for body in (list_body(settings_page_html), list_body(node_page_html)):
        for shell in ("st-tile", "st-kind", "st-top", "st-name", "st-desc", "st-foot",
                      "st-cog", "st-trash", "st-scope", "st-badge"):
            assert shell not in body, f"tile shell markup {shell!r} leaked into the + Add list"
        # one NAME element per row, no more, no less
        assert body.count('<div class="st-add-name"') == body.count('<li class="st-add-item">')


@pytest.mark.asyncio
async def test_the_add_row_shape_is_the_same_on_both_surfaces(node_dir, node_data,  # noqa: F811
                                                              settings_dir):
    """The plugin ``+ Add`` row and the extension ``+ Add`` row are the VERY SAME component — one name,
    the same optional description line, the same existing add control — because both come from the ONE
    ``add_tile`` macro. Each posts its OWN existing save path (byte-identity of that path checked by
    the add tests above)."""
    settings_app = _settings_app(settings_dir)
    async with asgi_client(settings_app) as client:
        await _login(client, "staff")
        settings_page_html = await _get(client, settings_page.SETTINGS_PATH)
    node_app = _app(node_dir)
    async with asgi_client(node_app) as client:
        await _login(client, "staff")
        node_page_html = await _get(client, _page_url("dcs-a-01"))

    plugin_row = _raw_row(settings_page_html, "community")
    ext_row = _raw_row(node_page_html, "SRS")
    # a described row on BOTH surfaces is the same shape (name + tooltip copy + form)
    assert _row_skeleton(_raw_row(settings_page_html, "backup")) == \
        _row_skeleton(ext_row)
    # ... and the undescribed shape matches too (an empty accessible description copy)
    assert _row_skeleton(plugin_row) == \
        '<li class="st-add-item"><div class="st-add-name"></div><span class="st-add-sr"></span><form class="st-swform"></form></li>'
    assert 'action="' + settings_page.SETTINGS_SAVE_PATH in plugin_row
    assert "action=" in ext_row
