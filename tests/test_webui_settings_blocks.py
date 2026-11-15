"""A SCHEMA'S NAMED LITERAL SECTIONS ARE NOT SERVER TABS — the Settings blocks reading, pinned.

A plugin/service schema's root declares three kinds of key:

* ``DEFAULT`` — the shared block;
* ``regex;(.+)`` — the override container (any node/instance name); and
* keys with a CONCRETE name (``commands``, ``chat_commands``), each one FIXED SECTION of the file.

The scope model used to read every non-``DEFAULT`` key that was not a cluster node as an INSTANCE, so
a file carrying ``commands``/``chat_commands`` offered two extra tabs whose fields were the element
shape rebased onto a section that has nothing to do with servers. Those tabs are gone: the key's MEANING
is the SCHEMA's to decide, so a section name yields no block and is rendered once, on the DEFAULT tab.

Pinned here:

* the scheduler file's tabs are exactly DEFAULT + its node's two instances — no ``commands`` /
  ``chat_commands`` tab, while both sections still render on the DEFAULT tab;
* the literal names are READ from the schema (a synthetic schema with a differently named section is
  right by construction), never a list of two known plugin keys;
* a document that puts a server-shaped element under a section name yields no tab AND is refused by
  the file's own schema on save, so the operator cannot create that collision;
* the Settings PAGE shows the same tabs (server-side links, no JavaScript).
"""
from __future__ import annotations

import re
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

import pytest

from conftest import asgi_client
from services.webservice import configgen, scopes
from test_webui_dashboard import _build, _hash, _login

SCHEDULER_YAML = """\
DEFAULT:
  startup:
    mission_id: 42
commands:
  startup:
    enabled: true
    name: startup
    description: Starts the DCS server
    roles:
      - DCS Admin
chat_commands:
  restart:
    enabled: true
    name: restart
    aliases:
      - rs
    roles:
      - DCS Admin
    usage: "<server>"
    help: Restarts the mission.
node1:
  Syria:
    startup:
      mission_id: 7
  Caucasus:
    startup:
      mission_id: 8
"""

#: a document whose ``commands`` key holds a SERVER's element block instead of the section's shape.
COLLISION_YAML = """\
DEFAULT:
  startup:
    mission_id: 42
commands:
  startup:
    mission_id: 7
"""

#: one node in the cluster, one section name the shipped plugin schemas do not use.
CLUSTER_YAML = """\
node1:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/syria
    Caucasus:
      home: /home/caucasus
"""

SERVERS_YAML = """\
SRS-1:
  node: node1
"""

#: a schema shaped like the shipped ones but whose literal section is called ``hooks`` — proof that
#: the rule is read from the schema, not carried as a list of known plugin section names.
HOOKS_SCHEMA = """\
schema;element_schema:
  type: map
  nullable: false
  mapping:
    mission_id:
      type: int
      nullable: false

type: map
nullable: false
mapping:
  DEFAULT:
    include: 'element_schema'
  hooks:
    type: map
    nullable: false
    mapping:
      enabled:
        type: bool
        nullable: false
  regex;(.+):
    type: any
    func: any_of
    nullable: false
    enum: ['element_schema', 'element_schema']
"""


def _scheduler_schema() -> configgen.Schema:
    primary = str(_REPO / "plugins/scheduler/schemas/scheduler_schema.yaml")
    return configgen.load_schema(primary, schema_files=configgen.schema_files_for(primary))


def _document(text: str):
    from core.utils import configwrite

    return configwrite.load_config_text(text)


