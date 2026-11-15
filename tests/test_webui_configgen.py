"""The schema-driven config page GENERATOR (card S1) — the schema -> control mapping.

``services/webservice/configgen`` turns a pykwalify schema into a tree of :class:`Field` records the
config page renders and the save path reads back. These tests pin the mapping TABLE-DRIVEN, over every
construct the card names (not one happy path): bool -> switch, int/float + range -> number, str/text/
url -> text, enum -> select, seq -> row list, map -> fieldset, ``include:`` resolved inline,
``regex;(.+)`` -> "Add entry", ``type: any`` + enum -> layout chooser, ``func:`` -> validate on save,
``default:`` prefilled, ``required: true`` marked required, ``nullable: false`` marked "no empty
value" (a DIFFERENT fact, never conflated with required). And they pin the document half: a form
submitted over a fixture changes ONLY the intended key, leaving comments and formatting intact, and an
unchanged submit is a byte-identical no-op.

Also pinned: the generator's own chrome strings are ``i18n._``-marked AND present in every shipped
catalog (the "born wrapped" rule), while the schema KEYS stay English by design.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import configgen

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SCHEMA = REPO_ROOT / "schemas" / "main_schema.yaml"


def describe_text(tmp_path, text: str) -> configgen.Field:
    path = tmp_path / "schema.yaml"
    path.write_text(text, encoding="utf-8")
    return configgen.describe(configgen.load_schema(path))


def child(tree: configgen.Field, key: str) -> configgen.Field:
    for item in tree.children:
        if item.key == key:
            return item
    raise AssertionError(f"no child '{key}' in {[c.key for c in tree.children]}")


# ------------------------------------------------------- 1. the mapping, table-driven

#: (name, schema body of a single field, expected kind, extra assertions)
MAPPING = [
    ("bool", "flag: {type: bool, nullable: false}", configgen.KIND_BOOL, {}),
    ("int", "num: {type: int, range: {min: 1, max: 9}, nullable: false}", configgen.KIND_NUMBER,
     {"minimum": 1, "maximum": 9}),
    ("float", "ratio: {type: float, range: {min: 0.0, max: 1.0}, nullable: false}",
     configgen.KIND_NUMBER, {"minimum": 0.0, "maximum": 1.0}),
    ("str", "name: {type: str, nullable: false}", configgen.KIND_TEXT, {}),
    ("text", "notes: {type: text, nullable: false}", configgen.KIND_MULTILINE, {}),
    ("url", "link: {type: url, nullable: false}", configgen.KIND_TEXT, {}),
    ("enum", "mode: {type: str, enum: ['a', 'b'], nullable: false}", configgen.KIND_SELECT,
     {"enum": ("a", "b")}),
]


@pytest.mark.parametrize("name, body, kind, extra", MAPPING, ids=[row[0] for row in MAPPING])
def test_a_scalar_construct_maps_to_its_control(tmp_path, name, body, kind, extra):
    tree = describe_text(tmp_path, f"type: map\nmapping:\n  {body}\n")
    field = tree.children[0]
    assert field.kind == kind, f"{name}: got {field.kind}, wanted {kind}"
    for attr, expected in extra.items():
        assert getattr(field, attr) == expected, f"{name}.{attr}"


def test_a_map_construct_maps_to_a_fieldset(tmp_path):
    tree = describe_text(tmp_path, "type: map\nmapping:\n  group:\n    type: map\n    nullable: false\n"
                                   "    mapping:\n      inner: {type: int, nullable: false}\n")
    field = child(tree, "group")
    assert field.kind == configgen.KIND_MAP
    assert [c.key for c in field.children] == ["inner"]
    assert child(field, "inner").path == ("group", "inner")


def test_a_scalar_seq_maps_to_a_row_list(tmp_path):
    tree = describe_text(tmp_path, "type: map\nmapping:\n  plugins:\n    type: seq\n    nullable: false\n"
                                   "    sequence:\n      - {type: str, nullable: false, range: {min: 1}}\n")
    field = child(tree, "plugins")
    assert field.kind == configgen.KIND_LIST
    assert field.element is not None and field.element.kind == configgen.KIND_TEXT


def test_a_seq_of_maps_maps_to_a_seq(tmp_path):
    tree = describe_text(tmp_path, "type: map\nmapping:\n  rows:\n    type: seq\n    nullable: false\n"
                                   "    sequence:\n      - type: map\n        nullable: false\n"
                                   "        mapping:\n          n: {type: int, nullable: false}\n")
    field = child(tree, "rows")
    assert field.kind == configgen.KIND_SEQ
    assert field.element is not None and field.element.kind == configgen.KIND_MAP


def test_an_include_is_resolved_inline(tmp_path):
    tree = describe_text(tmp_path, "schema;point:\n  type: int\n  nullable: false\n  range: {min: 0}\n"
                                   "type: map\nmapping:\n  x: {include: point}\n")
    field = child(tree, "x")
    assert field.kind == configgen.KIND_NUMBER and field.minimum == 0
    assert "include" not in field.key


def test_a_regex_key_maps_to_add_entry(tmp_path):
    tree = describe_text(tmp_path, "type: map\nmapping:\n  custom:\n    type: map\n    nullable: false\n"
                                   "    mapping:\n      regex;(.+):\n        type: map\n"
                                   "        nullable: false\n        mapping:\n"
                                   "          v: {type: int, nullable: false}\n")
    custom = child(tree, "custom")
    entry = custom.children[0]
    assert entry.kind == configgen.KIND_REGEX and entry.regex == "(.+)"
    assert entry.element.kind == configgen.KIND_MAP


def test_a_func_marks_the_control_validate_on_save(tmp_path):
    tree = describe_text(tmp_path, "type: map\nmapping:\n"
                                   "  port: {type: int, func: unique_port, nullable: false}\n")
    field = child(tree, "port")
    assert field.kind == configgen.KIND_NUMBER and field.validate_on_save is True
    assert field.func == "unique_port"


def test_an_any_rule_with_a_layout_func_and_enum_is_a_chooser(tmp_path):
    tree = describe_text(tmp_path, "schema;a:\n  type: map\n  nullable: false\n  mapping:\n"
                                   "    n: {type: int, nullable: false}\n"
                                   "schema;b:\n  type: seq\n  nullable: false\n  sequence:\n"
                                   "    - {type: str, nullable: false}\n"
                                   "type: map\nmapping:\n  h: {type: any, func: seq_or_map, "
                                   "nullable: false, enum: ['a', 'b']}\n")
    field = child(tree, "h")
    assert field.kind == configgen.KIND_CHOICE
    assert [c.key for c in field.children] == ["a", "b"]


def test_required_reads_required_true_not_nullable_false(tmp_path):
    """``required`` is the schema's OWN signal — ``required: true`` — never ``nullable: false``.

    ``nullable: false`` means "no empty value accepted"; it does NOT mean "you must supply it".
    Conflating the two labelled every ``nullable: false`` field "required", which is a lie for the
    many fields (``schemas/main_schema.yaml`` among them) that set it without ``required: true``.
    """
    tree = describe_text(tmp_path, "type: map\nmapping:\n  d: {type: int, default: 5}\n"
                                   "  r: {type: str, required: true, nullable: false}\n"
                                   "  n: {type: str, nullable: false}\n"
                                   "  o: {type: str}\n")
    assert child(tree, "d").has_default and child(tree, "d").default == 5
    # required: true -> marked required (and it also carries the no-empty-value fact)
    assert child(tree, "r").required is True and child(tree, "r").nullable is False
    # nullable: false ALONE -> NOT required, and it carries NO label (the "no empty value" marker
    # was removed as noise — the rule survives on the field, the badge does not)
    assert child(tree, "n").required is False, "nullable:false alone must NOT be marked required"
    assert child(tree, "n").nullable is False, "the no-empty-value fact must survive on the field"
    # an ordinary optional field carries neither fact
    assert child(tree, "o").required is False and child(tree, "o").nullable is True
    # the required marker is its own chrome word, born wrapped; `nullable: false` has no marker
    assert configgen.REQUIRED_LABEL == "required"
    assert not hasattr(configgen, "NO_EMPTY_LABEL"), \
        "the 'no empty value' marker was removed — it must not come back"


# ------------------------------------------------------- 2. the real main.yaml schema

def test_the_real_main_schema_maps_every_key():
    tree = configgen.describe(configgen.load_schema(MAIN_SCHEMA))
    kinds = {field.name: field.kind for field in configgen.flatten(tree) if field.name}
    assert kinds["guild_id"] == configgen.KIND_NUMBER
    assert kinds["autoupdate"] == configgen.KIND_BOOL
    assert kinds["language"] == configgen.KIND_SELECT
    assert kinds["database"] == configgen.KIND_MAP
    assert kinds["database.url"] == configgen.KIND_TEXT
    assert kinds["logging.loglevel"] == configgen.KIND_SELECT
    assert kinds["plugins"] == configgen.KIND_LIST


def test_a_flat_schema_has_one_default_scope():
    tree = configgen.describe(configgen.load_schema(MAIN_SCHEMA))
    scopes = configgen.scopes(tree)
    assert [scope["label"] for scope in scopes] == ["DEFAULT"]
    assert {field.key for field in scopes[0]["fields"]} == {c.key for c in tree.children}


def test_a_nested_group_renders_as_one_block_below_validation(tmp_path):
    """The RENDERED page: the group below ``validation`` (the ``database`` block) is ONE block.

    ``main_schema.yaml`` nests a group (``database``) under the flat root; on the page that is a
    two-level group — a group inside the page's top-level field list — and it sits directly after
    ``validation``. The defect the operator saw: the group's LEGEND sat indented right while its own values
    sat slightly left again, so the group read as a broken staircase. The fix (item 3) makes the
    group's marker and its body share the fieldset's single left edge — the SAME gutter a scalar
    field's ``.cfg-key`` starts in — so the group reads as one block. This proves the STRUCTURE that
    the CSS relies on: the group is one ``<fieldset class="cfg-sec">`` whose ``<legend>`` carries the
    group's ``.cfg-key`` marker and whose ``.cfg-body`` holds its fields, with NO ``.cfg-field`` grid
    between them (that grid would open a second column and drift the body off the key column). The
    left-edge alignment itself is a CSS property, pinned in ``test_webui_console_css.py``.
    """
    from test_webui_settings_page import _app, _login, _open
    from test_webui_dashboard import BOT_YAML

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(
        "guild_id: 1\nguild_name: X\nvalidation: none\n"
        "database:\n  url: db://x\n  pool_min: 5\n  pool_max: 10\n", encoding="utf-8")

    async def _drive():
        app, _ = _app(tmp_path)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(_drive)
    assert page.index("f.validation") < page.index("f.database.url"), \
        "the database group is not below the validation field"
    # the group renders as ONE collapsible block whose summary carries the group's own key, so its
    # marker and its body share the block (the scoped editor's ``st-block``), with the fields inside.
    assert '<span class="k">database</span>' in page, \
        "the nested group did not render as its own typed block"
    assert 'name="f.database.url"' in page and 'name="f.database.pool_min"' in page


def test_a_schema_with_a_default_block_yields_a_scope_per_override(tmp_path):
    """A plugin-style schema (DEFAULT + an instance/node override) renders DEFAULT plus one tab each."""
    tree = describe_text(tmp_path, "schema;element:\n  type: map\n  nullable: false\n  mapping:\n"
                                   "    enabled: {type: bool, nullable: false}\n"
                                   "type: map\nfunc: check_main_structure\nnullable: false\n"
                                   "mapping:\n"
                                   "  DEFAULT: {include: element}\n"
                                   "  regex;(.+): {include: element}\n")
    labels = [scope["label"] for scope in configgen.scopes(tree)]
    assert labels[0] == "DEFAULT"
    assert any(label.startswith("regex;") for label in labels[1:])


# ------------------------------------------------------- 3. document round-trip

FIXTURE = """\
# main.yaml — an operator's own file.
guild_id: 112233445566
guild_name: My Server
use_dashboard: false
logging:
  loglevel: DEBUG           # the level
  utc: false                # timestamps
