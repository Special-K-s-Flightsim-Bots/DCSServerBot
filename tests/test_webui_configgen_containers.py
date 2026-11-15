"""Container rows/entries must RENDER, and an unchanged save must not erase them.

THE BLOCKER THIS PINS. ``load_page`` read a container's current value with
``values.get(field.name)``, but ``configgen.collect_current`` fills the LEAF fields only (``leaves()``
excludes :data:`KIND_SEQ` and :data:`KIND_REGEX`). So a seq/regex container's value was ALWAYS
``None``:

* no existing row / regex entry was rendered as an editable input (only the ``<template>`` seed
  appeared);
* a seq's hidden length counter rendered ``f.<path>.__n = 0``, so posting the page UNCHANGED made
  ``apply_form`` truncate the sequence to ``[]`` — SILENT DATA LOSS while the banner still said
  "Saved".

The fix reads the container's value from the DOCUMENT at the container's own path
(``configgen.value_at`` for a seq, ``configgen.regex_value`` for a regex), and the template reads the
per-row value with ``row['values']`` (a bare ``row.values`` resolves to ``dict.values`` — the method —
not the entry's mapping).

The tests drive the REAL application: they render the page, scrape the fields a browser WOULD post
(template contents are inert and stripped), and check the file is byte-identical — plus the page model
must list exactly the operator's own keys, never a fixed sibling or ``DEFAULT``.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import configgen
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGGEN_JS = REPO_ROOT / "services" / "webservice" / "static" / "configgen.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"


SEQ_SCHEMA = """\
type: map
nullable: false
mapping:
  rows:
    type: seq
    nullable: false
    sequence:
      - type: map
        nullable: false
        mapping:
          n: {type: int, nullable: false}
"""
SEQ_FIXTURE = "rows:\n  - n: 3\n  - n: 4\n"

REGEX_SCHEMA = """\
type: map
nullable: false
mapping:
  commands:
    type: map
    nullable: false
    mapping:
      regex;(.+):
        type: map
        nullable: false
        mapping:
          name: {type: str, nullable: false, range: {min: 1}}
"""
REGEX_FIXTURE = "commands:\n  greet.user:\n    name: hello\n"

SIBLING_SCHEMA = """\
type: map
nullable: false
mapping:
  enabled: {type: bool, nullable: false}
  regex;(.+):
    type: map
    nullable: false
    mapping:
      name: {type: str, nullable: false, range: {min: 1}}
"""
SIBLING_FIXTURE = "enabled: true\ngreet:\n  name: hi\n"

TOPREGEX_SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      n: {type: int, nullable: false}
  regex;(.+):
    type: map
    nullable: false
    mapping:
      n: {type: int, nullable: false}
"""
TOPREGEX_FIXTURE = "DEFAULT:\n  n: 1\ninst1:\n  n: 2\n"

CHOICE_SCHEMA = """\
schema;a:
  type: seq
  nullable: false
  sequence:
    - {type: str, nullable: false}
schema;b:
  type: map
  nullable: false
  mapping:
    n: {type: int, nullable: false}
type: map
nullable: false
mapping:
  wrap:
    type: map
    nullable: false
    mapping:
      h: {type: any, func: seq_or_map, nullable: false, enum: ['a', 'b']}
"""
CHOICE_FIXTURE = "wrap:\n  h:\n    - x\n"


def _config_dir(tmp_path, body: str, schema_text: str) -> Path:
    """A config dir with the auth ``services/bot.yaml``, the file, AND its schema in ``schemas/``."""
    (tmp_path / "services").mkdir(exist_ok=True)
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(body, encoding="utf-8")
    schemas = tmp_path / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "s.yaml").write_text(schema_text, encoding="utf-8")
    return tmp_path


def _app(config_dir, roles=("Admin",)):
    return _build(config_dir, users=[{"username": "staff", "password_hash": _hash(),
                                      "roles": list(roles)}])[0]


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Point the Settings page at *tmp_path*'s main.yaml + schemas/s.yaml, then return a builder."""
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(tmp_path / "schemas" / "s.yaml"))
    return lambda body, schema_text: _config_dir(tmp_path, body, schema_text)


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search('name="_csrf_token" value="([^"]*)"', page).group(1)
    revision = re.search('name="_revision" value="([^"]*)"', page).group(1)
    return csrf, revision


