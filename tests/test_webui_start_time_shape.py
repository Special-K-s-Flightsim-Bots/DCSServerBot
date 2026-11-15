"""The start time reads as ``HH:MM`` and the document still writes the mission's seconds.

ONE clock, TWO shapes: the mission file (and ``/mission save preset``) states the clock as SECONDS
SINCE MIDNIGHT (``28800``), every control and every panel states it ``HH:MM`` (``08:00``). This module
pins both directions and the fact that no panel shows a bare count:

* the conversion homes — ``presets.start_time_display`` (stored value -> what a control carries) and
  ``presets.start_time_value`` (control text -> what a document keeps), each the ONE direction of the
  round trip, with a named moment and a count no clock can spell passing through unchanged;
* the RENDERED wizard — a preset stored as ``start_time: 28800`` shows ``08:00`` in the field, in the
  time helper beside it, in the sky panel and in the review's value rows;
* the DIALOG — the mission-weather read answers ``08:00``, the values it answers POST back to a
  document carrying ``28800`` (byte-identical to ``/mission save preset``'s), and an edited ``09:30``
  produces ``34200``;
* the shipped fill asset — the prefilled control's displayed text, the time helper the page's own
  asset mirrors it into, and the value the form posts;
* the combination editor's effective weather, which reads member PRESET documents (stored values),
  so it is a second entrance for a raw count.

Driven through the REAL application and the REAL fixture mission, and the shipped asset browser-less
over the RENDERED controls — the same harnesses the sibling dialog suites use.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from core import Status
from services.webservice import weather_fields
from services.webservice.pages import actions as actions_page
from services.webservice.pages import presets as presets_page
from services.webservice.pages import server_detail as detail_page
from core.utils import configwrite
# the fixture mission, its document and its archive builder
from test_mission_weather_read import MizFile, build_miz
from test_webui_mission_weather import miz_service  # noqa: F401 - fixture imported on purpose
from test_webui_presets_clouds import CloudsNode, install_clouds
from test_webui_presets_halo import install_halo
from test_webui_startup_mission_prefill import (
    MissionServer, mission_line, temp_controls, uattr,
)
from test_webui_startup_temporary_preset import posted, weather_fieldset
from test_webui_startup_weather_picker import open_dialog
from test_webui_write_pause import (  # noqa: F401 - fixtures imported on purpose
    Cluster, build, clean_registry, client, config_dir, fleet, instance, sign_in,
)

SERVER = "SRS-1"
CUSTOM = actions_page.PRESET_CUSTOM_VALUE
MISSION_NAME = "Training.miz"
STARTUP = "/actions/server/startup"

TESTS = Path(__file__).resolve().parent
ASSET = TESTS.parent / "services" / "webservice" / "static" / "dialog-mission-weather.js"
HARNESS = TESTS / "dialog_mission_harness.js"

#: the mission's own clock, and the two shapes it is written in.
SECONDS = 28800
HHMM = "08:00"

#: a preset file as ``/mission save preset`` writes one: the clock in seconds.
SECONDS_PRESET = """\
Dawn Patrol:
  date: '2024-06-01'
  start_time: 28800
  temperature: 15
  qnh: 760
  clouds:
    preset: Preset1
"""


def requires_node(func):
    return pytest.mark.skipif(shutil.which("node") is None,
                              reason="node is not installed: no DOM to drive the shipped asset with")(func)


#: the page's OTHER shipped control asset — the one that keeps the time box in step with its field.
SLIDER_ASSET = TESTS.parent / "services" / "webservice" / "static" / "weather-slider.js"


def run_harness(tmp_path, data: dict, *, slider: bool = False) -> dict:
    """Drive the shipped assets over a minimal DOM; *slider* loads the time-box/pair asset too."""
    data_file = tmp_path / "mission_data.json"
    data_file.write_text(json.dumps(data), encoding="utf-8")
    command = ["node", str(HARNESS), str(ASSET), str(data_file)]
    if slider:
        command.append(str(SLIDER_ASSET))
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def mission_fleet(tmp_path, config_dir):
    """ONE server whose node carries a DCS installation AND the fixture mission archive."""
    (config_dir / "presets.yaml").write_text("Summer:\n  clouds:\n    base: 1000\n", encoding="utf-8")
    install_clouds(tmp_path)
    install_halo(tmp_path)
    node = InstalledCloudsNode(installation=tmp_path)
    mission = build_miz(tmp_path / MISSION_NAME)
    server = ReadableMissionServer(SERVER, current=mission, status=Status.SHUTDOWN,
                                   managed_by=["Host A"])
    server.node = node
    node_a = types.SimpleNamespace(name=node.name, master=True,
                                   instances={"Host-A-Instance": instance("Host-A-Instance", server)})
    return server, Cluster([server], nodes={node.name: node_a}, node=node_a,
                           instances=[instance("Host-A-Instance", server)])


class ReadableMissionServer(MissionServer):
    """The dialog's server double, plus the two reads the mission-weather action makes."""

    async def getMissionList(self):
        return [self._current] if self._current else []

    async def get_missions_dir(self):
        return str(Path(self._current).parent) if self._current else ""