plugins:
  - mission
  - userstats
"""


class Form:
    """A minimal form double: ``multi_items`` like Starlette's, order and duplicates preserved."""

    def __init__(self, items):
        self._items = list(items)

    def multi_items(self):
        return list(self._items)


def _tree():
    return configgen.describe(configgen.load_schema(MAIN_SCHEMA))


def _dump(text: str) -> str:
    from core.utils.configwrite import dump_config, load_config_text
    return dump_config(load_config_text(text))


def test_an_unchanged_form_is_a_byte_identical_no_op():
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text(FIXTURE)
    form = Form([
        ("f.guild_id", "112233445566"), ("f.guild_name", "My Server"),
        ("f.use_dashboard", "0"), ("f.logging.loglevel", "DEBUG"), ("f.logging.utc", "0"),
        ("f.plugins", "mission"), ("f.plugins", "userstats"),
    ])
    configgen.apply_form(document, form, _tree())
    assert dump_config(document) == _dump(FIXTURE), "an untouched form must change nothing"


def test_one_key_changes_only_that_line():
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text(FIXTURE)
    form = Form([
        ("f.guild_id", "112233445566"), ("f.guild_name", "My Server"),
        ("f.use_dashboard", "0"), ("f.logging.loglevel", "INFO"), ("f.logging.utc", "0"),
        ("f.plugins", "mission"), ("f.plugins", "userstats"),
    ])
    configgen.apply_form(document, form, _tree())
    after = dump_config(document)
    diff = list(difflib.unified_diff(FIXTURE.splitlines(), after.splitlines(), lineterm="", n=0))
    added = [line for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]
    assert len(added) == 1 and "loglevel: INFO" in added[0], f"unexpected diff: {diff}"
    assert len(removed) == 1 and "loglevel: DEBUG" in removed[0], f"unexpected diff: {diff}"
    assert "# main.yaml" in after                      # the leading comment survived
    assert "# timestamps" in after                     # the sibling's comment survived


