"""The launch dialog FILLS its temporary preset from the mission's own weather, on demand.

The read side (``GET /fleet/<name>/weather/mission.json``) is pinned by ``test_webui_mission_weather``;
this module pins the DIALOG's half of it — the shipped ``static/dialog-mission-weather.js``:

* ON DEMAND ONLY: nothing is fetched while the dialog renders; the read fires the moment the pick's
  "Create your own weather" entry is selected, and it never submits the launch form;
* THE LINE: "Loading current mission …" while the read runs, then "Read from <name>.miz" once it
  answers, the read route's own sentence when it fails, and empty when the selection is cleared;
* THE ROWS: the mission's own dotted-key values land in the matching controls at FIELD level;
* THE PICKERS: a mission that names a cloud preset or an ice-crystal preset the node's own list
  carries is SET to that entry; a name the list does NOT carry leaves the control as rendered;
* CLEARING: selecting anything else restores every control this script filled and empties the line;
* THE GATE: a server that names NO current mission renders no weather pick and a sentence saying why,
  while the LOAD dialog (which always has its picked row) keeps its pick;
* THE CATALOGS: every new sentence is translated, with the compiled ``.mo`` newer than its ``.po``.

The asset is driven browser-less (``node``) against the RENDERED controls and the REAL route values,
the same technique the sibling dialog tests use.
"""
from __future__ import annotations

import io

import gettext
import html
import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from core import Status
from services.webservice import templating, weather_fields
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from services.webservice.pages import server_detail as detail_page
# the fixtures and harnesses of the sibling dialog suites, reused so this module drives the SAME pages
from test_mission_weather_read import EXPECTED_DOCUMENT
from test_webui_presets_clouds import CloudsNode, install_clouds
from test_webui_presets_halo import install_halo
from test_webui_startup_temporary_preset import _attr, weather_fieldset
from test_webui_startup_weather_picker import (  # noqa: F401 - fixtures imported on purpose
    PresetNode, open_dialog, node_fleet,
)
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, instance, open_page, sign_in, token_of,
)
from test_webui_write_server_actions import FakeServer

import plugins.mission.actions as mission_actions

STARTUP = "/actions/server/startup"
CONFIRM = actions_page.CONFIRM_SUFFIX
SERVER = "SRS-1"
CUSTOM = actions_page.PRESET_CUSTOM_VALUE
MISSION_NAME = "Training.miz"
#: the mission-weather read's OWN sentence for a ``.miz`` the console cannot parse — the class of
#: answer the ROUTE returns as the failure body the line must show.
FAILURE_SENTENCE = mission_actions.MISSION_WEATHER_UNREADABLE.format(mission="Broken.miz", server=SERVER)

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
SERVICES = REPO / "services" / "webservice"
ASSET = SERVICES / "static" / "dialog-mission-weather.js"
HARNESS = TESTS / "dialog_mission_harness.js"
GATE_ASSET = SERVICES / "static" / "dialog-preset.js"
GATE_HARNESS = TESTS / "dialog_gate_harness.js"
LOCALE = REPO / "locale"

#: the four sentences this card introduces, with the placeholder each carries.
NEW_SENTENCES = (
    actions_page.PRESET_READ_LOADING,
    actions_page.PRESET_READ_DONE,
    actions_page.PRESET_READ_FAILED,
    actions_page.PRESET_NO_MISSION,
)
#: the languages whose compiled catalog must serve a NON-empty translation for each new sentence.
TRANSLATED = ("de", "es", "ru", "cn")


def requires_node(func):
    return pytest.mark.skipif(shutil.which("node") is None,
                              reason="node is not installed: no DOM to drive the shipped asset with")(func)


def readable_fleet(tmp_path, config_dir):
    """ONE server whose node carries a DCS installation (cloud + halo lists) AND a preset file."""
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    install_clouds(tmp_path)
    install_halo(tmp_path)
    node = CloudsNode(installation=tmp_path)
    return node, node_fleet(node)


class MissionServer(FakeServer):
    """A server double that ANSWERS ``get_current_mission_file`` — the STARTUP gate's own read."""

    def __init__(self, name, *, current="", **kwargs):
        super().__init__(name, **kwargs)
        self._current = current

    async def get_current_mission_file(self):
        return self._current