class InstalledCloudsNode(CloudsNode):
    """A node double that also reports the DCS build, which the mission's fog keys are read by."""

    def __init__(self, name="dcs-a-01", *, installation, dcs_version="2.9.14"):
        super().__init__(name, installation=installation)
        self.dcs_version = dcs_version


def save_preset_document(tmp_path) -> dict:
    """The document ``/mission save preset`` writes for the fixture mission — the ONE mapping home."""
    miz = MizFile(build_miz(tmp_path / "SavePreset.miz"))
    miz.node = types.SimpleNamespace(dcs_version="2.9.14", locals={"DCS": {"installation": ""}})
    return miz.preset_document()


def yaml_entry(document: dict, key: str = "start_time") -> str:
    """*key* of *document* dumped by the SAME writer the save uses — the bytes, not the object."""
    return configwrite.dump_config({key: document[key]})


async def route_values(http, name=SERVER) -> dict:
    """The mission-weather read's own answer, as the dialog's fill receives it."""
    response = await http.get(detail_page.mission_weather_path(name))
    assert response.status_code == 200, response.text
    return response.json()["values"]


def posted_values(values: dict) -> dict:
    """The dialog's POST for the fill it just received: every control posts its own value back."""
    fields = {key: str(value) for key, value in values.items()
              if key != weather_fields.FOG_ROWS_KEY and str(value) != ""}
    fields[actions_page.PRESET_FIELD] = CUSTOM
    return fields


# --------------------------------------------------- 1. the two conversion homes, both directions

def test_a_stored_second_count_reads_as_a_clock():
    """The stored shape -> the shape a control carries: the clock, not the count."""
    assert presets_page.start_time_display(SECONDS) == HHMM
    assert presets_page.start_time_display(str(SECONDS)) == HHMM
    assert presets_page.start_time_display(34200) == "09:30"
    assert presets_page.start_time_display(0) == "00:00"
    assert presets_page.start_time_display("") == ""
    assert presets_page.start_time_display(None) == ""


def test_a_control_text_becomes_the_missions_own_seconds():
    """The shape a control carries -> the document's: the count ``/mission save preset`` writes."""
    assert presets_page.start_time_value(HHMM) == SECONDS
    assert presets_page.start_time_value("09:30") == 34200
    assert presets_page.start_time_value("00:00") == 0
    assert presets_page.start_time_value("9:30") == 34200, "a one-digit hour is the same clock"


@pytest.mark.parametrize("value", ["morning", "dusk +00:30", "dawn -01:15", "now"])
def test_a_named_moment_is_text_in_both_directions(value):
    """A named moment is not a clock: neither direction rewrites it."""
    assert presets_page.start_time_value(value) == value
    assert presets_page.start_time_display(value) == value


@pytest.mark.parametrize("value", ["25:00", "08:60", "90000", "28800.5", "not a time"])
def test_a_value_no_clock_can_spell_is_never_turned_into_a_number(value):
    """A count outside one day and a clock that does not exist pass through as the text they are."""
    assert presets_page.start_time_value(value) == value
    assert presets_page.start_time_display(value) == value


def test_the_round_trip_is_stable():
    """stored -> control -> stored gives back the same count: no drift on a loaded preset."""
    for seconds in (0, 900, 28800, 34200, 86340):
        text = presets_page.start_time_display(seconds)
        assert presets_page.start_time_value(text) == seconds, text


# ------------------------------------------------- 2. the wizard renders the clock, not the count

@pytest.mark.asyncio
async def test_the_wizard_field_and_its_time_helper_carry_the_clock(config_dir, clean_registry):
    """A preset stored in seconds opens with ``08:00`` in the field AND in the helper beside it."""
    (config_dir / "presets.yaml").write_text(SECONDS_PRESET, encoding="utf-8")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        response = await http.get(f"{presets_page.wizard_url('Dawn Patrol')}?step=time")
        assert response.status_code == 200, response.text
        html = response.text

    box = re.search(r'<input[^>]*type="text"[^>]*name="start_time"[^>]*>', html)
    assert box is not None, "the start time's text box"
    assert f'value="{HHMM}"' in box.group(0), box.group(0)
    helper = re.search(r'<input class="pw-time"[^>]*>', html)
    assert helper is not None, "the time helper"
    assert f'value="{HHMM}"' in helper.group(0), helper.group(0)


@pytest.mark.asyncio
async def test_the_sky_panel_and_the_review_rows_read_the_clock(config_dir, clean_registry):
    """The two PANELS that print the value read the clock too: the sky panel and the review's rows."""
    (config_dir / "presets.yaml").write_text(SECONDS_PRESET, encoding="utf-8")
    app = build(config_dir, fleet())
    async with client(app) as http:
        await sign_in(http, "specialk")
        step = (await http.get(f"{presets_page.wizard_url('Dawn Patrol')}?step=time")).text
        review = (await http.get(f"{presets_page.wizard_url('Dawn Patrol')}?step=review")).text

    assert f"<dt>start time</dt><dd>{HHMM}</dd>" in step, "the sky panel prints the count"
    assert f"<dt>start_time</dt><dd>{HHMM}</dd>" in review, "the review's value rows print the count"
    # the review's YAML panel is the DOCUMENT itself (pinned in section 3), never a formatted value
    assert f"start_time: {SECONDS}" in review, "the produced document keeps the mission's seconds"


