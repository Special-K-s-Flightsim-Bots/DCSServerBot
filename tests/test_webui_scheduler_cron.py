"""The scheduler wizard's pure half: cron fires, ``times``, and the form's own language.

Nothing here renders a page. These are the helpers the steps lean on, pinned on their own so a
refusal can never be mistaken for an empty answer: a cron expression that parses but has no next
date must SAY so, and a value the operator did not touch must come back exactly as the file holds
it — including the shape (one ``action_map`` stays one map, a sequence stays a sequence).

The reference instant is passed in, so the next-fire pins cannot drift with the wall clock.
"""
from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pytest

from services.webservice import scheduler_plan

BERLIN = "Europe/Berlin"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo(BERLIN))


# ------------------------------------------------------------------------------------ cron fires

def test_three_fires_are_utc_stamps_in_the_plans_timezone():
    """04:00 Berlin in late October is 03:00 UTC — three consecutive days of it."""
    result = scheduler_plan.next_fires("0 4 * * *", BERLIN, now=NOW)

    assert result["error"] == ""
    assert result["fires"] == ["2026-10-29T03:00:00+00:00",
                               "2026-10-30T03:00:00+00:00",
                               "2026-10-31T03:00:00+00:00"]


def test_the_same_expression_reads_differently_in_another_zone():
    """The zone is the plan's, not the process's: the same expression shifts with it."""
    berlin = scheduler_plan.next_fires("0 4 * * *", BERLIN, now=NOW)["fires"]
    utc = scheduler_plan.next_fires("0 4 * * *", "UTC", now=NOW)["fires"]

    assert berlin != utc
    assert utc[0] == "2026-10-29T04:00:00+00:00"
    assert berlin[0] == "2026-10-29T03:00:00+00:00"


def test_an_expression_that_parses_but_never_fires_is_refused_not_empty():
    """30 February: croniter's bad-DATE error, its own sentence, and NEVER an empty run list."""
    result = scheduler_plan.next_fires("0 0 30 2 *", BERLIN, now=NOW)

    assert result["fires"] == []
    assert result["error"] == scheduler_plan.CRON_NEVER_FIRES
    assert "never fires" in result["error"]


def test_an_expression_that_is_not_cron_is_refused():
    result = scheduler_plan.next_fires("bad", BERLIN, now=NOW)

    assert result["fires"] == []
    assert result["error"] == scheduler_plan.CRON_NOT_AN_EXPRESSION


def test_a_six_field_expression_warns_and_still_returns_fires():
    """The sixth field is SECONDS: accepted, and the page says what it means."""
    result = scheduler_plan.next_fires("0 0 1 1 * *", BERLIN, now=NOW)

    assert result["error"] == ""
    assert result["warning"] == scheduler_plan.CRON_EXTRA_FIELD
    assert len(result["fires"]) == 3
    assert result["fires"][1] != result["fires"][0], "a seconds field moves every fire"


def test_an_empty_expression_states_nothing():
    result = scheduler_plan.next_fires("", BERLIN, now=NOW)

    assert result == {"expression": "", "fires": [], "error": "", "warning": ""}


def test_an_unknown_zone_is_its_own_sentence():
    result = scheduler_plan.next_fires("0 4 * * *", "Nowhere/Special", now=NOW)

    assert result["fires"] == []
    assert result["error"] == scheduler_plan.UNKNOWN_TIMEZONE


# ------------------------------------------------------------------------------------- the times

def test_a_time_of_day_is_read_in_the_plans_timezone():
    """04:00 Berlin, three days of it, as UTC stamps — the same shape a cron fire has."""
    result = scheduler_plan.time_fires("04:00", BERLIN, now=NOW)

    assert result["error"] == ""
    assert result["fires"] == ["2026-10-29T03:00:00+00:00",
                               "2026-10-30T03:00:00+00:00",
                               "2026-10-31T03:00:00+00:00"]


def test_a_time_the_schema_refuses_is_refused():
    """The schema's own ``HH:MM`` pattern decides, not a second one invented here."""
    for value in ("25:00", "4", "04:60", "evening"):
        result = scheduler_plan.time_fires(value, BERLIN, now=NOW)
        assert result["error"] == scheduler_plan.TIME_NOT_A_TIME, value
        assert result["fires"] == [], value


