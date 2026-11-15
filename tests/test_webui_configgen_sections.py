"""A ``regex;`` key whose pattern is a FIXED list of literal names renders TYPED SECTIONS.

THE CARD. A pykwalify map key may be ``regex;<pattern>``. When the pattern is a pure
alternation of literal names — ``regex;(frontend|client)``, ``regex;(backend|server)``,
``regex;(on_add|on_remove|on_leave)`` — the schema is not describing "any key the operator invents"
but a FINITE menu of names it may carry (the Olympus extension is the reported example: the section
name changed from ``frontend`` to ``client`` between versions and the schema allows both). Before
this card such a key fell back to the free-text "Custom entries" block, so the operator typed the
name by hand and the page could not say which names were legal.

The fix reads the names OUT OF THE SCHEMA (:func:`configgen.alternatives`) and renders:
* one TYPED block per name the file already carries — the SAME markup and the SAME control names a
  literal-named ``frontend:`` map produces;
* a ``+ Add section`` control offering EXACTLY the schema's names the file does not carry yet;
* adds/removes through the SAME regex container save path, so every other key stays byte-identical.

A pattern that is NOT a finite list (``regex;(.+)``, ``regex;(\\d+)``, a port range) keeps the honest
"Add entry" fallback — the schema is the source of truth for which case applies, never a hardcoded
plugin list.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import asgi_client
from services.webservice import configgen
from services.webservice.pages import settings as settings_page
from test_webui_configgen_containers import (
    _Form, _config_dir, _csrf_and_revision, _posted_form, _tree_of,
)
from test_webui_dashboard import _build, _hash, _login

#: a FIXED-ALTERNATIVE container beside a normal literal key, so "every other key is untouched" has
#: something to stay untouched about.
SECTIONS_SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      name: {type: str, nullable: false}
      regex;(frontend|client):
        type: map
        nullable: false
        mapping:
          port: {type: int, nullable: false}
"""

#: the SAME shape with the section spelled LITERALLY — the markers a typed section must reproduce.
LITERAL_SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      name: {type: str, nullable: false}
      frontend:
        type: map
        nullable: false
        mapping:
          port: {type: int, nullable: false}
"""

#: the SAME shape marked ``func: one_of`` — its names are SPELLINGS OF ONE SECTION, not several
#: sections, so once one spelling is present the ``+`` offers nothing (adding the other would write the
#: same block twice). The function itself lives in ``core/utils/validators.py``.
ONE_OF_SCHEMA = """\
type: map
nullable: false
mapping:
  DEFAULT:
    type: map
    nullable: false
    mapping:
      name: {type: str, nullable: false}
      regex;(frontend|client):
        type: map
        nullable: false
        func: one_of
        mapping:
          port: {type: int, nullable: false}
"""

#: an UNBOUNDED key — keeps the honest "Add entry" fallback (it names no fixed list).
OPEN_SCHEMA = """\
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
          name: {type: str, nullable: false}
"""

#: the whole dict the container write must produce, byte-for-byte.
BOTH = "DEFAULT:\n  name: x\n  frontend:\n    port: 1\n  client:\n    port: 5\n"


def _app(config_dir, roles=("Admin",)):
    return _build(config_dir, users=[{"username": "staff", "password_hash": _hash(),
                                      "roles": list(roles)}])[0]


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Point the Settings page at *tmp_path*'s main.yaml + schemas/s.yaml (the containers fixture)."""
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(tmp_path / "schemas" / "s.yaml"))
    return lambda body, schema_text: _config_dir(tmp_path, body, schema_text)


async def _open(client):
    return (await client.get(settings_page.SETTINGS_MAIN_PATH)).text


async def _post(client, page, change=None) -> str:
    csrf, revision = _csrf_and_revision(page)
    form = _posted_form(page)
    form["_csrf_token"] = csrf
    form["_revision"] = revision
    if change:
        form.update(change)
    await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    return (await client.get(settings_page.SETTINGS_MAIN_PATH)).text


