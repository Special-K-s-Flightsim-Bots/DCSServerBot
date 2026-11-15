"""The launch dialog's temporary preset: EVERY row draws the wizard's own control.

The dialog builds its rows with ``presets.dialog_rows`` (the wizard's own row builder, filled from the
SAME choice builders the wizard's step reads) and draws them with ``confirm.html``'s ``weather_input``
macro. This module pins that the macro knows every kind the field table declares, that no row comes out
a bare text box, that the halo select is never empty, that the wizard's step and the dialog cannot
drift into two option lists, and that the posted document keeps its shape.

One section per acceptance criterion:

* the macro's VOCABULARY — every ``row.kind`` the field table declares has a branch (the two news:
  the halo is a LIST, the wind direction a DIAL);
* the PER-ROW CENSUS through the repo's own fixtures, for BOTH launch dialogs — how many of the 23
  temporary rows draw as a REAL control (a select, a picker, a date, a dial, a number, the fog table),
  naming every row that still draws as a text box and WHY;
* the HALO SELECT — never an empty select: the table's own ``off``/``auto`` are always carried, and the
  node's ``icehalo.lua`` entries are appended when the file is readable;
* ONE HOME — the wizard's step select and the dialog's select carry the SAME option values;
* the DOCUMENT — the posted fields (the fog table's indexed cells included) build the ONE document
  builder's shape, unchanged.

Driven through the REAL application and the repo's own node doubles, exactly as the sibling dialog tests.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.webservice import weather_fields
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from core import Status
from test_webui_missions_load_form import load_fleet, open_load_dialog  # noqa: F401 - fixtures reused
from test_webui_presets_clouds import CloudsNode, install_clouds
from test_webui_presets_halo import HALO_ENTRIES, install_halo
from test_webui_startup_weather_picker import PresetNode, node_fleet, open_dialog  # noqa: F401
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, recording_bot, sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

STARTUP = "/actions/server/startup"
CONFIRM_TEMPLATE = Path(actions_page.__file__).resolve().parents[1] / "templates" / "confirm.html"
FOG_ROWS_KEY = weather_fields.FOG_ROWS_KEY


# --------------------------------------------------------------------------- helpers

def fleet_with(node):
    """ONE SHUTDOWN server whose OWN node AND whose scoped-source node are *node*.

    ``node_fleet`` maps the scoped source to a bare namespace, which is enough for the preset-file
    pick but NOT for the wizard's ``clouds.lua``/``icehalo.lua`` read (it reaches the real node's
    ``read_file`` and its installation). This maps the REAL node in both places, so the wizard and the
    dialog read the SAME list.
    """
    server = FakeServer("SRS-1", status=Status.SHUTDOWN, managed_by=["Host A"])
    server.node = node
    return Cluster([server], nodes={node.name: node}, node=node,
                   instances=[instance("Host-A-Instance", server)])

def weather_fieldset(html: str) -> str:
    """The rendered ``<div class="dlg-temp">…</div>`` block."""
    match = re.search(r'<div class="dlg-temp">.*?<p class="dlg-temp-note">.*?</p>\s*</div>', html, re.S)
    assert match, "the dialog rendered no temporary-preset fieldset"
    return match.group(0)


def drawn_rows(block: str) -> list[tuple[str, str]]:
    """``(key, row markup)`` for every row the fieldset draws, in the order it draws them."""
    out: list[tuple[str, str]] = []
    for row in re.findall(r'<div class="dlg-wrow"[^>]*>.*?</div>\s*</div>', block, re.S):
        key = re.search(r'<span class="kk">([^<]+)</span>', row)
        assert key, row
        out.append((key.group(1), row))
    return out


#: a control that is NOT a bare text box — the wizard's own vocabulary, minus ``text``.
REAL_CONTROL = ('<select', 'type="date"', 'type="range"', 'type="number"', 'type="time"')

#: the two rows that draw as a text box ONLY because their key is a picture whose node list cannot be
#: read: the SAME free-text fallback the wizard's step offers. A readable node draws them as selects.
PICTURE_KEYS = ("clouds.preset", "halo.crystalsPreset")


def draws_real(row_html: str) -> bool:
    return any(kind in row_html for kind in REAL_CONTROL)


# --------------------------------------------------------------- 1. the macro's vocabulary

def test_the_dialog_macro_knows_every_kind_the_table_declares():
    """Every ``kind`` the field table declares has its own branch in ``confirm.html``'s macro.

    A RED here means a row would fall through to the ``else`` text box — the exact defect this card
    exists to fix. ``KIND_TOGGLE`` is declared but unused by the table; the macro still draws it.
    """
    macro = re.search(r'\{% macro weather_input.*?\{%- endmacro %\}',
                      CONFIRM_TEMPLATE.read_text(encoding="utf-8"), re.S)
    assert macro, "confirm.html no longer carries the weather_input macro"
    # ``text`` is the macro's own ELSE branch, so it counts as known; every OTHER kind must be named.
    drawn = set(re.findall(r"row\.kind == '([^']+)'", macro.group(0))) | {weather_fields.KIND_TEXT}
    # the macro must know every kind the table uses AND the declared-but-unused toggle.
    expected = {entry.kind for entry in weather_fields.FIELDS}
    expected |= {column.kind for column in weather_fields.FOG_COLUMNS}
    expected |= {weather_fields.KIND_TOGGLE}
    assert drawn == expected, \
        f"branches missing: {sorted(expected - drawn)}; branches that name no declared kind: " \
        f"{sorted(drawn - expected)}"
    for kind in (weather_fields.KIND_DIAL, weather_fields.KIND_FOGTABLE, weather_fields.KIND_PICKER,
                 weather_fields.KIND_DATE, weather_fields.KIND_MOMENT):
        assert kind in drawn, kind


# --------------------------------------------------------------- 2. the per-row census

@pytest.mark.asyncio
async def test_every_temporary_row_draws_a_real_control_with_a_readable_node(
        config_dir, clean_registry, tmp_path):
    """BOTH launch dialogs: with the node's own lists readable, all 23 rows draw a REAL control.

    The node carries a DCS installation with ``clouds.lua`` and ``icehalo.lua``, so the two picture
    keys draw the wizard's own plate CHOICES as selects and the halo control lists ``off``/``auto``
    plus the file's entries.
    """
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    install_clouds(tmp_path)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, node_fleet(node))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = weather_fieldset(dialog.text)
    rows = drawn_rows(block)
    assert [key for key, _row in rows] == list(actions_page.TEMPORARY_KEYS)
    as_text = [key for key, row in rows if not draws_real(row)]
    assert as_text == [], f"rows still drawn as a text box: {as_text}"

    # the two picture keys carry the node's own entries as a select's options
    by_key = dict(rows)
    assert '<option value="Preset1"' in by_key["clouds.preset"]
    assert '<option value="AllKinds">' in by_key["halo.crystalsPreset"]


@pytest.mark.asyncio
async def test_the_picture_keys_fall_back_to_free_text_like_the_wizard(config_dir, clean_registry):
    """A node whose vendor files cannot be read draws the two picture keys as TEXT — the wizard's own
    fallback, not a defect. Every OTHER row is still a real control, and the halo select is never empty.
    """
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    rows = drawn_rows(weather_fieldset(dialog.text))
    as_text = [key for key, row in rows if not draws_real(row)]
    assert as_text == list(PICTURE_KEYS), \
        f"only the two picture keys may fall back to text, got {as_text}"
    halo = dict(rows)["halo.preset"]
    assert '<option value="off">off</option>' in halo
    assert '<option value="auto">auto</option>' in halo


@pytest.mark.asyncio
async def test_the_load_dialog_draws_the_same_controls_as_the_startup_dialog(config_dir, clean_registry):
    """The Missions tab's Load dialog is the SAME launch form: its rows carry the same real controls,
    and its halo select is never empty either.

    Its node carries no DCS installation (the mission fixture), so the two picture keys draw the
    free-text fallback exactly as Startup's do on such a node — the classification must MATCH.
    """
    app = build(config_dir, load_fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        load = await open_load_dialog(http)

    block = weather_fieldset(load.text)
    rows = drawn_rows(block)
    assert [key for key, _row in rows] == list(actions_page.TEMPORARY_KEYS)
    as_text = [key for key, row in rows if not draws_real(row)]
    assert as_text == list(PICTURE_KEYS), as_text
    halo = dict(rows)["halo.preset"]
    assert '<option value="off">off</option>' in halo and '<option value="auto">auto</option>' in halo
    for key in ("date", "start_time", "wind.atGround.dir", "wind.at2000.dir", "wind.at8000.dir"):
        assert draws_real(dict(rows)[key]), key
    assert 'name="fog_row_0_time"' in block


# --------------------------------------------------------------- 3. one home for the choices

def test_the_halo_choices_have_one_home():
    """The closed pair lives in the FIELD TABLE; the wizard and the dialog read the SAME builder."""
    assert [value for value, _label in weather_fields.field("halo.preset").choices] == ["off", "auto"]
    assert [option["value"] for option in presets_page.halo_row_choices([])] == ["off", "auto"]

    class Entry:
        name = "AtmoHighClouds"
        label = "Atmo High Clouds"

    # a node entry is APPENDED, never a replacement — the pair cannot be lost
    assert [option["value"] for option in presets_page.halo_row_choices([Entry()])] == \
        ["off", "auto", "AtmoHighClouds"]
    # and the names the wizard's page uses are the table's own values, not a second spelling
    assert presets_page.HALO_OFF_PRESET == weather_fields.HALO_OFF
    assert presets_page.HALO_AUTO_PRESET == weather_fields.HALO_AUTO
    from services.webservice import dcs_halo
    assert dcs_halo.HALO_AUTO_PRESET == weather_fields.HALO_AUTO


@pytest.mark.asyncio
async def test_the_wizard_step_and_the_dialog_halo_selects_cannot_disagree(
        config_dir, clean_registry, tmp_path):
    """The SAME node: the wizard's ``halo.preset`` select and the dialog's carry the SAME options."""
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    app = build(config_dir, fleet_with(node))
    async with client(app) as http:
        await sign_in(http, "specialk")
        wizard = await http.get(f"{presets_page.wizard_url('Summer')}?step=clouds&node={node.name}")
        assert wizard.status_code == 200, wizard.text
        dialog = await open_dialog(http)

    def halo_options(html: str) -> list[str]:
        select = re.search(r'<select[^>]*\bname="halo\.preset"[^>]*>.*?</select>', html, re.S)
        assert select, "no halo select rendered"
        return re.findall(r'<option value="([^"]*)"', select.group(0))

    wizard_options = halo_options(wizard.text)
    dialog_options = halo_options(weather_fieldset(dialog.text))
    assert wizard_options[1:] == ["off", "auto", *HALO_ENTRIES], wizard_options
    assert dialog_options == wizard_options, (dialog_options, wizard_options)


# --------------------------------------------------------------- 4. the document, unmoved

@pytest.mark.asyncio
async def test_the_filled_form_builds_the_one_document_shape(config_dir, clean_registry, recording_bot):
    """A filled temporary preset (the fog table included) produces the ONE order's document.

    The dialog posts the fog table's indexed cells; the route composes them into the ``fog.rows`` value
    through the wizard's own serializer before the ONE builder reads it. The document — the shape that
    reaches ``Server.startup`` — is unchanged: preset keys, coerced types, no ``modify`` DSL.
    """
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={
            "server": "SRS-1", "_csrf_token": token_of(dialog.text), "preset": "__create_your_own__",
            "date": "2016-04-21", "start_time": "morning", "clouds": "dynamic",
            "clouds.base": "1500", "clouds.density": "4", "wind.atGround.speed": "7",
            "wind.atGround.dir": "220", "enable_dust": "fog", "fog.mode": "manual",
            "fog_row_0_time": "0", "fog_row_0_thickness": "100", "fog_row_0_visibility": "500",
        })

    assert reply.status_code == 303, reply.text
    (call,) = cluster.servers[0].call_args
    document = call["preset"]
    assert document["date"] == "2016-04-21" and document["start_time"] == "morning"
    assert document["clouds"] == {"base": 1500, "density": 4}
    assert document["wind"]["atGround"] == {"speed": 7, "dir": 220}
    assert document["enable_fog"] is True
    assert document["fog"] == {"mode": "manual", 0: {"thickness": 100, "visibility": 500}}
    assert "modify" not in document, "no expert DSL is invented by the weather controls"
    assert "fog.rows" not in document, "the control's own key never reaches the document"


@pytest.mark.asyncio
async def test_a_blank_fog_row_posts_nothing(config_dir, clean_registry, recording_bot):
    """The table's blank row is a control, not a value: an untouched blank row writes no fog key."""
    cluster = node_fleet(PresetNode(config_dir))
    app = build(config_dir, cluster)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        reply = await http.post(STARTUP, data={
            "server": "SRS-1", "_csrf_token": token_of(dialog.text), "preset": "__create_your_own__",
            "temperature": "18",
            "fog_row_0_time": "", "fog_row_0_thickness": "", "fog_row_0_visibility": "",
        })
    assert reply.status_code == 303, reply.text
    document = cluster.servers[0].call_args[0]["preset"]
    assert document == {"temperature": 18}, document