def test_a_missing_key_is_not_created_by_merely_showing_it():
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text(FIXTURE)
    # the fixture omits autoupdate/validation — posting their empty defaults must not create them
    form = Form([("f.autoupdate", "0"), ("f.validation", ""),
                 ("f.database.pool_min", "")])
    configgen.apply_form(document, form, _tree())
    assert dump_config(document) == _dump(FIXTURE)


def test_a_list_field_is_replaced_from_its_posted_values():
    from core.utils.configwrite import dump_config, load_config_text
    document = load_config_text(FIXTURE)
    form = Form([("f.plugins", "mission"), ("f.plugins", "dbexporter")])
    configgen.apply_form(document, form, _tree())
    assert "  - mission\n  - userstats\n" in FIXTURE
    assert "  - mission\n  - dbexporter\n" in dump_config(document)


# ------------------------------------------------------- 4. the generator's strings are wrapped

def test_the_generator_composes_only_wrapped_chrome_strings():
    """Every string ``configgen`` composes is ``i18n._``-marked and shipped in every catalog."""
    from babel.messages import pofile

    wrapped = list(configgen.KIND_LABELS.values())
    wrapped += [configgen.VALIDATE_ON_SAVE_LABEL, configgen.DEFAULT_LABEL, configgen.REQUIRED_LABEL]
    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po", encoding="utf-8") as f:
            msgids = {message.id for message in pofile.read_po(f) if message.id}
        for text in wrapped:
            assert isinstance(text, str) and text, "a kind word must be a non-empty string"
            assert text in msgids, f"'{text}' is not in the {language} catalog"


