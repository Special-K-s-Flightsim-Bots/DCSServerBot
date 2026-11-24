"""The Settings page's EXPERT MODE editor (``static/settings-yaml.js``) — the promise, TESTED.

The page ships an Expert Mode toggle over the read-only YAML panel and a UI line that reads: "Edit the
file directly. Every change is checked against the bot's own schema, and a document that does not pass
cannot be saved." This module is the EVIDENCE for that sentence — every test here would FAIL if the
sentence were false.

Two halves, both over the REAL shipped code (never a copy):

* the JAVASCRIPT half drives ``static/settings-yaml.js`` through the existing browser-less harness
  (``tests/configgen_harness.js``, the same technique that drives ``static/configgen.js``) — the toggle,
  the ``_expert``/``_yaml`` carriers, the server verdict rendered in the status line, and the submit
  guard, asserted on the script's own answers;
* the SERVER half drives the real application in-process: the validate route (a JSON verdict, nothing
  written) and the save route (the document lands, or is refused with nothing written).

The validator callable the editor reaches is the BOT'S OWN — ``core.utils.validators.validate``
(pykwalify against the file's shipped schema), through ``core.utils.configwrite.validate_against_schema``
(the same function a save uses via ``configwrite._validate_candidate``). It is NOT a second
implementation: ``test_the_validate_route_checks_through_the_bots_own_validator`` patches THAT callable
and proves both the validate route and the save route drive it.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import asgi_client, run_node_harness
from services.webservice.pages import settings as settings_page
from test_webui_dashboard import BOT_YAML, _build, _hash, _login
from test_webui_settings_files import _csrf_and_revision, _rendered_form

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_YAML_JS = REPO_ROOT / "services" / "webservice" / "static" / "settings-yaml.js"
CONFIGGEN_HARNESS = Path(__file__).resolve().parent / "configgen_harness.js"

#: A main.yaml the schema accepts (the fixture the settings page's own tests use shape-for-shape).
MAIN_FIXTURE = """\
# main.yaml — an operator's own file.
guild_id: 112233445566    # the Discord server
guild_name: My Server
language: en
plugins:
  - mission
opt_plugins:
  - serverstats
"""

#: Valid YAML, INVALID against ``main_schema.yaml``: ``plugins`` must be a list, not a string.
SCHEMA_INVALID = "guild_id: 112233445566\nguild_name: My Server\nplugins: mission\n"

#: BROKEN YAML: the second line is over-indented — an unclosed flow sequence reports its mark at
#: end-of-stream instead, so this names the offending line unambiguously.
BROKEN_YAML = "guild_id: 112233445566\n  guild_name: My Server\n"


@pytest.fixture
def settings_dir(tmp_path):
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_FIXTURE, encoding="utf-8")
    return tmp_path


def _app(settings_dir, roles=("Admin",)):
    return _build(settings_dir, users=[{"username": "staff", "password_hash": _hash(),
                                        "roles": list(roles)}])


def _run(drive):
    return asyncio.new_event_loop().run_until_complete(drive())


async def _open(client) -> str:
    response = await client.get(settings_page.SETTINGS_MAIN_PATH)
    assert response.status_code == 200, response.text[:300]
    return response.text


def _panel_text(page: str) -> str:
    """The YAML the read-only panel renders — the same text the editor is seeded from."""
    found = re.search(r'<pre class="st-yaml"[^>]*>(.*?)</pre>', page, re.DOTALL)
    assert found is not None, "the read-only YAML panel was not rendered"
    return html.unescape(found.group(1))


async def _post_validate(client, page: str, text: str):
    csrf, _ = _csrf_and_revision(page)
    return await client.post(settings_page.SETTINGS_VALIDATE_PATH,
                             data={"_csrf_token": csrf, settings_page.SETTINGS_YAML_FIELD: text})


async def _post_expert_save(client, page: str, text: str):
    csrf, revision = _csrf_and_revision(page)
    return await client.post(settings_page.SETTINGS_SAVE_PATH, data={
        "_csrf_token": csrf, "_revision": revision,
        settings_page.SETTINGS_EXPERT_FIELD: "1",
        settings_page.SETTINGS_YAML_FIELD: text,
    })


def _notice(page: str) -> str:
    found = re.search(r'notice-msg">([^<]*)<', page)
    assert found is not None, "no notice was rendered"
    return html.unescape(found.group(1))


# ---------------------------------------------- 1. the editor is validated by the BOT'S OWN validator

def test_the_validate_route_checks_through_the_bots_own_validator(settings_dir, monkeypatch):
    """The editor's live check drives ``core.utils.validators.validate`` — the same callable a save
    uses, not a second implementation. Proven by patching THAT callable: both the validate route and
    the save route then run the patched object with the SAME schema files."""
    import core.utils.validators as validators

    calls: list[tuple[str, list[str]]] = []
    real = validators.validate

    def spy(source_file, schema_files, *args, **kwargs):
        calls.append((str(source_file), list(schema_files)))
        return real(source_file, schema_files, *args, **kwargs)

    monkeypatch.setattr(validators, "validate", spy)

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            await _post_validate(client, page, MAIN_FIXTURE)
            verdict = await _post_validate(client, page, MAIN_FIXTURE)
            assert verdict.status_code == 200, verdict.text[:300]
            assert verdict.json()["ok"] is True
            await _post_expert_save(client, page, MAIN_FIXTURE.replace("My Server", "Renamed"))

    _run(drive)

    assert calls, "the bot's own validator was never called — the editor used something else"
 # the editor checks against the file's OWN schema set — exactly what a save validates against.
    assert calls[0][1] == settings_page._schema_files()
 # BOTH routes reached the patched callable: one validate, and one from the save.
    assert len(calls) >= 3, f"expected the validate and save routes to share the callable: {calls}"


# ------------------------------------------------- 2. a schema-invalid document cannot be saved

def test_a_schema_invalid_document_cannot_be_saved_and_nothing_is_written(settings_dir):
    path = settings_dir / "main.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            response = await _post_expert_save(client, page, SCHEMA_INVALID)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)

    assert response.status_code == 303
    assert path.read_bytes() == before, "an invalid document rewrote the file"
    notice = _notice(landed)
    assert notice.startswith(settings_page.REFUSED_BANNER), notice
    assert "not a list" in notice, "the reason did not carry the validator's fault: " + notice


def test_a_schema_invalid_document_is_reported_with_its_reason(settings_dir):
    """The editor's own verdict for the same document: refused, and it names the offending block."""
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            verdict = await _post_validate(client, page, SCHEMA_INVALID)
        return verdict

    verdict = _run(drive)
    body = verdict.json()
    assert body["ok"] is False
    assert body["kind"] == "schema"
    assert body["block"] == "plugins", body
    assert "plugins" in body["message"], body["message"]
    assert body["line"] == 3, body


