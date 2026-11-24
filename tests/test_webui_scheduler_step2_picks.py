"""Step 2's CLOSED picks: the mission id, the mission file and the preset multi-pick.

A pick is closed when the bot can enumerate the choices, so the step draws a multiple ``<select>`` and
no free-text box beside it; the text box returns only when the read answered nothing, and then it
carries one sentence saying why. Three sections:

1. THE OPTION ROWS — what each pick offers: the mission's ``index`` as the value and its ``name`` as
   the label, the missions-directory-relative ``path`` for the file pick, and the preset pick's
   ``preset_option_value`` entries in one ``<optgroup>`` per preset FILE;
2. THE READER — a repeated field name accumulates into ONE list (a single post stays a scalar), a
   file-qualified pick entry is never comma-split, and ``presets`` is not touched by a pick;
3. THE PAGE — the rendered controls, both failure paths, and the round trip through the GET form.

The two reads (``get_mission_list`` and the node's preset files) are stubbed at the seam the PAGE
calls them through; the route, the template and the plan reader are the shipped ones.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import types
from pathlib import Path
from urllib.parse import urlencode

import pytest

from services.webservice import scheduler_plan
from services.webservice.pages import server_detail as detail_page
from services.webservice.pages import settings as settings_page
from test_webui_scheduler_save import (  # noqa: F401 - fixtures and helpers imported on purpose
    ADMIN, INSTANCE_A, INSTANCE_A_BLOCK, app_for, csrf_of, form_pairs, node_data, read_block,
    revision_of, scope_dir,
)
from test_webui_scheduler_tab import (  # noqa: F401 - fixtures and helpers imported on purpose
    SERVER_A, WIZARD_PLAN, actions_body, build, clean_registry, client, cluster, config_dir,
    hidden_inputs, install_plugin, scheduler_file, sign_in, step_page, tab_body,
)

#: the DCS mission list one read answers with: the indexes are 1-based and are what a ``mission_id``
#: wants. It is the CONFIGURED list, so a mission file the server does not list is absent here.
MISSIONS = (
    {"index": 1, "name": "Caucasus Free Flight", "path": "Missions/FreeFlight.miz"},
    {"index": 2, "name": "Night Ops", "path": "Missions/NightOps.miz"},
    {"index": 3, "name": "Training Day", "path": "Missions/sub/Training.miz"},
)

#: the missions-directory SCAN one read answers with: every ``.miz``/``.sav``, recursive. It carries a
#: file the DCS list does not — which is the point of picking by file.
SCANNED = (
    {"name": "Missions/FreeFlight", "path": "Missions/FreeFlight.miz", "installed": True},
    {"name": "Missions/NightOps", "path": "Missions/NightOps.miz", "installed": True},
    {"name": "Missions/sub/Training", "path": "Missions/sub/Training.miz", "installed": True},
    {"name": "Missions/New/Unlisted", "path": "Missions/New/Unlisted.miz", "installed": False},
)

#: the preset files one node read answers with: ``[(file label, path, names)]``, in the read's order.
PRESETS = (
    ("config/presets.yaml", "/opt/dcs/config/presets.yaml", ["NoMods", "Clear Skies"]),
    ("config/presets_winter.yaml", "/opt/dcs/config/presets_winter.yaml", ["Snow"]),
)


@pytest.fixture
def reads(monkeypatch):
    """The three reads step 2's picks come from, stubbed at the page's own seams.

    ``state`` holds what each one answers; ``failed`` makes one answer nothing at all — the state
    the failure pins drive. Each read is independent, so one failure never blanks another pick. The
    route, the template and the reader are the shipped ones, so what a pin reads here is what a
    browser receives.
    """
    state = {"missions": list(MISSIONS), "files": list(SCANNED), "presets": list(PRESETS),
             "failed": set()}

    async def mission_list(request, server_name, *, ctx=None):
        if "missions" in state["failed"]:
            raise RuntimeError("the mission action is not reachable")
        return types.SimpleNamespace(success=True, data={"missions": state["missions"]})

    async def mission_scan(request, server_name):
        if "files" in state["failed"]:
            raise RuntimeError("the missions directory did not answer")
        return types.SimpleNamespace(success=True, missions=state["files"])

    async def node_preset_files(node):
        if "presets" in state["failed"]:
            raise RuntimeError("the node did not answer")
        return state["presets"], bool(state["presets"])

    monkeypatch.setattr(detail_page.actions_page, "mission_list", mission_list)
    monkeypatch.setattr(detail_page, "mission_scan", mission_scan)
    monkeypatch.setattr(detail_page.presets_page, "node_preset_files", node_preset_files)
    return state


def pick_view(plan: dict, state: dict, step: str = "2") -> dict:
    """The wizard's own view of *plan* at *step*, with the stub reads handed in."""
    return scheduler_plan.wizard_view(plan, [], step=step, missions=list(state["missions"]),
                                      mission_files=list(state["files"]),
                                      presets=list(state["presets"]))