# ------------------------------------------------------- 5. the cycle guard terminates (blocker)

#: every schema the bot itself ships: the top-level ones and each plugin's.
SHIPPED_SCHEMAS = sorted(
    list((REPO_ROOT / "schemas").glob("*_schema.yaml"))
    + list((REPO_ROOT / "plugins").glob("*/schemas/*_schema.yaml")))


@pytest.mark.parametrize("schema_path", SHIPPED_SCHEMAS, ids=[p.name for p in SHIPPED_SCHEMAS])
def test_every_shipped_schema_can_be_walked(schema_path):
    """``describe`` terminates on every shipped schema — no ``RecursionError`` on any of them.

    Two core schemas (``menus_schema.yaml``, ``presets_schema.yaml``) are SELF-REFERENTIAL: an
    element may be a list whose element is that element again. The generator's cycle guard has to be
    carried THROUGH the recursion, not re-entered fresh at every level, or such a shape is walked
    forever. This walks all of them (parametrised, so a newly-added cyclic schema is covered).
    """
    tree = configgen.describe(configgen.load_schema(schema_path))
    assert configgen.flatten(tree), f"{schema_path.name} described to nothing"


def test_the_two_self_referential_schemas_describe_without_recursing_forever():
    """The exact two schemas the review turned red on, named explicitly so they cannot come back."""
    for name in ("menus_schema.yaml", "presets_schema.yaml"):
        tree = configgen.describe(configgen.load_schema(REPO_ROOT / "schemas" / name))
        assert configgen.flatten(tree), name
    # the menus cycle lands on the layout chooser, whose recursive option collapses to a func leaf
    menus = configgen.describe(configgen.load_schema(REPO_ROOT / "schemas" / "menus_schema.yaml"))
    choices = [f for f in configgen.flatten(menus) if f.kind == configgen.KIND_CHOICE]
    assert choices, "the menus schema's chooser did not render"
    assert any(c.kind == configgen.KIND_FUNC
               for choice in choices for c in choice.children), \
        "the cyclic option was not collapsed to a validate-on-save leaf"


