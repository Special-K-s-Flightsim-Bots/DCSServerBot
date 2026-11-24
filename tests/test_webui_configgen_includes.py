"""Card S1-includes — cross-file ``include:`` in the generated page, resolved AND made visible.

The S1 review found the generator's most common real construct handled wrongly: a plugin schema's
``include: 'commands_schema'`` names a partial declared in ANOTHER file (``schemas/commands_schema.yaml``),
and with only the primary file loaded it resolved to an empty textarea. These tests pin the fix:

* a REAL plugin schema (``plugins/mission/schemas/mission_schema.yaml``) renders its cross-file include
  as its OWN fieldset with the included fields — not the opaque textarea;
* the fieldset says which file the block is described by AND where an edit is written (the INCLUDING
  file) — the round-trip edit lands there, and the schema file is never touched;
* an include CYCLE is stopped by the guard and said out loud rather than recursing forever;
* the whole file set ``core`` reads for a schema is reproduced (``schema_files_for``) and every shipped
  schema's set is collision-free for pykwalify;
* the new page strings are ``i18n``-marked and filled in the four translated catalogs.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pytest
from babel.messages import pofile

from conftest import asgi_client
from services.webservice import configgen
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

REPO_ROOT = Path(__file__).resolve().parents[1]
MISSION_SCHEMA = REPO_ROOT / "plugins" / "mission" / "schemas" / "mission_schema.yaml"
MAIN_SCHEMA = REPO_ROOT / "schemas" / "main_schema.yaml"
NODES_SCHEMA = REPO_ROOT / "schemas" / "nodes_schema.yaml"
COMMANDS_SCHEMA = REPO_ROOT / "schemas" / "commands_schema.yaml"

#: a mission config carrying an INCLUDED ``commands:`` block (the schema's ``commands_schema`` partial).
MISSION_FIXTURE = """\
# mission.yaml — an operator's own file.
commands:                # the shared command block
  enabled: true
  name: ban
  description: Ban a player
  roles:
    - DCS Admin
"""

#: a schema whose ``a`` maps to ``b_schema`` which maps back to ``a`` — a pure include CYCLE.
CYCLE_SCHEMA = """\
schema;a:
  type: map
  nullable: false
  mapping:
    b: {include: b_schema}
schema;b_schema:
  type: map
  nullable: false
  mapping:
    back: {include: a}
type: map
mapping:
  start: {include: a}
"""

#: the three strings the include page composes (the ones this card is responsible for filling).
NEW_STRINGS = [
    "included: %(file)s",
    "This block is described by %(file)s; its values live in this file, so a save writes them here "
    "— never into %(file)s.",
    "include cycle: %(name)s is included again here, so it is not expanded",
]


class Form:
    """A minimal posted-form double: ``multi_items`` like Starlette's, order preserved."""

    def __init__(self, items):
        self._items = list(items)

    def multi_items(self):
        return list(self._items)


def _app(config_dir, roles=("Admin",)):
    return _build(config_dir, users=[{"username": "staff", "password_hash": _hash(),
                                      "roles": list(roles)}])[0]


def _config_dir(tmp_path, *, file_name, body):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / file_name).write_text(body, encoding="utf-8")
    return tmp_path


# ------------------------------------------------------- 1. a real plugin schema renders its include

def test_a_real_plugin_schema_resolves_its_cross_file_include():
    """The generator resolves ``commands_schema`` out of ``schemas/commands_schema.yaml``."""
    schema = configgen.load_schema(MISSION_SCHEMA)
    assert schema.included_files.get("commands_schema") == "commands_schema.yaml"
    tree = configgen.describe(schema)
    commands = next(f for f in tree.children if f.key == "commands")
    assert commands.kind == configgen.KIND_MAP, "commands must be a fieldset, not a func textarea"
    assert commands.include == "commands_schema"
    assert commands.include_source == "commands_schema.yaml"
    assert [child.key for child in commands.children] == \
        ["enabled", "name", "description", "roles", "regex;(.+)"]
    assert child_path(commands, "name") == ("commands", "name")