def select_of(body: str, name: str) -> str:
    """ONE rendered ``<select>`` by its field name — the control a pin reads, never the whole page."""
    match = re.search(r'<select[^>]*name="' + re.escape(name) + r'"[^>]*>.*?</select>', body, re.S)
    assert match, f"no select for {name}"
    return match.group(0)


def option_rows(select: str) -> list[tuple[str, str, bool]]:
    """Every option of a select as ``(value, label, selected)``, in the page's order."""
    rows: list[tuple[str, str, bool]] = []
    for value, rest, label in re.findall(r'<option value="([^"]*)"([^>]*)>([^<]*)</option>', select):
        rows.append((value, label, bool(re.search(r"\bselected\b", rest))))
    return rows


# ------------------------------------------------------------------- 1. the option rows

def test_the_mission_id_pick_offers_the_reads_indexes_labelled_with_its_names():
    """The value is the 1-based ``index`` a ``mission_id`` wants, the label the mission's name."""
    pick = scheduler_plan.mission_id_pick(list(MISSIONS), 2)

    assert pick["offered"] is True and pick["reason"] == ""
    assert [(row["value"], row["label"]) for row in pick["options"]] == [
        ("2", "Night Ops"),
        ("1", "Caucasus Free Flight"),
        ("3", "Training Day"),
    ], "the plan's id stands first, the rest of the read in its own order"
    assert [row["selected"] for row in pick["options"]] == [True, False, False]


def test_the_mission_file_pick_offers_relative_paths_and_never_an_absolute_one():
    """The option's value AND label are the scanned missions-directory-relative ``path``."""
    pick = scheduler_plan.mission_file_pick(list(SCANNED), "Missions/sub/Training.miz")
    values = [row["value"] for row in pick["options"]]

    assert values[0] == "Missions/sub/Training.miz"
    assert values == ["Missions/sub/Training.miz", "Missions/FreeFlight.miz",
                      "Missions/NightOps.miz", "Missions/New/Unlisted.miz"], \
        "the plan's pick stands first, the scan's own order follows"
    assert [row["label"] for row in pick["options"]] == values, "the path is both value and label"
    for value in values:
        assert not value.startswith("/"), value
        assert not value.startswith("\\\\"), value
        assert ":" not in value, value
    assert pick["options"][0]["selected"] is True


def test_the_two_mission_picks_read_different_sources():
    """The id pick comes from the DCS list, the file pick from the directory scan — never one list."""
    picks = scheduler_plan.startup_picks({"mission_id": 2}, list(MISSIONS), list(SCANNED))

    assert [row["value"] for row in picks["mission_id"]["options"]] == ["2", "1", "3"]
    assert "Missions/New/Unlisted.miz" not in [row["value"]
                                               for row in picks["mission_id"]["options"]]
    file_values = [row["value"] for row in picks["mission_file"]["options"]]
    assert "Missions/New/Unlisted.miz" in file_values, \
        "a file the DCS list does not carry is still offered by the scan"
    assert "2" not in file_values, "the id is not a file path"


def test_a_stated_value_the_read_does_not_carry_keeps_its_own_option():
    """Opening the step never silently drops an id the plan still states."""
    pick = scheduler_plan.mission_id_pick(list(MISSIONS), [9, 1])

    assert pick["options"][0] == {"value": "9", "label": "9", "selected": True}
    assert [row["value"] for row in pick["options"][1:]] == ["1", "2", "3"]


def test_the_preset_pick_posts_preset_option_values_grouped_by_file():
    """One ``<optgroup>`` per file, each option's value the file label and the name it resolves by."""
    from core.utils.presets import preset_option_value

    pick = scheduler_plan.preset_pick(list(PRESETS), ["config/presets.yaml\nClear Skies"])
    groups = pick["groups"]

    assert pick["offered"] is True
    assert [group["label"] for group in groups] == ["config/presets.yaml",
                                                    "config/presets_winter.yaml"]
    for label, options in (("config/presets.yaml", groups[0]["options"]),
                           ("config/presets_winter.yaml", groups[1]["options"])):
        for option in options:
            assert option["value"] == preset_option_value(label, option["label"])
            assert "\n" in option["value"], "the entry carries its file, a newline and the name"
    assert [(row["label"], row["selected"]) for row in groups[0]["options"]] == [
        ("NoMods", False), ("Clear Skies", True)]