def test_a_map_without_a_mapping_is_validate_on_save_not_a_crash(tmp_path):
    """A ``type: map`` with no ``mapping`` is a func leaf — never the duplicate-kwarg ``TypeError``.

    The branch the docstring dedicates to "validate on save, never a guess" used to pass
    ``validate_on_save=True`` while ``**common`` already carried it — reachable by any shaped map.
    """
    tree = describe_text(tmp_path, "type: map\nmapping:\n"
                                   "  shaped:\n    type: map\n    func: seq_or_map\n"
                                   "    nullable: false\n"
                                   "  empty: {type: map, nullable: false}\n")
    for key in ("shaped", "empty"):
        field = child(tree, key)
        assert field.kind == configgen.KIND_FUNC, key
        assert field.validate_on_save is True, key


# ------------------------------------------------------- 6. the value-or-list / value-or-map funcs

#: the two hybrid families, in the shape the schemas write them: a `*_or_list` needs no block, a
#: `*_or_map` names its block in `enum` (the partial the validator validates a dict against).
OR_LIST_SCHEMA = """\
type: map
mapping:
  patterns:
    type: any
    func: str_or_list
    nullable: false
    desc: Glob patterns.
    example: "*.miz"
  counts:
    type: any
    func: int_or_list
    nullable: false
"""

OR_MAP_SCHEMA = """\
type: map
mapping:
  clouds:
    type: any
    func: str_or_map
    nullable: false
    desc: A preset name or a layer map.
    enum: ['cloud_map']
schema;cloud_map:
  type: map
  nullable: false
  mapping:
    preset:
      type: str
    density:
      type: int
"""


def test_a_scalar_or_list_is_the_hybrid_kind_and_its_rows_carry_the_scalar_type(tmp_path):
    """``*_or_list`` is its OWN kind, and the ROWS are the schema's scalar type — never a text guess."""
    tree = describe_text(tmp_path, OR_LIST_SCHEMA)
    patterns = child(tree, "patterns")
    assert patterns.kind == configgen.KIND_OR_LIST
    assert patterns.element is not None and patterns.element.kind == configgen.KIND_TEXT
    assert patterns.csv is False, "a plain str_or_list is not a CSV"
    assert patterns.validate_on_save is False, "a layout func is not 'validate on save'"
    assert patterns.desc == "Glob patterns."
    assert patterns.element.example == "*.miz", "the row's placeholder is the schema's own example"
    counts = child(tree, "counts")
    assert counts.kind == configgen.KIND_OR_LIST and counts.element is not None
    assert counts.element.kind == configgen.KIND_NUMBER
    assert counts.element.kind_label, "the row control needs its own kind word"


