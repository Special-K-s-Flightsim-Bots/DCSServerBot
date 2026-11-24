"""The launch dialog's TEMPORARY preset: the weather fields, on the SAME Startup dialog.

One section per acceptance criterion:

* the RENDERED dialog — the fieldset is drawn from the ONE field table the presets wizard reads
  (``weather_fields.FIELDS``), every row's control named by its preset KEY and form-associated to
  the dialog's ONE form, and neither the preset NAME nor the THEATRE chips offered (neither is
  weather for THIS launch);
* the DOCUMENT — the fields posted produce a dict, it reaches ``Server.startup`` as ``preset=<dict>``
  (asserted on the document that is actually passed), ``MizEdit.apply_presets`` accepts it, and the
  chosen fields land in the mission ``MizFile.apply_preset`` writes;
* the ONE-SHOT rule is the EXISTING ``use_orig`` box (no second lifetime rule is invented);
* NOTHING written — every file under the node's config dir is byte-identical (sha256) after a
  temporary launch;
* the CONFLICT — a launch stating a preset NAME and the fields BOTH is refused with one sentence;
* the HOSTILE VALUE — a name carrying braces, a quote and a newline does not corrupt the document
  or the message.

The document is fed to the REAL ``MizEdit.apply_presets`` / ``MizFile.apply_preset`` (the node's own
code), with ``MizFile``'s construction replaced by a bare instance — the same technique
``tests/test_webui_presets_wizard.py`` uses for ``parse_moment``. Nothing here needs a live DCS
install: what a real node would write is exercised at the mission-object level.
"""
from __future__ import annotations

import hashlib
import logging
import re

import pytest

from services.webservice import weather_fields
from services.webservice.pages import actions as actions_page
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    PresetNode, node_fleet, open_dialog,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, open_page, recording_bot, sign_in, token_of,
)

STARTUP = "/actions/server/startup"
PRESET_FIELD = actions_page.PRESET_FIELD
TEMPORARY_KEYS = actions_page.TEMPORARY_KEYS


# --------------------------------------------------------------------------- the mission double

def _bare_miz():
    """A bare ``MizFile`` whose mission carries the weather the setters write into.

    Construction (``MizFile.__init__``) reaches a live node through the service registry, so the
    instance is made with ``__new__`` and its mission seeded — exactly as the wizard's own test seeds
    one for ``parse_moment``. Everything exercised from here is the REAL ``apply_preset``.
    """
    import core.mizfile as mizfile

    miz = mizfile.MizFile.__new__(mizfile.MizFile)
    miz.log = logging.getLogger("tests.temporary_preset")
    miz.filename = "mission.miz"
    miz.mission = {
        "theatre": "Caucasus",
        "start_time": 0,
        "date": {"Year": 2016, "Month": 4, "Day": 21},
        "weather": {
            "season": {"temperature": 0},
            "atmosphere_type": 0,
            "groundTurbulence": 0,
            "qnh": 760,
            "wind": {"atGround": {}, "at2000": {}, "at8000": {}},
        },
    }
    miz._files = []
    # the node writes the file it opened; the in-memory mission is what this test inspects.
    miz.save = lambda *args, **kwargs: None
    return miz


async def _apply_through_mizedit(monkeypatch, document):
    """Feed *document* to the REAL ``MizEdit.apply_presets`` and hand back the mission it wrote into.

    ``MizEdit.apply_presets`` is the node's own entry point (``MizEdit.modify_mission`` calls it), so
    this is the shape check the acceptance asks for: the document that was actually passed must be one
    ``apply_presets`` accepts, and its values must land through ``MizFile.apply_preset``.
    """
    import extensions.mizedit.extension as mizedit

    miz = _bare_miz()
    monkeypatch.setattr(mizedit, "MizFile", lambda filename=None: miz)
    # a document (not a list) never reaches the RealWeather branch, which is the only use of `server`.
    await mizedit.MizEdit.apply_presets(server=None, filename="mission.miz", preset=document,  # type: ignore[arg-type]
                                        debug=False)
    return miz.mission


# --------------------------------------------------------------------------- helpers