# ------------------------------------------ 3. broken YAML is refused WITH THE LINE NUMBER

def test_broken_yaml_is_refused_with_the_line_number(settings_dir):
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            verdict = await _post_validate(client, page, BROKEN_YAML)
        return verdict

    verdict = _run(drive)
    body = verdict.json()
    assert body["ok"] is False
    assert body["kind"] == "syntax"
    assert body["line"] == 2, body
    assert "2" in body["message"], body["message"]


def test_broken_yaml_cannot_be_saved_and_nothing_is_written(settings_dir):
    path = settings_dir / "main.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            response = await _post_expert_save(client, page, BROKEN_YAML)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before, "broken YAML rewrote the file"
    notice = _notice(landed)
    assert notice.startswith(settings_page.REFUSED_BANNER), notice
    assert "line 2" in notice, notice


# ------------------------- 4. a valid edit lands; an unchanged document leaves the file identical

def test_an_unchanged_expert_document_leaves_the_file_byte_identical(settings_dir):
    path = settings_dir / "main.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            text = _panel_text(page)                       # exactly the document the editor is seeded with
            response = await _post_expert_save(client, page, text)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before, "an unchanged expert save rewrote the file"
    assert _notice(landed) == settings_page.SAVED_NO_CHANGE


def test_a_valid_expert_edit_lands_in_the_file(settings_dir):
    path = settings_dir / "main.yaml"

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            edited = _panel_text(page).replace("My Server", "Renamed Server")
            response = await _post_expert_save(client, page, edited)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert "Renamed Server" in path.read_text(encoding="utf-8")
    assert _notice(landed) == settings_page.SAVED_RESTART


# --------------------- 5. Expert Mode OFF: the plain form, and no smuggled raw write

def test_the_page_ships_the_expert_carriers_defaulted_off(settings_dir):
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(drive)
 # the toggle is off by default and drives the hidden companion; the carrier ships empty.
    assert "data-cfg-expert-toggle" in page
    assert re.search(r'name="_expert" value="0" data-cfg-expert', page), \
        "the _expert carrier does not default off"
    assert re.search(r'name="_yaml"[^>]*data-cfg-yaml-text', page), \
        "the _yaml carrier is missing"
    assert settings_page.SETTINGS_VALIDATE_PATH in page