def test_times_rows_answers_one_row_per_entry():
    rows = scheduler_plan.times_rows(["04:00", "08:00"], BERLIN, now=NOW)

    assert [row["value"] for row in rows] == ["04:00", "08:00"]
    assert rows[0]["fires"][0] == "2026-10-29T03:00:00+00:00"
    assert rows[1]["fires"][0] == "2026-10-29T07:00:00+00:00"


# ------------------------------------------------------------------------------- the step routing

def test_a_step_is_a_member_or_the_first_and_absence_is_the_landing():
    assert scheduler_plan.normalise_step(None) == scheduler_plan.SCHEDULER_LANDING
    assert scheduler_plan.normalise_step("3") == "3"
    for value in ("99", "", "0", "week"):
        assert scheduler_plan.normalise_step(value) == "1", repr(value)
    assert scheduler_plan.normalise_step(" 2 ") == "2", "whitespace around a step is not a step"


def test_the_steps_read_in_the_order_the_plan_is_applied_in():
    """When it runs, how it starts, what it does, what it announces — and the review last.

    The ORDER is the wizard's, and each step's ownership is what the rail's blurbs promise, so the
    two are pinned together: a step that renders another step's cards would otherwise leave that
    step with everything and its own controls nowhere.
    """
    assert scheduler_plan.SCHEDULER_STEPS == ("1", "2", "3", "4", "5")
    assert scheduler_plan.SCHEDULER_REVIEW_STEP == "5"
    assert scheduler_plan.STEP_KEYS == {
        "1": ("timezone", "schedule"),
        "2": ("startup",),
        "3": ("action",) + scheduler_plan.COMMAND_KEYS,
        "4": ("warn",),
        "5": (),
    }
    assert scheduler_plan.WIZARD_KEYS == (
        "timezone", "schedule", "warn", "action", "startup") + scheduler_plan.COMMAND_KEYS


# ------------------------------------------------------------------------------- reading a form

#: one plan carrying every shape the wizard has to keep: a sequence action, a warn map with an
#: empty message, a list-valued startup key and both command shapes.
PLAN = {
    "timezone": BERLIN,
    "schedule": {"00-12": "YYYYYYY", "12-24": "NNNNNNN"},
    "warn": {"message": "restarting", "times": {600: "ten minutes", 60: ""}},
    "action": [
        {"times": ["04:00", "08:00"], "method": "restart", "populated": False, "shutdown": True},
        {"cron": "0 4 * * *", "method": "rotate"},
    ],
    "startup": {"mission_id": 3, "presets": ["a.yaml", "b.yaml"], "clear_maintenance": True},
    "onShutdown": {"command": "run:shutdown /r", "sleep": 5},
    "onMissionEnd": "load:end.lua",
}


def round_trip(plan):
    """The plan as a step's form states it: the visible fields plus the shape markers."""
    pairs = [(field["name"], field["value"])
             for field in scheduler_plan.form_fields(plan) + scheduler_plan.shape_markers(plan)]
    return scheduler_plan.plan_from_form(pairs)


def test_a_plan_survives_a_round_trip_through_the_form_exactly():
    assert round_trip(PLAN) == PLAN


def test_an_action_sequence_stays_a_sequence():
    back = round_trip(PLAN)

    assert isinstance(back["action"], list)
    assert [entry["method"] for entry in back["action"]] == ["restart", "rotate"]


def test_one_action_map_stays_one_map():
    """The schema's ``seq_or_map``: a file holding a single block keeps a single block."""
    single = {"timezone": "UTC", "action": {"times": ["04:00"], "method": "rotate"}}
    back = round_trip(single)

    assert isinstance(back["action"], dict)
    assert back == single


def test_a_warn_map_keeps_its_shape_and_its_empty_message():
    """The seconds ARE the map's keys, so a warn time with no text of its own must survive."""
    back = round_trip(PLAN)

    assert back["warn"]["times"] == {600: "ten minutes", 60: ""}


def test_a_warn_sequence_stays_a_sequence():
    plan = {"timezone": "UTC", "warn": {"message": "m", "times": [600, 60]}}
    back = round_trip(plan)

    assert back["warn"]["times"] == [600, 60]


def test_a_command_keeps_its_str_or_map_shape():
    back = round_trip(PLAN)

    assert back["onShutdown"] == {"command": "run:shutdown /r", "sleep": 5}
    assert back["onMissionEnd"] == "load:end.lua"


def test_an_empty_string_is_an_absent_key():
    """A control the operator cleared states nothing — it must not become an empty value."""
    back = scheduler_plan.plan_from_form([
        ("timezone", ""),
        ("warn.message", ""),
        ("action.0.method", "rotate"),
    ])

    assert back == {"action": [{"method": "rotate"}]}, "absent, and never an empty string"


