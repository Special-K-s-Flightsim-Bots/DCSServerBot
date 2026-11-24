"""THE COGWHEEL'S TABBED CONFIG VIEW (Settings slice 2) — pinned through the REAL application.

Every plugin, service and extension file has the SAME shape: a ``DEFAULT`` block plus zero or more
node/instance OVERRIDE blocks. This slice shows it as TABS (DEFAULT first, one per block), with each
tab's keys printed RELATIVE to the tab (no ``default.x.y.z``), the blocks COLLAPSIBLE, and a
``+ Add block`` door that WRITES the block into the YAML through the one write primitive.

Pinned here:
* a file with DEFAULT + two node/instance blocks renders THREE tabs with the right keys per tab;
* adding a block writes the documented shape (``<node>: {<instance>: {}}``) and refuses an unknown
  instance (the bot's own validator);
* an unchanged save is BYTE-IDENTICAL;
* collapsing cannot lose a value (the server renders every field; the collapse is client-side);
* a plugin and a service each reach their own view;
* a service whose schema declares NO node/instance block (a flat ``bot``/``backup`` schema, or a bare
  ``DEFAULT`` block) reaches the SAME tabbed view — never the older single-form page — with an honest
  "one scope, no blocks" note and no ``+ Add block`` door;
* the tab strip is server-side links, so it works with JavaScript off.
"""
from __future__ import annotations

import html
import re
import types
from pathlib import Path

#: the repository root, resolved from this file - never a hardcoded home directory.
_REPO = Path(__file__).resolve().parents[1]

import pytest

from conftest import asgi_client
from services.webservice import configgen, scopes
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import _build, _hash, _login

BOT_YAML = """\
roles:
  Admin:
    - specialk
  DCS Admin:
    - mod1
  DCS:
    - member1
"""

NODES_YAML = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
    Caucasus:
      home: /home/a-caucasus
  extensions:
    RealWeather:
      installation: "D:/DCS/realweather"
      autoupdate: true
dcs-b-01:
  listen_address: 127.0.0.1
  listen_port: 10043
  instances:
    Nevada:
      home: /home/b-nevada
      extensions:
        RealWeather:
          enabled: true
"""

SERVERS_YAML = """\
SRS-1:
  node: dcs-a-01
"""

MISSION_YAML = """\
DEFAULT:
  messages:
    ban_username: "banned"
    ban_evasion: "evade"
  event_filter:
    - connect
dcs-a-01:
  Syria:
    messages:
      ban_username: "syria banned"
"""

MUSIC_YAML = """\
DEFAULT:
  music_dir: "D:/Music"
  pause_without_players: true
dcs-a-01:
  Syria:
    music_dir: "D:/Music2"