def test_a_preset_the_files_no_longer_carry_is_kept_in_its_own_group():
    """A bare name, or one whose file is gone, is stated — never dropped by opening the step."""
    pick = scheduler_plan.preset_pick(list(PRESETS), ["NoMods", "config/gone.yaml\nOld"])
    gone = pick["groups"][-1]

    assert gone["label"] == scheduler_plan.PICK_NOT_IN_FILES
    assert gone["options"] == [{"value": "NoMods", "label": "NoMods", "selected": True},
                               {"value": "config/gone.yaml\nOld",
                                "label": "config/gone.yaml\nOld", "selected": True}]


def test_a_pick_with_no_read_behind_it_is_not_offered():
    """A step that made no read claims no closed list — it states why it has none instead."""
    picks = scheduler_plan.startup_picks({"mission_id": 2, "settings": ["NoMods"]})

    assert picks["mission_id"]["offered"] is False
    assert picks["mission_id"]["reason"] == scheduler_plan.MISSION_LIST_UNAVAILABLE
    assert picks["presets"]["offered"] is False
    assert picks["presets"]["reason"] == scheduler_plan.PRESET_LIST_UNAVAILABLE
    assert picks["mission_id"]["options"] == [] and picks["presets"]["groups"] == []


# ------------------------------------------------------------------------- 2. the reader

def test_a_repeated_field_name_accumulates_into_one_list():
    """A multiple select posts its name once per entry; the last one must not win."""
    first = preset_entry("config/presets.yaml", "NoMods")
    second = preset_entry("config/presets_winter.yaml", "Snow")
    plan = scheduler_plan.plan_from_form([("startup.settings", first), ("startup.settings", second)])

    assert plan["startup"]["settings"] == [first, second]


def test_a_single_post_stays_a_single_value():
    """One occurrence is not wrapped by the accumulation — the leaf's own schema type decides."""
    once = scheduler_plan.plan_from_form([("startup.presets", "config/presets.yaml")])

    assert once["startup"]["presets"] == "config/presets.yaml", "str_or_list: one value is a scalar"
    twice = scheduler_plan.plan_from_form([("startup.presets", "config/presets.yaml"),
                                           ("startup.presets", "config/presets_winter.yaml")])
    assert twice["startup"]["presets"] == ["config/presets.yaml", "config/presets_winter.yaml"]
    # a leaf the schema types as a SEQUENCE answers a one-element list for one pick, never a nested one
    settings = scheduler_plan.plan_from_form([("startup.settings", preset_entry("a.yaml", "X"))])
    assert settings["startup"]["settings"] == [preset_entry("a.yaml", "X")]


def test_a_pick_entry_is_never_comma_split():
    """A preset name is operator data and may carry a comma: splitting it drops the whole pick."""
    entry = preset_entry("config/presets.yaml", "Fog, Heavy")
    plan = scheduler_plan.plan_from_form([("startup.settings", entry)])

    assert plan["startup"]["settings"] == [entry]


def test_a_pick_leaves_presets_exactly_as_the_file_states_it():
    """The picker writes ``settings``; the configured file path a bare name falls back to is untouched."""
    kept = [preset_entry("config/presets.yaml", "NoMods"),
            preset_entry("config/presets.yaml", "Clear Skies")]
    plan = {"startup": {"presets": ["config/presets.yaml", "config/presets_winter.yaml"],
                        "settings": list(kept)}}
    posted = [(field["name"], field["value"])
              for field in scheduler_plan.form_fields(plan)
              + scheduler_plan.shape_markers(plan)]
    posted.append(("startup.settings", preset_entry("config/presets_winter.yaml", "Snow")))
    back = scheduler_plan.plan_from_form(posted)

    assert back["startup"]["presets"] == ["config/presets.yaml", "config/presets_winter.yaml"]
    assert back["startup"]["settings"] == kept + [
        preset_entry("config/presets_winter.yaml", "Snow")], \
        "every picked entry travels as its own field"