# ------------------------------------------------- 3. the document: the mission's own shape

@pytest.mark.asyncio
async def test_the_prefilled_dialog_posts_the_missions_own_seconds(config_dir, clean_registry,
                                                                  tmp_path):
    """ACCEPTANCE — the read answers ``08:00``, and what the untouched prefill POSTS builds ``28800``."""
    server, fleet = mission_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        values = await route_values(http)
        assert values["start_time"] == HHMM, "the read answers the shape the control carries"
        reply = await posted(http, dialog.text, **posted_values(values))

    assert reply.status_code == 303, reply.text
    (call,) = server.call_args
    document = call["preset"]
    assert document["start_time"] == SECONDS, document["start_time"]
    # ...BYTE-IDENTICAL to what ``/mission save preset`` writes for this mission
    assert yaml_entry(document) == yaml_entry(save_preset_document(tmp_path))
    assert yaml_entry(document) == f"start_time: {SECONDS}\n"


@pytest.mark.asyncio
async def test_an_edited_clock_posts_its_seconds(config_dir, clean_registry, tmp_path):
    """ACCEPTANCE — an operator editing the field to ``09:30`` writes ``34200``."""
    server, fleet = mission_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        values = dict(await route_values(http))
        values["start_time"] = "09:30"
        reply = await posted(http, dialog.text, **posted_values(values))

    assert reply.status_code == 303, reply.text
    (call,) = server.call_args
    assert call["preset"]["start_time"] == 34200, call["preset"]["start_time"]


@pytest.mark.asyncio
async def test_a_named_moment_survives_the_dialog(config_dir, clean_registry, tmp_path):
    """The other legal shape still reaches the document as TEXT — the builder resolves it later."""
    server, fleet = mission_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        values = dict(await route_values(http))
        values["start_time"] = "morning +00:30"
        reply = await posted(http, dialog.text, **posted_values(values))

    assert reply.status_code == 303, reply.text
    (call,) = server.call_args
    assert call["preset"]["start_time"] == "morning +00:30"


def test_the_combination_panel_reads_its_members_clock():
    """The combination editor merges member PRESET documents, so its effective weather holds the
    STORED value — the panel must print the clock, not the count."""
    document = {"Dawn Patrol": {"start_time": SECONDS, "temperature": 15},
                "Dusk Patrol": {"start_time": 66600}}
    context = presets_page.combination_editor(
        presets_page.wizard_base("Combo"), "review", {}, document, ["Dawn Patrol", "Dusk Patrol"],
        exclude="")
    rows = {row["key"]: row["value"] for row in context["effective_rows"]}
    assert rows["start_time"] == "18:30", rows
    assert rows["temperature"] == "15", "every other key is untouched"


def test_the_sky_values_of_a_combination_read_the_clock():
    """The sky panel of a combination reads the same merged document."""
    values = presets_page._effective_sky_values({"start_time": SECONDS})
    assert values["start_time"] == HHMM


# ------------------------------------- 4. the shipped fill asset, over the RENDERED controls

@requires_node
@pytest.mark.asyncio
async def test_the_shipped_asset_fills_a_clock_and_the_form_posts_seconds(config_dir,
                                                                        clean_registry, tmp_path):
    """ACCEPTANCE (FIELD LEVEL) — the prefilled control's displayed text, the helper beside it and the
    value the form posts, driven against the RENDERED dialog and the REAL route answer."""
    server, fleet = mission_fleet(tmp_path, config_dir)
    app = build(config_dir, fleet)
    async with client(app) as http:
        await sign_in(http, "mod1")
        dialog = await open_dialog(http)
        values = await route_values(http)

    html = dialog.text
    report = run_harness(tmp_path, {
        "custom": CUSTOM,
        "url": uattr(mission_line(html), "data-mission-url"),
        "loading": uattr(mission_line(html), "data-mission-loading"),
        "read": uattr(mission_line(html), "data-mission-read"),
        "error": uattr(mission_line(html), "data-mission-error"),
        "mission": "Training.miz",
        "controls": temp_controls(weather_fieldset(html)),
        "values": values,
        "unknown_values": values,
        "name": MISSION_NAME,
        "ok": True,
        "failure": "",
        "helper": "start_time",
    }, slider=True)

    filled = report["answered"]
    assert filled["controls"]["start_time"] == HHMM, "the control's displayed text"
    assert filled["helper"] == HHMM, "the time box the page's own asset keeps in step"
    # what the FORM would post for that fill, turned into the document by the wizard's own builder
    document = actions_page.temporary_document(_Form(posted_values(filled["controls"])))
    assert document["start_time"] == SECONDS, document["start_time"]


class _Form:
    """The minimum of a Starlette ``FormData`` the document builder reads."""

    def __init__(self, data):
        self.data = {key: str(value) for key, value in data.items()}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def getlist(self, key):
        return [self.data[key]] if key in self.data else []

    def items(self):
        return self.data.items()