def _picker_options(page: str, index: int = 0) -> list[str]:
    """The names the ``+ Add section`` picker at *index* offers, in rendered order.

    A block may carry SEVERAL alternative containers (Olympus: ``(backend|server)`` and
    ``(frontend|client)``), so each picker is read from its own ``<select>`` block.
    """
    selects = re.findall(r'<select[^>]*data-cfg-sectionpick[^>]*>(.*?)</select>', page, re.S)
    if index >= len(selects):
        return []
    return re.findall(r'<option value="([^"]*)">', selects[index])


# ------------------------------------------------------------- 1. the parser reads the schema

def test_alternatives_parses_the_fixed_lists_and_rejects_the_patterns():
    """The names come from the PATTERN; a pattern that is not a fixed list yields nothing."""
    assert configgen.alternatives("(frontend|client)") == ("frontend", "client")
    assert configgen.alternatives("(backend|server)") == ("backend", "server")
    assert configgen.alternatives("on_add|on_remove|on_leave") == ("on_add", "on_remove", "on_leave")
    assert configgen.alternatives("(Airbase|Carrier|FARP|\\*)") == ("Airbase", "Carrier", "FARP", "*")
    assert configgen.alternatives(r"(\_OK\_|OK|\(OK\)|B|\-\-|WO|C|NC|_n)") == \
        ("_OK_", "OK", "(OK)", "B", "--", "WO", "C", "NC", "_n")
 # NOT expressible as a finite literal list -> the honest fallback (empty)
    for pattern in ("(.+)", "(.*)", r"(\d+)", r"(^\d+$)",
                    r"(^(0?[0-9]|1[0-9]|2[0-4])(:[0-5][0-9])?-(0?[0-9]|1[0-9]|2[0-4])(:?[0-5][0-9])?$)",
                    ""):
        assert configgen.alternatives(pattern) == (), pattern


def test_the_olympus_instance_block_carries_its_section_names():
    """The reported example: Olympus's ``regex;(frontend|client)``/``(backend|server)`` are alternatives.

    Read from the REAL extension schema, through the node schema the console renders (which pulls every
    ``extensions/**/*_schema.yaml``). Both names are the schema's own list, never hardcoded here.
    """
    from services.webservice import scopes as scopes_module

    schema, _node_partial, instance_partial = scopes_module.extension_schema_parts("Olympus")
    assert instance_partial, "Olympus declares no instance partial"

    root = configgen.describe(configgen.Schema(
        root={"type": "map", "mapping": {"DEFAULT": {"include": instance_partial}}},
        partials=schema.partials, included_files=schema.included_files, primary=schema.primary))
    found = {field.regex: field.alternatives
             for field in configgen.flatten(root)
             if field.kind == configgen.KIND_REGEX and field.alternatives}
    assert found.get("(frontend|client)") == ("frontend", "client")
    assert found.get("(backend|server)") == ("backend", "server")


# ------------------------------------------------------------- 2. the page renders TYPED sections

@pytest.mark.asyncio
async def test_a_fixed_alternative_section_renders_like_a_literal_named_map(tmp_path, monkeypatch):
    """The chosen name renders EXACTLY as if the schema carried it literally: same control names.

    TWO config dirs, because the schema is read from disk at REQUEST time: A's schema is the
    alternative form, B's spells the same section literally.
    """
    body = "DEFAULT:\n  name: x\n  frontend:\n    port: 1\n"
    monkeypatch.setattr(settings_page, "CONFIG_FILE_NAME", "main.yaml")

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    dir_a = _config_dir(tmp_path / "a", body, SECTIONS_SCHEMA)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(dir_a / "schemas" / "s.yaml"))
    async with asgi_client(_app(dir_a)) as client:
        await _login(client, "staff")
        page = await _open(client)

    dir_b = _config_dir(tmp_path / "b", body, LITERAL_SCHEMA)
    monkeypatch.setattr(settings_page, "MAIN_SCHEMA_PATH", str(dir_b / "schemas" / "s.yaml"))
    async with asgi_client(_app(dir_b)) as client:
        await _login(client, "staff")
        literal_page = await _open(client)

    assert 'name="f.DEFAULT.frontend.port" value="1"' in page
    assert 'name="f.DEFAULT.frontend.port" value="1"' in literal_page
    assert '>frontend<' in page                                     # a TYPED block keyed by the name
    assert "A key the schema describes with a regex" not in page    # NOT the free-text fallback
    assert "data-cfg-sections" in page