def test_an_emptied_pick_removes_the_settings_key_the_block_states():
    """Unticking every preset states nothing, so the save must PRUNE the key — not keep it in force."""
    plan = scheduler_plan.plan_from_form([("startup.presets", "config/presets.yaml")])
    override = scheduler_plan.minimal_override({}, {"startup": {"settings": ["NoMods"]}}, plan)

    assert override["prune"]["startup"] == ["settings"]
    assert override["set"]["startup"] == {"presets": "config/presets.yaml"}


def preset_entry(label: str, name: str) -> str:
    """One pick entry as the control posts it — the console's own rule, never a second spelling."""
    from core.utils.presets import preset_option_value

    return preset_option_value(label, name)


# ----------------------------------------------------------------------------- 3. the page

@pytest.mark.asyncio
async def test_step_2_renders_the_three_picks_as_multiple_selects(config_dir, clean_registry,
                                                                  monkeypatch, reads):
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 2, "mission_file":
                                                          "Missions/NightOps.miz"}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    ids = select_of(body, "startup.mission_id")
    files = select_of(body, "startup.mission_file")
    settings = select_of(body, "startup.settings")
    for select, name in ((ids, "startup.mission_id"), (files, "startup.mission_file"),
                         (settings, "startup.settings")):
        assert " multiple" in select, f"{name} must accept several entries"
        assert 'type="text"' not in select
    assert 'id="sw-mission-id"' in ids and 'id="sw-mission-file"' in files
    assert option_rows(ids)[0] == ("2", "Night Ops", True)
    assert ("1", "Caucasus Free Flight", False) in option_rows(ids)
    assert option_rows(files)[0] == ("Missions/NightOps.miz", "Missions/NightOps.miz", True)
    assert ("Missions/New/Unlisted.miz", "Missions/New/Unlisted.miz", False) in option_rows(files), \
        "a file the DCS list does not carry is offered by the file pick"
    assert "Missions/New/Unlisted.miz" not in [value for value, _label, _selected in option_rows(ids)], \
        "and it is not offered by the id pick"
    # the preset pick: one optgroup per file, its options' values the pick's own entries
    groups = re.findall(r'<optgroup label="([^"]*)">(.*?)</optgroup>', settings, re.S)
    assert [label for label, _inner in groups] == ["config/presets.yaml",
                                                   "config/presets_winter.yaml"]
    assert option_rows(groups[0][1]) == [("config/presets.yaml\nNoMods", "NoMods", False),
                                         ("config/presets.yaml\nClear Skies", "Clear Skies", False)]
    # a typed value never competes with the list: no text box beside any of the three
    for name in ("startup.mission_id", "startup.mission_file", "startup.settings"):
        assert f'type="text" name="{name}"' not in body, name


@pytest.mark.asyncio
async def test_a_plan_with_several_mission_ids_renders_them_all_selected(config_dir, clean_registry,
                                                                        monkeypatch, reads):
    """Several ids, in the plan's OWN order — not in the read's."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": [3, 1]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    rows = option_rows(select_of(body, "startup.mission_id"))
    assert [value for value, _label, selected in rows if selected] == ["3", "1"]
    assert [label for _value, label, selected in rows if selected] == ["Training Day",
                                                                      "Caucasus Free Flight"]


@pytest.mark.asyncio
async def test_the_pick_round_trips_through_the_get_form(config_dir, clean_registry, monkeypatch,
                                                         reads):
    """The select posts one pair per entry; the page reads them back as the plan's own list."""
    from urllib.parse import urlencode

    scheduler_file(config_dir)
    install_plugin(monkeypatch, WIZARD_PLAN)
    pairs = [("startup.settings", preset_entry("config/presets.yaml", "NoMods")),
             ("startup.settings", preset_entry("config/presets_winter.yaml", "Snow")),
             ("startup.mission_id", "1"),
             ("startup.mission_id", "3")]
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2", urlencode(pairs)))

    settings = re.findall(r'<option value="([^"]*)" selected>', select_of(body, "startup.settings"))
    assert settings == [preset_entry("config/presets.yaml", "NoMods"),
                        preset_entry("config/presets_winter.yaml", "Snow")]
    assert [value for value, _label, selected in option_rows(select_of(body, "startup.mission_id"))
            if selected] == ["1", "3"]