def test_the_page_loads_the_completion_popup_before_the_editor(settings_dir):
    """The page ships the addon and its stylesheet, each VERSIONED, and loads the addon first.

    A stale `settings-yaml.js` (or a CodeMirror from yesterday) in a browser cache is why every asset
    URL carries the build's own token; the popup is only reachable when the addon is ahead of the
    script that calls `CodeMirror.showHint`.
    """
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(drive)
    assert re.search(r'/static/vendor/codemirror/addon/hint/show-hint\.css\?v=\d+', page), \
        "the popup's stylesheet is missing or unversioned"
    assert re.search(r'/static/vendor/codemirror/addon/hint/show-hint\.js\?v=\d+" defer', page), \
        "the popup addon is missing or unversioned"
    assert re.search(r'/static/settings-yaml\.js\?v=\d+" defer', page), \
        "the editor script is served without a version"
    assert page.index("addon/hint/show-hint.js") < page.index("/static/settings-yaml.js"), \
        "the editor script is loaded before the addon it calls"


def test_expert_mode_off_ignores_a_smuggled_document_in_a_plain_form_post(settings_dir):
    """With ``_expert`` at 0 the posted form is a PLAIN form: a raw ``_yaml`` document riding along
    must be inert — the save reads the ``f.`` fields, not the carrier. Nothing is written."""
    path = settings_dir / "main.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            csrf, revision = _csrf_and_revision(page)
            form = _rendered_form(page)                    # exactly what the plain form posts
            form["_csrf_token"] = csrf
            form["_revision"] = revision
            form[settings_page.SETTINGS_EXPERT_FIELD] = "0"
 # a whole different document smuggled into the carrier:
            form[settings_page.SETTINGS_YAML_FIELD] = "guild_id: 999\nguild_name: SMUGGLED\n"
            response = await client.post(settings_page.SETTINGS_SAVE_PATH, data=form)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before, "the plain form wrote a smuggled raw document"
    assert "SMUGGLED" not in path.read_text(encoding="utf-8")
    assert _notice(landed) == settings_page.SAVED_NO_CHANGE


def test_a_caller_without_the_capability_cannot_validate_or_expert_save(settings_dir):
    """The validate route is gated like the write — an out-of-scope caller cannot probe or save."""
    async def drive():
        app, _ = _app(settings_dir, roles=("DCS Admin",))
        async with asgi_client(app) as client:
            await _login(client, "staff")
            validate = await client.post(settings_page.SETTINGS_VALIDATE_PATH,
                                         data={"_csrf_token": "x", "_yaml": "a: 1\n"})
            save = await client.post(settings_page.SETTINGS_SAVE_PATH,
                                     data={"_csrf_token": "x", "_expert": "1", "_yaml": "a: 1\n"})
        return validate, save

    validate, save = _run(drive)
    assert validate.status_code == 403
    assert save.status_code == 403


# -------- 6. an EMPTY document is refused (it must never clobber a schema-less file with null)

#: A schema-less global file's own content — a service that ships no ``schemas/`` directory.
MYSTERY_YAML = "anything:\n  goes: here\n"


@pytest.fixture
def schemaless_dir(tmp_path):
    """A config dir with a service file whose kind ships NO schema (the empty-document path)."""
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "bot.yaml").write_text(BOT_YAML, encoding="utf-8")
    (tmp_path / "services" / "mystery.yaml").write_text(MYSTERY_YAML, encoding="utf-8")
    (tmp_path / "main.yaml").write_text(MAIN_FIXTURE, encoding="utf-8")
    return tmp_path


def _service_page_url(name: str) -> str:
    return settings_page.SETTINGS_FILE_PATH.format(kind=settings_page.KIND_SERVICE, name=name)


def _service_save_url(name: str) -> str:
    return settings_page.SETTINGS_FILE_SAVE_PATH.format(kind=settings_page.KIND_SERVICE, name=name)


def test_an_empty_document_cannot_clobber_a_schema_less_file(schemaless_dir):
    """The probe's OWN input: ``_expert=1`` with an empty ``_yaml`` on a schema-less file.

    An empty document is not a document — the round-trip loader parses "" to ``None`` and, with no
    schema to catch it, writing that replaced the whole file with ``null``. It must now be refused
    exactly as an invalid document is: nothing written, the file left sha-identical.
    """
    path = schemaless_dir / "services" / "mystery.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(schemaless_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = (await client.get(_service_page_url("mystery"))).text
            csrf, revision = _csrf_and_revision(page)
            response = await client.post(_service_save_url("mystery"), data={
                "_csrf_token": csrf, "_revision": revision,
                settings_page.SETTINGS_EXPERT_FIELD: "1",
                settings_page.SETTINGS_YAML_FIELD: "",
            })
            landed = await client.get(_service_page_url("mystery"))
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before, "an empty document rewrote the file"
    assert b"null" not in path.read_bytes()
 # the page carries the schema-less banner AND the write's own refusal — pick the write notice.
    assert settings_page.SAVE_REFUSED_EMPTY_DOCUMENT in html.unescape(landed.text)