"""


@pytest.fixture
def scope_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "mission.yaml").write_text(MISSION_YAML, encoding="utf-8")
    (tmp_path / "services" / "music.yaml").write_text(MUSIC_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(NODES_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(scope_dir, monkeypatch):
    """Make the bot's own validator see THIS install's cluster.

    ``core.utils.validators.NodeData`` (pykwalify's extension) reads ``nodes.yaml``/``servers.yaml``
    from ``COMMAND_LINE_ARGS.config``; the binding lives on the validators module, and the singleton is
    reset so it reads THIS config dir rather than whatever a previous test left. Restored afterwards.
    """
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(scope_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


def _app(scope_dir):
    return _build(scope_dir, users=[{"username": "staff", "password_hash": _hash(),
                                     "roles": ["Admin"]}])[0]


def _csrf(page: str) -> str:
    return re.search(r'name="_csrf_token" value="([^"]*)"', page).group(1)


def _revision(page: str) -> str:
    return re.search(r'name="_revision" value="([^"]*)"', page).group(1)


# ======================================================== 1. the tab model (three tabs, right keys)

def test_default_plus_two_blocks_renders_three_tabs_with_relative_keys():
    schema = configgen.load_schema(
        str(_REPO / "plugins/mission/schemas/mission_schema.yaml"),
        schema_files=configgen.schema_files_for(
            str(_REPO / "plugins/mission/schemas/mission_schema.yaml")))
    tree = configgen.describe(schema)
    from core.utils import configwrite
    document = configwrite.load_config_text(MISSION_YAML)
    tabs = scopes.build_scopes(tree, document, ["dcs-a-01", "dcs-b-01"])

    assert [tab["label"] for tab in tabs] == ["DEFAULT", "dcs-a-01 + Syria"], \
        [tab["label"] for tab in tabs]
    # the DEFAULT tab shows its own block plus the file's root literals
    default_names = {scopes.display_name(f, tabs[0]["base"]) for f in tabs[0]["fields"]}
    assert "messages" in default_names and "commands" in default_names
    # the override tab shows the element shape, keyed RELATIVE to the tab — no `default.` prefix
    override_names = {scopes.display_name(f, tabs[1]["base"]) for f in tabs[1]["fields"]}
    assert "messages" in override_names and "uploads" in override_names
    assert not any(name.lower().startswith("default.") for name in default_names | override_names)


def test_single_node_install_labels_tabs_with_the_plain_instance_name():
    schema = configgen.load_schema(
        str(_REPO / "services/music/schemas/music_schema.yaml"),
        schema_files=configgen.schema_files_for(
            str(_REPO / "services/music/schemas/music_schema.yaml")))
    tree = configgen.describe(schema)
    from core.utils import configwrite
    document = configwrite.load_config_text(
        "DEFAULT:\n  debug: false\nSyria:\n  debug: true\nCaucasus:\n  chat: radio\n")
    tabs = scopes.build_scopes(tree, document, ["dcs-a-01"])
    assert [tab["label"] for tab in tabs] == ["DEFAULT", "Syria", "Caucasus"]


# ================== 1b. an ``any_of`` container — which cases are a STRUCTURE and which a CHOICE
#
# ``func: any_of`` names the blocks a key may carry. In every shipped schema the pair is
# ``element_schema`` / ``instance_schema`` — which is NOT a choice but the Node => Instance structure of
# a config file, so the SCOPE model reads it (DEFAULT and a node's instance carry the element block,
# keyed by the real document path) and the key decides the shape. A container offering two PLAIN maps
# is the genuine choice, and only there does the block picker apply.

#: a schema shaped like the shipped ones: a flat element block and an instance block that is a KEYED
#: MAP (``regex;(.+)`` of elements) — a node's block.
ANY_OF_STRUCTURE_SCHEMA = """\
schema;instance_schema:
  type: map
  func: is_node
  nullable: false
  desc: Applies to one instance only.
  mapping:
    regex;(.+):
      include: 'element_schema'

schema;element_schema:
  type: map
  func: is_element
  nullable: false
  desc: Applies to every instance.
  mapping:
    timezone:
      type: str
      nullable: false
    speed:
      type: int
      nullable: false

type: map
nullable: false
mapping:
  DEFAULT:
    include: 'element_schema'
  regex;(.+):
    type: any
    func: any_of
    nullable: false
    enum: ['element_schema', 'instance_schema']
"""

#: the OTHER case: two plain flat maps, neither of them an element/instance pair — a real choice.
ANY_OF_CHOICE_SCHEMA = """\
schema;fast_schema:
  type: map
  nullable: false
  desc: The fast block.
  mapping:
    rpm:
      type: int
      nullable: false

schema;slow_schema:
  type: map
  nullable: false
  desc: The slow block.
  mapping:
    rpm:
      type: int
      nullable: false

type: map
nullable: false
mapping:
  DEFAULT:
    include: 'fast_schema'
  regex;(.+):
    type: any
    func: any_of
    nullable: false
    enum: ['fast_schema', 'slow_schema']
"""

#: the brief's own example, verbatim in shape: DEFAULT, one instance, one node with two instances.
NODE_INSTANCE_YAML = """\
DEFAULT:
  timezone: UTC
  speed: 10
instance01:
  timezone: UTC
  speed: 20
node01:
  instance02:
    timezone: UTC
    speed: 30
  instance03:
    timezone: Europe/Berlin
    speed: 40
"""


def _synthetic_schema(tmp_path, text: str):
    path = tmp_path / "synthetic_schema.yaml"
    path.write_text(text, encoding="utf-8")
    return configgen.describe(configgen.load_schema(str(path)))


def test_an_element_instance_any_of_is_the_node_instance_structure_not_a_choice(tmp_path):
    from core.utils import configwrite

    tree = _synthetic_schema(tmp_path, ANY_OF_STRUCTURE_SCHEMA)
    document = configwrite.load_config_text(NODE_INSTANCE_YAML)
    tabs = scopes.build_scopes(tree, document, ["node01", "node02"])

    assert [tab["label"] for tab in tabs] == [
        "DEFAULT", "instance01", "node01 + instance02", "node01 + instance03"], \
        [tab["label"] for tab in tabs]
    kinds = {tab["label"]: tab["kind"] for tab in tabs}
    assert kinds["DEFAULT"] == "default"
    assert kinds["instance01"] == "instance"
    assert kinds["node01 + instance02"] == "node"
    # the ELEMENT block, at the block's REAL path — never ``node01.timezone`` beside the node map
    by_label = {tab["label"]: tab for tab in tabs}
    assert [f.name for f in by_label["node01 + instance02"]["fields"]] == \
        ["node01.instance02.timezone", "node01.instance02.speed"]
    assert [f.name for f in by_label["DEFAULT"]["fields"]] == ["DEFAULT.timezone", "DEFAULT.speed"]


def test_one_node_prints_the_block_under_its_plain_instance_name(tmp_path):
    from core.utils import configwrite

    tree = _synthetic_schema(tmp_path, ANY_OF_STRUCTURE_SCHEMA)
    tabs = scopes.build_scopes(tree, configwrite.load_config_text(NODE_INSTANCE_YAML), ["node01"])
    assert [tab["label"] for tab in tabs] == ["DEFAULT", "instance01", "instance02", "instance03"]


def test_a_block_that_is_a_nested_keyed_map_offers_no_picker(tmp_path):
    """The point of the guard: an ``instance_schema`` block holds a KEYED MAP, so no row can name its
    fields — the picker is WITHHELD rather than offering a block the operator could not fill in."""
    tree = _synthetic_schema(tmp_path, ANY_OF_STRUCTURE_SCHEMA)
    container = scopes.override_container(tree)
    assert container is not None
    assert configgen.entry_blocks(container) == []


def test_two_plain_maps_are_a_choice_and_the_picker_offers_both_with_their_sentences(tmp_path):
    from core.utils import configwrite

    tree = _synthetic_schema(tmp_path, ANY_OF_CHOICE_SCHEMA)
    container = scopes.override_container(tree)
    assert container is not None
    blocks = configgen.entry_blocks(container)
    assert [block["key"] for block in blocks] == ["fast_schema", "slow_schema"]
    # each block is LABELLED by the sentence its schema carries, and carries the fields a row fills in
    assert [block["desc"] for block in blocks] == ["The fast block.", "The slow block."]
    assert [[rel for _leaf, rel in block["leaves"]] for block in blocks] == [["rpm"], ["rpm"]]
    # ... and WHICH block an entry carries is read from the entry's own data
    entries = configgen.custom_entries(container, configwrite.load_config_text("job1:\n  rpm: 10\n"))
    assert [(entry["key"], entry["block"]) for entry in entries] == [("job1", "fast_schema")]


# ============================================================= 2. the rendered plugin cogwheel page

@pytest.mark.asyncio
async def test_the_plugin_cogwheel_renders_the_tabs_and_omits_the_default_prefix(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get("/settings/plugin/mission")
    assert response.status_code == 200, response.text[:400]
    page = response.text
    assert 'class="st-tabs"' in page
    assert '>DEFAULT<' in page and "dcs-a-01 + Syria" in page
    # the tab strip is server-side links (works with JS off)
    assert 'href="/settings/plugin/mission?tab=1"' in page
    # a field on the DEFAULT tab: printed key is relative, the form name is the real path
    assert 'class="key">messages.ban_username</span>' in page
    assert 'name="f.DEFAULT.messages.ban_username"' in page
    assert 'class="key">DEFAULT.' not in page  # no field is printed with the prefix


# ===================================================== 3. collapsing cannot lose a value

@pytest.mark.asyncio
async def test_a_collapsed_block_still_carries_its_value_and_saves_byte_identical(scope_dir, node_data):
    before = (scope_dir / "plugins" / "mission.yaml").read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/mission")).text
        # a block is a native <details> ... and the field inside it is still rendered (server-side),
        # so a collapse (a client-side hide) can never drop the value.
        assert '<details class="st-block"' in page
        assert 'name="f.DEFAULT.messages.ban_username"' in page
        # a save that posts the file's own current values changes nothing
        reply = await client.post("/actions/settings/config/plugin/mission",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_expert": "0",
                                        "f.DEFAULT.messages.ban_username": "banned"})
    assert reply.status_code == 303, reply.text
    assert (scope_dir / "plugins" / "mission.yaml").read_bytes() == before, \
        "an unchanged scoped save rewrote the file"


# ============================================================= 4. an unchanged save is byte-identical

@pytest.mark.asyncio
async def test_an_untouched_scoped_save_is_byte_identical(scope_dir, node_data):
    before = (scope_dir / "plugins" / "mission.yaml").read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/mission?tab=1")).text
        reply = await client.post("/actions/settings/config/plugin/mission",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_expert": "0"})
    assert reply.status_code == 303, reply.text
    assert (scope_dir / "plugins" / "mission.yaml").read_bytes() == before


# ============================================================= 5. + Add block writes the block

@pytest.mark.asyncio
async def test_adding_a_block_writes_the_documented_node_instance_shape(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/mission")).text
        reply = await client.post("/actions/settings/addblock/plugin/mission",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_node": "dcs-a-01", "_instance": "Caucasus"})
    assert reply.status_code == 303, reply.text
    text = (scope_dir / "plugins" / "mission.yaml").read_text(encoding="utf-8")
    # the documented shape: the node block gains the instance's (empty) block
    assert re.search(r"dcs-a-01:\n  Syria:", text)
    assert re.search(r"Caucasus: \{\}", text), text
    # and the new tab now renders
    async with asgi_client(app) as client:
        await _login(client, "staff")
        after = (await client.get("/settings/plugin/mission")).text
    assert "dcs-a-01 + Caucasus" in after


@pytest.mark.asyncio
async def test_adding_a_block_with_an_unknown_instance_is_refused_and_writes_nothing(scope_dir, node_data):
    before = (scope_dir / "plugins" / "mission.yaml").read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/mission")).text
        reply = await client.post("/actions/settings/addblock/plugin/mission",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_node": "dcs-a-01", "_instance": "DoesNotExist"})
        landed = (await client.get("/settings/plugin/mission")).text
    assert reply.status_code == 303, reply.text
    assert (scope_dir / "plugins" / "mission.yaml").read_bytes() == before, \
        "a refused add-block wrote the file"
    assert "No block was added" in landed


# ============================================== 6. a plugin and a service each reach their view

@pytest.mark.asyncio
async def test_the_two_kinds_reach_their_own_scoped_view(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        plugin = await client.get("/settings/plugin/mission")
        service = await client.get("/settings/service/music")
    assert plugin.status_code == 200, plugin.text[:300]
    assert service.status_code == 200, service.text[:300]
    assert 'class="st-tabs"' in plugin.text and "dcs-a-01 + Syria" in plugin.text
    assert 'class="st-tabs"' in service.text and "dcs-a-01 + Syria" in service.text
    # a service field renders with its TAB-RELATIVE key — never the ``DEFAULT.``-prefixed full name —
    # and, when that relative key is byte-identical to the key already printed in bold above it, the
    # row does not repeat it on a second line (it is the duplicate the schema prose replaced).
    assert '<div class="nm">music_dir</div>' in service.text
    assert 'class="key">music_dir</span>' not in service.text, \
        "the row does not repeat the key it already printed in bold above"


@pytest.mark.asyncio
async def test_the_tile_cogwheels_point_at_the_scoped_views(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        landing = (await client.get("/settings")).text
    assert "/settings/plugin/mission" in landing
    # extensions left Settings: no extension cogwheel is offered any more
    assert "/settings/extension/RealWeather" not in landing
    # main.yaml keeps its own file page
    assert 'href="/settings/main/main"' in landing


# ================================================= 7. the scoped save refuses an unplaceable field

@pytest.mark.asyncio
async def test_an_unplaceable_field_refuses_the_scoped_save_and_writes_nothing(scope_dir, node_data):
    before = (scope_dir / "plugins" / "mission.yaml").read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/mission")).text
        reply = await client.post("/actions/settings/config/plugin/mission",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_expert": "0", "f.NOPE.not.a.field": "x"})
    assert reply.status_code == 303, reply.text
    assert (scope_dir / "plugins" / "mission.yaml").read_bytes() == before


# ================== 8. a service with NO node/instance blocks uses the SAME tabbed view as a plugin
#
# no override container (a flat ``bot``/``backup`` schema, or a bare ``DEFAULT`` block such as
# ``webservice``/``dashboard``) used to fall through to the OLDER single-form page (``cfg-layout``)
# while a plugin always got the tabbed one. is_scoped() now selects the renderer by the file's KIND,
# so EVERY plugin/service file with a schema gets the tabbed view (see pages/settings.is_scoped).

DASHBOARD_YAML = """\
DEFAULT:
  servers:
    hide_remote_servers: false
    background: white on navy_blue
    border: white
"""


def _write_dashboard(scope_dir) -> None:
    (scope_dir / "services" / "dashboard.yaml").write_text(DASHBOARD_YAML, encoding="utf-8")


@pytest.mark.asyncio
async def test_a_flat_schema_service_renders_the_same_tabbed_view_as_a_plugin(scope_dir, node_data):
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        service = await client.get("/settings/service/bot")
        plugin = await client.get("/settings/plugin/mission")
    assert service.status_code == 200, service.text[:300]
    page = service.text
    # the SAME markers the plugin page's own tests assert (tabs, collapsible blocks, expert view)
    assert 'class="st-tabs"' in page
    assert 'class="st-layout"' in page
    assert '<details class="st-block"' in page
    assert 'class="st-yaml"' in page and "data-cfg-expert" in page
    # NOT the older single-form layout
    assert 'class="cfg-layout"' not in page, "a flat-schema service rendered the old single-form page"
    # a flat schema has no block to switch between, so the page says so and offers no `+ Add block`
    assert settings_page.SCOPE_NOTE_PLAIN in html.unescape(page)
    assert "+ Add block" not in page
    # the plugin view is unchanged: it still has its tabs and its own `+ Add block` door
    assert plugin.status_code == 200, plugin.text[:300]
    assert 'class="st-tabs"' in plugin.text and "+ Add block" in plugin.text


@pytest.mark.asyncio
async def test_a_default_only_service_renders_the_same_tabbed_view_as_a_plugin(scope_dir, node_data):
    """``webservice``/``dashboard`` have a bare ``DEFAULT`` block and no override container: the same
    tabbed renderer, one ``DEFAULT`` tab (like a plugin whose config carries only DEFAULT)."""
    _write_dashboard(scope_dir)
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get("/settings/service/dashboard")
    assert response.status_code == 200, response.text[:300]
    page = response.text
    assert 'class="st-tabs"' in page and 'DEFAULT<span class="n">defaults</span>' in page
    assert '<details class="st-block"' in page
    assert 'class="key">servers.background</span>' in page     # key relative to the tab
    assert 'name="f.DEFAULT.servers.background"' in page
    assert settings_page.SCOPE_NOTE_PLAIN in html.unescape(page)
    assert "+ Add block" not in page


@pytest.mark.asyncio
async def test_a_flat_service_no_op_save_is_byte_identical_and_a_change_round_trips(scope_dir, node_data):
    from test_webui_settings_files import _rendered_form
    path = scope_dir / "services" / "bot.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/service/bot")).text
        form = _rendered_form(page)                       # post back EXACTLY what was rendered
        form.update({"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0"})
        reply = await client.post("/actions/settings/config/service/bot", data=form)
        landed = (await client.get("/settings/service/bot")).text
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a no-op save of a flat service rewrote the file"
    assert settings_page.SAVED_NO_CHANGE in html.unescape(landed)
    # and a REAL change lands in the file that was opened
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/service/bot")).text
        form = _rendered_form(page)
        form["f.roles.Admin"] = "someone_else"
        form.update({"_csrf_token": _csrf(page), "_revision": _revision(page), "_expert": "0"})
        reply = await client.post("/actions/settings/config/service/bot", data=form)
    assert reply.status_code == 303, reply.text
    text = path.read_text(encoding="utf-8")
    assert "someone_else" in text


@pytest.mark.asyncio
async def test_a_field_naming_another_node_is_refused_on_a_flat_service(scope_dir, node_data):
    """A field naming a node is refused on the flat service EXACTLY as everywhere else: nothing is
    written and the file stays byte-identical."""
    path = scope_dir / "services" / "bot.yaml"
    before = path.read_bytes()
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/service/bot")).text
        reply = await client.post("/actions/settings/config/service/bot",
                                  data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                        "_expert": "0", "f.dcs-a-01.token": "nope"})
        landed = (await client.get("/settings/service/bot")).text
    assert reply.status_code == 303, reply.text
    assert path.read_bytes() == before, "a foreign-node field wrote the file"
    assert settings_page.SAVE_REFUSED_FOREIGN_NODE in html.unescape(landed)


# ============== 9. a `*_or_list` field: the list by default, a scalar with the convert control
#
# ``plugins/scheduler``'s ``startup.mission_id`` is a real ``func: int_or_list`` rule ("an id, or a list
# of them"), so it is the honest fixture for the whole family: the SAME field renders as the scalar the
# file carries, or as the list — and what the save writes follows the shape the control RENDERED.

SCHEDULER_YAML = """\
DEFAULT:
  startup:
    mission_id: {value}
"""


def _write_scheduler(scope_dir, value: str) -> None:
    (scope_dir / "plugins" / "scheduler.yaml").write_text(
        SCHEDULER_YAML.format(value=value), encoding="utf-8")


@pytest.mark.asyncio
async def test_a_scalar_or_list_field_renders_the_value_with_its_convert_control(scope_dir, node_data):
    """A SCALAR in the file is shown as the scalar, beside the ONE control that turns it into a list."""
    _write_scheduler(scope_dir, "42")
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/scheduler")).text
    assert 'data-cfg-orlist="DEFAULT.startup.mission_id"' in page
    assert 'name="f.DEFAULT.startup.mission_id.__form" value="scalar"' in page, \
        "the control does not state the shape it rendered"
    assert "data-cfg-tolist" in page, "a scalar already in the file offers no way to become a list"
    assert 'name="f.DEFAULT.startup.mission_id" value="42"' in page


@pytest.mark.asyncio
async def test_an_or_list_field_holding_a_list_renders_its_rows(scope_dir, node_data):
    """A LIST in the file is the list editor: one row per value, and no convert control."""
    _write_scheduler(scope_dir, "\n      - 42\n      - 7")
    app = _app(scope_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/scheduler")).text
    assert 'name="f.DEFAULT.startup.mission_id.__form" value="list"' in page
    # one input per value (plus the empty row the template ships) — never a single scalar input
    assert page.count('name="f.DEFAULT.startup.mission_id"') >= 2
    assert 'name="f.DEFAULT.startup.mission_id" value="42"' in page


@pytest.mark.asyncio
async def test_the_rendered_shape_is_what_the_save_writes(scope_dir, node_data):
    """THROUGH THE REAL ROUTE: posting the list shape writes a LIST, and the scalar shape a scalar.

    This is the end the whole control exists for — the marker the page rendered decides, and the file on
    disk is the evidence.
    """
    app = _app(scope_dir)
    path = scope_dir / "plugins" / "scheduler.yaml"

    _write_scheduler(scope_dir, "42")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/scheduler")).text
        converted = await client.post("/actions/settings/config/plugin/scheduler",
                                      data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                            "_expert": "0",
                                            # a repeated name IS the list: httpx encodes each value
                                            "f.DEFAULT.startup.mission_id": ["42", "7"],
                                            "f.DEFAULT.startup.mission_id.__form": "list"})
    assert converted.status_code == 303, converted.text
    text = path.read_text(encoding="utf-8")
    assert re.search(r"mission_id:\n\s+- 42\n\s+- 7\n", text), f"the list did not land:\n{text}"

    # and a scalar stays a scalar when the control rendered the scalar
    _write_scheduler(scope_dir, "\n      - 42\n      - 7")
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get("/settings/plugin/scheduler")).text
        kept = await client.post("/actions/settings/config/plugin/scheduler",
                                 data={"_csrf_token": _csrf(page), "_revision": _revision(page),
                                       "_expert": "0",
                                       "f.DEFAULT.startup.mission_id": "42",
                                       "f.DEFAULT.startup.mission_id.__form": "scalar"})
    assert kept.status_code == 303, kept.text
    assert re.search(r"mission_id: 42\n", path.read_text(encoding="utf-8"))