@pytest.mark.asyncio
async def test_the_configured_preset_path_is_carried_hidden_not_offered(config_dir, clean_registry,
                                                                      monkeypatch, reads):
    """``presets`` is the bare-name fallback file: it rides hidden, so a step-2 submit cannot lose it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN,
                                     startup={"presets": "config/presets.yaml",
                                              "settings": ["NoMods"]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    assert hidden_inputs(body)["startup.presets"] == "config/presets.yaml"
    assert 'name="startup.presets"' not in re.sub(r'<input type="hidden"[^>]*>', "", body), \
        "the picker never offers the configured file path for editing"


@pytest.mark.asyncio
async def test_a_failed_mission_read_renders_its_own_text_box_and_leaves_the_file_pick(
        config_dir, clean_registry, monkeypatch, reads):
    """No id list: the id text box says why, and the file pick is still offered from its own read."""
    reads["failed"].add("missions")
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": [3, 1]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    assert 'name="startup.mission_id" multiple' not in body, "an empty select is never drawn"
    assert 'type="text" name="startup.mission_id" value="3, 1"' in body, "the stated ids survive"
    assert 'name="startup.mission_file" multiple' in body, "the file read still offers its list"
    assert body.count(scheduler_plan.MISSION_LIST_UNAVAILABLE) == 1, "one sentence for the id pick"
    assert scheduler_plan.MISSION_FILES_UNAVAILABLE not in body


@pytest.mark.asyncio
async def test_a_failed_file_read_renders_its_own_text_box_and_leaves_the_id_pick(
        config_dir, clean_registry, monkeypatch, reads):
    """No directory scan: the file text box says why, and the id pick is still offered."""
    reads["failed"].add("files")
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_file": "Missions/Gone.miz"}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    assert 'name="startup.mission_file" multiple' not in body
    assert 'type="text" name="startup.mission_file" value="Missions/Gone.miz"' in body
    assert 'name="startup.mission_id" multiple' in body, "the id read still offers its list"
    assert body.count(scheduler_plan.MISSION_FILES_UNAVAILABLE) == 1
    assert scheduler_plan.MISSION_LIST_UNAVAILABLE not in body


@pytest.mark.asyncio
async def test_a_failed_preset_read_renders_the_text_box_with_the_sentence(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch, reads):
    reads["failed"].add("presets")
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"settings": ["NoMods", "Clear"]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    assert 'name="startup.settings" multiple' not in body
    assert 'type="text" name="startup.settings" value="NoMods, Clear"' in body
    assert scheduler_plan.PRESET_LIST_UNAVAILABLE in body
    assert "NoMods, Clear" not in select_of(body, "startup.mission_id"), "the missions still pick"


@pytest.mark.asyncio
async def test_the_chips_block_is_the_scripts_and_never_the_renders(config_dir, clean_registry,
                                                                   monkeypatch, reads):
    """With the script off the native selection is the whole feedback; the script ADDS the chips."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"settings": ["NoMods"]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "2"))

    assert '<div class="sw-chosen">' not in body, "the chips are built by the script"
    assert "data-chosen-head=" in body, "the translated heading rides in the field's attribute"
    assert " hidden" not in body, "nothing the operator needs is hidden by the render"


@pytest.mark.asyncio
async def test_the_new_strings_are_translated_on_the_page(config_dir, clean_registry, monkeypatch,
                                                          reads):
    """The German render shows the catalog's words, the two fallback sentences included."""
    from services.webservice import i18n

    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, startup={"mission_id": 2,
                                                           "settings": ["NoMods"]}))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))

    async def both_languages(http) -> tuple[str, str]:
        english = tab_body(await step_page(http, "2"))
        http.cookies.set(i18n.COOKIE_NAME, "de")
        german = tab_body(await step_page(http, "2"))
        http.cookies.pop(i18n.COOKIE_NAME, None)
        return english, german

    async with client(app) as http:
        await sign_in(http, "specialk")
        offered, offered_de = await both_languages(http)
        reads["failed"].add("missions")
        no_missions, no_missions_de = await both_languages(http)
        reads["failed"].add("presets")
        no_presets, no_presets_de = await both_languages(http)

    for english, german, pairs in (
            (offered, offered_de, (
                ("Presets to apply", "Anzuwendende Presets"),
                ("not in these files any more", "nicht mehr in diesen Dateien"),
                ("Tick every mission the schedule may start", "Wähle jede Mission aus"),
                ("The path relative to the missions directory", "Der Pfad relativ zum "
                                                               "Missionsverzeichnis"),
                ("One group per preset file", "Eine Gruppe pro Preset-Datei"))),
            (no_missions, no_missions_de, (
                (scheduler_plan.MISSION_LIST_UNAVAILABLE,
                 "Die Missionsliste konnte nicht gelesen werden, deshalb stehen die Missionen hier "
                 "nicht zur Auswahl. Trage den Wert stattdessen ein."),)),
            (no_presets, no_presets_de, (
                (scheduler_plan.PRESET_LIST_UNAVAILABLE,
                 "Die Preset-Dateien konnten nicht gelesen werden, deshalb stehen die Presets hier "
                 "nicht zur Auswahl. Trage die Einträge stattdessen ein."),))):
        for english_string, german_string in pairs:
            assert english_string in english, english_string
            assert german_string in german, german_string
            assert english_string not in german, english_string