def test_a_checkbox_that_posts_nothing_is_absent_and_false_is_false():
    """``populated`` posts its own ``false`` when the box is on, and nothing when it is off."""
    checked = scheduler_plan.plan_from_form([("action.0.populated", "false"),
                                             ("action.0.shutdown", "true")])
    unchecked = scheduler_plan.plan_from_form([("action.0.method", "rotate")])

    assert checked["action"][0] == {"populated": False, "shutdown": True}
    assert checked["action"][0]["populated"] is False
    assert unchecked["action"][0] == {"method": "rotate"}, "an unticked box states nothing"


def test_the_default_radio_states_no_populated_key_at_all():
    """``populated`` is a radio pair: the empty value is the DEFAULT and states nothing.

    The two radios share the field name, so a browser always posts one of them; posting the empty
    value must leave the key out of the plan rather than write ``populated: true`` into the file.
    """
    acting = scheduler_plan.plan_from_form([
        ("action.0.method", "restart"),
        ("action.0.populated", ""),
    ])
    waiting = scheduler_plan.plan_from_form([
        ("action.0.method", "restart"),
        ("action.0.populated", "false"),
    ])

    assert acting == {"action": [{"method": "restart"}]}, "the default writes no key"
    assert waiting["action"][0]["populated"] is False


def test_a_list_valued_key_survives_as_a_list():
    back = round_trip(PLAN)

    assert back["startup"]["presets"] == ["a.yaml", "b.yaml"]
    assert back["action"][0]["times"] == ["04:00", "08:00"]


def test_a_number_a_flag_and_a_list_are_read_by_their_schema_type():
    back = round_trip(PLAN)

    assert back["startup"]["mission_id"] == 3
    assert back["onShutdown"]["sleep"] == 5
    assert back["action"][0]["shutdown"] is True


def test_a_name_outside_the_wizard_is_ignored():
    back = scheduler_plan.plan_from_form([("tab", "scheduler"), ("step", "2"), ("csrf_token", "x")])

    assert back == {}


def test_a_step_carries_every_value_it_does_not_own():
    for step, owned in scheduler_plan.STEP_KEYS.items():
        carried = {field["name"].split(".", 1)[0]
                   for field in scheduler_plan.carried_fields(PLAN, step)}
        assert carried and not (carried & set(owned)), step
    # and the two moved groups are carried by the step that no longer owns them
    stated_commands = set(scheduler_plan.COMMAND_KEYS) & set(PLAN)
    for step, moved, kept in (("2", stated_commands, "startup"), ("3", ("startup",), None)):
        heads = {field["name"].split(".", 1)[0]
                 for field in scheduler_plan.carried_fields(PLAN, step)}
        assert set(moved) <= heads, step
        if kept:
            assert kept not in heads, step


# ------------------------------------------------- step 2: the startup switch and the two picks

def test_a_form_that_turns_the_startup_switch_off_states_no_startup_block():
    """Off is a DELETION: the fields the step would have written are dropped, not written back."""
    on = scheduler_plan.plan_from_form([
        (scheduler_plan.STARTUP_FLAG, scheduler_plan.STARTUP_FLAG_ON),
        ("startup.mission_id", "3"),
        ("startup.password", "hunter2"),
    ])
    off = scheduler_plan.plan_from_form([
        (scheduler_plan.STARTUP_FLAG, scheduler_plan.STARTUP_FLAG_OFF),
        ("startup.mission_id", "3"),
        ("startup.password", "hunter2"),
    ])

    assert on == {"startup": {"mission_id": 3, "password": "hunter2"}}
    assert off == {}, "the switch off states nothing, whichever fields the form carried"


def test_the_switch_reads_the_post_the_browser_sends_for_one_name():
    """A hidden ``0`` and the checkbox's ``1`` share a name; the LAST value is the answer."""
    pairs = [(scheduler_plan.STARTUP_FLAG, scheduler_plan.STARTUP_FLAG_OFF),
             (scheduler_plan.STARTUP_FLAG, scheduler_plan.STARTUP_FLAG_ON),
             ("startup.mission_id", "3")]

    assert scheduler_plan.plan_from_form(pairs)["startup"] == {"mission_id": 3}
    assert scheduler_plan.plan_from_form(
        pairs[:1] + [("startup.mission_id", "3")]) == {}, "an unticked box posts the hidden 0"