def child_path(field, key):
    for child in field.children:
        if child.key == key:
            return child.path
    raise AssertionError(f"no child {key}")


@pytest.mark.asyncio
async def test_the_cross_file_include_renders_its_fields_not_a_textarea(tmp_path, monkeypatch):
    """The RENDERED page: the included block is its own typed block carrying its fields and its source."""
    config_dir = _config_dir(tmp_path, file_name="mission.yaml", body=MISSION_FIXTURE)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(MISSION_SCHEMA))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "mission.yaml")
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200
    page = response.text

 # the included block is a typed block keyed ``commands`` whose body carries its fields
    assert '<span class="k">commands</span>' in page
    assert 'name="f.commands.name"' in page and 'name="f.commands.enabled"' in page
 # the marker the card asks for: which file the block belongs to, and where a save writes it
    assert "included: commands_schema.yaml" in page
    assert "never into commands_schema.yaml" in page
 # and it is NOT the opaque textarea the old branch produced for an unresolved include
    assert 'name="f.commands"' not in page
 # the YAML panel shows the included values IN PLACE (they live in the file being edited)
    panel_match = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.S)
    assert panel_match
    assert "commands:" in html.unescape(panel_match.group(1))
    assert "enabled: true" in html.unescape(panel_match.group(1))


def test_an_unresolved_include_is_a_validate_on_save_leaf(tmp_path):
    """A partial the file set cannot find is an editable value the SCHEMA must vouch for on the write."""
    path = tmp_path / "schema.yaml"
    path.write_text("type: map\nmapping:\n  x: {include: not_a_partial}\n", encoding="utf-8")
    tree = configgen.describe(configgen.load_schema(path))
    field = next(f for f in tree.children if f.key == "x")
    assert field.kind == configgen.KIND_FUNC
    assert field.validate_on_save is True
    assert field.include == "not_a_partial" and field.include_source is None


# ------------------------------------------------------- 2. where a save writes an included edit

def test_an_edit_inside_an_included_block_lands_in_the_including_file(tmp_path):
    """The DECISION, pinned: an included block is a SCHEMA partial; its values live in THIS file."""
    from core.utils.configwrite import dump_config, load_config_text

    schema_path = tmp_path / "mission_schema.yaml"     # a copy beside a sibling commands_schema
    schema_path.write_text(MISSION_SCHEMA.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "commands_schema.yaml").write_text(COMMANDS_SCHEMA.read_text(encoding="utf-8"),
                                                   encoding="utf-8")
    config_path = tmp_path / "mission.yaml"
    config_path.write_text(MISSION_FIXTURE, encoding="utf-8")
    schema_before = COMMANDS_SCHEMA.read_bytes()

    tree = configgen.describe(configgen.load_schema(schema_path))
    document = load_config_text(config_path.read_text(encoding="utf-8"), path=config_path)
    configgen.apply_form(document, Form([("f.commands.name", "kick")]), tree)

    after = dump_config(document)
    assert "name: kick" in after, "the edit did not land in the including file"
    assert "# mission.yaml" in after, "the including file's comment was lost"
 # the schema the block is DESCRIBED by is not a write target — it never changes
    assert COMMANDS_SCHEMA.read_bytes() == schema_before


@pytest.mark.asyncio
async def test_the_save_targets_the_including_file_with_the_full_schema_set(tmp_path, monkeypatch):
    """The route writes the including config, and validates against the file set that has the partial."""
    config_dir = _config_dir(tmp_path, file_name="mission.yaml", body=MISSION_FIXTURE)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(MISSION_SCHEMA))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "mission.yaml")
    calls = {}
    real = settings_page.configwrite.patch_config_file

    def spy(path, mutate, **kwargs):
        calls["path"] = path
        calls["schemas"] = kwargs.get("schemas")
        mutate({})                       # do not touch a real file; record the call shape only
        from core.utils.configwrite import SaveResult
        return SaveResult(path=path, changed=True, revision="x", applies="restart")

    monkeypatch.setattr(settings_page.configwrite, "patch_config_file", spy)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf_match = re.search(r'name="_csrf_token" value="([^"]*)"', page)
        revision_match = re.search(r'name="_revision" value="([^"]*)"', page)
        assert csrf_match and revision_match
        await client.post(settings_page.SETTINGS_SAVE_PATH,
                          data={"f.commands.name": "kick", "_csrf_token": csrf_match.group(1),
                                "_revision": revision_match.group(1)})
    assert calls["path"] == config_dir / "mission.yaml", "a save must write the INCLUDING file"
    assert calls["schemas"] == configgen.schema_files_for(str(MISSION_SCHEMA))
    assert "commands_schema.yaml" in [Path(s).name for s in calls["schemas"]], \
        "the partial file must be in the validation set or the save would be refused"
    assert real is not None


