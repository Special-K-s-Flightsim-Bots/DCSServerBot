"""The expert-DSL preset's OWN YAML editor — one page, one block, no settings furniture.

A preset whose value carries ``modify`` is the expert DSL, which the weather wizard refuses. Its tile's
cog opens ``/presets/preset/<name>/yaml``: the preset's name and the file it lives in, ONE YAML editor
holding that preset's own block (its top-level key plus its value), a Save and a Cancel.

What the page must NOT carry is pinned here as hard as what it must: the Settings area's file tabs, a
schema-driven field, the read-only ``<pre>``/badge and the Expert Mode toggle all belong to a page that
edits a whole file, and this page edits one preset.

One section per acceptance criterion:

* the page renders ONE editor and none of the settings furniture;
* the editor's text is EXACTLY this preset's block — the other presets of the file are not in it;
* a save rewrites that one key, keeps every other preset byte-identical, keeps a ``.bak`` and refuses a
  stale revision;
* validation is the bot's, against ``presets_schema.yaml`` AND the realweather partial its ``include:``
  names, with nothing written on a refusal;
* a changed key, a second key and a non-mapping document are each refused with their own sentence;
* an in-use preset is editable (editing is not removal) and removing it is still refused;
* with JavaScript off the textarea carries the document and the form still posts ``_yaml``.

Driven in-process through the real application (the production factory, the shell and the local login
backend), reusing the write suite's harness so the app under test is the running one.
"""
from __future__ import annotations

import hashlib
import html as html_module
import re
from pathlib import Path

import pytest

from core.utils import configwrite
from services.webservice.auth import local as local_auth
from services.webservice.pages import presets as presets_page
from test_webui_write_pause import (  # noqa: F401 - the fixtures are imported on purpose
    PASSWORD, build, clean_registry, client, fleet, sign_in,
)

BOT_YAML = """\
roles:
  Admin:
    - admin
"""

#: one preset of its own values and one carrying the modify DSL, in ONE file.
PRESETS_YAML = """\
Spring:
  date: '2016-04-21'
  temperature: 8
  clouds:
    preset: Preset1
Carrier Freqs:
  modify:
    file: mission
    variables:
      theatre: theatre
"""

#: the block this page edits, as the page hands it to the editor.
CARRIER_BLOCK = """\
Carrier Freqs:
  modify:
    file: mission
    variables:
      theatre: theatre
"""

#: the SAME block after an edit — one variable added, nothing else touched.
CARRIER_EDITED = """\
Carrier Freqs:
  modify:
    file: mission
    variables:
      theatre: theatre
      speed: 40
"""

#: VALID YAML the schema REFUSES: ``terrain`` is an enum, so a name it does not carry is a schema
#: fault the DSL-specific validator alone would accept (the DSL validates ``modify`` only).
INVALID_TERRAIN = """\
Carrier Freqs:
  modify:
    file: mission
  terrain: Nowhere
"""

#: the realweather partial's own rule: an ICAO station must match ``^[A-Z]{4}$``, so a longer one is
#: refused ONLY when the partial behind the schema's ``include:`` is really loaded (without it the
#: whole ``RealWeather`` block would be an unknown key).
INVALID_REALWEATHER = """\
Carrier Freqs:
  modify:
    file: mission
  RealWeather:
    enabled: true
    options:
      weather:
        enable: true
        icao: PGUM1
"""

#: the same block with a value the partial accepts — proof the include is not simply unknown.
VALID_REALWEATHER = """\
Carrier Freqs:
  modify:
    file: mission
  RealWeather:
    enabled: true
    options:
      weather:
        enable: true
        icao: PGUM
"""

#: a document keyed with another preset's name: this editor does not rename.
RENAMED = CARRIER_BLOCK.replace("Carrier Freqs:", "Carrier:")

#: two top-level keys — a whole-file document posted at a one-preset page.
SECOND_KEY = CARRIER_BLOCK + "Autumn:\n  modify:\n    file: mission\n"

#: a document that is not a mapping at all.
NOT_A_MAPPING = "- Carrier Freqs\n- Spring\n"

NODES_REFERENCING = """\
NODE1:
  instances:
    Instance1:
      extensions:
        MizEdit:
          settings:
            "00:01-06:00": "Carrier Freqs"
"""


def _hash() -> str:
    return local_auth.hash_password(PASSWORD, iterations=local_auth.MIN_ITERATIONS)


@pytest.fixture
def config_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "presets.yaml").write_text(PRESETS_YAML, encoding="utf-8")
    return tmp_path