def test_a_form_without_the_switch_keeps_the_startup_it_carries():
    """Every OTHER step carries ``startup.*`` hidden and has no switch: it must not clear it."""
    plan = scheduler_plan.plan_from_form([("startup.mission_id", "3"),
                                         ("timezone", "UTC")])

    assert plan["startup"] == {"mission_id": 3}
    view = scheduler_plan.wizard_view({"timezone": "UTC"}, [], step="3", now=NOW)
    assert view["startup_on"] is False, "a step with no switch and no block starts off"


def test_the_step_opens_switched_on_when_the_block_states_something():
    """Nothing the file states may be hidden behind a switch nobody ticked."""
    stated = scheduler_plan.wizard_view({"startup": {"mission_id": 3}}, [], step="2", now=NOW)
    bare = scheduler_plan.wizard_view({"timezone": "UTC"}, [], step="2", now=NOW)
    off = scheduler_plan.wizard_view({"startup": {"mission_id": 3}},
                                     [(scheduler_plan.STARTUP_FLAG,
                                       scheduler_plan.STARTUP_FLAG_OFF)], step="2", now=NOW)

    assert stated["startup_on"] is True
    assert bare["startup_on"] is False
    assert off["startup_on"] is False, "the post wins over the block"


def test_a_start_names_its_mission_one_of_two_ways():
    """The radio decides; with no post the block's own shape does, and both keys read as the ID."""
    def kind(block, posted=None):
        pairs = [(scheduler_plan.STARTUP_PICK, posted)] if posted else []
        return scheduler_plan.startup_pick_kind(pairs, block)

    assert kind({"mission_file": "Missions/a.miz"}) == scheduler_plan.STARTUP_PICK_FILE
    assert kind({"mission_id": 3}) == scheduler_plan.STARTUP_PICK_ID
    assert kind({}) == scheduler_plan.STARTUP_PICK_ID, "a fresh start takes the ID pick"
    assert kind({"mission_id": 3, "mission_file": "a.miz"}) == scheduler_plan.STARTUP_PICK_ID
    assert kind({"mission_id": 3}, scheduler_plan.STARTUP_PICK_FILE) == \
        scheduler_plan.STARTUP_PICK_FILE, "the radio wins over the block"
    assert kind({"mission_id": 3}, "nonsense") == scheduler_plan.STARTUP_PICK_ID


def test_the_whole_plan_reaches_the_step_inside_the_forms_own_bound():
    """The URL is the state: the field count must stay far under the 1000-field form bound."""
    assert len(scheduler_plan.form_fields(PLAN)) <= scheduler_plan.FORM_FIELD_BOUND
    assert scheduler_plan.FORM_FIELD_BOUND < 1000


def test_startup_delay_is_never_carried():
    """A DEFAULT-level value is not this wizard's to write, so no step carries it."""
    plan = dict(PLAN, startup_delay=10)

    names = {field["name"] for field in scheduler_plan.form_fields(plan)}
    assert "startup_delay" not in names


def test_the_wizard_plan_is_the_effective_plan_until_something_is_posted():
    """An untouched step must not promote an inherited value into an override."""
    assert scheduler_plan.wizard_plan({"timezone": "UTC"}, []) == {"timezone": "UTC"}
    posted = scheduler_plan.wizard_plan({"timezone": "UTC"}, [("schedule.00-12", "YYYYYYY")])
    assert posted == {"schedule": {"00-12": "YYYYYYY"}}, "what the form states IS the plan"


def test_the_provenance_chip_reads_the_file_not_the_merge():
    """Step 1 marks the timezone inherited when the server's own block is silent about it."""
    view = scheduler_plan.wizard_view({"timezone": "UTC"}, [], raw={}, default={"timezone": "UTC"},
                                      step="1", now=NOW)
    own = scheduler_plan.wizard_view({"timezone": "UTC"}, [], raw={"timezone": "UTC"},
                                     default={}, step="1", now=NOW)

    assert view["state"]["timezone"] == scheduler_plan.INHERITED
    assert own["state"]["timezone"] == scheduler_plan.OWN


def test_a_plan_with_no_schedule_proposes_one_row_of_the_whole_day():
    """Nothing scheduled renders ONE row, 00-24 with every day on — the default proposal."""
    view = scheduler_plan.wizard_view({"timezone": "UTC"}, [], step="1", now=NOW)

    assert len(view["week_rows"]) == 1
    row = view["week_rows"][0]
    assert (row["start"], row["end"], row["days"]) == ("00:00", "00:00", "YYYYYYY")
    assert row["range"] == "00:00-24:00", "midnight composes to the schema's 24:00 end"
    assert view["week_errors"] == []