def _form(values: dict):
    """A form-like double: ``get``/``getlist``/``items`` over *values* (the route reads these three)."""
    class Form(dict):
        def getlist(self, name):
            value = self.get(name)
            return value if isinstance(value, list) else ([] if value is None else [value])

        def get(self, name, default=None):
            return dict.get(self, name, default)

    return Form(values)


def test_the_document_is_unmoved_whether_the_old_text_or_the_new_cells_are_posted():
    """The write shape is proven unmoved: the SAME document whether the fog value arrives as the old
    ``fog.rows`` text field or as the fog table's indexed cells.

    This is the before/after of the change: ``fog.rows`` was a text box (the operator typed
    ``0:100:500``); it is now the indexed cells, and the route composes the SAME value the ONE builder
    consumes. Nothing else in the document moves.
    """
    common = {"enable_dust": "fog", "fog.mode": "manual", "temperature": "18",
              "clouds": "dynamic", "clouds.base": "1500"}
    before = actions_page.temporary_document(_form({**common, "fog.rows": "0:100:500"}))
    after = actions_page.temporary_document(_form({
        **common, "fog_row_0_time": "0", "fog_row_0_thickness": "100",
        "fog_row_0_visibility": "500"}))
    assert before == after, (before, after)
    assert before["fog"] == {"mode": "manual", 0: {"thickness": 100, "visibility": 500}}
    assert before["clouds"] == {"base": 1500}
    assert before["temperature"] == 18


def test_the_fog_table_states_a_filled_preset():
    """A filled keyframe is a stated weather field even while every other control is empty."""
    assert actions_page.temporary_stated(_form({"fog_row_0_time": "0", "fog_row_0_thickness": "100"}))
    assert not actions_page.temporary_stated(_form({"fog_row_0_time": "", "fog_row_0_thickness": ""}))
    assert not actions_page.temporary_stated(_form({}))