def weather_fieldset(html: str) -> str:
    """The rendered ``<div class="dlg-temp">…</div>`` block (it wraps the rows and the note, so it is
    matched through its own closing note rather than the first nested ``</div>``)."""
    match = re.search(r'<div class="dlg-temp">.*?<p class="dlg-temp-note">.*?</p>\s*</div>', html, re.S)
    assert match, "the dialog rendered no temporary-preset fieldset"
    return match.group(0)


def hashes(root):
    """``{relative path: sha256}`` for every file under *root*."""
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


def posted(http, page, **fields):
    data = {"server": "SRS-1", "_csrf_token": token_of(page)}
    data.update(fields)
    return http.post(STARTUP, data=data)


# --------------------------------------------------------------- 1. the rendered fieldset

@pytest.mark.asyncio
async def test_the_dialog_draws_a_control_for_every_weather_row(config_dir, clean_registry):
    """The fieldset is the ONE field table's rows, each control named by its preset KEY and
    form-associated to the dialog's own form; the preset NAME and the THEATRE chips are NOT offered.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    html = dialog.text
    block = weather_fieldset(html)
    # every row the document builder reads has a control, offered ENABLED and named by its own key.
    # the fog table is the ONE key that posts indexed CELLS instead of its own name (its value is
    # composed from them), so its row is checked by its cells.
    for key in TEMPORARY_KEYS:
        if key == weather_fields.FOG_ROWS_KEY:
            for cell in ("time", "thickness", "visibility"):
                assert f'name="{weather_fields.fog_row_field(0, cell)}"' in block, \
                    f"no fog cell for the '{key}' row"
            continue
        assert f'name="{key}"' in block, f"no control for the '{key}' row"
    assert 'name="terrains"' not in block, "the theatre is not weather for THIS launch"
    assert 'name="date"' in block and 'name="start_time"' in block
    # every NAMED control the fieldset draws rides the dialog's ONE form (the footer's), wherever it is
    # drawn: the range beside a slider (and the dial's ring) carries no name and is not a control.
    named = re.findall(r'<(?:input|select)\b[^>]*\bname="([^"]+)"[^>]*>', block)
    assert named, "the fieldset drew no named control"
    for tag in re.findall(r'<(?:input|select)\b[^>]*>', block):
        if re.search(r'\bname="', tag):
            assert 'form="dlg-form"' in tag, f"a named control is not form-associated: {tag}"
    assert block.count('form="dlg-form"') == len(named)
    # the table's own words and limits, not a second vocabulary
    assert weather_fields.field("clouds.iprecptns").label in html
    # the distance controls state the unit THIS render uses (feet in English); the rest keep theirs.
    assert "feet" in html and "m/s" in html and "mmHg" in html
    low, high, _step = weather_fields.display_bounds(weather_fields.field("clouds.base"))
    assert f'min="{low}"' in block and f'max="{high}"' in block, \
        "the cloud base keeps its table bounds, in the render's own unit"
    assert "Or create a temporary preset" in html
    # and the ONE-SHOT switch is the EXISTING box, not a second lifetime rule
    assert 'name="use_orig"' in html
    assert 'name="modify_mission"' in html
    for forbidden in ("keep the weather", "one-shot", "make this permanent"):
        assert forbidden not in html, f"a second lifetime rule appeared: {forbidden}"


def test_the_keys_are_the_tables_weather_rows_only():
    """``TEMPORARY_KEYS`` is the field table minus exactly the NAME and the THEATRE."""
    expected = tuple(entry.key for entry in weather_fields.FIELDS
                     if entry.key not in ("name", "terrains"))
    assert TEMPORARY_KEYS == expected
    assert "name" not in TEMPORARY_KEYS and "terrains" not in TEMPORARY_KEYS


# ------------------------------------------- 2. the document, the forwarding, the mission

@pytest.mark.asyncio
async def test_the_fields_reach_startup_as_a_document_and_land_in_the_mission(
        config_dir, clean_registry, recording_bot, monkeypatch):
    """ACCEPTANCE 1 + 2 — the posted fields produce a dict, ``Server.startup`` receives it as
    ``preset=<dict>``, ``MizEdit.apply_presets`` accepts that exact document, and the chosen values
    land in the mission ``MizFile.apply_preset`` writes.
    """
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(
            http, dialog.text,
            clouds="dynamic", **{"clouds.base": "1500", "clouds.density": "4",
                                 "clouds.iprecptns": "1"},
            **{"wind.atGround.speed": "7", "wind.atGround.dir": "220"},
            temperature="21", qnh="760", enable_dust="true", dust_density="3",
            start_time="06:30")

    assert reply.status_code == 303, reply.text
    (call,) = cluster.servers[0].call_args
    document = call["preset"]
    assert isinstance(document, dict), f"preset must be the document, got {document!r}"
    assert call["modify_mission"] is True and call["use_orig"] is True
    # the document MizEdit consumes: the preset's own keys, coerced to the schema's types.
    assert document["clouds"] == {"base": 1500, "density": 4, "iprecptns": 1}
    assert document["wind"]["atGround"] == {"speed": 7, "dir": 220}
    assert document["temperature"] == 21 and document["qnh"] == 760
    assert document["enable_dust"] is True and document["dust_density"] == 3
    # the clock is written in the MISSION's own shape: ``06:30`` as the seconds it states, which is
    # what ``/mission save preset`` writes for a mission starting then
    assert document["start_time"] == 23400
    assert "modify" not in document, "no expert DSL is invented by the weather controls"

    mission = await _apply_through_mizedit(monkeypatch, document)
    weather = mission["weather"]
    assert weather["clouds"]["base"] == 1500
    assert weather["clouds"]["density"] == 4
    assert weather["clouds"]["iprecptns"] == 1
    assert weather["wind"]["atGround"] == {"speed": 7, "dir": 220}
    assert weather["season"]["temperature"] == 21
    assert weather["qnh"] == 760
    assert weather["enable_dust"] is True and weather["dust_density"] == 3
    assert mission["start_time"] == 23400            # 06:30 as the mission stores it

    # the message names it as TEMPORARY, which is what tells it apart from a named preset.
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up with a temporary weather preset."]


@pytest.mark.asyncio
async def test_a_named_preset_and_no_choice_keep_their_own_shapes(config_dir, clean_registry,
                                                                 recording_bot):
    """The other two shapes of the SAME choice: a NAME is still a string, and nothing at all is
    ``None`` — the temporary document must not swallow either.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")

    named = node_fleet(PresetNode(config_dir))
    app = build(config_dir, named)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        await posted(http, dialog.text, **{PRESET_FIELD: "Summer"})
    assert named.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": "Summer"}]

    none = node_fleet(PresetNode(config_dir))
    app = build(config_dir, none)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        await posted(http, dialog.text)
    assert none.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}]

    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up — weather preset 'Summer' requested.",
        "Server 'SRS-1' started up.",
    ]