def test_an_empty_document_is_refused_on_main_yaml_too(settings_dir):
    """The same guard covers ``main.yaml``: an empty expert document writes nothing, there either."""
    path = settings_dir / "main.yaml"
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            response = await _post_expert_save(client, page, "")
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before
    assert _notice(landed) == settings_page.SAVE_REFUSED_EMPTY_DOCUMENT


# -------------------- 7. a parse-error page still offers a submit, so a corrected doc can land

def test_a_parse_error_page_offers_a_submit_and_a_corrected_document_lands(settings_dir):
    """The recovery the docstring promises: a broken file renders, the operator fixes it in the
    editor, the submit control is AVAILABLE, and the corrected save lands. The server stays the gate.
    """
    path = settings_dir / "main.yaml"
    path.write_text(BROKEN_YAML, encoding="utf-8")

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)                 # the broken file renders
            response = await _post_expert_save(client, page, MAIN_FIXTURE)   # the corrected doc
            landed = await _open(client)
        return page, response, landed

    page, response, landed = _run(drive)
 # the fault is stated, AND the submit control is right there (it used to be hidden — the page
 # rendered the error but left the operator no way to submit the correction)
    assert "not valid YAML" in page, "the parse error was not shown"
    assert 'class="st-btn pri"' in page, "the submit control was hidden on a parse-error page"
    assert settings_page.SETTINGS_EXPERT_FIELD in page, "the editor carriers are missing"
    assert response.status_code == 303
    assert "guild_name: My Server" in path.read_text(encoding="utf-8")
    assert _notice(landed) == settings_page.SAVED_RESTART


def test_a_parse_error_page_still_refuses_an_uncorrected_document(settings_dir):
    """The submit being available does NOT weaken the gate: posting the same broken document is
    still refused and nothing is written."""
    path = settings_dir / "main.yaml"
    path.write_text(BROKEN_YAML, encoding="utf-8")
    before = path.read_bytes()

    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            page = await _open(client)
            response = await _post_expert_save(client, page, BROKEN_YAML)
            landed = await _open(client)
        return response, landed

    response, landed = _run(drive)
    assert response.status_code == 303
    assert path.read_bytes() == before, "an uncorrected document rewrote the file"
 # the parse-error banner is the FIRST notice-msg on this page; the write's refusal is still shown.
    assert settings_page.REFUSED_BANNER in html.unescape(landed)


# ------------------- 8. the two nitpicks: no toggle without a schema; the save states its effect

def test_the_expert_toggle_is_not_offered_on_a_file_without_a_schema(schemaless_dir):
    """A schema-less file's page cannot validate an edited document and has no write story, so the
    Expert Mode toggle (and its hint) must NOT be offered there — a control that can do nothing."""
    async def drive():
        app, _ = _app(schemaless_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await client.get(_service_page_url("mystery"))

    response = _run(drive)
    page = response.text
    assert response.status_code == 200
    assert settings_page.NO_SCHEMA_BANNER in html.unescape(page)
    assert "data-cfg-expert-toggle" not in page, "the Expert Mode toggle was offered with no schema"
    assert "An Expert Mode save writes the document" not in page


def test_a_schema_backed_page_keeps_the_expert_toggle_and_the_save_note(settings_dir):
    """On a file WITH a schema the toggle is offered and the save's effect on form fields is stated —
    an expert save writes the document; the form's pending changes are not applied."""
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(drive)
    assert "data-cfg-expert-toggle" in page
    assert "the form&#39;s pending changes are not applied" in page or \
        "the form's pending changes are not applied" in page


def test_the_validation_line_is_always_present_so_its_box_is_reserved(settings_dir):
    """The status line ships EMPTY but PRESENT, so its box is reserved from first paint and the editor
    is already at its final vertical position before a single verdict arrives.

    A status line rendered only WHEN there is something to say could reserve nothing — that is half the
    defect. The other half (a FIXED height, not merely a floor) is pinned over ``shell.css`` in
    ``test_webui_console_css``.
    """
    async def drive():
        app, _ = _app(settings_dir)
        async with asgi_client(app) as client:
            await _login(client, "staff")
            return await _open(client)

    page = _run(drive)
    status = re.search(r'<div class="cfg-yaml-status"[^>]*data-cfg-yaml-status[^>]*>(.*?)</div>',
                       page, re.DOTALL)
    assert status is not None, "the validation line is not a rendered element on arrival"
    assert status.group(1).strip() == "", "the status line should arrive empty (no verdict yet)"


# --------------------------------------------------------------- the JavaScript half (the harness)

_requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                    reason="node is not installed: the shipped script cannot be run")