# ------------------------------------------------------- 3. a cycle is stopped and said out loud

def test_an_include_cycle_is_stopped_and_marked(tmp_path):
    path = tmp_path / "cycle_schema.yaml"
    path.write_text(CYCLE_SCHEMA, encoding="utf-8")
    tree = configgen.describe(configgen.load_schema(path))   # must not hang or raise
    fields = {f.name: f for f in configgen.flatten(tree)}
    marker = fields["start.b.back"]
    assert marker.include_cycle == "a"
    assert marker.include == "a"
    assert marker.kind == configgen.KIND_FUNC


@pytest.mark.asyncio
async def test_the_cycle_render_says_so(tmp_path, monkeypatch):
    config_dir = _config_dir(tmp_path, file_name="main.yaml", body="")
    schema = tmp_path / "cycle_schema.yaml"
    schema.write_text(CYCLE_SCHEMA, encoding="utf-8")
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(schema))
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        response = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200
    assert "include cycle: a is included again here" in response.text


# ------------------------------------------------------- 4. the file sets the bot reads

def test_schema_files_for_reproduces_the_bots_own_sets():
    plugin = [Path(p).name for p in configgen.schema_files_for(MISSION_SCHEMA)]
    assert plugin[0] == "mission_schema.yaml"
    assert "commands_schema.yaml" in plugin

 # main.yaml is validated against main_schema.yaml ALONE (sibling core schemas carry root keys)
    assert [Path(p).name for p in configgen.schema_files_for(MAIN_SCHEMA)] == ["main_schema.yaml"]

 # the node schema pulls the whole extensions tree (node/instance extension blocks)
    node = [Path(p).name for p in configgen.schema_files_for(NODES_SCHEMA)]
    assert "extensions_schema.yaml" in node and "dsmc_schema.yaml" in node

 # a plugin whose ballots live in sibling files: they are all picked up
    voting = [Path(p).name for p in
              configgen.schema_files_for(REPO_ROOT / "plugins" / "voting" / "schemas"
                                         / "voting_schema.yaml")]
    assert {"kick_schema.yaml", "tempban_schema.yaml"} <= set(voting)


def test_every_shipped_schema_file_set_is_collision_free_for_pykwalify():
    """pykwalify merges every file's top-level keys and refuses a duplicate — the sets must be disjoint."""
    from pykwalify.core import Core

    schemas = sorted(
        list((REPO_ROOT / "schemas").glob("*_schema.yaml"))
        + list((REPO_ROOT / "plugins").glob("*/schemas/*_schema.yaml"))
        + list((REPO_ROOT / "extensions").glob("*/schemas/*_schema.yaml")))
    assert schemas
    for path in schemas:
        Core(source_data={}, schema_files=configgen.schema_files_for(path))


# ------------------------------------------------------- 5. the new strings are wrapped everywhere

def test_the_include_strings_are_born_wrapped_in_every_catalog():
    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po",
                  encoding="utf-8") as handle:
            catalog = pofile.read_po(handle)
        by_id = {message.id: message for message in catalog if message.id}
        for text in NEW_STRINGS:
            assert text in by_id, f"'{text}' is missing from the {language} catalog"
            assert not by_id[text].fuzzy, f"'{text}' is fuzzy in {language}"
            assert by_id[text].string, f"'{text}' is left untranslated in {language}"