@pytest.mark.asyncio
async def test_a_filled_field_without_its_mode_writes_nothing(config_dir, clean_registry,
                                                             recording_bot):
    """The ONE builder's own semantics, pinned: ``clouds.base`` with no cloud MODE writes no key.

    The document IS the builder's output (the same one the wizard's save uses), so a control whose
    mode is unset contributes nothing and the launch carries no preset — and the message says so
    rather than claiming a weather was applied.
    """
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{"clouds.base": "1500"})
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}]
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up."]


def test_the_action_declares_the_document_shape():
    """The action's contract: ``preset`` accepts the document, and the control declares the fields."""
    import inspect

    from plugins.mission.actions import startup_server

    params = inspect.signature(startup_server).parameters
    assert "preset" in params
    assert params["preset"].default == ""
    assert "dict" in str(params["preset"].annotation), params["preset"].annotation

    startup = next(action for action in actions_page.SERVER_ACTIONS if action.key == "startup")
    assert startup.weather is True
    assert startup.select is not None and startup.select.field == PRESET_FIELD


# ------------------------------------------------------ 3. nothing is written anywhere

@pytest.mark.asyncio
async def test_nothing_under_the_config_dir_changes(config_dir, clean_registry, recording_bot):
    """ACCEPTANCE 3 — ``presets.yaml`` and EVERY file under the node's config dir keep their bytes.

    This is what says the temporary preset is form data on the launch POST and never a file write: no
    ``presets.write`` capability is needed because there is nothing to write.
    """
    (config_dir / "presets.yaml").write_text(
        "Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    (config_dir / "presets").mkdir()
    (config_dir / "presets" / "extra.yaml").write_text(
        "Winter:\n  clouds:\n    base: 500\n", encoding="utf-8")
    (config_dir / "servers.yaml").write_text("instances: {}\n", encoding="utf-8")

    before = hashes(config_dir)
    assert before, "the fixture wrote nothing to hash"

    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{"clouds.preset": "Rainy",
                                                   "clouds.base": "1700", "temperature": "12"})
    assert reply.status_code == 303, reply.text
    assert isinstance(cluster.servers[0].call_args[0]["preset"], dict)

    after = hashes(config_dir)
    assert after == before, "a temporary launch wrote to the node's config dir"
    # and the file the wizard's save would touch is the one that did not move
    assert after["presets.yaml"] == before["presets.yaml"]
    for path in before:
        assert after[path] == before[path], path


