"""The launch dialog's temporary rows GATE exactly like the wizard's step, and the cloud base prefills.

The rows are built by ``presets.dialog_rows`` over the ONE field table; each row carries the wizard's
OWN gate (:func:`presets.row_gate`) as ``data-gate-*`` attributes, and the dialog's shipped asset
(``static/dialog-preset.js``) shows/hides the row by the CURRENT mode values. This module pins:

* the SERVER-side gate data equals :func:`presets.row_gate` (one source, never a second list);
* the SHIPPED asset, driven against the RENDERED rows at FIELD level, shows EXACTLY the rows the
  wizard's own step would draw for the same values — mode by mode;
* the cloud base prefills from the chosen preset's own ``min(altitudeMin)`` (the number it shows);
* the custom entry's word and place;
* the asset never submits the launch (the dialog is a POST form).

The scripts are driven browser-less (``node``), the same technique the sibling dialog tests use; the
rows come from the RENDERED page, so the shipped bytes meet the real gate data.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.webservice import weather_fields
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from test_webui_presets_clouds import CloudsNode, install_clouds
from test_webui_presets_halo import install_halo
from test_webui_startup_temporary_preset import weather_fieldset
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    PresetNode, node_fleet, open_dialog, pick_block, pick_values,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    build, clean_registry, client, config_dir, sign_in,
)

TESTS = Path(__file__).resolve().parent
SERVICES = TESTS.parent / "services" / "webservice"
GATE_JS = SERVICES / "static" / "dialog-preset.js"
WIZARD_JS = SERVICES / "static" / "presets-wizard.js"
GATE_HARNESS = TESTS / "dialog_gate_harness.js"
SCROLL_HARNESS = TESTS / "presets_scroll_harness.js"

CUSTOM = actions_page.PRESET_CUSTOM_VALUE
#: the two weather steps — their union is every temporary key except the always-drawn date/time rows.
WEATHER_STEPS = (presets_page.WIZARD_STEP_CLOUDS, presets_page.WIZARD_STEP_WIND)
#: the rows the dialog always draws, whatever the mode (they belong to the wizard's TIME step).
ALWAYS = frozenset({"date", "start_time"})


def requires_node(func):
    return pytest.mark.skipif(shutil.which("node") is None,
                              reason="node is not installed: no DOM to drive the shipped asset with")(func)


def readable_fleet(tmp_path, config_dir):
    """ONE server whose node carries a DCS installation AND a preset file — lists readable on both."""
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    install_clouds(tmp_path)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    return node, node_fleet(node)


def row_entries(block: str) -> list[dict]:
    """Every row's ``{key, cloud, haze, fogMode, haloReal}``, read from the RENDERED markup."""
    rows: list[dict] = []
    for row in re.findall(r'<div class="dlg-wrow"[^>]*>.*?</div>\s*</div>', block, re.S):
        heading = re.search(r'<div class="dlg-wrow"([^>]*)>', row)
        attrs = heading.group(1) if heading else ""
        name = re.search(r'<span class="kk">([^<]+)</span>', row)
        assert name, row

        def value(attribute: str) -> str:
            found = re.search(rf'\b{attribute}="([^"]*)"', attrs)
            return found.group(1) if found else ""

        rows.append({
            "key": name.group(1),
            "cloud": value("data-gate-cloud"),
            "haze": value("data-gate-haze"),
            "fogMode": value("data-gate-fog-mode"),
            "haloReal": "data-halo-real" in attrs,
        })
    return rows


def wizard_gate(values: dict, *, plates_readable: bool = True, halo_readable: bool = True) -> set[str]:
    """The keys the wizard's OWN step draws for *values* — the parity reference."""
    keys: set[str] = set()
    for step in WEATHER_STEPS:
        keys.update(presets_page.step_controls(step, values, plates_readable=plates_readable,
                                               halo_readable=halo_readable))
    return keys


# ------------------------------------------------------------------ 1. the gate data is the one source