@pytest.mark.asyncio
async def test_a_preset_pick_reaches_the_file_as_a_list(scope_dir, node_data, clean_registry,
                                                        monkeypatch):
    """The whole path: a pick posted twice is written as the LIST the bot iterates, in posted order."""
    from test_webui_scheduler_save import effective

    first = preset_entry("config/presets.yaml", "NoMods")
    second = preset_entry("config/presets_winter.yaml", "Snow")
    # the plan as the wizard carries it, with the pick as the ONLY change in startup
    carried = [(field["name"], field["value"])
               for field in scheduler_plan.form_fields(effective())
               + scheduler_plan.shape_markers(effective())
               if field["name"] != "startup.settings"]
    app = app_for(scope_dir, monkeypatch)
    async with client(app) as http:
        await sign_in(http, ADMIN)
        # ONE name per entry, exactly what a multiple select posts: the body has to be a real
        # urlencoded body rather than a dict, or the second pick would overwrite the first
        body = urlencode([("_csrf_token", await csrf_of(http)),
                          ("_revision", await revision_of(http)),
                          ("_scope", SERVER_A),
                          ("startup.presets", "config/presets.yaml")]
                         + carried
                         + [("startup.settings", first), ("startup.settings", second)])
        reply = await http.post(settings_page.SCHEDULER_SAVE_PATH, content=body,
                                headers={"content-type": "application/x-www-form-urlencoded"})

    assert reply.status_code == 303, reply.text[:300]
    block = read_block(scope_dir, INSTANCE_A)
    assert block["startup"] == {"settings": [first, second],
                               "presets": "config/presets.yaml"}, block["startup"]
    assert {key: value for key, value in block.items() if key != "startup"} == INSTANCE_A_BLOCK


# ------------------------------------------- 4. the radio SWAPS the source (plan + script)
#
# The two picks are not two views of one read: switching the radio must remove the other key from
# the plan and make the hidden control post nothing. The script-off path is a radio plus Next; the
# script path is pinned by driving the shipped inline block against a minimal DOM.

def test_switching_the_startup_radio_drops_the_other_mission_key():
    """The radio names which source this start states; the other key leaves the plan."""
    by_file = scheduler_plan.plan_from_form([
        ("startup_on", "1"), ("startup_pick", "mission_file"),
        ("startup.mission_id", "3"), ("startup.mission_file", "Missions/NightOps.miz"),
    ])
    by_id = scheduler_plan.plan_from_form([
        ("startup_on", "1"), ("startup_pick", "mission_id"),
        ("startup.mission_id", "3"), ("startup.mission_file", "Missions/NightOps.miz"),
    ])

    assert by_file["startup"] == {"mission_file": "Missions/NightOps.miz"}
    assert by_id["startup"] == {"mission_id": 3}


def test_a_form_without_the_startup_radio_drops_neither_key():
    """Steps 3 to 5 carry both keys as plain values and state no choice."""
    back = scheduler_plan.plan_from_form([
        ("startup_on", "1"), ("startup.mission_id", "3"),
        ("startup.mission_file", "Missions/NightOps.miz"),
    ])

    assert back["startup"] == {"mission_id": 3, "mission_file": "Missions/NightOps.miz"}