def fleet_for(server, node):
    """ONE server (``SRS-1``, Host A) whose node is *node*."""
    server.node = node
    node_a = types.SimpleNamespace(name=node.name, master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", server)})
    return Cluster([server], nodes={node.name: node_a}, node=node_a,
                   instances=[instance("Host-A-Instance", server)])


def temp_controls(block: str) -> list[dict]:
    """Every NAMED control the rendered temporary block draws, as the harness's own control specs.

    The dialog's OWN markup is read (never a re-declaration): a ``<select>`` carries its option values
    and its rendered value is its first (empty) entry; an ``<input>`` carries its rendered ``value``.
    """
    controls: list[dict] = []
    for tag in re.findall(r'<select\b[^>]*>.*?</select>', block, re.S):
        name = _attr(tag, "name")
        if name:
            controls.append({"name": name, "kind": "select", "value": "",
                             "options": re.findall(r'<option value="([^"]*)"', tag)})
    for tag in re.findall(r'<input\b[^>]*>', block):
        name = _attr(tag, "name")
        # the hidden UNIT field states the render's unit; it is not a weather control the read fills.
        if name and name != presets_page.UNITS_FIELD:
            controls.append({"name": name, "kind": "input", "value": _attr(tag, "value"),
                             "options": []})
    return controls


def uattr(tag: str, name: str) -> str:
    """An attribute's value as the BROWSER's ``getAttribute`` returns it — the markup is escaped."""
    return html.unescape(_attr(tag, name))


def mission_line(html_text: str) -> str:
    """The rendered ``[data-mission-line]`` element, as its own tag plus body."""
    match = re.search(r'<p class="dlg-temp-read"[^>]*>\s*</p>', html_text, re.S)
    assert match, "the dialog rendered no mission-read line"
    return match.group(0)


#: the route's OWN answer for an English request: the same values, with each distance stated in feet
#: (the unit the dialog's boxes hold), so the harness is driven with what the browser really receives.
MISSION_VALUES = presets_page.display_values(
    presets_page.preset_values("", EXPECTED_DOCUMENT, {}), feet=True)


def harness_data(html: str, *, values: dict | None = None, unknown: dict | None = None) -> dict:
    """The harness's own input, built from the RENDERED dialog and the REAL route values."""
    block = weather_fieldset(html)
    line = mission_line(html)
    #: the launch's own "apply them to the original mission" box, as the dialog RENDERS it (a checkbox
    #: beside a hidden ``0`` fallback) — the read must follow the checkbox, never the hidden field.
    box = re.search(r'<input type="checkbox"[^>]*name="use_orig"[^>]*>', html)
    box_tag = box.group(0) if box else ""
    return {
        "custom": CUSTOM,
        "url": uattr(line, "data-mission-url"),
        "loading": uattr(line, "data-mission-loading"),
        "read": uattr(line, "data-mission-read"),
        "error": uattr(line, "data-mission-error"),
        "mission": uattr(line, "data-mission-name"),
        "controls": temp_controls(block),
        "values": MISSION_VALUES if values is None else values,
        "unknown_values": {**MISSION_VALUES, "clouds.preset": "Missing"} if unknown is None else unknown,
        "name": MISSION_NAME,
        "ok": True,
        "failure": FAILURE_SENTENCE,
        #: THE MISSION PICK, when the dialog draws one: the weather read follows it, so the harness
        #: drives a CHANGE on it. The Load dialog draws none — its mission rides the line instead.
        "mission_pick": bool(re.search(r'select[^>]*name="mission"', html)),
        "mission_pick_value": "Training.miz",
        "mission_pick_change": "Rotated.miz",
        "has_use_orig": bool(box_tag),
        "use_orig_checked": bool(re.search(r"\bchecked\b", box_tag)),
    }


def run_harness(tmp_path, data: dict) -> dict:
    data_file = tmp_path / "mission_data.json"
    data_file.write_text(json.dumps(data), encoding="utf-8")
    done = subprocess.run(["node", str(HARNESS), str(ASSET), str(data_file)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


async def open_startup(http):
    return await open_dialog(http, path=STARTUP, name=SERVER)


# ---------------------------------------------------------- 1. nothing on render, and the assets

@pytest.mark.asyncio
async def test_the_dialog_renders_the_line_empty_and_loads_the_read_asset(config_dir, clean_registry,
                                                                         tmp_path):
    """ON RENDER: the block carries the line EMPTY and the read's data, and the page loads the asset."""
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_startup(http)

    html = dialog.text
    block = weather_fieldset(html)
    line = mission_line(html)
    # the line is drawn EMPTY — nothing is prefilled at render
    assert re.search(r'<p class="dlg-temp-read"[^>]*>\s*</p>', line), line
    # ...and it carries the read's own route and the sentences the caller translated for this request
    assert _attr(line, "data-mission-url") == detail_page.mission_weather_path(SERVER)
    assert uattr(line, "data-mission-loading") == actions_page.PRESET_READ_LOADING
    assert uattr(line, "data-mission-read") == actions_page.PRESET_READ_DONE
    assert uattr(line, "data-mission-error") == actions_page.PRESET_READ_FAILED
    # STARTUP NAMES the mission its pick shows — the rotation's start mission — because the weather
    # edited here is the weather of the file this launch will load.
    assert uattr(line, "data-mission-name") == "Training.miz"
    # the rows start blank, whatever the mission would say — the fill is the script's, never the markup.
    # The ONE exception is a range that excludes 0: it states its own floor, so its first nudge cannot
    # jump there from a value its range does not contain.
    floors = {entry.key: entry.default for entry in weather_fields.FIELDS
              if entry.default is not None}
    for tag in re.findall(r'<input type="(?:number|text|date)"[^>]*\bname="[^"]+"[^>]*>', block):
        name = _attr(tag, "name")
        expected = "" if name not in floors else str(int(weather_fields.display_value(
            name, floors[name])))
        assert _attr(tag, "value") == expected, tag
    assert href_script(html) == "/static/dialog-mission-weather.js"


def href_script(html: str) -> str:
    """The read asset's own ``<script src>`` path, read from the page that loads it.

    The path is returned without the ``?v=<version>`` the page appends: the VERSION is what makes a
    browser fetch an updated script (see ``templating.asset_version``), and this pin is about WHICH
    asset the page loads.
    """
    match = re.search(r'<script src="(/static/dialog-mission-weather\.js)(?:\?v=[^"]*)?"', html)
    return match.group(1) if match else ""


def test_the_read_asset_never_submits_the_launch():
    """The fill is a READ: the shipped asset carries NO ``submit`` — the dialog's form POSTS the launch."""
    text = ASSET.read_text(encoding="utf-8")
    assert ".submit(" not in text and "requestSubmit" not in text, \
        "the mission read must never submit the launch form"
    assert "fetch(" in text, "the asset never fetches the mission's weather"
    assert "data-mission-url" in text and "data-mission-line" in text


#: the five attributes the read asset reads — all rendered on ONE element (the `[data-mission-line]`).
READ_ATTRIBUTES = ("data-mission-url", "data-mission-loading", "data-mission-read",
                   "data-mission-error", "data-mission-name")


def asset_read_selector() -> str:
    """The CSS selector the shipped asset reads its ``data-mission-*`` attributes off of.

    Derived from the ASSET's OWN source, never a re-spelling of it: the receiver of the
    ``getAttribute("data-mission-url")`` call is traced to the local it is assigned from, that local
    to the ``document_`` helper it calls, and the helper to the string its ``querySelector`` carries
    (resolving the module constant when the helper passes one by name).
    """
    text = ASSET.read_text(encoding="utf-8")
    receiver = re.search(r'(\w+)\.getAttribute\("data-mission-url"\)', text)
    assert receiver, "the asset never reads data-mission-url"
    symbol = receiver.group(1)
    assigned = re.search(r'\bvar\s+' + re.escape(symbol) + r'\s*=\s*(\w+)\(document_\)', text)
    assert assigned, f"the asset's {symbol} is not the result of a document_ helper"
    helper = assigned.group(1)
    query = re.search(r'function\s+' + re.escape(helper)
                      + r'\s*\(document_\)\s*\{\s*return\s+document_\.querySelector\('
                      + r'(\w+|"[^"]*"|\'[^\']*\')\)', text)
    assert query, f"{helper} does not return document_.querySelector(<selector>)"
    token = query.group(1)
    if token[0] in "\"'":
        return token[1:-1]
    constant = re.search(r'\bvar\s+' + re.escape(token) + r'\s*=\s*"([^"]*)"', text)
    assert constant, f"the constant {token} carries no string selector"
    return constant.group(1)


def rendered_elements(html_text: str) -> list[str]:
    """Every START tag the rendered page draws, verbatim (attributes and all)."""
    return re.findall(r'<[a-zA-Z][^>]*>', html_text)


def element_matches(tag: str, selector: str) -> bool:
    """Whether the asset's own SIMPLE selector — ``[attr]`` / ``.class`` / ``#id`` — matches *tag*.

    The asset reads through ONE ``querySelector`` call, and the selectors it uses are those simple
    forms, so this evaluates exactly those conditions against the tag's own rendered attributes.
    """
    attributes = dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))
    #: boolean attributes (``data-mission-line``) carry no ``="…"``, so presence is read from the
    #: NAMES rather than from the key/value pairs.
    names = set(re.findall(r'([a-zA-Z][\w:-]*)(?=[\s=/>])', tag))
    classes = attributes.get("class", "").split()
    conditions = re.findall(r'\[([\w-]+)\]|([.#])([\w-]+)', selector)
    assert conditions, f"not a simple attribute/class/id selector: {selector!r}"
    for attribute, kind, name in conditions:
        if attribute:
            if attribute not in names:
                return False
        elif kind == ".":
            if name not in classes:
                return False
        elif attributes.get("id") != name:
            return False
    return True


@pytest.mark.asyncio
async def test_the_asset_reads_its_attributes_off_the_element_the_template_renders_them_on(
        config_dir, clean_registry, tmp_path):
    """PIN — the element the asset READS from IS the element the template CARRIES the data on.

    Both sides are DERIVED, not declared. The asset's selector comes out of its own source (traced
    through the helper it queries with), and the element it resolves to is found in the RENDERED HTML
    the app produced; the element that actually carries the five ``data-mission-*`` attributes is
    found in that same HTML. They must be the SAME single element. A hand-built DOM cannot make this
    pass: the page is rendered by the app and the selector is read out of the shipped bytes, so a fake
    block that carries the attributes cannot silently "correct" a real page whose asset reads them
    off the wrong element — the failure mode that let the shipped defect through.
    """
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_startup(http)

    html = dialog.text
    carrying = [tag for tag in rendered_elements(html) if "data-mission-url=" in tag]
    assert len(carrying) == 1, "the read's data must be rendered on exactly one element"
    carrying = carrying[0]

    selector = asset_read_selector()
    matches = [tag for tag in rendered_elements(html) if element_matches(tag, selector)]

    assert matches == [carrying], (
        f"the asset reads its attributes off {selector!r}, which resolves to {matches!r}, but the "
        f"rendered template carries them on {carrying!r}")

    # ...and every one of the five that is rendered for THIS dialog lives on that ONE element
    for attribute in READ_ATTRIBUTES:
        if attribute == "data-mission-name":
            continue                      # the STARTUP dialog names no mission, by design
        assert f"{attribute}=" in carrying, carrying

    # the block the asset FILLS is a DIFFERENT element and carries none of the read's attributes
    blocks = [tag for tag in rendered_elements(html) if element_matches(tag, ".dlg-temp")]
    assert len(blocks) == 1, "the dialog renders exactly one temporary block"
    assert all(f"{attribute}=" not in blocks[0] for attribute in READ_ATTRIBUTES), blocks[0]


# ------------------------------------------- 2. the field-level probe of every state (the harness)

@requires_node
@pytest.mark.asyncio
async def test_the_shipped_asset_fills_each_control_and_says_what_it_read(config_dir, clean_registry,
                                                                         tmp_path):
    """ACCEPTANCE — every state at FIELD level, driven against the RENDERED controls and REAL values."""
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_startup(http)

    data = harness_data(dialog.text)
    report = run_harness(tmp_path, data)

    # 1. nothing is fetched on render, and every control is blank — bar a range whose own floor is
    # above zero, which states that floor so its slider cannot start on a value outside its range
    floors = {entry.key: str(int(weather_fields.display_value(entry.key, entry.default)))
              for entry in weather_fields.FIELDS if entry.default is not None}
    assert report["render"]["fetches"] == 0, "the dialog fetched before the entry was selected"
    assert report["render"]["line"] == ""
    for key, value in report["render"]["controls"].items():
        assert value == floors.get(key, ""), f"{key} rendered {value!r}"

    # 2. selecting the entry shows the loading line and fires ONE read at the route's own URL, which
    # states which FILE the launch will rewrite: the dialog's "apply them to the original mission"
    # box is ticked as rendered, so the read asks for the original.
    assert report["select"]["line"] == actions_page.PRESET_READ_LOADING
    assert report["select"]["fetches"] == 1
    assert report["select"]["url"] == (detail_page.mission_weather_path(SERVER)
                                       + "?mission=Training.miz&original=1")

    # 2b. CHOOSING ANOTHER MISSION re-reads: the launch will load THAT file, so the values already
    # filled belong to a mission it no longer names.
    assert report["mission"]["fetches"] == [
        detail_page.mission_weather_path(SERVER) + "?mission=Rotated.miz&original=1"], \
        report["mission"]["fetches"]

    # 3. the answer fills the controls and the line names the mission
    filled = report["answered"]["controls"]
    for key, value in MISSION_VALUES.items():
        if key == "fog.rows" or value == "":
            continue                       # no such control, or the mission carries no such value
        assert filled[key] == str(value), f"{key}: {filled[key]!r} != {value!r}"
    assert report["answered"]["line"] == actions_page.PRESET_READ_DONE.replace(
        "{mission}", MISSION_NAME)
    # the mission names a cloud preset the node's OWN list carries -> the select is set to it
    assert filled["clouds.preset"] == "Preset1"

    # 4. a picker name the list does NOT carry leaves the control as rendered
    assert report["unknown"]["controls"]["clouds.preset"] == "", \
        "a preset the node's list does not carry must never be claimed"

    # 5. deselecting clears every filled value and the line
    assert report["deselect"]["filled"]["temperature"] == str(MISSION_VALUES["temperature"]), \
        "the values really landed before the deselect"
    for key, value in report["deselect"]["controls"].items():
        assert value == floors.get(key, ""), f"{key} after the deselect: {value!r}"
    assert report["deselect"]["line"] == ""

    # 6. a failed read prefills NOTHING and carries the route's own sentence
    assert report["failure"]["loading"] == actions_page.PRESET_READ_LOADING
    assert report["failure"]["line"] == FAILURE_SENTENCE
    for key, value in report["failure"]["controls"].items():
        assert value == floors.get(key, ""), f"{key}: {value!r}"

    # 7. a change of mind mid-read is not fought: the late answer is discarded
    assert report["midfetch"]["line"] == ""
    for key, value in report["midfetch"]["controls"].items():
        assert value == floors.get(key, ""), f"{key}: {value!r}"

    # 8. the read never submits the launch — what fires is the control's change, nothing else
    assert report["submits"] == 0, "the read submitted the launch form"

    # 9. UNTICKING "apply them to the original mission" RE-READS the file the server is on: the values
    # already filled belong to the original, and the launch no longer rewrites it.
    assert report["original"]["fetches"] == [
        detail_page.mission_weather_path(SERVER) + "?mission=Training.miz&original=0"], \
        report["original"]
    assert report["original"]["line"] == actions_page.PRESET_READ_DONE.replace("{mission}", MISSION_NAME)


@requires_node
@pytest.mark.asyncio
async def test_the_shipped_asset_fills_the_fog_table_with_the_missions_keyframes(config_dir,
                                                                                 clean_registry,
                                                                                 tmp_path):
    """ACCEPTANCE — the mission's manual fog keyframes reach the dialog's OWN table cells.

    The dialog draws one blank row; the answer carries the keyframes as the single ``fog.rows`` value.
    The table is mounted in the order ``time, visibility, thickness`` while the value's own spelling is
    ``time:thickness:visibility``, so this also pins that each cell is written by its NAME and never by
    its position — and that leaving the mode drops the rows the fill added.
    """
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_startup(http)

    data = harness_data(dialog.text)
    data["fog_columns"] = ["time", "visibility", "thickness"]
    data["fog_rows"] = [{}]                     # the dialog renders ONE blank row
    data["values"] = {**MISSION_VALUES, "enable_dust": "fog", "fog.mode": "manual",
                      "fog.rows": "0:656:4921, 300:984:6562"}
    report = run_harness(tmp_path, data)

    assert report["answered"]["fog"] == [
        {"fog_row_0_time": "0", "fog_row_0_visibility": "4921", "fog_row_0_thickness": "656"},
        {"fog_row_1_time": "300", "fog_row_1_visibility": "6562", "fog_row_1_thickness": "984"},
    ], report["answered"]["fog"]

    # leaving the mode restores the ONE row the page rendered, blank
    assert report["deselect"]["fog"] == [
        {"fog_row_0_time": "", "fog_row_0_visibility": "", "fog_row_0_thickness": ""}]


@pytest.mark.asyncio
async def test_every_script_the_dialog_loads_states_the_asset_version(config_dir, clean_registry,
                                                                     tmp_path):
    """A cached script is a FIXED page that keeps running old behaviour — so the dialog's own assets
    carry a version that changes when the files do.

    The version is derived here from the shipped directory itself, never re-declared: a template
    version that drifts from the files would be exactly the staleness it exists to prevent. Only the
    assets THIS page ships are checked — the shell's shared scripts are loaded by every page and are
    not this dialog's to version.
    """
    _node, fleet = readable_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_startup(http)

    html = dialog.text
    newest = max(entry.stat().st_mtime_ns for entry in templating.SHELL_STATIC_DIR.iterdir()
                 if entry.is_file())
    for name in ("player-lookup.js", "weather-slider.js", "dialog-preset.js",
                 "dialog-mission-weather.js"):
        match = re.search(r'<script src="/static/' + re.escape(name) + r'\?v=([^"]*)"', html)
        assert match, f"the page loads {name} with no asset version"
        assert match.group(1) == str(newest), f"{name} states a stale version"


@requires_node
def test_the_gate_asset_clears_the_base_when_the_picker_goes_back_to_empty():
    """THE STALE-BASE LEFTOVER: clearing the cloud picker clears the base it pre-filled."""
    rows = [
        {"key": "clouds.preset", "cloud": "", "haze": "", "fogMode": "", "haloReal": False},
    ]
    import tempfile
    with tempfile.TemporaryDirectory() as scratch:
        rows_file = Path(scratch) / "rows.json"
        rows_file.write_text(json.dumps(rows), encoding="utf-8")
        done = subprocess.run(["node", str(GATE_HARNESS), str(GATE_ASSET), str(rows_file)],
                              capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    report = {entry["step"]: entry for entry in json.loads(done.stdout)}
    assert report["base-prefill"]["base"] == "2520", "the prefill is unchanged"
    assert report["base-cleared"]["base"] == "", \
        "clearing the picker must clear the base, never leave the preset's number behind"


# --------------------------------------------- 3. the Startup gate: no mission, no pick

@pytest.mark.asyncio
async def test_a_server_with_an_empty_rotation_offers_no_pick_and_says_why(config_dir,
                                                                           clean_registry):
    """ACCEPTANCE — the STARTUP dialog whose ROTATION holds no mission: no pick, and the sentence.

    The gate is the rotation, not the running mission: the pick chooses what the launch LOADS, and a
    server with an empty ``missionList`` would start without one — so there is nothing to apply a
    preset to and the dialog says so instead of drawing an empty dropdown.
    """
    server = MissionServer(SERVER, current="", status=Status.SHUTDOWN, managed_by=["Host A"])
    server.getMissionList = _empty_list
    server.getAllMissionFiles = _empty_files

    app = build(config_dir, fleet_for(server, PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        reply = await open_page_confirm(http)

    assert reply.status_code == 200, reply.text
    html = reply.text
    assert 'id="mission-dialog"' not in html, "no rotation, nothing to choose"
    assert "dlg-field dlg-pick" not in html, "the pick must not be offered with no mission"
    assert '<div class="dlg-temp">' not in html, "the temporary block must not be offered either"
    assert "rotation names no mission" in html, "the dialog does not say why"
    assert 'data-weather-note' in html
    # the launch form is still a real form: the other option boxes are there
    assert 'name="modify_mission"' in html


async def _empty_list():
    return []


async def _empty_files():
    return []


@pytest.mark.asyncio
async def test_a_server_with_a_rotation_but_no_running_mission_still_offers_the_pick(config_dir,
                                                                                    clean_registry):
    """The gate's POSITIVE control, and the rule that CHANGED: Startup used to withhold the pick unless
    the server named a CURRENT mission. What a launch loads is the rotation's start mission, so a
    shut-down server with a rotation gets the pick — the current mission has nothing to do with it."""
    app = build(config_dir, fleet_for(
        MissionServer(SERVER, current="", status=Status.SHUTDOWN, managed_by=["Host A"]),
        PresetNode(config_dir)))
    async with client(app) as http:
        await sign_in(http, "mod1")
        reply = await open_page_confirm(http)

    assert reply.status_code == 200, reply.text
    assert "dlg-field dlg-pick" in reply.text
    assert 'id="mission-dialog"' in reply.text
    assert actions_page.PRESET_NO_MISSION not in reply.text


async def open_page_confirm(http, name=SERVER):
    """Open the Startup DIALOG route (an options form: no confirm token) and return its response."""
    page = await open_page(http)
    return await http.post(STARTUP + CONFIRM, data={"server": name, "_csrf_token": token_of(page)})


# ------------------------------------------------- 4. the Load dialog always keeps its pick

@pytest.mark.asyncio
async def test_the_load_dialog_names_the_row_it_reads_from(config_dir, clean_registry):
    """LOAD always has its picked row, so its pick stays and the read NAMES that mission."""
    from test_webui_missions_load_form import load_fleet, open_load_dialog

    app = build(config_dir, load_fleet(), plugins=("mission",))
    async with client(app) as http:
        await sign_in(http, "specialk")
        dialog = await open_load_dialog(http)

    html = dialog.text
    assert "dlg-field dlg-pick" in html, "the Load pick must stay"
    block = weather_fieldset(html)
    assert block, "the Load dialog lost its temporary block"
    line = mission_line(html)
    assert _attr(line, "data-mission-name") == "Rotated.miz", line
    assert uattr(line, "data-mission-url") == detail_page.mission_weather_path(SERVER)


# --------------------------------------------------------- 5. the catalogs of the new sentences

def test_every_new_sentence_is_in_every_catalog_and_translated():
    """The four new sentences are extracted, present in the four translated catalogs, and translated.

    English is the source language and every English string its own msgid, so it ships no catalog: its
    side is asserted through the LOOKUP, which must hand back the msgid itself. The four translated
    catalogs must serve a string that DIFFERS from the msgid with the SAME placeholders, out of the
    COMPILED ``.mo``.
    """
    from babel.messages.pofile import read_po
    english = gettext.translation("webui", localedir=str(LOCALE), languages=["en"], fallback=True)
    for msgid in NEW_SENTENCES:
        served = english.gettext(msgid)
        assert served == msgid, (
            f"English serves {served!r} for {msgid!r} — English is the source language, so the "
            "lookup must hand back the msgid")
    counts = {"translated": 0, "untranslated": 0, "mismatch": 0}
    for language in TRANSLATED:
        po = LOCALE / language / "LC_MESSAGES" / "webui.po"
        mo = LOCALE / language / "LC_MESSAGES" / "webui.mo"
        # a checkout materialises both files in arbitrary sub-second order: only a genuinely
        # stale .mo (edited .po, never recompiled) is the bug this guards
        assert mo.stat().st_mtime_ns + 2_000_000_000 >= po.stat().st_mtime_ns, \
            f"{language}: the .mo is older than its .po — the render would serve English"
        with po.open("rb") as handle:
            catalog = read_po(io.BytesIO(handle.read().replace(b"\r\n", b"\n")))
        ids = {message.id for message in catalog if isinstance(message.id, str)}
        translations = gettext.translation("webui", localedir=str(LOCALE), languages=[language],
                                           fallback=True)
        for msgid in NEW_SENTENCES:
            assert msgid in ids, f"{language}: the catalog is missing {msgid!r}"
            served = translations.gettext(msgid)
            if served == msgid:
                counts["untranslated"] += 1
                raise AssertionError(f"{language}: {msgid!r} renders the English msgid")
            counts["translated"] += 1
            if set(re.findall(r"\{(\w+)\}", served)) != set(re.findall(r"\{(\w+)\}", msgid)):
                counts["mismatch"] += 1
                raise AssertionError(f"{language}: {msgid!r} changes its placeholders")
    assert counts == {"translated": len(NEW_SENTENCES) * len(TRANSLATED),
                      "untranslated": 0, "mismatch": 0}, counts