@pytest.mark.asyncio
async def test_the_add_control_offers_exactly_the_allowed_names(patched):
    """The ``+`` offers the schema's names the file does not carry yet — here, only ``client``."""
    app = _app(patched("DEFAULT:\n  name: x\n  frontend:\n    port: 1\n", SECTIONS_SCHEMA))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    assert _picker_options(page) == ["client"]
    assert "+ Add section" in page


@pytest.mark.asyncio
async def test_an_empty_alternative_container_offers_all_its_names(patched):
    """With nothing present the container is a "+ Add" CHOICE; once a section is set its own picker
    offers the REST of the schema's list, in the schema's own order."""
    config_dir = patched("DEFAULT:\n  name: x\n", SECTIONS_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
 # the container is optional AND absent: it is NOT rendered live; it is a picker choice naming
 # the schema's own list (its markup survives only inside the picker's inert <template>).
        assert "frontend | client" in page
        assert "f.DEFAULT.frontend.port" not in page
 # once a section is present the container renders live and its picker offers the OTHER name
        await _post(client, page, {"f.DEFAULT.__key": "frontend",
                                   "f.DEFAULT.frontend.port": "1"})
        page = await _open(client)
        assert "f.DEFAULT.frontend.port" in page
        assert _picker_options(page) == ["client"]


# ------------------------------------------- 2b. `func: one_of` — the names are ONE section

def _container_of(schema_text, tmp_path):
    from test_webui_configgen_containers import _config_dir
    config_dir = _config_dir(tmp_path, "DEFAULT:\n  name: x\n", schema_text)
    tree = configgen.describe(configgen.load_schema(str(config_dir / "schemas" / "s.yaml")))
    return next(field for field in configgen.flatten(tree)
                if field.kind == configgen.KIND_REGEX and field.alternatives)


def test_a_one_of_container_offers_nothing_once_a_spelling_is_present(tmp_path):
    """The schema's own marker decides: present ⇒ the section EXISTS ⇒ no second spelling on offer."""
    container = _container_of(ONE_OF_SCHEMA, tmp_path)
    assert container.element is not None and container.element.func == configgen.ONE_OF_FUNC

    empty = configgen.custom_entries(container, {})
    assert configgen.section_state(container, empty)["missing"] == ["frontend", "client"]

    one = configgen.custom_entries(container, {"frontend": {"port": 1}})
    assert configgen.section_state(container, one) == {"present": ["frontend"], "missing": []}

    both = configgen.custom_entries(container, {"frontend": {"port": 1}, "client": {"port": 2}})
    assert configgen.section_state(container, both)["missing"] == []


def test_a_container_without_the_marker_still_offers_the_other_spelling(tmp_path):
    """The other 11 containers carry no ``func:``: their sibling names stay on offer, as before."""
    container = _container_of(SECTIONS_SCHEMA, tmp_path)
    one = configgen.custom_entries(container, {"frontend": {"port": 1}})
    assert configgen.section_state(container, one)["missing"] == ["client"]


@pytest.mark.asyncio
async def test_a_one_of_container_renders_the_spelling_the_file_uses_but_offers_no_second(patched):
    """End to end on the page: the section renders and saves; the ``+`` is simply not there."""
    config_dir = patched("DEFAULT:\n  name: x\n  frontend:\n    port: 1\n", ONE_OF_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        landed = await _post(client, page)
    assert "f.DEFAULT.frontend.port" in page, "the spelling the file uses must keep rendering"
    assert _picker_options(page) == []
    assert "+ Add section" not in page, "a second spelling is never offered"
    assert "Nothing changed" in landed, "the save is a no-op, as an unchanged page must be"
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == \
        "DEFAULT:\n  name: x\n  frontend:\n    port: 1\n", "an unchanged save stays byte-identical"


# ------------------------------------------------------------- 3. add & remove are exact

@pytest.mark.asyncio
async def test_adding_a_section_writes_exactly_that_key_and_nothing_else(patched):
    """Add ``client``: exactly that key is written; the existing name/section stay byte-identical."""
    config_dir = patched("DEFAULT:\n  name: x\n  frontend:\n    port: 1\n", SECTIONS_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        landed = await _post(client, page, {"f.DEFAULT.__key": "client",
                                            "f.DEFAULT.client.port": "5"})
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == BOTH
    assert "Nothing changed" not in landed


@pytest.mark.asyncio
async def test_adding_a_second_section_is_allowed(patched):
    """A container that may carry several names gets a SECOND section: add frontend, then client."""
    config_dir = patched("DEFAULT:\n  name: x\n", SECTIONS_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        await _post(client, page, {"f.DEFAULT.__key": "frontend", "f.DEFAULT.frontend.port": "1"})
        page = await _open(client)
        assert _picker_options(page) == ["client"]
        await _post(client, page, {"f.DEFAULT.__key": "client", "f.DEFAULT.client.port": "5"})
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == BOTH


@pytest.mark.asyncio
async def test_removing_a_section_deletes_exactly_that_key(patched):
    """Remove ``frontend``: only it is deleted; the sibling section and the literal key are untouched."""
    body = "DEFAULT:\n  name: x\n  frontend:\n    port: 1\n  client:\n    port: 2\n"
    config_dir = patched(body, SECTIONS_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        csrf, revision = _csrf_and_revision(page)
        form = _posted_form(page)
        for key in [k for k in form if k.startswith("f.DEFAULT.frontend.")]:
            del form[key]                       # the browser dropped the frontend section's row
        form["_csrf_token"] = csrf
        form["_revision"] = revision
        await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == (
        "DEFAULT:\n  name: x\n  client:\n    port: 2\n")


@pytest.mark.asyncio
async def test_an_unchanged_save_of_a_sections_page_is_byte_identical(patched):
    """No-op: posting the rendered page unchanged keeps the file exactly as it was."""
    body = "DEFAULT:\n  name: x\n  frontend:\n    port: 1\n"
    config_dir = patched(body, SECTIONS_SCHEMA)
    app = _app(config_dir)
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
        landed = await _post(client, page)
    assert (config_dir / "main.yaml").read_text(encoding="utf-8") == body
    assert "Nothing changed" in landed


# ------------------------------------------------------------- 4. the schema is the source of truth

def test_a_new_section_key_outside_the_schema_list_is_refused(tmp_path, patched):
    """A new key that is NOT one of the schema's names is refused — nothing written."""
    config_dir = patched("DEFAULT:\n  name: x\n", SECTIONS_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.DEFAULT.__entries", ""),
                  ("f.DEFAULT.__key", "backend"),           # NOT in (frontend|client)
                  ("f.DEFAULT.backend.port", "9")])
    with pytest.raises(configgen.UnknownFieldError):
        configgen.apply_form(document, form, _tree_of(config_dir))
    assert "backend" not in document["DEFAULT"]


def test_deleting_a_key_outside_the_schema_list_deletes_nothing(tmp_path, patched):
    """A forged ``__entries`` naming a key OUTSIDE this container's list never deletes it."""
    config_dir = patched("DEFAULT:\n  name: x\n  frontend:\n    port: 1\n", SECTIONS_SCHEMA)
    document = configgen.read_document(config_dir / "main.yaml")
    form = _Form([("f.DEFAULT.__entries", "frontend\nnonsense")])
    configgen.apply_form(document, form, _tree_of(config_dir))
    assert "frontend" not in document["DEFAULT"]     # the real, listed section IS removed
    assert document["DEFAULT"]["name"] == "x"        # the literal key survived
    assert "nonsense" not in document["DEFAULT"]


# ------------------------------------------------------------- 5. the honest fallback stays

@pytest.mark.asyncio
async def test_an_unbounded_regex_keeps_the_custom_entries_fallback(patched):
    """``regex;(.+)`` names no fixed list, so it stays the free-text "Add entry" control."""
    app = _app(patched("commands:\n  greet:\n    name: hi\n", OPEN_SCHEMA))
    async with asgi_client(app) as client:
        await _login(client, "staff")
        page = await _open(client)
    assert "A key the schema describes with a regex" in page
    assert "data-cfg-sections" not in page
    assert "+ Add entry" in page


# ------------------------------------------------- 5b. the REAL Olympus shape on its own page

OLYMPUS_NODES = """\
dcs-a-01:
  listen_address: 127.0.0.1
  listen_port: 10042
  instances:
    Syria:
      home: /home/a-syria
      bot_port: 6666
      webgui_port: 8086
      dcs_port: 10306
      extensions:
        Olympus:
          frontend:
            path: /opt/olympus-frontend
            port: 8080
"""
OLYMPUS_BOT_YAML = "roles:\n  Admin:\n    - specialk\n"


@pytest.fixture
def olympus_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(OLYMPUS_BOT_YAML, encoding="utf-8")
    (tmp_path / "nodes.yaml").write_text(OLYMPUS_NODES, encoding="utf-8")
    return tmp_path


@pytest.fixture
def olympus_data(olympus_dir, monkeypatch):
    """Bind the bot's validator to THIS install's cluster (the node-page tests' own recipe)."""
    import types
    import core.commandline as commandline
    from core.utils import validators

    argv = types.SimpleNamespace(node="master", config=str(olympus_dir))
    monkeypatch.setattr(commandline, "COMMAND_LINE_ARGS", argv, raising=False)
    monkeypatch.setattr(validators, "COMMAND_LINE_ARGS", argv, raising=False)
    validators.NodeData._instance = None
    yield


@pytest.mark.asyncio
async def test_the_olympus_extension_page_renders_its_sections(olympus_dir, olympus_data):
    """The reported example end to end: Olympus's ``frontend`` renders as a typed SECTION.

    Both containers carry ``func: one_of`` — frontend/client are two SPELLINGS of ONE section, as are
    backend/server — so the file's ``frontend`` renders as a ``st-block`` keyed by the name (the same
    markup a literal map produces) and offers NO second spelling. The ABSENT ``(backend|server)``
    container is a "+ Add" CHOICE naming the schema's own list, so its names appear as a picker row,
    not as a rendered section — and never leak into the frontend block.
    """
    from test_webui_node_page import _fleet_source

    app = _build(olympus_dir, users=[{"username": "specialk", "password_hash": _hash(),
                                      "roles": ["Admin"]}])[0]
    app.state.webui_source_provider = _fleet_source
    async with asgi_client(app) as client:
        await _login(client, "specialk")
        page = (await client.get("/nodes/dcs-a-01/extensions/Olympus")).text

    assert "data-cfg-sections" in page
    assert 'name="f.dcs-a-01.instances.Syria.extensions.Olympus.frontend.path"' in page
    assert ">frontend<" in page
 # Both containers are ``func: one_of``. The (frontend|client) container carries ``frontend``, so it
 # renders that block and offers NO second spelling. The ABSENT (backend|server) container is a
 # "+ Add" CHOICE naming the schema's own list — its names appear as a picker row, never as a
 # rendered section, and never leak into the frontend block.
    assert _picker_options(page, 0) == ["backend", "server"]
    assert _picker_options(page, 1) == [], "one_of: the present spelling offers no second picker"
    assert 'data-cfg-all="backend,server"' in page, "the picker carries the schema's FULL name list"
    assert "backend | server" in page


# ------------------------------------------------------------- 6. the shipped front-end carries it

def test_the_templates_and_script_carry_the_sections_mechanism():
    """Static pins: both control templates branch on ``f.alternatives``; the script wires the picker."""
    templates = Path(configgen.__file__).resolve().parent / "templates"
    config_fields = (templates / "_config_fields.html").read_text(encoding="utf-8")
    scope_fields = (templates / "_scope_fields.html").read_text(encoding="utf-8")
    assert "f.kind == 'regex' and f.alternatives" in config_fields
    assert "f.kind == 'regex' and f.alternatives" in scope_fields
    assert "data-cfg-sections" in config_fields and "data-cfg-sections" in scope_fields
    assert "data-cfg-sectionpick" in config_fields and "data-cfg-sectionpick" in scope_fields
    # a removed section's name must be offerable again without a reload: both pickers carry the FULL list
    assert "data-cfg-all" in config_fields and "data-cfg-all" in scope_fields
    script = (templates.parent / "static" / "configgen.js").read_text(encoding="utf-8")
    assert "data-cfg-sections" in script and "data-cfg-sectionpick" in script


_REPO_ROOT = Path(configgen.__file__).resolve().parents[2]
CONFIGGEN_JS = _REPO_ROOT / "services" / "webservice" / "static" / "configgen.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"

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
def test_the_shipped_script_adds_a_section_with_the_chosen_name(configgen_script):
    """The ``+`` clones the empty section, stamps the picked name, renames its fields, drops the option.

    Driven over the REAL ``configgen.js`` (a DOM stub), so the picker-driven add is exercised as the
    browser runs it — not only as the server reads the resulting post.
    """
    got = configgen_script["sectionAdded"]
    assert got["rows"] == 1
 # the picker `<select>` carries no name, so only the section's own controls are asserted here
    assert [n for n in got["names"] if n] == ["f.DEFAULT.__key", "f.DEFAULT.client.port"]
    assert got["nameSpan"] == "client"
    assert got["keyValue"] == "client"
    assert got["remaining"] == ["frontend"], "the used name must not be offered again"


@_requires_node
def test_the_shipped_script_offers_a_removed_section_again(configgen_script):
    """The reported bug: the ``+`` takes the name OUT of the picker, so REMOVING that section must put
    it back at once — otherwise it cannot be re-added without reloading, and when it was the last
    option the add bar stays hidden and the section is unreachable."""
    got = configgen_script["sectionRemoved"]
    assert got["afterAdd"] == {"remaining": [], "barHidden": True}, got["afterAdd"]
    assert got["rows"] == 0
    assert got["offered"] == ["client"], "the removed section's name must be offered again"
    assert got["barHidden"] is False, "the add bar must come back"


@_requires_node
def test_a_removed_section_comes_back_in_the_schemas_own_order(configgen_script):
    """It is re-inserted where the schema lists it, not appended: ``frontend`` before ``client``."""
    got = configgen_script["sectionRemovedInOrder"]
    assert got["rows"] == 0
    assert got["offered"] == ["frontend", "client"]


def test_the_new_strings_are_translated_in_every_catalog():
    """The new chrome is i18n-marked and filled in the four translated catalogs, never fuzzy."""
    from babel.messages import pofile

    repo = Path(configgen.__file__).resolve().parents[2]
    new_ids = [configgen.SECTION_LABEL, "+ Add section",
               "The schema names the sections this block may carry. Add the one this file "
               "should use.", "Section"]
    for language in ("cn", "de", "es", "ru"):
        with open(repo / "locale" / language / "LC_MESSAGES" / "webui.po", encoding="utf-8") as handle:
            catalog = pofile.read_po(handle)
        by_id = {message.id: message for message in catalog if message.id}
        for text in new_ids:
            assert text in by_id, f"'{text}' missing from the {language} catalog"
            assert not by_id[text].fuzzy, f"'{text}' is fuzzy in {language}"
            assert by_id[text].string, f"'{text}' is left untranslated in {language}"