@pytest.fixture(scope="module")
def editor_script() -> dict:
    """The shipped ``settings-yaml.js``'s own answers, driven by the harness over the REAL file."""
    assert CONFIGGEN_HARNESS.is_file() and SETTINGS_YAML_JS.is_file()
    done = run_node_harness(["node", str(CONFIGGEN_HARNESS), str(SETTINGS_YAML_JS)])
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@_requires_node
def test_editor_off_leaves_the_readonly_panel_and_a_plain_form(editor_script):
    off = editor_script["off"]
    assert off["editorBuilt"] is False
    assert off["expert"] == "0"
    assert off["carrier"] == ""
    assert off["readonlyShown"] is True and off["editorHidden"] is True
    assert off["submitPrevented"] is False, "a plain submit was intercepted with Expert Mode off"
    assert off["fetches"] == 0


@_requires_node
def test_editor_on_builds_the_editor_and_checks_through_the_server(editor_script):
    on = editor_script["on"]
    assert on["editorBuilt"] is True
    assert on["seededFromPanel"] is True
    assert on["expert"] == "1"
    assert on["carrierMatchesEditor"] is True
    assert on["editorShown"] is True and on["readonlyHidden"] is True
    assert on["badge"] == "editing"
    assert on["status"] == "Valid."
    assert on["state"] == "ok"
    assert on["fetchMethod"] == "POST"
    assert on["fetchUrl"] == settings_page.SETTINGS_VALIDATE_PATH
    assert on["bodyCarriesCsrf"] is True and on["bodyCarriesDocument"] is True


@_requires_node
def test_editor_change_posts_the_edited_document(editor_script):
    change = editor_script["change"]
    assert change["carrierUpdated"] is True
    assert change["checksIssued"] >= 1
    assert change["bodyCarriesEdited"] is True


@_requires_node
def test_a_check_never_moves_the_caret_or_mangles_the_document(editor_script):
    """A full line typed ONE KEYSTROKE AT A TIME, with a verdict carrying an offending LINE/COLUMN
    arriving on every debounced check, must end EXACTLY as typed — no shifted characters, no lost row
.

    The old ``apply()`` called ``editor.setCursor()`` on each verdict, so the caret was yanked mid-word
    and the rest of the line landed at the offending column; the harness reproduces the corruption as a
    reversed string. A check must only NAME the error (status line) and MARK it (``.cm-yaml-error``/
    ``.cm-yaml-block``) — never move the caret.
    """
    keystrokes = editor_script["keystrokes"]
    assert keystrokes["intact"] is True, \
        f"validation mangled the document: {keystrokes['value']!r} != {keystrokes['typed']!r}"
    assert keystrokes["value"] == keystrokes["typed"]
    assert keystrokes["cursorMoves"] == 0, "a background check moved the caret"
    assert keystrokes["cursor"]["line"] == 1, "the newline was lost: the row did not survive"


@_requires_node
def test_editor_submit_guard_follows_the_servers_verdict(editor_script):
 # a refusal holds the submit and shows the server's own sentence
    refused = editor_script["submitRefused"]
    assert refused["submitPrevented"] is True
    assert refused["state"] == "bad"
    assert refused["status"] == "Not valid against the schema — line 1."
 # a clean verdict lets it through
    assert editor_script["submitClean"]["submitPrevented"] is False
 # a submit while a check is still in flight is held back
    assert editor_script["submitPending"]["submitPrevented"] is True
 # and a submit whose text moved since the last verdict is held back too
    assert editor_script["submitDirty"]["submitPrevented"] is True


@_requires_node
def test_editor_off_again_restores_the_plain_form(editor_script):
    """Turning Expert Mode back OFF leaves the panel exactly as it arrived: read-only, both carriers
    cleared, and a plain submit NOT intercepted. (This is the defect the harness caught — the editor
    object used to survive the toggle-off, so the guard kept holding ordinary saves.)"""
    off = editor_script["offAgain"]
    assert off["expert"] == "0"
    assert off["carrier"] == ""
    assert off["editorHidden"] is True and off["readonlyShown"] is True
    assert off["readonlyHoldsEdit"] is True
    assert off["status"] == ""
    assert off["badge"] == "read-only"
    assert off["submitPrevented"] is False, "Expert Mode OFF still intercepted the plain form"