def test_a_stated_schedule_renders_one_row_per_range_in_the_files_order():
    plan = {"timezone": "UTC", "schedule": {"18-24": "NNNNNNN", "00-12": "YYYYYYY"}}

    view = scheduler_plan.wizard_view(plan, [], step="1", now=NOW)

    assert [row["range"] for row in view["week_rows"]] == ["18:00-24:00", "00:00-12:00"]
    assert [row["days"] for row in view["week_rows"]] == ["NNNNNNN", "YYYYYYY"]
    assert [row["index"] for row in view["week_rows"]] == ["0", "1"]


def test_the_rendered_schedule_is_composed_with_the_schemas_own_key_shape():
    """The control posts times; the plan must hold the schema's range key, not the times."""
    plan = scheduler_plan.plan_from_form([
        ("timezone", "UTC"),
        ("week_start.0", "06:30"), ("week_end.0", "12:00"), ("week_days.0", "YNYNYNY"),
        ("week_start.1", "18:00"), ("week_end.1", "00:00"), ("week_days.1", "NNNNNNN"),
    ])

    # whole-hour boundaries drop their :00 — the schema's own example style — so 18:00-24:00 is
    # "18-24"; a range with minutes keeps them
    assert plan["schedule"] == {"06:30-12:00": "YNYNYNY", "18-24": "NNNNNNN"}, \
        "a row ending at midnight composes the day's end, not 00:00"
    for key in plan["schedule"]:
        assert detail_schema_pattern().match(key), "the schema must accept what the control emits"