def _app(config_dir, roles=("Admin",)):
    users = [{"username": "staff", "password_hash": _hash(), "roles": list(roles)}]
    return build(config_dir, fleet(), users=users)


def _page_url(name: str = "Carrier Freqs") -> str:
    return presets_page.preset_yaml_url(name)


def _save_url(name: str = "Carrier Freqs") -> str:
    return presets_page.preset_yaml_save_url(name)


def _validate_url(name: str = "Carrier Freqs") -> str:
    return presets_page.preset_yaml_validate_url(name)


def _csrf_and_revision(page: str) -> tuple[str, str]:
    csrf = re.search(r'name="_csrf_token" value="([^"]*)"', page)
    revision = re.search(r'name="_revision" value="([^"]*)"', page)
    assert csrf is not None and revision is not None, "the page rendered no form carriers"
    return csrf.group(1), revision.group(1)


def _notice(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html_module.unescape(found.group(1))


def _editor_text(page: str) -> str:
    """The text the page's ONE editor carries — the textarea's own body, unescaped."""
    found = re.search(r'<textarea[^>]*data-cfg-yaml-text[^>]*>(.*?)</textarea>', page, re.DOTALL)
    assert found is not None, "the page rendered no editor carrier"
    return html_module.unescape(found.group(1))


def _digests(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


async def _open(http, url: str) -> str:
    response = await http.get(url)
    assert response.status_code == 200, f"{url} answered {response.status_code}: {response.text[:300]}"
    return response.text


# ---------------------------------------------- 1. ONE editor, and none of the settings furniture

@pytest.mark.asyncio
async def test_the_page_renders_one_editor_and_no_settings_furniture(config_dir):
    """Exactly one editor, holding the preset; none of the whole-file page's parts beside it."""
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())

    # the page names the preset and the file it lives in
    assert "Carrier Freqs" in page, "the page does not name the preset it edits"
    assert "config/presets.yaml" in page, "the page does not name the file the preset lives in"

    # ONE editor
    assert page.count("data-cfg-yaml-text") == 1, "the page does not carry exactly one editor"
    assert page.count("<textarea") == 1, "the page carries more than one text control"

    # NO settings file tabs (``_settings_files.html``)
    assert 'class="cfg-files"' not in page, "the settings area's file tabs are on the page"
    assert 'aria-label="Configuration files"' not in page, "the settings file list is on the page"

    # NO schema-driven field (``settings.html`` + ``_scope_fields.html``)
    assert 'class="cfg-controls"' not in page, "a schema-driven field row is on the page"
    assert 'class="cfg-scope' not in page, "a schema-driven scope section is on the page"
    assert 'class="cfg-tabs"' not in page, "the settings schema tabs are on the page"

    # NO read-only panel or its badge
    assert "data-cfg-yaml-readonly" not in page, "the read-only YAML panel is on the page"
    assert "data-cfg-yaml-badge" not in page, "the read-only badge is on the page"
    assert "<pre" not in page, "a read-only <pre> block is on the page"

    # NO Expert Mode toggle — the editor is the editor
    assert "data-cfg-expert-toggle" not in page, "the Expert Mode toggle is on the page"
    assert "Expert Mode" not in page, "the page names Expert Mode"
    assert 'name="_expert"' not in page, "the expert-mode companion field is on the page"

    # the contract the shared script reads: the panel, its validate URL and the status line
    assert "data-cfg-yaml" in page, "the editor panel is not the documented contract"
    assert f'data-validate-url="{_validate_url()}"' in page, "the editor has no validate URL"
    assert "data-cfg-yaml-status" in page, "the validation status line is missing"
    assert f'action="{_save_url()}"' in page, "the form does not post to this page's save"


def test_the_page_loads_the_shared_editor_and_the_vendored_yaml_mode(config_dir):
    """One editor behaviour in the codebase: the ONE script, the ONE vendored mode."""
    template = (Path(presets_page.__file__).parent.parent / "templates" / "presets_yaml.html")
    text = template.read_text(encoding="utf-8")
    assert "/static/settings-yaml.js" in text, "the page does not load the shared editor script"
    assert "/static/vendor/codemirror/mode/yaml.min.js" in text, "the vendored YAML mode is missing"
    assert "<script>" not in text, "the page carries an inline script"


# ---------------------------------------------- 2. the editor's text is this preset's block only

@pytest.mark.asyncio
async def test_the_editor_holds_exactly_this_presets_block(config_dir):
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
    text = _editor_text(page)
    document = configwrite.load_config_text(text, path=config_dir / "presets.yaml")
    assert list(document) == ["Carrier Freqs"], f"the editor carries {list(document)}"
    assert "modify" in document["Carrier Freqs"]
    # the other preset of the same file is NOT in the editor
    assert "Spring" not in text, "the editor shows another preset of the same file"
    assert "Preset1" not in text, "the editor carries another preset's values"
    assert text.strip() == CARRIER_BLOCK.strip(), f"the editor text is not the block: {text!r}"


# ---------------------------------------------- 3. the save: one key, the primitive, the guard

@pytest.mark.asyncio
async def test_a_save_rewrites_one_key_and_keeps_every_other_preset_identical(config_dir):
    path = config_dir / "presets.yaml"
    original = path.read_text(encoding="utf-8")
    before = configwrite.load_config_text(original, path=path)
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        response = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": CARRIER_EDITED})
    assert response.status_code == 303, response.text[:300]

    after_text = path.read_text(encoding="utf-8")
    after = configwrite.load_config_text(after_text, path=path)
    assert set(after) == {"Spring", "Carrier Freqs"}, "a preset appeared or vanished"
    assert after["Carrier Freqs"]["modify"]["variables"]["speed"] == 40, "the edit did not land"
    # every OTHER preset is byte-identical: its own dump is the same, and its lines are untouched
    for key in ("Spring",):
        assert (configwrite.dump_config({key: before[key]})
                == configwrite.dump_config({key: after[key]})), f"'{key}' changed"
        assert configwrite.dump_config({key: before[key]}).strip() in after_text, \
            f"'{key}' is not present verbatim after the save"
    # the primitive kept the previous generation
    backup = Path(str(path) + ".bak")
    assert backup.is_file(), "the primitive did not keep the previous generation"
    assert backup.read_text(encoding="utf-8") == original, "the .bak is not the previous content"


@pytest.mark.asyncio
async def test_a_stale_revision_is_refused_and_writes_nothing(config_dir):
    path = config_dir / "presets.yaml"
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, _revision = _csrf_and_revision(page)
        path.write_text(PRESETS_YAML + "Autumn:\n  modify:\n    file: mission\n", encoding="utf-8")
        moved = path.read_bytes()
        response = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": "not-the-current-revision", "_yaml": CARRIER_EDITED})
        landed = await _open(http, _page_url())
    assert response.status_code == 303
    assert path.read_bytes() == moved, "a stale save wrote to the file"
    assert "stale" in _notice(landed).lower(), "the refusal does not say the page was stale"


# ---------------------------------------------- 4. the bot's own schema pair decides

@pytest.mark.asyncio
async def test_a_block_the_schema_refuses_is_refused_with_nothing_written(config_dir):
    path = config_dir / "presets.yaml"
    before = path.read_bytes()
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        bad = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": INVALID_TERRAIN})
        landed = await _open(http, _page_url())
    assert bad.status_code == 303
    assert path.read_bytes() == before, "a block the schema refuses was written anyway"
    assert not Path(str(path) + ".bak").exists(), "a refused save left a backup behind"
    assert _notice(landed).startswith(presets_page.PRESETS_REFUSED_BANNER), \
        "the refusal does not carry the refused-change banner"


@pytest.mark.asyncio
async def test_the_realweather_partial_behind_the_include_is_really_loaded(config_dir):
    """The pair, not the primary alone: a value only the PARTIAL has a type rule for is refused."""
    path = config_dir / "presets.yaml"
    before = path.read_bytes()
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        refused = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": INVALID_REALWEATHER})
        after_refusal = path.read_bytes()
        accepted = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": VALID_REALWEATHER})
    assert refused.status_code == 303
    assert after_refusal == before, "the partial's own rule did not stop the write"
    assert accepted.status_code == 303
    written = configwrite.load_config_text(path.read_text(encoding="utf-8"), path=path)
    assert written["Carrier Freqs"]["RealWeather"]["options"]["weather"]["icao"] == "PGUM", \
        "the block the partial accepts was not written"
    assert presets_page.preset_schema_files(), "the schema pair is empty"
    assert [Path(name).name for name in presets_page.preset_schema_files()] == \
        ["presets_schema.yaml", "realweather_schema.yaml"]


@pytest.mark.asyncio
async def test_the_live_check_answers_for_one_block_and_writes_nothing(config_dir):
    before = _digests(config_dir)
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, _revision = _csrf_and_revision(page)
        good = await http.post(_validate_url(), data={"_csrf_token": csrf, "_yaml": CARRIER_EDITED})
        bad = await http.post(_validate_url(), data={"_csrf_token": csrf, "_yaml": INVALID_TERRAIN})
    assert good.status_code == 200 and good.json()["ok"] is True, good.text
    assert bad.status_code == 200 and bad.json()["ok"] is False, bad.text
    assert bad.json()["kind"] == "schema", bad.text
    assert _digests(config_dir) == before, "the live check wrote to the config tree"


# ---------------------------------------------- 5. the name cannot change here

@pytest.mark.asyncio
@pytest.mark.parametrize("document, marker", [
    (RENAMED, "keyed"),
    (SECOND_KEY, "carries"),
    (NOT_A_MAPPING, "not a map"),
])
async def test_a_document_that_is_not_this_one_preset_is_refused(config_dir, document, marker):
    path = config_dir / "presets.yaml"
    before = path.read_bytes()
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        response = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": document})
        landed = await _open(http, _page_url())
    assert response.status_code == 303
    assert path.read_bytes() == before, f"a refused document was written: {document!r}"
    assert not Path(str(path) + ".bak").exists(), "a refused save left a backup behind"
    assert marker in _notice(landed), f"the refusal does not name the fault: {_notice(landed)!r}"


# ---------------------------------------------- 6. editing is not removing

@pytest.mark.asyncio
async def test_an_in_use_preset_is_editable_and_removal_is_still_refused(config_dir):
    (config_dir / "nodes.yaml").write_text(NODES_REFERENCING, encoding="utf-8")
    path = config_dir / "presets.yaml"
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        edited = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": CARRIER_EDITED})
        after_edit = configwrite.load_config_text(path.read_text(encoding="utf-8"), path=path)
        removal = await http.post(presets_page.PRESETS_SAVE_PATH, data={
            "_csrf_token": csrf, "_action": presets_page.PRESETS_ACTION_REMOVE,
            "_original": "Carrier Freqs", "_revision": revision})
        landed = await _open(http, presets_page.PRESETS_PATH)
    assert edited.status_code == 303
    assert after_edit["Carrier Freqs"]["modify"]["variables"]["speed"] == 40, \
        "an in-use preset could not be edited"
    assert removal.status_code == 303
    assert "Carrier Freqs" in path.read_text(encoding="utf-8"), "an in-use preset was removed"
    assert "still named by" in _notice(landed), \
        f"the removal refusal does not say it is in use: {_notice(landed)!r}"


# ---------------------------------------------- 7. the JS-off contract

@pytest.mark.asyncio
async def test_with_javascript_off_the_textarea_carries_and_posts_the_document(config_dir):
    path = config_dir / "presets.yaml"
    app = _app(config_dir)
    async with client(app) as http:
        await sign_in(http, "staff")
        page = await _open(http, _page_url())
        csrf, revision = _csrf_and_revision(page)
        # the carrier IS the editable control: no ``hidden``, a name, the block as its body
        carrier = re.search(r'<textarea[^>]*name="_yaml"[^>]*>', page)
        assert carrier is not None, "the textarea does not post under ``_yaml``"
        assert "hidden" not in carrier.group(0), "the textarea is hidden without JavaScript"
        assert _editor_text(page).strip() == CARRIER_BLOCK.strip()
        # what a browser without JavaScript posts: the textarea's own body
        response = await http.post(_save_url(), data={
            "_csrf_token": csrf, "_revision": revision, "_yaml": CARRIER_EDITED})
        stored = configwrite.load_config_text(path.read_text(encoding="utf-8"), path=path)
    assert response.status_code == 303
    assert stored["Carrier Freqs"]["modify"]["variables"]["speed"] == 40, \
        "a plain form post did not write the preset"


def test_the_shared_script_carries_the_textarea_as_its_source():
    """The ONE editor script builds its editor from the textarea when the page has no toggle."""
    script = (Path(presets_page.__file__).parent.parent / "static" / "settings-yaml.js")
    source = script.read_text(encoding="utf-8")
    assert "data-cfg-expert-toggle" in source, "the script no longer knows the toggle contract"
    assert "data-cfg-yaml-text" in source, "the script no longer reads the text carrier"
    assert "data-cfg-yaml-source" in source, \
        "the script carries no toggle-less (textarea-source) mode"
    assert "PLAIN_SOURCE" in source, "the toggle-less mode has no marker constant"
    assert "build(textField.value)" in source, "the script never seeds the editor from the textarea"