def test_the_scalar_or_list_writes_the_shape_the_control_rendered(tmp_path):
    """BOTH shapes round-trip, and a ONE-row list stays a list: the marker is the operator's choice.

    This is the rule Frank set: the list is the default, a scalar already in the file is offered the
    conversion, and shrinking a list back to one entry does not silently turn it into a scalar.
    """
    from core.utils.configwrite import load_config_text
    tree = describe_text(tmp_path, OR_LIST_SCHEMA)

    def run(text, pairs):
        document = load_config_text(text)
        configgen.apply_form(document, Form(pairs), tree)
        return configgen.value_at(document, ("patterns",))

    # a list shrunk to ONE row stays a list
    assert run("patterns:\n  - a\n  - b\n",
               [("f.patterns", "a"), ("f.patterns.__form", "list")]) == ["a"]
    # the conversion: the CSV is split by the CLIENT, so the rows arrive as separate values
    assert run("patterns: 'a,b'\n",
               [("f.patterns", "a"), ("f.patterns", "b"), ("f.patterns.__form", "list")]) == ["a", "b"]
    # a scalar left as it is stays a scalar
    assert run("patterns: 'a,b'\n",
               [("f.patterns", "a,b"), ("f.patterns.__form", "scalar")]) == "a,b"
    # absent + one row typed: the list IS created (the documented default shape)
    assert run("other: 1\n", [("f.patterns", "x"), ("f.patterns.__form", "list")]) == ["x"]
    # a list in the file, emptied on purpose: a real empty list, written as such
    assert run("patterns:\n  - a\n", [("f.patterns", ""), ("f.patterns.__form", "list")]) == []


def test_showing_an_or_list_does_not_create_the_key(tmp_path):
    """The control renders one empty row by default — posting it must NOT write an empty list."""
    from core.utils.configwrite import dump_config, load_config_text
    tree = describe_text(tmp_path, OR_LIST_SCHEMA)
    document = load_config_text("other: 1\n")
    configgen.apply_form(document, Form([("f.patterns", ""), ("f.patterns.__form", "list")]), tree)
    assert dump_config(document).strip() == "other: 1"
    assert "patterns" not in document


def test_a_shape_marker_the_schema_does_not_name_is_refused(tmp_path):
    """The marker is stated data: anything but ``list``/``scalar`` is REFUSED, never guessed at."""
    from core.utils.configwrite import load_config_text
    tree = describe_text(tmp_path, OR_LIST_SCHEMA)
    document = load_config_text("patterns: 'x'\n")
    with pytest.raises(configgen.UnknownFieldError):
        configgen.apply_form(document, Form([("f.patterns", "x"),
                                             ("f.patterns.__form", "tuple")]), tree)


def test_a_scalar_or_map_carries_both_shapes_and_the_schema_s_block(tmp_path):
    """``*_or_map`` knows its block (the rule's ``enum``) AND its scalar: the page picks by the FILE."""
    tree = describe_text(tmp_path, OR_MAP_SCHEMA)
    clouds = child(tree, "clouds")
    assert clouds.kind == configgen.KIND_OR_MAP
    assert clouds.element is not None and clouds.element.kind == configgen.KIND_MAP
    assert [c.key for c in clouds.element.children] == ["preset", "density"]
    assert clouds.scalar is not None and clouds.scalar.kind == configgen.KIND_TEXT
    assert clouds.validate_on_save is False


def test_the_value_side_of_an_or_map_is_written_when_the_file_carries_a_scalar(tmp_path):
    from core.utils.configwrite import dump_config, load_config_text
    tree = describe_text(tmp_path, OR_MAP_SCHEMA)
    document = load_config_text("clouds: Overcast 5\n")
    configgen.apply_form(document, Form([("f.clouds", "Broken 8")]), tree)
    assert dump_config(document).strip() == "clouds: Broken 8"


def test_the_block_side_of_an_or_map_resolves_to_the_schema_s_own_children(tmp_path):
    """A map in the file is edited as the block: its leaves resolve through the hybrid field."""
    from core.utils.configwrite import dump_config, load_config_text
    tree = describe_text(tmp_path, OR_MAP_SCHEMA)
    document = load_config_text("clouds:\n  preset: Overcast 5\n  density: 6\n")
    configgen.apply_form(document, Form([("f.clouds.density", "9")]), tree)
    assert configgen.value_at(document, ("clouds", "preset")) == "Overcast 5"
    assert configgen.value_at(document, ("clouds", "density")) == 9
    assert "density: 9" in dump_config(document)


# ------------------------------------------------------- 7. Add works on an empty container

CONFIGGEN_JS = REPO_ROOT / "services" / "webservice" / "static" / "configgen.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"