@pytest.mark.asyncio
async def test_every_row_carries_the_wizards_own_gate(config_dir, clean_registry, tmp_path):
    """Each rendered row's ``data-gate-*`` equals :func:`presets.row_gate` for its key — one list."""
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    rows = row_entries(weather_fieldset(dialog.text))
    assert [row["key"] for row in rows] == list(actions_page.TEMPORARY_KEYS)
    for row in rows:
        gate = presets_page.row_gate(row["key"], plates_readable=True)
        assert row["cloud"] == " ".join(gate.get("cloud", ())), row["key"]
        assert row["haze"] == " ".join(gate.get("haze", ())), row["key"]
        assert row["fogMode"] == " ".join(gate.get("fog_mode", ())), row["key"]
        assert row["haloReal"] is bool(gate.get("halo_real")), row["key"]


# ------------------------------------------------- 2. the shipped asset gates EXACTLY like the wizard

@requires_node
@pytest.mark.asyncio
async def test_the_shipped_asset_shows_the_rows_the_wizard_step_would_draw(
        config_dir, clean_registry, tmp_path):
    """The shipped asset, driven against the RENDERED rows, shows the wizard's own set — per mode.

    FIELD level: the report lists the visible KEY of every row, and each state is compared against
    :func:`presets.step_controls` for the same values.
    """
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    rows = row_entries(weather_fieldset(dialog.text))
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(rows), encoding="utf-8")
    done = subprocess.run(["node", str(GATE_HARNESS), str(GATE_JS), str(rows_file)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = {entry["step"]: entry for entry in json.loads(done.stdout)}

    # the state each harness step sets
    states = {
        "none": {},
        "clouds-preset": {"clouds": "preset"},
        "clouds-dynamic": {"clouds": "dynamic"},
        "clouds-clear": {"clouds": "clear"},
        "dust": {weather_fields.HAZE_KEY: "dust"},
        "fog-auto": {weather_fields.HAZE_KEY: "fog", weather_fields.FOG_MODE_KEY: "auto"},
        "fog-manual": {weather_fields.HAZE_KEY: "fog", weather_fields.FOG_MODE_KEY: "manual"},
        "halo-off": {"clouds": "preset", "halo.preset": "off"},
        "halo-real": {"clouds": "preset", "halo.preset": "Preset1"},
    }
    for step, values in states.items():
        seen = set(report[step]["visible"]) - ALWAYS
        assert seen == wizard_gate(values), (step, sorted(seen), sorted(wizard_gate(values)))

    # the owner's exact report: a cloud preset mode draws NO density/thickness/precipitation
    preset_rows = set(report["clouds-preset"]["visible"])
    assert {"clouds.density", "clouds.thickness", "clouds.iprecptns"}.isdisjoint(preset_rows)
    assert {"clouds.preset", "clouds.base"} <= preset_rows
    # ...and the fog table is drawn for manual only, never for auto
    assert weather_fields.FOG_ROWS_KEY in report["fog-manual"]["visible"]
    assert weather_fields.FOG_ROWS_KEY not in report["fog-auto"]["visible"]
    # ...and the ONE either-or control shows exactly one side: dust draws its visibility and NO fog
    # control, fog draws its mode and NO dust visibility.
    assert "dust_density" in report["dust"]["visible"]
    assert weather_fields.FOG_MODE_KEY not in report["dust"]["visible"]
    assert weather_fields.FOG_MODE_KEY in report["fog-manual"]["visible"]
    assert "dust_density" not in report["fog-manual"]["visible"]


@requires_node
def test_the_dialog_asset_never_submits_the_launch():
    """The gate is a display toggle: the asset carries NO `submit` — the dialog form POSTS the launch."""
    text = GATE_JS.read_text(encoding="utf-8")
    assert ".submit(" not in text and "requestSubmit" not in text, \
        "a mode change must never submit the launch form"
    assert 'data-gate-cloud' in text and 'data-gate-haze' in text, "the asset reads the gate data"


# ------------------------------------------------------------------ 3. the cloud base prefills

@pytest.mark.asyncio
async def test_the_cloud_preset_option_carries_the_presets_own_base(config_dir, clean_registry, tmp_path):
    """The picker option carries the preset's ``min(altitudeMin)`` — the number the dialog shows."""
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = weather_fieldset(dialog.text)
    row = next(r for r in re.findall(r'<div class="dlg-wrow"[^>]*>.*?</div>\s*</div>', block, re.S)
               if 'name="clouds.preset"' in r)
    option = re.search(r'<option value="Preset1"[^>]*>', row)
    assert option, "the Preset1 option is missing"
    presets = presets_page.dcs_clouds.visible_presets(
        presets_page.dcs_clouds.parse_cloud_presets(
            (tmp_path / presets_page.dcs_clouds.CLOUDS_LUA_RELATIVE_PATH).read_text(encoding="utf-8")))
    chosen = next(preset for preset in presets if preset.name == "Preset1")
    assert chosen.base is not None and chosen.base == 2520.0, "the read layer's own figure"
    # the option states it in the unit the dialog's box is drawn in: feet for English.
    stated = int(weather_fields.display_value("clouds.base", chosen.base))
    assert f'data-base="{stated}"' in option.group(0), option.group(0)


@requires_node
@pytest.mark.asyncio
async def test_choosing_a_preset_fills_the_base_and_a_readable_node_is_required(
        config_dir, clean_registry, tmp_path):
    """The shipped asset fills ``clouds.base`` from the option's own ``data-base`` (the harness number).

    With a node whose list cannot be read, the picker is free text and carries NO ``data-base``, so
    nothing is invented.
    """
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
    rows = row_entries(weather_fieldset(dialog.text))
    rows_file = tmp_path / "rows.json"
    rows_file.write_text(json.dumps(rows), encoding="utf-8")
    done = subprocess.run(["node", str(GATE_HARNESS), str(GATE_JS), str(rows_file)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = {entry["step"]: entry for entry in json.loads(done.stdout)}
    assert report["base-prefill"]["base"] == "2520"
    # ...and clearing the picker back to its empty entry clears the base with it, so a stale editable
    # figure never rides along a selection that asked for no preset.
    assert report["base-cleared"]["base"] == ""

    # the fallback: no readable list -> a typed box, no data-base anywhere
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        fallback = await open_dialog(http)
    block = weather_fieldset(fallback.text)
    assert 'name="clouds.preset"' in block and 'data-base=' not in block


# ------------------------------------------------------------------ 4. the custom entry

@pytest.mark.asyncio
async def test_the_custom_entry_is_renamed_and_sits_below_leave_the_weather_alone(
        config_dir, clean_registry):
    """The three modes sit in the order the drawing asks for: leave, a file, your own."""
    (config_dir / "presets.yaml").write_text(
        "Summer:\n  clouds:\n    base: 1000\nWinter:\n  clouds:\n    base: 500\n", encoding="utf-8")
    app = build(config_dir, node_fleet(PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)

    block = pick_block(dialog.text)
    assert set(pick_values(block)) == {"Summer", "Winter"}, "the pick is names only"
    mode = re.search(r'<fieldset class="dlg-field dlg-radio".*?</fieldset>', dialog.text, re.S)
    assert mode, "the weather mode control is missing"
    rows = re.findall(r'value="(leave|file|custom)"', mode.group(0))
    assert rows == ["leave", "file", "custom"], rows
    assert ">Create your own weather</label>" in mode.group(0)
    assert actions_page.WEATHER_MODE_CUSTOM_LABEL == "Create your own weather"


# ------------------------------------------------------------------ 5. the rebuild keeps the position

@requires_node
def test_a_rerender_keeps_the_operator_where_they_were():
    """The shipped ``presets-wizard.js`` remembers the changed control (or the offset) and restores it.

    Driven browser-less with a stub ``sessionStorage``: a mode change stores the changed control AND
    submits the step's GET form; the next load scrolls it into view and consumes the slot; a Next/Back
    submit stores the OFFSET instead, restored through ``scrollTo``.
    """
    from conftest import run_node_harness

    assert SCROLL_HARNESS.is_file(), "the harness that drives the shipped asset is missing"
    done = run_node_harness(["node", str(SCROLL_HARNESS), str(WIZARD_JS)])
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)
    assert seen["bound"] == 1, "the script still binds ONE delegated change listener"
    assert seen["afterModeChange"]["submitted"] is True, "a mode control still submits the step"
    stored = json.loads(seen["afterModeChange"]["stored"])
    assert stored["name"] == "clouds", "the changed control is remembered"
    assert stored["y"] == 1234, "its scroll offset rides along"
    assert seen["afterRestore"]["scrolled"] == 1, "the control is scrolled into view on the next load"
    assert seen["afterRestore"]["left"] is None, "the slot is consumed"
    assert json.loads(seen["afterNext"]["stored"])["name"] == "", "a Next/Back press stores no control"
    assert seen["afterNextRestore"]["scrolledTo"] == [0, 1234], "the offset is restored for Next/Back"