def detail_schema_pattern():
    """The schedule mapping's OWN regex, read from the shipped schema."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parent.parent / "plugins" / "scheduler" / "schemas" \
        / "scheduler_schema.yaml"
    documents = [document for document in
                 yaml.safe_load_all(path.read_text(encoding="utf-8")) if isinstance(document, dict)]
    blocks = [block for document in documents for block in document.values()
              if isinstance(block, dict) and isinstance(block.get("mapping"), dict)]
    mapping = next(block["mapping"]["schedule"]["mapping"] for block in blocks
                   if isinstance(block["mapping"].get("schedule"), dict))
    key = next(name for name in mapping if name.startswith("regex;"))
    return re.compile(key.split("regex;(", 1)[1][:-2], re.S)


def test_addrow_appends_one_row_that_continues_the_week_and_removerow_drops_that_row():
    rows = [("week_start.0", "00:00"), ("week_end.0", "12:00"), ("week_days.0", "YYYYYYY"),
            ("week_start.1", "12:00"), ("week_end.1", "24:00"), ("week_days.1", "NNNNNNN")]

    added = scheduler_plan.week_rows_from_pairs(rows + [("addrow", "1")])
    removed = scheduler_plan.week_rows_from_pairs(rows + [("removerow", "0")])

    assert added is not None and removed is not None, "a step-1 form always posts rows"
    assert len(added) == 3, "one more row"
    # the new row starts where the last one ended, one whole hour long: 24:00 is the day's end,
    # which a time control cannot hold, so the day restarts at 00:00-01:00
    assert (added[-1]["start"], added[-1]["end"], added[-1]["days"]) == ("00:00", "01:00",
                                                                       "YYYYYYY")
    assert [row["index"] for row in added] == ["0", "1", "2"], "rows renumber from zero"
    assert len(removed) == 1
    assert removed[0]["start"] == "12:00", "the SECOND row survived the removal of the first"


def test_a_row_missing_a_time_is_not_part_of_the_plan():
    plan = scheduler_plan.plan_from_form([
        ("week_start.0", "06:00"), ("week_end.0", ""), ("week_days.0", "YYYYYYY"),
        ("week_start.1", "12:00"), ("week_end.1", "18:00"), ("week_days.1", "NNNNNNN"),
    ])

    assert plan["schedule"] == {"12-18": "NNNNNNN"}


def test_two_rows_whose_ranges_overlap_are_refused_naming_the_row():
    schedule, errors = scheduler_plan.schedule_from_week([
        {"start": "00:00", "end": "12:00", "days": "YYYYYYY"},
        {"start": "10:00", "end": "14:00", "days": "YYYYYYY"},
    ])

    assert schedule == {"00-12": "YYYYYYY"}, "the ambiguous row is not written"
    assert errors == [{"row": 2, "msgid": scheduler_plan.WEEK_RANGE_OVERLAP}]


def test_a_start_that_is_not_before_its_end_is_refused_naming_the_row():
    schedule, errors = scheduler_plan.schedule_from_week([
        {"start": "12:00", "end": "12:00", "days": "YYYYYYY"},
        {"start": "18:00", "end": "06:00", "days": "YYYYYYY"},
    ])

    assert schedule == {}
    assert [error["row"] for error in errors] == [1, 2]
    assert {error["msgid"] for error in errors} == {scheduler_plan.WEEK_START_NOT_BEFORE_END}


def test_the_week_a_form_posts_is_the_plan_even_when_it_states_nothing():
    """Clearing every row is a STATEMENT, not an unsubmitted page — the schedule must not return."""
    effective = {"timezone": "UTC", "schedule": {"00-24": "YYYYYYY"}}
    cleared = [("week_start.0", ""), ("week_end.0", ""), ("week_days.0", "YYYYYYY")]

    assert scheduler_plan.wizard_plan(effective, cleared) == {}
    view = scheduler_plan.wizard_view(effective, cleared, step="1", now=NOW)
    assert len(view["week_rows"]) == 1, "one empty row keeps the form's own language"
    assert view["week_rows"][0]["range"] == ""
    assert view["week_rows"][0]["start"] == ""


def test_the_timezone_picker_lists_every_zone_and_keeps_an_unknown_one():
    """A closed list from the interpreter, the empty option first, and no silent drop."""
    from zoneinfo import available_timezones

    options = scheduler_plan.timezone_options("Europe/Berlin")
    unknown = scheduler_plan.timezone_options("Mars/Olympus_Mons")

    assert options[0] == "", "the empty option is the machine's own local time"
    assert set(available_timezones()) <= set(options)
    assert options[1:] == sorted(options[1:]), "the zones are sorted"
    assert "Mars/Olympus_Mons" in unknown, "a value the list does not carry is kept"
    assert unknown[1:] == sorted(unknown[1:]), "and it is kept in its place"


@pytest.mark.parametrize("step", scheduler_plan.SCHEDULER_STEPS)
def test_every_step_builds_its_view(step):
    view = scheduler_plan.wizard_view(PLAN, [], step=step, now=NOW)

    assert view["step"] == step
    assert view["carried"]
    assert view["markers"]


def test_one_action_map_renders_one_card():
    plan = {"timezone": "UTC", "action": {"times": ["04:00"], "method": "rotate"}}
    view = scheduler_plan.wizard_view(plan, [], step="3", now=NOW)

    assert len(view["actions"]) == 1
    assert view["actions"][0]["index"] == 0
    assert view["actions"][0]["entry"]["method"] == "rotate"
    assert [marker["value"] for marker in view["markers"] if marker["name"] == "shape.action"] == ["map"]


def test_a_plan_with_nothing_scheduled_renders_no_entries():
    view = scheduler_plan.wizard_view({"timezone": "UTC"}, [], step="3", now=NOW)

    assert view["actions"] == []
    assert view["warn"] is None
    assert view["warn_times"] == []
    assert [row["command"] for row in view["commands"]] == ["", "", "", ""]


# ---------------------------------- step 3: ONE trigger, the time rows, and the method's own keys

#: one entry stating EVERY trigger and every method-owned key, so a rule that keeps too much and a
#: rule that keeps too little both show up in the same assertion.
FULL_ENTRY = {
    "times": ["04:00"], "cron": "0 5 * * *", "mission_time": 60, "max_mission_time": 90,
    "real_time": 30, "idle_time": 10, "method": "load", "mission_id": 3,
    "mission_file": "Missions/night.miz", "no_reload": True, "password": "hunter2",
    "settings": ["NoMods"], "presets": ["config/a.yaml", "config/b.yaml"], "run_extensions": True,
    "use_orig": False, "populated": False, "shutdown": True, "clear_maintenance": True,
}

#: the keys that are NOT a trigger and NOT method-owned: they state conditions or a carried flag, and
#: every method keeps them.
KEPT_ALWAYS = {"method", "populated", "clear_maintenance"}


def posted_entry(entry, *extra):
    """The pairs a step form posts for ONE entry: the plan's own values plus *extra*.

    A name *extra* states REPLACES the plan's pair for it, the way a control the operator changed
    overrides the value the form carried.
    """
    replaced = {name for name, _value in extra}
    pairs = [(name, value)
             for name, value in [(field["name"], field["value"])
                                 for field in scheduler_plan.form_fields({"action": [dict(entry)]})
                                 + scheduler_plan.shape_markers({"action": [dict(entry)]})]
             if name not in replaced]
    return pairs + list(extra)


def test_the_bot_reads_the_triggers_in_its_own_order():
    """``_match_action`` checks times, mission time, real time, idle time, then cron — and so do we."""
    assert scheduler_plan.ACTION_TRIGGERS == ("times", "mission_time", "real_time", "idle_time",
                                              "cron")
    pairs = [("times", 1), ("cron", 2), ("idle_time", 3), ("real_time", 4), ("mission_time", 5),
             ("max_mission_time", 6)]
    for kept, entry in (
            ("times", {"times": ["04:00"], "cron": "0 5 * * *", "idle_time": 10}),
            ("mission_time", {"cron": "0 5 * * *", "idle_time": 10, "mission_time": 60}),
            ("real_time", {"idle_time": 10, "real_time": 30, "cron": "0 5 * * *"}),
            ("idle_time", {"idle_time": 10, "cron": "0 5 * * *"}),
            ("cron", {"cron": "0 5 * * *"}),
            ("mission_time", {"max_mission_time": 90}),
            ("", {"method": "restart"})):
        assert scheduler_plan.entry_trigger(entry) == kept, entry
    # each key belongs to exactly one trigger, and ``max_mission_time`` to mission time
    owned = {key for keys in scheduler_plan.ACTION_TRIGGER_KEYS_BY_KIND.values() for key in keys}
    assert owned == {"times", "cron", "mission_time", "max_mission_time", "real_time", "idle_time"}
    assert any(len(keys) == 2 for keys in scheduler_plan.ACTION_TRIGGER_KEYS_BY_KIND.values())
    assert pairs  # the trigger table above is exhaustive over the schema's own trigger keys


@pytest.mark.parametrize("chosen", scheduler_plan.ACTION_TRIGGERS)
def test_only_the_chosen_trigger_survives_the_form(chosen):
    kept = set(scheduler_plan.ACTION_TRIGGER_KEYS_BY_KIND[chosen])
    back = scheduler_plan.plan_from_form(posted_entry(FULL_ENTRY, ("action_trigger.0", chosen)))

    entry = back["action"][0]
    assert {key for key in entry if key in
            set().union(*scheduler_plan.ACTION_TRIGGER_KEYS_BY_KIND.values())} == kept, entry
    # nothing outside the trigger group is touched by the switch
    assert entry["method"] == "load"
    assert entry["mission_id"] == 3


def test_the_time_rows_replace_the_times_key():
    """Rows are the control: what they state is the list, and an empty row states nothing."""
    pairs = posted_entry(FULL_ENTRY, ("action_trigger.0", "times"),
                         ("action_time.0.0", "04:00"), ("action_time.0.1", "21:30"),
                         ("action_time.0.2", ""))
    back = scheduler_plan.plan_from_form(pairs)

    assert back["action"][0]["times"] == ["04:00", "21:30"]
    # a form with no row field is not this control, so a comma-carried value is left alone
    carried = [(name, value) for name, value in pairs
               if not name.startswith("action_time.") and name != "action_trigger.0"]
    assert scheduler_plan.plan_from_form(carried)["action"][0]["times"] == ["04:00"]


def test_adding_and_removing_a_time_row_is_two_submits_of_one_form():
    rows = [("action_time.0.0", "04:00"), ("action_time.0.1", "08:00")]

    assert scheduler_plan.posted_time_rows(rows) == {"0": ["04:00", "08:00"]}
    assert scheduler_plan.posted_time_rows(rows + [("addtime", "0")]) == {
        "0": ["04:00", "08:00", ""]}
    assert scheduler_plan.posted_time_rows(rows + [("removetime", "0.0")]) == {"0": ["08:00"]}
    assert scheduler_plan.posted_time_rows(rows + [("removetime", "0.9")]) == {
        "0": ["04:00", "08:00"]}, "a row that is not posted is not removed"
    assert scheduler_plan.posted_time_rows([("action.0.times", "04:00")]) is None


@pytest.mark.parametrize("method", scheduler_plan.ACTION_METHODS)
def test_the_method_keeps_only_the_keys_it_uses(method):
    back = scheduler_plan.plan_from_form(posted_entry(FULL_ENTRY, ("action_trigger.0", "cron"),
                                                      ("action.0.method", method)))
    entry = back["action"][0]

    for key in scheduler_plan.ACTION_METHOD_DROP[method]:
        assert key not in entry, (method, key)
    for key in scheduler_plan.ACTION_METHOD_KEYS[method]:
        assert key in entry, (method, key)
    assert set(entry) - KEPT_ALWAYS - {"cron"} == set(scheduler_plan.ACTION_METHOD_KEYS[method])
    # the conditions and the carried flags survive every method
    assert entry["populated"] is False and entry["clear_maintenance"] is True
    if method in ("restart", "rotate"):
        assert entry["shutdown"] is True, "the two methods that reload in the same process keep it"


#: the methods that (re)start a mission — the bot sets the server password right after loading one.
PASSWORD_METHODS = ("restart", "rotate", "load")


@pytest.mark.parametrize("method", scheduler_plan.ACTION_METHODS)
def test_the_password_belongs_to_the_methods_that_start_a_mission(method):
    """Exhaustive over the five methods: the key survives and the control is drawn for the same three."""
    back = scheduler_plan.plan_from_form(posted_entry(FULL_ENTRY, ("action_trigger.0", "cron"),
                                                      ("action.0.method", method)))
    entry = back["action"][0]

    assert ("password" in entry) is (method in PASSWORD_METHODS), method
    assert ("password" in scheduler_plan.ACTION_METHOD_CONTROLS[method]) is (
        method in PASSWORD_METHODS), method


def test_a_load_states_one_of_its_two_mission_keys():
    """The pick radio decides which key is written; the other leaves the plan and is pruned."""
    both = posted_entry(FULL_ENTRY, ("action_trigger.0", "cron"), ("action.0.method", "load"))
    by_id = scheduler_plan.plan_from_form(both + [("action_pick.0", "mission_id")])["action"][0]
    by_file = scheduler_plan.plan_from_form(both + [("action_pick.0", "mission_file")])["action"][0]

    assert by_id["mission_id"] == 3 and "mission_file" not in by_id
    assert by_file["mission_file"] == "Missions/night.miz" and "mission_id" not in by_file


def test_a_form_without_the_choice_carriers_drops_nothing():
    """Steps 4 and 5 carry the plan as plain values: no trigger radio, no row, no method change."""
    # a TWO-item preset list on purpose: a one-item list travels as its single value through a form
    entry = {"times": ["04:00"], "method": "restart", "settings": ["NoMods"],
             "presets": ["config/a.yaml", "config/b.yaml"], "run_extensions": True,
             "use_orig": False, "shutdown": True, "password": "hunter2", "populated": False,
             "clear_maintenance": True}
    carried = [(field["name"], field["value"])
               for field in scheduler_plan.form_fields({"action": [dict(entry)]})
               + scheduler_plan.shape_markers({"action": [dict(entry)]})]
    back = scheduler_plan.plan_from_form(carried)

    assert back["action"][0] == entry, "the review step's own form must change nothing"


def test_the_keys_a_switch_removes_are_prune_rows_in_the_review():
    """The review reads the SAME override the save writes: what leaves the plan is a deletion."""
    block = {"action": [dict(FULL_ENTRY)]}
    plan = scheduler_plan.plan_from_form(posted_entry(FULL_ENTRY, ("action_trigger.0", "times"),
                                                      ("action.0.method", "restart")))
    review = scheduler_plan.review_view({}, block, plan)

    rows = [row for row in review["prune"] if row["key"] == "action.0"]
    assert rows, review["prune"]
    # a restart sets the password, so it is NOT a prune row here; every key it does not use is
    assert set(rows[0]["subkeys"]) == {
        "cron", "mission_time", "max_mission_time", "real_time", "idle_time",
        "mission_id", "mission_file", "no_reload"}
    assert review["changed"] is True
    assert rows[0]["entry"] == 1


def test_a_stop_prunes_the_password_it_never_sets():
    """stop and shutdown load no mission, so a carried password is a deletion, not a value."""
    block = {"action": [dict(FULL_ENTRY)]}
    for method in ("stop", "shutdown"):
        plan = scheduler_plan.plan_from_form(posted_entry(FULL_ENTRY, ("action_trigger.0", "times"),
                                                          ("action.0.method", method)))
        review = scheduler_plan.review_view({}, block, plan)
        rows = [row for row in review["prune"] if row["key"] == "action.0"]
        assert rows, (method, review["prune"])
        assert "password" in rows[0]["subkeys"], (method, rows[0]["subkeys"])