# ------------------------------------------------------------------- 4. the conflict

@pytest.mark.asyncio
async def test_a_preset_and_the_fields_together_are_refused(config_dir, clean_registry,
                                                            recording_bot):
    """ACCEPTANCE 6 — both shapes at once is refused with ONE sentence, naming the conflict, and no
    launch happens. Nothing is silently preferred.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{PRESET_FIELD: "Summer", "clouds.base": "1500"})
        assert reply.status_code == 400, reply.text
        assert actions_page.REFUSAL_WEATHER_CONFLICT in reply.json()["detail"]
        # the refusal reaches the person where they land, through the console's one-shot notice
        page = await open_page(http)

    assert cluster.servers[0].call_args == [], "nothing may launch on a refused form"
    assert recording_bot.calls == [], "the route refused before any action ran"
    fragment = "pick one, not both"
    assert fragment in page or "Not done" in page, "the refusal is not visible on the page"
    assert "500" not in reply.text


# ---------------------------------------------- 4b. "Create your own", the selection's own entry

def _pick_block(html: str) -> str:
    match = re.search(r'<fieldset class="dlg-field dlg-pick".*?</fieldset>', html, re.S)
    assert match, "the dialog rendered no weather pick"
    return match.group(0)


@pytest.mark.asyncio
async def test_the_custom_mode_is_the_third_row_and_owns_the_temporary_fields(
        config_dir, clean_registry):
    """ACCEPTANCE 1 — "Create your own weather" is the THIRD ROW of the mode control, under its own
    field, and the temporary weather fields are the block that mode shows.

    The mode is native radios, so the BROWSER enforces the exclusivity; the block's visibility is the
    dialog's own script (``static/dialog-preset.js``), driven against a DOM by
    ``test_webui_startup_weather_picker``. Nothing here is a list entry any more.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    html = dialog.text
    block = _pick_block(html)
    assert actions_page.PRESET_CUSTOM_VALUE not in block, "the custom entry must not be a list entry"
    assert ">Create your own weather</option>" not in block
    assert "<select" in block and "multiple" in block
    assert 'name="preset"' in block
    mode = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', html, re.S)
    assert mode, "the weather mode control is missing"
    assert 'value="custom"' in mode.group(0)
    assert ">Create your own weather</label>" in mode.group(0)
    assert html.count('type="radio"') == 3, "the three modes are not native radios"
    assert '<div class="dlg-temp">' in html, "the temporary fieldset is not drawn"
    assert "body:not(" not in html, "a CSS gate on a <select multiple> can never match"
    assert "/static/dialog-preset.js" in html, "the gate's asset is not loaded"