@pytest.mark.asyncio
async def test_choosing_load_reveals_the_picks_from_the_two_sources(config_dir, clean_registry,
                                                                    monkeypatch, reads):
    """Drive the method to ``load``: the card draws the picks, each from its OWN read."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN,
                                     action=[{"cron": "0 4 * * *", "method": "restart"}]))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        restarted = tab_body(await step_page(http, "3",
                                             "action_trigger.0=cron&action.0.method=restart"))
        loaded = tab_body(await step_page(http, "3",
                                          "action_trigger.0=cron&action.0.method=load"))

    assert 'name="action.0.mission_id"' not in restarted, "a restart names no mission"
    ids = select_of(loaded, "action.0.mission_id")
    files = select_of(loaded, "action.0.mission_file")
    assert option_rows(ids) == [("1", "Caucasus Free Flight", False),
                                ("2", "Night Ops", False),
                                ("3", "Training Day", False)], "the id options are the DCS list"
    assert "Missions/New/Unlisted.miz" in [value for value, _label, _selected in option_rows(files)]
    assert "Missions/New/Unlisted.miz" not in [value for value, _label, _selected
                                               in option_rows(ids)]


def test_an_action_entry_uses_the_same_two_sources():
    """The action card and step 2 share the macro, so the sources are exclusive the same way."""
    rows = scheduler_plan.wizard_view(
        {"action": [{"cron": "0 4 * * *", "method": "load", "mission_id": 2}]}, [],
        step="3", missions=list(MISSIONS), mission_files=list(SCANNED))["actions"]

    assert rows[0]["picks"]["mission_id"]["options"][0]["label"] == "Night Ops"
    file_values = [row["value"] for row in rows[0]["picks"]["mission_file"]["options"]]
    assert "Missions/New/Unlisted.miz" in file_values, "the card's file pick reads the scan"


#: node is a developer tool; a missing one skips the script pin rather than failing the suite.
requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed: no way to run the inline script")

HARNESS = Path(__file__).resolve().parent / "scheduler_picks_harness.js"

#: the inline block that owns the action picks/method hooks — extracted from the RENDERED page.
PICK_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>((?:(?!</script>).)*data-action-picks"
                         r"(?:(?!</script>).)*)</script>", re.S)


@requires_node
@pytest.mark.asyncio
async def test_the_script_swaps_the_pick_and_submits_the_method_change(config_dir, clean_registry,
                                                                      monkeypatch, tmp_path):
    """Changing the method submits the step (so ``load`` reveals); the pick swap disables the other
    control so it posts nothing. Driven, not read."""
    body = await actions_body(config_dir, monkeypatch,
                              {"cron": "0 4 * * *", "method": "load", "mission_id": 2})
    match = PICK_SCRIPT.search(body)
    assert match, "the actions step rendered no pick/method script — the pin would be vacuous"
    script = tmp_path / "scheduler-picks.js"
    script.write_text(match.group(1), encoding="utf-8")
    done = subprocess.run(["node", str(HARNESS), str(script)], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)

    assert result["methodListener"] is True, "no change listener on the method select"
    assert result["methodSubmits"] >= 1, "choosing a method never reveals its controls"
    assert result["pick"]["idOff"] is True and result["pick"]["fileOff"] is False
    assert result["pick"]["idDisabled"] is True, "the hidden pick must post nothing"
    assert result["pick"]["fileDisabled"] is False, "the visible pick must post its value"


# ----------------------------------------------------- 5. the event delay and the default trigger
#
# Only the mission start carries a delay. The other three event rows draw no delay control, and a
# `sleep` the file states there is carried hidden so a save never prunes it.

EVENT_COMMANDS = {
    "onSimulationStart": {"command": "load:Scripts/net/start.lua", "sleep": 3},
    "onSimulationStop": {"command": "load:Scripts/net/stop.lua", "sleep": 5},
    "onMissionEnd": "load:Scripts/net/end.lua",
    "onShutdown": {"command": "run:shutdown /r", "sleep": 9},
}


@pytest.mark.asyncio
async def test_only_the_mission_start_carries_a_delay_control(config_dir, clean_registry,
                                                              monkeypatch):
    """One number input, on onMissionStart; the other stated sleeps ride hidden."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, **EVENT_COMMANDS))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    delays = re.findall(r'<input[^>]*type="number"[^>]*name="([^"]*\.sleep)"', body)
    assert delays == ["onSimulationStart.sleep"], delays
    assert "Delay before the command (seconds)" in body
    hidden = hidden_inputs(body)
    assert hidden["onSimulationStop.sleep"] == "5"
    assert hidden["onShutdown.sleep"] == "9"
    assert "onMissionEnd.sleep" not in hidden, "no sleep is stated for that event"
    assert '> sleep <' not in body, "the bare word is no longer a control label"


@pytest.mark.asyncio
async def test_a_stated_sleep_on_the_other_events_survives_the_post(config_dir, clean_registry,
                                                                   monkeypatch):
    """The rendered step's own submission keeps every stated sleep exactly as the file has it."""
    scheduler_file(config_dir)
    install_plugin(monkeypatch, dict(WIZARD_PLAN, **EVENT_COMMANDS))
    app = build(config_dir, cluster(), plugins=("mission", "mods", "scheduler"))
    async with client(app) as http:
        await sign_in(http, "specialk")
        body = tab_body(await step_page(http, "3"))

    back = scheduler_plan.plan_from_form(form_pairs(body))
    assert back["onSimulationStart"] == {"command": "load:Scripts/net/start.lua", "sleep": 3}
    assert back["onSimulationStop"] == {"command": "load:Scripts/net/stop.lua", "sleep": 5}
    assert back["onMissionEnd"] == "load:Scripts/net/end.lua"
    assert back["onShutdown"] == {"command": "run:shutdown /r", "sleep": 9}


@pytest.mark.asyncio
async def test_a_new_entry_opens_on_times_of_day_and_writes_no_trigger_key(config_dir,
                                                                          clean_registry,
                                                                          monkeypatch):
    """The default is SELECTED but not stated: the empty row posts nothing, so no key is written."""
    body = await actions_body(config_dir, monkeypatch, {"method": "restart"})
    radios = re.findall(r'<input type="radio" name="action_trigger\.0" value="([^"]+)"([^>]*)>',
                        body)
    assert [value for value, attrs in radios if "checked" in attrs] == ["times"]
    assert 'name="action_time.0.0"' in body
    assert scheduler_plan.ACTION_NO_TRIGGER in body, \
        "it still says nothing fires until a time is entered"

    plan = scheduler_plan.plan_from_form(form_pairs(body))
    assert plan["action"] == [{"method": "restart"}], "no trigger key was written"


@pytest.mark.asyncio
async def test_an_entry_that_states_a_trigger_keeps_its_own(config_dir, clean_registry,
                                                           monkeypatch):
    """The default applies only where nothing is stated: a cron entry still opens on cron."""
    body = await actions_body(config_dir, monkeypatch, {"cron": "0 4 * * *", "method": "restart"})
    radios = re.findall(r'<input type="radio" name="action_trigger\.0" value="([^"]+)"([^>]*)>',
                        body)

    assert [value for value, attrs in radios if "checked" in attrs] == ["cron"]
    assert 'name="action_time.0.0"' not in body


# --------------------------------------------------------------------- 6. the URL's field bound

#: the WORST realistic pick a step carries: the flat plan with eight missions, eight files and eight
#: preset entries on ``startup`` — a pick the wizard's own controls can produce.
def worst_case_plan() -> dict:
    return {
        "timezone": "Europe/Berlin",
        "schedule": {"00-06": "YYYYYYY", "06-12": "NNNNNNN", "12-18": "PPPPPPP", "18-24": "YYYYYYY"},
        "warn": {"message": "restarting", "times": {600: "ten", 300: "", 60: "one"}},
        "action": [
            {"times": ["04:00", "08:00", "12:00"], "method": "restart", "mission_time": 480,
             "max_mission_time": 600, "populated": False, "shutdown": True, "run_extensions": True,
             "mission_id": [1, 2, 3], "mission_file": ["Missions/a.miz", "Missions/b.miz"],
             "settings": ["NoMods", "Clear"], "password": "x"},
        ] * 3,
        "startup": {
            "mission_id": list(range(1, 9)),
            "mission_file": [f"Missions/mission-{n}.miz" for n in range(1, 9)],
            "presets": ["config/presets.yaml", "config/presets_winter.yaml"],
            "settings": [preset_entry("config/presets.yaml", f"Preset {n}") for n in range(1, 9)],
            "password": "Secret",
            "clear_maintenance": True,
        },
        "onSimulationStart": "load:Scripts/net/start.lua",
        "onSimulationStop": {"command": "load:Scripts/net/stop.lua", "sleep": 5},
        "onMissionEnd": "load:Scripts/net/end.lua",
        "onShutdown": {"command": "run:shutdown /r", "sleep": 5},
    }


def test_the_worst_realistic_pick_stays_inside_the_forms_own_field_bound():
    """One name per selected entry: the count, not a byte guess, is what the bound can be held to."""
    fields = scheduler_plan.form_fields(worst_case_plan())

    assert len(fields) <= scheduler_plan.FORM_FIELD_BOUND, len(fields)
    assert scheduler_plan.FORM_FIELD_BOUND < 1000
    settings = [field for field in fields if field["name"] == "startup.settings"]
    assert len(settings) == 8, "one hidden input per picked entry, so the whole pick travels"