def _posted_form(page: str) -> dict:
    """Every ``f.`` field a browser WOULD post for *page* — the ``<template>`` contents (inert in a
    real browser) are stripped, so the page's own real inputs are what is reproduced."""
    page = re.sub(r"<template\b.*?</template>", "", page, flags=re.S)
    form = {}
    for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", page):
        name = re.search(r'name="([^"]*)"', tag)
        if not name or not name.group(1).startswith("f."):
            continue
        value = re.search(r'value="([^"]*)"', tag)
        form[name.group(1)] = html.unescape(value.group(1)) if value else ""
    return form


async def _open_and_post(client, page: str, change: dict | None = None) -> str:
    """Post the page's own form (optionally moving fields in *change*); return the landed HTML."""
    csrf, revision = _csrf_and_revision(page)
    form = _posted_form(page)
    form["_csrf_token"] = csrf
    form["_revision"] = revision
    if change:
        form.update(change)
    await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    return (await client.get(settings_page.SETTINGS_MAIN_PATH)).text


class _Form:
    """A minimal posted-form double: ``multi_items`` like Starlette's, order preserved."""

    def __init__(self, items):
        self._items = list(items)

    def multi_items(self):
        return list(self._items)


def _tree_of(config_dir):
    """The generator tree for *config_dir*'s ``schemas/s.yaml`` — the same tree the page renders from."""
    return configgen.describe(configgen.load_schema(config_dir / "schemas" / "s.yaml",
                                                    schema_files=[]))