@pytest.mark.asyncio
async def test_choosing_the_custom_entry_builds_the_temporary_document(config_dir, clean_registry,
                                                                      recording_bot):
    """The entry is the DECLARED way into the temporary shape: with it ticked, the posted weather
    fields produce the document and reach ``Server.startup`` as ``preset=<dict>``."""
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text,
                             preset=actions_page.PRESET_CUSTOM_VALUE,
                             **{"clouds": "dynamic", "clouds.base": "1500", "temperature": "21"})

    assert reply.status_code == 303, reply.text
    (call,) = cluster.servers[0].call_args
    assert isinstance(call["preset"], dict), f"preset must be the document, got {call['preset']!r}"
    assert call["preset"]["clouds"] == {"base": 1500}
    assert call["preset"]["temperature"] == 21


@pytest.mark.asyncio
async def test_the_custom_entry_beside_a_ticked_name_is_refused(config_dir, clean_registry,
                                                               recording_bot):
    """The VISUAL exclusivity is the offer, never the rule: a post that states a NAME and "Create your
    own" at once is refused with the SAME one sentence, and nothing launches.

    This is the branch a browser without JavaScript (or a direct caller) reaches, and it is why the
    refusal stays after the entry was put into the selection.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, preset=[actions_page.PRESET_CUSTOM_VALUE, "Summer"],
                             **{"clouds.base": "1500"})
        assert reply.status_code == 400, reply.text
        assert actions_page.REFUSAL_WEATHER_CONFLICT in reply.json()["detail"]
        page = await open_page(http)

    assert cluster.servers[0].call_args == [], "nothing may launch on a refused form"
    assert recording_bot.calls == [], "the route refused before any action ran"
    assert "pick one, not both" in page or "Not done" in page


@pytest.mark.asyncio
async def test_the_custom_entry_with_no_field_launches_nothing(config_dir, clean_registry,
                                                               recording_bot):
    """The entry alone is a choice that states no weather: the document is empty and the launch
    carries no preset — the dialog never invents one."""
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, preset=actions_page.PRESET_CUSTOM_VALUE)
    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}]


# ------------------------------------------------------------------- 5. hostile input

HOSTILE = 'We{"x"}-ird\n"quoted" \' name'


@pytest.mark.asyncio
async def test_a_hostile_name_does_not_corrupt_the_document_or_the_message(
        config_dir, clean_registry, recording_bot, monkeypatch):
    """ACCEPTANCE 4 — a name carrying braces, a quote and a newline survives verbatim in the document
    and in the message, and the document still applies cleanly.
    """
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, **{PRESET_FIELD: HOSTILE})
    assert reply.status_code == 303, reply.text

    (call,) = cluster.servers[0].call_args
    assert call["preset"] == HOSTILE, "the name must travel verbatim, never re-encoded"
    assert [c[0] for c in recording_bot.calls] == [
        f"Server 'SRS-1' started up — weather preset '{HOSTILE}' requested."]

    # ...and the SAME hostile value inside a weather field lands in the document untouched and the
    # document still applies: a brace or a newline is data, never syntax.
    cluster2 = node_fleet(PresetNode(config_dir))
    app2 = build(config_dir, cluster2)
    async with client(app2) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text, clouds="preset",
                             **{"clouds.preset": HOSTILE, "temperature": "18"})
    assert reply.status_code == 303, reply.text
    document = cluster2.servers[0].call_args[0]["preset"]
    assert document["clouds"] == HOSTILE
    assert document["temperature"] == 18
    assert await _apply_through_mizedit(monkeypatch, document) is not None
    assert recording_bot.calls[-1][0] == \
        "Server 'SRS-1' started up with a temporary weather preset."


@pytest.mark.asyncio
async def test_a_hostile_value_is_escaped_where_the_page_shows_it(config_dir, clean_registry,
                                                                recording_bot):
    """The message is operator data on a page: it renders ESCAPED, never as markup."""
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)

    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        await posted(http, dialog.text, **{PRESET_FIELD: "<b>x</b>\\n{y}"})
        page = await open_page(http)

    assert "<b>x</b>" not in page, "the posted name was rendered as markup"
    assert "&lt;b&gt;x&lt;/b&gt;" in page


# ------------------------------------------------------------------- the i18n of the new copy

@pytest.mark.asyncio
async def test_the_new_copy_and_the_conflict_render_in_german(config_dir, clean_registry):
    """The new sentences are wrapped and land in the catalogs: with German chosen, the fieldset's own
    copy AND the conflict refusal (a CONSOLE refusal, rendered through the one-shot notice) are German.

    English stays the msgid, so the English render is byte-for-byte what it always was.
    """
    from services.webservice import i18n

    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))

    async with client(app) as http:
        await sign_in(http, "mod1")
        http.cookies.set(i18n.COOKIE_NAME, "de")
        dialog = await open_dialog(http)
        assert "Oder ein temporäres Preset anlegen" in dialog.text
        assert "(optional)" in dialog.text, "the summary's optional word is not translated"
        assert "Gilt nur für diesen Start" in dialog.text
        assert "in diesem Modus die ganze Wahl" in dialog.text
        # the CONFLICT refusal is the console's own wording, so it translates too
        reply = await posted(http, dialog.text, **{PRESET_FIELD: "Summer", "clouds.base": "1500"})
        assert reply.status_code == 400
        page = await open_page(http)

    assert "Nicht ausgeführt (es wurde ein Preset gewählt" in page


# ----------------------------------------------------- the block: closed, optional, JS-off safe

@pytest.mark.asyncio
async def test_the_temporary_block_is_a_plain_region_with_a_heading_that_says_optional(
        config_dir, clean_registry):
    """ACCEPTANCE 4 — the block is a PLAIN region (never a closed ``<details>`` the operator has to
    open), whose heading states in words that adding a preset is optional. Its controls are plain
    form-associated inputs, so it needs no script to submit; with JavaScript off it stays visible.
    """
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    html = dialog.text
    block = weather_fieldset(html)
    assert block.startswith('<div class="dlg-temp">'), "the block must be a plain region"
    assert "<details" not in html and "</details>" not in html, \
        "the block must not be a disclosure the fields can hide behind"
    assert re.search(r'<h4 class="dlg-temp-head">.*?</h4>', block, re.S), "the heading is missing"
    assert actions_page.TEMPORARY_HEAD in block
    # the heading says, in words, that this block is optional
    assert f"({actions_page.TEMPORARY_OPTIONAL})" in block
    assert "(optional)" in block
    # nothing starts hidden in the markup, so a no-JavaScript browser sees the fields
    assert "display:none" not in block and 'style="display' not in block
    # and it stays fully usable with JavaScript off: every control is a plain, form-associated input
    for key in TEMPORARY_KEYS:
        if key == weather_fields.FOG_ROWS_KEY:
            for cell in ("time", "thickness", "visibility"):
                assert f'name="{weather_fields.fog_row_field(0, cell)}"' in block
            continue
        assert f'name="{key}"' in block
    named = re.findall(r'<(?:input|select)\b[^>]*\bname="([^"]+)"[^>]*>', block)
    assert block.count('form="dlg-form"') == len(named)


@pytest.mark.asyncio
async def test_an_untouched_form_posts_no_preset_and_the_post_works_unopened(
        config_dir, clean_registry, recording_bot):
    """CONSTRAINT 2 + ACCEPTANCE 4 — a launch that never opened the block posts exactly the empty
    form's payload, reaches the action as no preset, and the launch still happens.

    The empty document is what "no preset chosen" means: the pre-fill is a STARTING VALUE, never a
    submitted one, so an untouched form can never become a preset.
    """
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await posted(http, dialog.text)  # nothing from the block, and it is never opened

    assert reply.status_code == 303, reply.text
    assert cluster.servers[0].call_args == [
        {"modify_mission": True, "use_orig": True, "preset": None}]
    assert [call[0] for call in recording_bot.calls] == [
        "Server 'SRS-1' started up."]


@pytest.mark.asyncio
async def test_the_launch_fieldset_starts_blank_and_never_invents_a_number(config_dir, clean_registry):
    """ACCEPTANCE 1 (the honest half) + REQUIRED OUTCOME 2 — with no mission weather to seed from,
    the block's controls start EMPTY: no invented number on any control, and the keyframe row carries
    no bare ``0 s`` literal (with no fog animation there is no seconds tag at all).
    """
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = weather_fieldset(dialog.text)
    # every named numeric/text control renders an EMPTY value — never an invented one
    controls = re.findall(r'type="(?:number|text)" name="([^"]+)" value="([^"]*)"', block)
    assert controls, "the fieldset drew no named controls"
    # every named numeric/text control renders an EMPTY value, except a range whose own floor is above
    # zero: an empty box there would read as 0, which the range does not contain.
    floors = {entry.key: entry.default for entry in weather_fields.FIELDS
              if entry.default is not None}
    for name, value in controls:
        # a control that states its own floor states it in the unit the dialog draws (feet here).
        expected = "" if name not in floors else str(int(weather_fields.display_value(
            name, floors[name])))
        assert value == expected, f"the '{name}' control stated {value!r}, not {expected!r}"
    assert [name for name, value in controls if value] == ["dust_density"], \
        "the dust visibility is the ONE control that starts at its own floor"
    # the keyframe row's bare seconds literal is gone; with no fog animation there is no tag at all
    assert '<span class="t">' not in block
    assert "0 s" not in block


# ---------------------------------------------- 6. the slider pair, the SAME shared asset

SLIDER_JS_TAG = re.compile(
    r'<script src="/static/weather-slider\.js(\?v=[^"]*)?" defer></script>')


def slider_pairs(block: str) -> list[str]:
    """Every rendered ``[data-weather-pair]`` slider group in the dialog's fieldset.

    Only a slider row names itself this way (the direction dial is a ``data-weather-pair`` /
    ``data-weather-dial`` group of its own), so a row that draws no pair is simply absent here.
    """
    return re.findall(r'<div class="dlg-wsl" data-weather-pair>(.*?)</div>', block, re.S)


def _attr(tag: str, name: str) -> str:
    """The value of attribute *name* in *tag*, or ``''`` when it carries none."""
    match = re.search(rf'\b{name}="([^"]*)"', tag)
    return match.group(1) if match else ""


@pytest.mark.asyncio
async def test_the_dialog_draws_the_same_slider_pair_and_loads_the_same_asset(config_dir,
                                                                             clean_registry):
    """CONSTRAINT 1 + ACCEPTANCE 4 — the launch dialog draws the SAME range/number/readout pair the
    wizard draws, wired by the ONE shared asset this page loads too. Its rows are built EMPTY, so the
    pair is blank here (the moment a slider moves, a value appears — proven by the shipped script's
    own harness in ``test_webui_presets_wizard``). The number box is the submitted control: named,
    form-associated, and bounded by the field table's own min/max/step.
    """
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    html = dialog.text
    pairs = slider_pairs(weather_fieldset(html))
    assert pairs, "the dialog drew no slider pair"
    for pair in pairs:
        rng = re.search(r'<input type="range"[^>]*>', pair)
        num = re.search(r'<input type="number"[^>]*\bname="([^"]+)"[^>]*>', pair)
        out = re.search(r'<output[^>]*data-weather-readout[^>]*>(.*?)</output>', pair, re.S)
        assert rng is not None and num is not None and out is not None, pair
        assert "name=" not in rng.group(0), "the range must never submit a field of its own"
        entry = weather_fields.field(num.group(1))
        stated = _attr(num.group(0), "value")
        # a control whose range excludes 0 states its own floor (in the render's unit); every other
        # starts blank — and the readout beside it says exactly what the box states.
        assert stated == ("" if entry.default is None
                          else str(int(weather_fields.display_value(entry.key, entry.default)))), \
            f"the '{entry.key}' box states {stated!r}"
        assert out.group(1) == stated, "the readout repeats the box's own value"
        low, high, _step = weather_fields.display_bounds(entry)
        assert (_attr(num.group(0), "min"), _attr(num.group(0), "max"),
                _attr(num.group(0), "step")) == \
            (str(low), str(high), entry.box_step), num.group(1)
    # and the page loads the SAME shipped asset the wizard loads — one implementation, never two
    assert SLIDER_JS_TAG.search(html), "the dialog does not load the shared slider asset"