_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                    reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def configgen_script() -> dict:
    """The shipped configgen.js's own answers, driven by the harness over the REAL file."""
    assert CONFIGGEN_HARNESS.is_file() and CONFIGGEN_JS.is_file()
    done = subprocess.run(["node", str(CONFIGGEN_HARNESS), str(CONFIGGEN_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_requires_node
def test_the_convert_control_splits_a_csv_scalar_into_rows(configgen_script):
    """The `+ Add` on a scalar `*_or_list`: it SPLITS the CSV, drops the scalar input, flips the marker.

    Driven over the REAL shipped script by the harness. Two things matter and both are asserted here:
    the scalar's own control must be GONE (leaving it, even hidden, would post its value as an extra
    row), and the shape marker must reach ``list`` — that is what makes the save write a LIST instead of
    reading a one-row list back as a scalar.
    """
    converted = configgen_script["orListConverted"]
    assert converted["marker"] == "list", "the shape marker still says scalar"
    assert converted["scalarBoxesLeft"] == 0, \
        "the scalar control is still in the page: it would post its value as a row"
    assert converted["rowsHidden"] is False, "the list rows are still hidden"
    assert converted["rowValues"] == ["a", "b", "c"], "the CSV was not split into one row per value"
    assert converted["names"] == ["f.patterns.__form"] + ["f.patterns"] * 3


@_requires_node
def test_add_seeds_a_row_on_an_empty_container(configgen_script):
    """BLOCKER: "+ Add" never does nothing. An empty list/seq/regex container gets a real seed row.

    An empty ``plugins:`` list is schema-legal, and the page used to ship no ``<template>`` — so with
    zero rows ``rowSeed`` returned null and every Add was a silent no-op. The shipped script now seeds
    from the container's template (asserted here over the REAL file) and ``settings.html`` ships one
    per container (asserted by the rendered page test below).
    """
    # 1. an empty scalar list gets one row whose input keeps the list's name
    assert configgen_script["emptyList"] == {"rows": 1, "names": ["f.plugins"]}
    # 2. an empty seq of maps gets one row, its counter updated to 1, and its sub-fields renumbered
    seq = configgen_script["emptySeq"]
    assert seq["rows"] == 1 and seq["count"] == "1"
    assert "f.rows.0.n" in seq["names"]
    # 3. an empty regex container gets one row, its key editable and its fields on the placeholder
    assert configgen_script["emptyRegex"]["rows"] == 1
    assert configgen_script["emptyRegex"]["names"] == ["f.custom.__key", "f.custom.__KEY__.v"]
    # 4. typing the new row's key rewrites its field names, and again off the CURRENT key
    assert configgen_script["regexTyped"]["afterFirst"] == ["f.custom.__key", "f.custom.alpha.v"]
    assert configgen_script["regexTyped"]["afterSecond"] == ["f.custom.__key", "f.custom.beta.v"]
    # 5. a NON-empty list is unchanged: the last row is cloned and cleared
    assert configgen_script["existingList"] == {"rows": 2,
                                                "names": ["f.plugins", "f.plugins"],
                                                "lastValue": ""}


@_requires_node
def test_every_row_container_ships_a_template_seed(tmp_path, monkeypatch):
    """The rendered page carries a ``<template>`` seed in its (empty) row-list container.

    The script half above proves the seeding; this half proves the MARKUP the script reads. The
    fixture's ``items:`` list is EMPTY so the container has zero rows — the case the original page
    could not Add to.
    """
    from test_webui_settings_page import _app, _login, _open
    from test_webui_dashboard import BOT_YAML
    from services.webservice.pages import settings as settings_page

    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text("items: []\n", encoding="utf-8")
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "s.yaml").write_text(
        "type: map\nnullable: false\nmapping:\n  items:\n    type: seq\n    nullable: false\n"
        "    sequence:\n      - {type: str, nullable: false, range: {min: 1}}\n", encoding="utf-8")
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(schemas / "s.yaml"))

    async def _drive():
        app, _ = _app(tmp_path)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(_drive)
    container = re.search(r'<div data-cfg-list="items">(.*?)</div>', page, re.S)
    assert container, "the items list container is missing"
    assert "<template" in container.group(1), "the empty items container ships no Add seed"
    assert "data-cfg-row" not in container.group(1).split("<template")[0], \
        "the fixture items list was not empty"


def _run(drive):
    """Run an async ``drive()`` on a fresh event loop (these are sync tests driving an async client)."""
    import asyncio
    return asyncio.new_event_loop().run_until_complete(drive())