@pytest.mark.asyncio
async def test_an_existing_seq_row_renders_its_value_and_the_length_counter(patched):
    """The rows the file carries are editable inputs, and ``__n`` counts them — not 0."""
    config_dir = patched(SEQ_FIXTURE, SEQ_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert 'name="f.rows.__n" value="2"' in page
    assert 'name="f.rows.0.n" value="3"' in page
    assert 'name="f.rows.1.n" value="4"' in page


@pytest.mark.asyncio
async def test_an_unchanged_save_of_a_page_with_seq_rows_is_a_byte_identical_no_op(patched):
    """THE failing case: posting the page UNCHANGED keeps every row (it did not truncate to [])."""
    config_dir = patched(SEQ_FIXTURE, SEQ_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        landed = await _open_and_post(client, page)
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == SEQ_FIXTURE
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_NO_CHANGE


@pytest.mark.asyncio
async def test_a_seq_row_edit_lands_without_touching_the_other_row(patched):
    """A moved row writes; the sibling row is untouched — the container reads AND writes per index."""
    config_dir = patched(SEQ_FIXTURE, SEQ_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        await _open_and_post(client, page, {"f.rows.1.n": "9"})
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == "rows:\n  - n: 3\n  - n: 9\n"


def test_clearing_a_row_value_is_not_silently_dropped(tmp_path, patched):
    """THE write-path blocker, on the exact input the review proved it with.

    ``value_at`` handled only ``*`` for a sequence, so a numeric token against a list returned ``None``:
    the CURRENT value of every posted row leaf read as ``None`` and the "absent current + empty post =
    nothing to change" guard (``apply_form``) then swallowed an operator CLEARING a row value.

        document rows: [{n: 3}, {n: 4}], posted f.rows.__n=2, f.rows.0.n='', f.rows.1.n='9'
        BEFORE: rows: [{n: 3}, {n: 9}]   <- the clear was silently dropped
        AFTER:  rows: [{n: ''}, {n: 9}]  <- the clear lands

    This drives ``apply_form`` directly (the unit the review probed), so it does not depend on the
    schema accepting an empty int — the page's own end-to-end flow is pinned separately below.

    """
    config_dir = patched(SEQ_FIXTURE, SEQ_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.rows.__n", "2"), ("f.rows.0.n", ""), ("f.rows.1.n", "9")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    rows = document["rows"]
    assert len(rows) == 2
    assert rows[0]["n"] != 3, "the cleared row kept its stale value — the clear was swallowed"
    assert rows[0]["n"] == "", f"the clear did not land: {rows[0]!r}"
    assert rows[1]["n"] == 9


@pytest.mark.asyncio
async def test_clearing_a_row_value_through_the_page_lands_and_is_reported_as_saved(patched):
    """The REAL page flow the card names: a row's value is cleared, the save writes it.

    Uses a row of a nullable ``str`` leaf so the cleared (empty) value is schema-legal — the point
    is that the clear is no longer swallowed and reported as "Nothing changed".

    """
    schema = ("type: map\nnullable: false\nmapping:\n  rows:\n    type: seq\n    nullable: false\n"
              "    sequence:\n      - type: map\n        nullable: false\n        mapping:\n"
              "          tag: {type: str}\n")
    config_dir = patched("rows:\n  - tag: a\n  - tag: b\n", schema)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        landed = await _open_and_post(client, page, {"f.rows.0.tag": ""})
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == "rows:\n  - tag: ''\n  - tag: b\n"
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_RESTART


@pytest.mark.asyncio
async def test_an_existing_regex_entry_renders_its_key_and_value(patched):
    """The entry the file carries is an editable input, keyed by its (escaped) free-text key."""
    config_dir = patched(REGEX_FIXTURE, REGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert 'name="f.commands.greet%2Euser.name" value="hello"' in page
    assert 'name="f.commands.greet%2Euser.__key" value="greet.user"' in page


@pytest.mark.asyncio
async def test_an_unchanged_save_of_a_page_with_regex_entries_is_a_byte_identical_no_op(patched):
    config_dir = patched(REGEX_FIXTURE, REGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        landed = await _open_and_post(client, page)
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == REGEX_FIXTURE
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_NO_CHANGE


@pytest.mark.asyncio
async def test_removing_a_regex_entry_through_the_page_deletes_it(patched):
    """Adjacent gap 1: removing an EXISTING entry with the X now DELETES it (it used to be a lie).

    A removed row posts neither its ``__key`` echo nor any sub-field, so the save cannot see it
    directly; the container's ``__entries`` companion is what records that the entry was RENDERED, and
    a rendered key with no survivor is a removal. Before this the save reported "Nothing changed"
    while the entry stayed — an operator who deletes an entry and is told nothing changed has been lied
    to.

    """
    config_dir = patched(REGEX_FIXTURE, REGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        dropped = [k for k in form if k.startswith("f.commands.greet%2Euser.")]
        assert dropped, "the existing entry's fields were not on the page"
        for key in dropped:
            del form[key]
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
        landed = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == "commands: {}\n"
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_RESTART


@pytest.mark.asyncio
async def test_a_new_regex_entry_cannot_clobber_an_existing_one(patched):
    """Adjacent gap 2: a NEW entry whose key matches an existing one is REFUSED, not a silent overwrite.

    The shipped ``configgen.js`` renames a new row's sub-fields onto the typed key, so a new entry
    keyed ``greet.user`` posts ``f.commands.greet%2Euser.name`` — the SAME name the existing entry
    uses — and the save used to overwrite the existing entry while the banner said "Saved". That is
    data loss: the save is now refused and nothing is written.

    """
    config_dir = patched(REGEX_FIXTURE, REGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        form["f.commands.__key"] = "greet.user"
        form["f.commands.greet%2Euser.name"] = "clobbered"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
        landed = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == REGEX_FIXTURE
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVE_REFUSED_DUPLICATE_ENTRY


@pytest.mark.asyncio
async def test_a_new_regex_entry_with_a_free_key_is_added(patched):
    """The positive control for gap 2: a new entry whose key is NEW is added, not refused."""
    config_dir = patched(REGEX_FIXTURE, REGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        form["f.commands.__key"] = "greet.new"
        form["f.commands.greet%2Enew.name"] = "welcome"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    after = (config_dir / "main.yaml").read_text(encoding="utf-8")
    assert "greet.new:" in after and "name: welcome" in after
    assert "greet.user:" in after and "name: hello" in after


_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                    reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def configgen_script() -> dict:
    """The shipped ``configgen.js``'s own answers, driven by the harness over the REAL file."""
    done = subprocess.run(["node", str(CONFIGGEN_HARNESS), str(CONFIGGEN_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_requires_node
def test_a_seq_of_maps_can_be_emptied_in_the_shipped_script(configgen_script):
    """Adjacent gap 3: the remove handler empties a seq — zero rows and ``__n`` posts 0.

    Verified against the REAL shipped script: removing a seq's last row does NOT keep an empty row,
    so the length counter reaches 0 and the save truncates the sequence to ``[]``.

    """
    assert configgen_script["seqEmptied"] == {"rows": 0, "count": "0"}


@pytest.mark.asyncio
async def test_a_seq_of_maps_can_be_emptied_through_the_page(patched):
    """The server half of gap 3: posting ``__n = 0`` with no rows writes ``rows: []``."""
    config_dir = patched(SEQ_FIXTURE, SEQ_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        for key in [k for k in form if k.startswith("f.rows.") and not k.endswith(".__n")]:
            del form[key]
        form["f.rows.__n"] = "0"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
        landed = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == "rows: []\n"
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_RESTART


def _regex_field(config_dir):
    tree = configgen.describe(configgen.load_schema(config_dir / "schemas" / "s.yaml"))
    field = next(f for f in configgen.flatten(tree) if f.kind == configgen.KIND_REGEX)
    return tree, field


def test_regex_value_excludes_a_fixed_key_beside_the_regex(tmp_path):
    """``enabled`` is a real key the mapping covers — never listed as a "custom entry"."""
    config_dir = _config_dir(tmp_path, SIBLING_FIXTURE, SIBLING_SCHEMA)
    tree, field = _regex_field(config_dir)
    value = configgen.regex_value(tree, field, configgen.read_document(config_dir / "main.yaml"))
    assert sorted(value) == ["greet"]


def test_regex_value_excludes_default_for_a_top_level_regex(tmp_path):
    """A top-level ``regex;(.+)`` reports the instance keys — NOT ``DEFAULT`` (nor the whole document)."""
    config_dir = _config_dir(tmp_path, TOPREGEX_FIXTURE, TOPREGEX_SCHEMA)
    tree, field = _regex_field(config_dir)
    value = configgen.regex_value(tree, field, configgen.read_document(config_dir / "main.yaml"))
    assert sorted(value) == ["inst1"]


@pytest.mark.asyncio
async def test_a_top_level_regex_instance_edit_lands(patched):
    """The ``()`` path case round-trips: the instance key is the field's FIRST token, not an empty one."""
    config_dir = patched(TOPREGEX_FIXTURE, TOPREGEX_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        landed = await _open_and_post(client, page, {"f.inst1.n": "9"})
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == "DEFAULT:\n  n: 1\ninst1:\n  n: 9\n"
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVED_RESTART


@pytest.mark.asyncio
async def test_the_layout_chooser_renders_prefilled_and_a_no_op_save_keeps_the_file(patched):
    """The chooser's editors read each option's value the same way — through item access, not a method."""
    config_dir = patched(CHOICE_FIXTURE, CHOICE_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        await _open_and_post(client, page)
    assert 'value="x"' in page
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == CHOICE_FIXTURE


def test_the_template_reads_a_rows_values_with_item_access():
    """A static pin: ``row.values`` would resolve to the ``dict.values`` METHOD, not the row's mapping.

    The control macros live in the shared partial ``_config_fields.html`` (the ONE renderer the
    Settings page and a node's settings page both import), so that is where the pin reads them.

    """
    template = (REPO_ROOT / "services" / "webservice" / "templates" /
                "_config_fields.html").read_text(encoding="utf-8")
    assert "row['values'].get(rel)" in template
    assert "row.values.get(" not in template
    assert "entry['values'].get(rel)" in template
    assert "option['values'].get(rel)" in template


def test_the_page_reads_a_containers_value_from_the_document():
    """The fix's own line, pinned statically: the container value comes from the document, not ``values``."""
    source = (REPO_ROOT / "services" / "webservice" / "pages" /
              "settings.py").read_text(encoding="utf-8")
    assert "configgen.value_at(document, field.path)" in source
    assert "configgen.regex_value(tree, field, document)" in source


def test_the_regex_container_posts_the_entries_it_rendered():
    """A static pin: the regex container ships the ``__entries`` companion the save reads removals from.

    Read from the shared control partial (``_config_fields.html``), where the regex macro now lives.
    """
    template = (REPO_ROOT / "services" / "webservice" / "templates" /
                "_config_fields.html").read_text(encoding="utf-8")
    assert "__entries" in template
    assert "map(attribute='enc_key')" in template


def test_the_duplicate_entry_refusal_string_is_shipped_in_every_catalog():
    """The new refusal string is i18n-marked and filled in the four translated catalogs (never fuzzy)."""
    from babel.messages import pofile

    for language in ("cn", "de", "es", "ru"):
        with open(REPO_ROOT / "locale" / language / "LC_MESSAGES" / "webui.po",
                  encoding="utf-8") as handle:
            catalog = pofile.read_po(handle)
        by_id = {message.id: message for message in catalog if message.id}
        text = settings_page.SAVE_REFUSED_DUPLICATE_ENTRY
        assert text in by_id, f"'{text}' is missing from the {language} catalog"
        assert not by_id[text].fuzzy, f"'{text}' is fuzzy in {language}"
        assert by_id[text].string, f"'{text}' is left untranslated in {language}"


TWO_ENTRIES_FIXTURE = "commands:\n  a:\n    name: hi\n  b:\n    name: yo\n"


def test_a_forged_entries_list_naming_a_literal_sibling_deletes_nothing(tmp_path):
    """Gate 1a: a posted ``__entries`` naming a key the mapping covers by NAME must not delete it.

    ``enabled`` is a literal sibling of the regex; the render path never lists it as a custom entry,
    but the DELETE path took the posted list at face value. A forged post naming it must delete nothing.
    """
    config_dir = _config_dir(tmp_path, SIBLING_FIXTURE, SIBLING_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.__entries", "enabled"), ("f.greet.__key", "greet"), ("f.greet.name", "hi")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    assert document.get("enabled") is True, "a forged entry list deleted a literal sibling"
    assert document["greet"]["name"] == "hi", "the real entry was disturbed"


def test_a_forged_entries_list_naming_default_deletes_nothing(tmp_path):
    """Gate 1b: a top-level regex forged to name ``DEFAULT`` must not delete the DEFAULT block."""
    config_dir = _config_dir(tmp_path, TOPREGEX_FIXTURE, TOPREGEX_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.__entries", "DEFAULT"), ("f.inst1.__key", "inst1"), ("f.inst1.n", "2")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    assert document["DEFAULT"]["n"] == 1, "a forged entry list deleted DEFAULT"
    assert document["inst1"]["n"] == 2, "the instance was disturbed"


def test_a_legitimately_removed_entry_is_still_deleted(tmp_path):
    """Gate 1c (positive control): an operator entry with no survivor is STILL deleted."""
    config_dir = _config_dir(tmp_path, REGEX_FIXTURE, REGEX_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.commands.__entries", "greet%2Euser")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    assert "greet.user" not in document["commands"], "the removed entry survived"


def test_one_of_two_entries_removed_leaves_the_survivor(tmp_path):
    """Gate 1d: two entries rendered, one removed — the survivor keeps its value, the other goes."""
    config_dir = _config_dir(tmp_path, TWO_ENTRIES_FIXTURE, REGEX_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.commands.__entries", "a\nb"), ("f.commands.b.__key", "b"),
                  ("f.commands.b.name", "yo")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    assert "a" not in document["commands"], "the removed entry survived"
    assert document["commands"]["b"]["name"] == "yo", "the surviving entry lost its value"


NULLABLE_SEQ_SCHEMA = """\
type: map
nullable: false
mapping:
  rows:
    type: seq
    nullable: false
    sequence:
      - type: map
        nullable: false
        mapping:
          n: {type: int}
"""
NULLABLE_SEQ_FIXTURE = "rows:\n  - n: 3\n  - n: 4\n"


def test_a_forged_out_of_range_row_index_invents_no_rows(tmp_path):
    """Gate 2 (unit): the exact forged input must leave the document with its two real rows only.

    ``n`` is nullable, so the schema cannot refuse the junk rows — the server itself must refuse the
    forged index. The document is asserted unchanged, so no empty row was invented.
    """
    config_dir = _config_dir(tmp_path, NULLABLE_SEQ_FIXTURE, NULLABLE_SEQ_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.rows.__n", "6"), ("f.rows.5.n", "7")])
    with pytest.raises(configgen.UnknownFieldError):
        configgen.apply_form(document, form, _tree_of(config_dir))
    assert [row["n"] for row in document["rows"]] == [3, 4], "the forged index invented rows"


@pytest.mark.asyncio
async def test_a_forged_out_of_range_row_index_refuses_the_save(patched):
    """Gate 2 (end to end): the forged index is refused and the FILE keeps its two real rows."""
    config_dir = patched(NULLABLE_SEQ_FIXTURE, NULLABLE_SEQ_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        form["f.rows.__n"] = "6"
        form["f.rows.5.n"] = "7"
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
        landed = (await client.get(settings_page.SETTINGS_MAIN_PATH)).text
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == NULLABLE_SEQ_FIXTURE, \
        "the forged out-of-range index invented rows on disk"
    banner = re.search('notice-msg">([^<]*)<', landed)
    assert html.unescape(banner.group(1)) == settings_page.SAVE_REFUSED_UNPLACEABLE