@pytest.fixture
def scheduler_dir(tmp_path):
    """A single-node install carrying a scheduler file with both literal sections and two instances."""
    (tmp_path / "plugins").mkdir()
    (tmp_path / "plugins" / "scheduler.yaml").write_text(SCHEDULER_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(CLUSTER_YAML, encoding="utf-8")
    (tmp_path / "servers.yaml").write_text(SERVERS_YAML, encoding="utf-8")
    return tmp_path


@pytest.fixture
def node_data(scheduler_dir, monkeypatch):
    """Make the bot's own validator read THIS install's cluster (``nodes.yaml`` + ``servers.yaml``)."""
    import core.commandline as commandline
    from core.utils import validators

    saved_argv = getattr(commandline, "COMMAND_LINE_ARGS", None)
    saved_instance = validators.NodeData._instance
    argv = types.SimpleNamespace(node="master", config=str(scheduler_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield
    validators.NodeData._instance = saved_instance
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", saved_argv, raising=False)


def _app(scheduler_dir):
    return _build(scheduler_dir, users=[{"username": "staff", "password_hash": _hash(),
                                         "roles": ["Admin"]}])[0]


def _tab_labels(page: str) -> list[str]:
    """The labels of the tab strip, in order — read from the rendered nav, tag-free."""
    nav = re.search(r'<nav class="st-tabs".*?</nav>', page, re.S)
    assert nav is not None, "the page rendered no tab strip"
    raw = re.findall(r'<a class="t[^"]*"[^>]*>\s*([^<]*)', nav.group(0))
    return [re.sub(r"\s+", " ", label).strip() for label in raw]


# ================================ 1. the scheduler file's tabs (no section, section still rendered)

def test_scheduler_literal_sections_are_not_tabs_and_still_render_on_the_default_tab():
    tree = configgen.describe(_scheduler_schema())
    document = _document(SCHEDULER_YAML)

    tabs = scopes.build_scopes(tree, document, ["node1"])

    assert [tab["label"] for tab in tabs] == ["DEFAULT", "Syria", "Caucasus"], \
        [tab["label"] for tab in tabs]
    assert [tab["kind"] for tab in tabs] == ["default", "node", "node"]
    assert [(tab["node"], tab["base"]) for tab in tabs[1:]] == [
        ("node1", ("node1", "Syria")), ("node1", ("node1", "Caucasus"))]
    # a section name is never a block, so it never reaches the node selector or a write slice either
    assert scopes.node_options(tabs) == ["node1"]
    assert "commands" not in {str(tab["instance"]) for tab in tabs}
    assert "chat_commands" not in {str(tab["instance"]) for tab in tabs}
    # ...and both sections still render, ONCE, on the DEFAULT tab
    printed = {scopes.display_name(field, tabs[0]["base"]) for field in tabs[0]["fields"]}
    assert {"commands", "chat_commands"} <= printed


def test_a_literal_section_is_shown_by_its_own_schema_shape_on_the_default_tab():
    """The section is not merely present — it renders the fields its OWN partial declares."""
    tree = configgen.describe(_scheduler_schema())
    document = _document(SCHEDULER_YAML)
    default = scopes.build_scopes(tree, document, ["node1"])[0]

    view = scopes.scope_view(default["fields"], document)

    # ``commands`` is the commands partial: enabled/name/description/roles at its top level
    assert view.values["commands.name"] is None
    assert "commands.roles" in view.values
    # ``chat_commands`` is a KEYED map: one entry per chat command, so its control is the entries one
    assert any(name.startswith("chat_commands.*.") for name in view.values)


# ===================== 2. the rule is the SCHEMA's, not a list of the section names shipped today

def test_literal_names_are_read_from_each_schema():
    scheduler = configgen.describe(_scheduler_schema())
    assert scopes.literal_names(scheduler) == ["commands", "chat_commands"]

    music = str(_REPO / "services/music/schemas/music_schema.yaml")
    music_tree = configgen.describe(configgen.load_schema(
        music, schema_files=configgen.schema_files_for(music)))
    assert scopes.literal_names(music_tree) == []

    # a FLAT schema has no override container at all, so no key of it can be mistaken for a block
    bot = str(_REPO / "services/bot/schemas/bot_schema.yaml")
    bot_tree = configgen.describe(configgen.load_schema(bot, schema_files=configgen.schema_files_for(bot)))
    assert scopes.literal_names(bot_tree) == []


def test_a_schema_with_a_differently_named_section_is_right_by_construction(tmp_path):
    path = tmp_path / "hooks_schema.yaml"
    path.write_text(HOOKS_SCHEMA, encoding="utf-8")
    tree = configgen.describe(configgen.load_schema(str(path)))
    assert scopes.literal_names(tree) == ["hooks"]

    document = _document("DEFAULT:\n  mission_id: 1\nhooks:\n  enabled: true\nSyria:\n  mission_id: 2\n")
    tabs = scopes.build_scopes(tree, document, ["node1"])

    assert [tab["label"] for tab in tabs] == ["DEFAULT", "Syria"]
    assert "hooks" in {scopes.display_name(field, tabs[0]["base"]) for field in tabs[0]["fields"]}


# ================================================ 3. the collision: a section name is not a server

def test_a_server_shaped_block_under_a_section_name_yields_no_tab(node_data):
    """The reading follows the schema, and the schema REFUSES the file: such a server cannot exist."""
    tree = configgen.describe(_scheduler_schema())
    document = _document(COLLISION_YAML)

    tabs = scopes.build_scopes(tree, document, ["node1"])

    assert [tab["label"] for tab in tabs] == ["DEFAULT"], [tab["label"] for tab in tabs]

    from core.utils import configwrite

    primary = str(_REPO / "plugins/scheduler/schemas/scheduler_schema.yaml")
    schemas = list(dict.fromkeys([primary, *configgen.schema_files_for(primary)]))
    with pytest.raises(configwrite.ConfigValidationError) as refused:
        configwrite.validate_against_schema(COLLISION_YAML, schemas)
    assert "/commands/" in str(refused.value.errors), str(refused.value.errors)


# ========================================================= 4. the Settings PAGE (server-rendered)

@pytest.mark.asyncio
async def test_the_settings_page_shows_the_default_and_its_instances_only(scheduler_dir):
    app = _app(scheduler_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get("/settings/plugin/scheduler")

    assert response.status_code == 200, response.text[:300]
    page = response.text
    assert _tab_labels(page) == ["DEFAULT", "Syria", "Caucasus"], _tab_labels(page)
    # no tab addresses a section, and no section is offered as one
    assert "tab=3" not in page
    assert not re.search(r'<a class="t[^"]*"[^>]*>\s*(chat_)?commands', page)
    # the sections are reachable where they belong: the DEFAULT tab's own controls, carrying the
    # file's entries (a chat command is one keyed entry, a plain command one block)
    assert 'name="f.chat_commands.restart.name" value="restart"' in page
    assert 'name="f.commands.name"' in page
