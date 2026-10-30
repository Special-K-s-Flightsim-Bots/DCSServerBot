"""The Scheduler tab's READ model — pure helpers, one job each.

The tab shows the plan the bot APPLIES: the plugin's own merged ``DEFAULT`` + server block
(``core/plugin.py`` ``get_config``), never a second parse of ``scheduler.yaml``. What these helpers
add is the one thing that merge cannot carry — PROVENANCE: which of the element keys on the page came
from the server's own block and which are inherited from ``DEFAULT``.

Nothing here reads a file, a request or the bot: the page hands in the effective plan, the raw block
and the ``DEFAULT`` block, and gets back plain dicts a template renders. That is what makes the
shapes testable without an install.

Two lookups are deliberately the BOT's, not this module's invention:

* the block path follows ``CPlugin.get_base_config`` — ``self.locals.get(node.name, self.locals)
  .get(instance.name)`` — so a node-nested file and a flat file both resolve the way the plugin reads
  them;
* the schedule's ``timezone`` may sit INSIDE the ``schedule`` block (the sample file ships it there),
  so it is skipped when the week grid is built.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from core.utils import configwrite
from core.utils.helper import deep_merge
from core.utils.presets import PRESET_PAIR_SEPARATOR, preset_option_value
from croniter import CroniterBadCronError, CroniterBadDateError, CroniterError, croniter

from . import i18n, readmodels

__all__ = [
    "DEFAULT_BLOCK", "RESERVED_SECTIONS", "ELEMENT_KEYS", "KEY_LABELS",
    "OWN", "INHERITED", "UNSET", "WEEKDAYS", "NOT_SCHEDULED",
    "block_path", "default_block", "raw_block", "block_at", "file_plan", "key_states",
    "schedule_rows", "legend", "action_rows", "warn_view", "startup_rows", "command_rows",
    "plan_view", "has_plan",
    # the wizard: steps, the form's own language, cron and times
    "SCHEDULER_LANDING", "SCHEDULER_STEPS", "STEP_KEYS", "WIZARD_KEYS",
    "WEEK_START", "WEEK_END", "WEEK_DAYS", "WEEK_ROW_FIELDS", "WEEK_ADD_FIELD",
    "WEEK_REMOVE_FIELD", "DEFAULT_PATTERN", "MIDNIGHT", "added_week_row",
    "COMMAND_KEYS", "ACTION_KEYS", "FORM_FIELD_BOUND", "SCHEDULER_REVIEW_STEP",
    "ACTION_ADD_FIELD", "ACTION_REMOVE_FIELD", "ACTION_ADD_LABEL", "ACTION_REMOVE_LABEL",
    "RENDERED_ACTION_KEYS", "ACTION_TRIGGER_KEYS", "NEW_ENTRY_METHOD",
    "ACTION_NO_TRIGGER", "ACTION_REMOVE_NOT_A_POSITION", "ACTION_REMOVE_NO_SUCH_ENTRY",
    "action_row_refusal",
    # step 3's ONE trigger, its time rows, and the keys the chosen method keeps
    "ACTION_TRIGGER_FIELD", "ACTION_TRIGGERS", "ACTION_TRIGGER_KEYS_BY_KIND",
    "ACTION_TRIGGER_LABELS", "ACTION_TRIGGER_HINTS", "ACTION_TRIGGER_QUESTION",
    "ACTION_TRIGGER_LEAD", "ACTION_TRIGGER_DROPPED",
    "ACTION_LEGACY_TITLE", "ACTION_LEGACY_LEAD", "ACTION_LEGACY_KEPT",
    "ACTION_TIME_FIELD", "ACTION_TIME_ADD_FIELD", "ACTION_TIME_REMOVE_FIELD",
    "ACTION_METHODS", "ACTION_METHOD_KEYS", "ACTION_METHOD_DROP", "ACTION_METHOD_CONTROLS",
    "ACTION_METHOD_LEAD", "ACTION_PICK_FIELD", "ACTION_PICK_LEAD",
    "ACTION_PASSWORD_LABEL", "ACTION_PASSWORD_HINT",
    "entry_trigger", "entry_legacy", "entry_pick", "posted_time_rows",
    # step 4's own language: the ladder rows, its row ops, the placeholder chips and the preview
    "WARN_TIME", "WARN_TEXT", "WARN_ROW_FIELDS", "WARN_ADD_FIELD", "WARN_REMOVE_FIELD",
    "WARN_PLACEHOLDERS", "WARN_EXAMPLES",
    "WARN_SECONDS_NOT_A_NUMBER", "WARN_DUPLICATE_SECONDS", "WARN_ROW_WITHOUT_SECONDS",
    "WARN_WRITES_MAP", "WARN_WRITES_LIST", "WARN_NO_LADDER",
    "warn_preview", "warn_rows_from_pairs", "warn_times_from_rows",
    "CRON_NOT_AN_EXPRESSION", "CRON_NEVER_FIRES", "CRON_EXTRA_FIELD", "TIME_NOT_A_TIME",
    "UNKNOWN_TIMEZONE", "WEEK_START_NOT_BEFORE_END", "WEEK_RANGE_OVERLAP", "WEEK_BAD_DAYS",
    "MISSION_LIST_UNAVAILABLE", "MISSION_FILES_UNAVAILABLE", "PRESET_LIST_UNAVAILABLE",
    "PICK_NOT_IN_FILES",
    "RENDERED_STARTUP_KEYS",
    # step 2's own language: the one switch that makes the step state anything, and the two
    # mutually exclusive mission picks
    "STARTUP_FLAG", "STARTUP_FLAG_ON", "STARTUP_FLAG_OFF", "STARTUP_ON_LABEL",
    "STARTUP_OFF_WHAT", "STARTUP_OFF_DEED",
    "STARTUP_PICK", "STARTUP_PICK_ID", "STARTUP_PICK_FILE", "STARTUP_PICK_KINDS",
    "STARTUP_PICK_LABELS", "STARTUP_PICK_LEAD",
    "startup_switch", "startup_pick_kind",
    "normalise_step", "next_fires", "time_fires", "times_rows",
    "timezone_options", "form_fields", "shape_markers", "carried_fields", "plan_from_form",
    "wizard_plan", "compose_range", "schedule_from_week", "week_rows_from_pairs",
    "mission_id_pick", "mission_file_pick", "preset_pick", "startup_picks", "wizard_view",
    # the write half: the plan keys this tab may state, the per-key override and its review
    "NEVER_WRITTEN", "PRUNABLE_ACTION_KEYS", "STARTUP_KEYS", "WARN_KEYS", "WARN_COUNTDOWN_KEYS",
    "REFUSED_STARTUP_DELAY", "REFUSED_DEPRECATED_KEY", "REFUSED_FOREIGN_KEY",
    "posted_key_refusal", "block_at", "minimal_override", "apply_override", "review_view",
    "PRUNE_LABELS", "prune_rows",
    # the block as YAML: what a save writes, and what an Expert Mode post states instead
    "BLOCK_YAML_FIELD", "EXPERT_NOT_YAML", "EXPERT_NOT_A_MAPPING", "EXPERT_FOREIGN_KEY",
    "expert_block", "apply_block", "saved_block", "block_yaml",
    # the write guard: a document that is not a mapping cannot carry a block
    "DEFAULT_FILE_NAME", "NOT_A_MAPPING", "require_mapping",
]

#: the shared block's key — the plugin's ``DEFAULT_TAG``, spelled once here for the page's provenance
#: read (the bot's own constant lives in ``core``; the literal is repeated rather than imported, so
#: importing this module does not pull the bot in).
DEFAULT_BLOCK = "DEFAULT"

#: the file's non-server top-level sections. ``commands`` / ``chat_commands`` carry their own schemas
#: and are NOT per-server element blocks, so nothing on the landing may present them as a plan.
RESERVED_SECTIONS = (DEFAULT_BLOCK, "commands", "chat_commands")

#: every key of the schema's ``element_schema``, in the order the landing lists them —
#: ``scheduler_schema.yaml``'s per-server block, exhaustively.
ELEMENT_KEYS: tuple[str, ...] = (
    "timezone", "startup_delay", "warn", "schedule", "startup", "action",
    "onSimulationStart", "onSimulationStop", "onMissionEnd", "onShutdown",
)

KEY_LABELS: dict[str, str] = {
    "timezone": i18n._("Timezone"),
    "startup_delay": i18n._("Startup delay"),
    "warn": i18n._("Warnings before a restart"),
    "schedule": i18n._("Schedule (the week)"),
    "startup": i18n._("Startup"),
    "action": i18n._("Scheduled actions"),
    "onSimulationStart": i18n._("On mission start"),
    "onSimulationStop": i18n._("On mission stop"),
    "onMissionEnd": i18n._("On mission end"),
    "onShutdown": i18n._("On server shutdown"),
}

#: the three answers ``key_states`` gives per key: the server's own block carries it, ``DEFAULT``
#: carries it instead, or neither declares it.
OWN = "own"
INHERITED = "inherited"
UNSET = "unset"

#: the week grid's day headers, Monday first — the letter order the file's 7-character pattern uses.
WEEKDAYS: tuple[str, ...] = (i18n._("Mon"), i18n._("Tue"), i18n._("Wed"), i18n._("Thu"),
                             i18n._("Fri"), i18n._("Sat"), i18n._("Sun"))

#: what a cell says for a letter, and for a day the pattern does not cover.
_CELLS: dict[str, tuple[str, str]] = {
    "Y": ("y", i18n._("must run")),
    "N": ("n", i18n._("must be off")),
    "P": ("p", i18n._("stays up until the last player leaves")),
}
NOT_SCHEDULED = "not scheduled"
_CELL_UNSET = ("x", i18n._(NOT_SCHEDULED))

#: an action's trigger keys and the sentence each one renders as. The value is interpolated by the
#: template's guarded ``say``, so a translation may move the placeholder.
_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("cron", i18n._("on the cron schedule {value}")),
    ("times", i18n._("at {value}")),
    ("mission_time", i18n._("after {value} minutes of mission time")),
    ("max_mission_time", i18n._("at most {value} minutes of mission time")),
    ("real_time", i18n._("after {value} minutes of server runtime")),
    ("idle_time", i18n._("after {value} minutes with nobody on the server")),
)

#: what each ``method`` does, in the operator's words (the schema's own five values).
_METHODS: dict[str, str] = {
    "restart": i18n._("Restart the mission"),
    "rotate": i18n._("Rotate to the next mission"),
    "load": i18n._("Load a mission"),
    "stop": i18n._("Stop the server"),
    "shutdown": i18n._("Shut the computer down"),
}

#: the ``action_map`` flags that change WHEN an entry applies, each with its own sentence.
_CONDITIONS: tuple[tuple[str, bool, str], ...] = (
    ("populated", False, i18n._("only when nobody is flying")),
    ("mission_end", True, i18n._("only when the mission ends")),
    ("coalition_win", True, i18n._("only when a coalition wins")),
)

#: the ``action_map`` flags that change WHAT else happens, last because they are secondary.
_EXTRAS: tuple[tuple[str, str], ...] = (
    ("shutdown", i18n._("shut the computer down afterwards")),
    ("run_extensions", i18n._("run the extensions")),
    ("use_orig", i18n._("use the original mission file")),
    ("no_reload", i18n._("do not reload the mission")),
)

#: the ``startup`` block's keys, in the order the summary lists them. ``password`` is absent on
#: purpose: it belongs to an ACTION entry, a startup block that states one keeps it as a carried value
#: (never pruned by this tab), and it is not printed as a summary row.
_STARTUP_KEYS: tuple[tuple[str, str], ...] = (
    ("mission_id", i18n._("Mission (id)")),
    ("mission_file", i18n._("Mission (file)")),
    ("presets", i18n._("Presets")),
    ("settings", i18n._("Settings to apply")),
    ("clear_maintenance", i18n._("Clear maintenance on start")),
)


# ------------------------------------------------------------------------------ the block's path

def block_path(document, node_name, instance_name) -> tuple[str, ...]:
    """The key path the PLUGIN reads for one server: ``(node, instance)`` or ``(instance,)``.

    ``CPlugin.get_base_config`` asks for ``locals[node.name]`` first and falls back to the whole
    document, so a node-nested scheduler file resolves through the two-level path and a flat one
    through the single key. An empty tuple means the file carries no block for this server — the
    server inherits everything.
    """
    if not isinstance(document, dict):
        return ()
    node = readmodels.text(node_name)
    instance = readmodels.text(instance_name)
    if not instance:
        return ()
    nested = document.get(node) if node else None
    if isinstance(nested, dict) and isinstance(nested.get(instance), dict):
        return (node, instance)
    if isinstance(document.get(instance), dict):
        return (instance,)
    return ()


def raw_block(document, node_name, instance_name) -> dict:
    """The server's OWN block as the file states it, or ``{}`` when it has none."""
    path = block_path(document, node_name, instance_name)
    if not path:
        return {}
    block = document
    for key in path:
        block = block[key]
    return dict(block)


def default_block(document) -> dict:
    """The shared ``DEFAULT`` block as the file states it, or ``{}`` when the file has none."""
    if isinstance(document, dict) and isinstance(document.get(DEFAULT_BLOCK), dict):
        return dict(document[DEFAULT_BLOCK])
    return {}


def key_states(raw, default) -> list[dict]:
    """One row per ``element_schema`` key: its label and where its value comes from.

    ``inherited`` is the fact the landing exists to show — the server's block is silent and the value
    arrives from ``DEFAULT``. A key neither block declares is ``unset``: no chip claims a source for a
    value nobody wrote. Only the two FILE blocks are asked, so the answer is provenance, never a
    comparison of the merged plan against itself.
    """
    mine = raw if isinstance(raw, dict) else {}
    shared = default if isinstance(default, dict) else {}
    rows: list[dict] = []
    for key in ELEMENT_KEYS:
        if key in mine:
            state = OWN
        elif key in shared:
            state = INHERITED
        else:
            # in neither block — including a value the plugin itself defaults into the merge, which
            # the FILE did not state either
            state = UNSET
        rows.append({"key": key, "label": KEY_LABELS[key], "state": state})
    return rows


# --------------------------------------------------------------------------- the schedule (week)

def schedule_rows(schedule) -> list[dict]:
    """The week grid: one row per time range, one cell per day, exactly as the file states it.

    The range keys are the file's own unit (``00-12``), and a day the 7-character pattern does not
    cover is drawn as "not scheduled" rather than guessed into a state. The block's own ``timezone``
    key is not a range and is skipped here — it is read at element level.
    """
    if not isinstance(schedule, dict):
        return []
    rows: list[dict] = []
    for period, pattern in schedule.items():
        name = readmodels.text(period)
        if not name or name == "timezone":
            continue
        days = readmodels.text(pattern)
        cells: list[dict] = []
        for index, day in enumerate(WEEKDAYS):
            letter = days[index] if index < len(days) else ""
            state, word = _CELLS.get(letter, _CELL_UNSET)
            cells.append({"day": day, "letter": letter or "—", "state": state, "word": word})
        rows.append({"period": name, "cells": cells})
    return rows


def legend() -> list[dict]:
    """The grid's key: every letter a cell can carry, and what it means."""
    return [{"letter": letter, "state": state, "word": word}
            for letter, (state, word) in _CELLS.items()] + [
        {"letter": "—", "state": "x", "word": i18n._(NOT_SCHEDULED)}]


# --------------------------------------------------------------------------- the action entries

def _trigger(entry: dict) -> dict | None:
    """The first trigger key the entry carries, as a sentence record — or ``None``."""
    for key, sentence in _TRIGGERS:
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, (list, tuple)):
            value = ", ".join(readmodels.text(item) for item in value if readmodels.text(item))
        else:
            value = readmodels.text(value)
        if not value:
            continue
        return {"msgid": sentence, "params": {"value": value}}
    return None


def _method(entry: dict) -> str:
    """The entry's method as its operator sentence, or the raw value when it is not a known one."""
    method = readmodels.text(entry.get("method"))
    if not method:
        return ""
    return _METHODS.get(method, method)


def _conditions(entry: dict) -> list[str]:
    """Every flag that narrows WHEN the entry applies, in the schema's own spelling of it."""
    found: list[str] = []
    for key, wanted, sentence in _CONDITIONS:
        if key in entry and bool(entry[key]) is wanted:
            found.append(sentence)
    return found


def _extras(entry: dict) -> list[str]:
    """Every flag that adds something to WHAT the entry does."""
    return [sentence for key, sentence in _EXTRAS if entry.get(key)]


def action_rows(action) -> list[dict]:
    """One row per entry, preserving the SHAPE the file holds.

    ``action`` is either one ``action_map`` or a sequence of them (the schema's ``seq_or_map``), and
    the landing renders one card per entry either way — a file that holds one map reads as one entry,
    never as a one-item list, and nothing here rewrites the shape.
    """
    if isinstance(action, dict):
        entries = [action]
    elif isinstance(action, (list, tuple)):
        entries = [entry for entry in action if isinstance(entry, dict)]
    else:
        return []
    rows: list[dict] = []
    for entry in entries:
        rows.append({
            "trigger": _trigger(entry),
            "method": _method(entry),
            "conditions": _conditions(entry),
            "extras": _extras(entry),
        })
    return rows


# ------------------------------------------------------------------------------ the warn ladder

def warn_view(warn) -> dict | None:
    """The warning ladder: the shared message, the per-time texts, the sound and the countdown.

    ``times`` is the schema's ``seq_or_map``: a list of seconds, or a map of ``seconds -> message``
    (which is how one warn time gets its own text). Both shapes render as one ladder of seconds.
    """
    if not isinstance(warn, dict) or not warn:
        return None
    times = warn.get("times")
    ladder: list[dict] = []
    if isinstance(times, dict):
        for seconds, message in times.items():
            ladder.append({"seconds": readmodels.text(seconds),
                           "message": readmodels.text(message)})
    elif isinstance(times, (list, tuple)):
        for seconds in times:
            ladder.append({"seconds": readmodels.text(seconds), "message": ""})
    countdown = warn.get("countdown")
    return {
        "message": readmodels.text(warn.get("message")),
        "sound": readmodels.text(warn.get("sound")),
        "ladder": ladder,
        "countdown": {
            "time": readmodels.text(countdown.get("time")),
            "message": readmodels.text(countdown.get("message")),
        } if isinstance(countdown, dict) else None,
    }


# --------------------------------------------------------------------------------- the startup

def _startup_value(value) -> str:
    """One startup row's value as text — a list is joined, a flag reads as yes/no."""
    if isinstance(value, bool):
        return i18n._("yes") if value else i18n._("no")
    if isinstance(value, (list, tuple)):
        return ", ".join(readmodels.text(item) for item in value if readmodels.text(item))
    if isinstance(value, dict):
        return ", ".join(readmodels.text(item) for item in value)
    return readmodels.text(value)


def startup_rows(startup) -> list[dict]:
    """The ``startup`` block as summary rows: only the keys the file states, in the schema's order."""
    if not isinstance(startup, dict):
        return []
    rows: list[dict] = []
    for key, label in _STARTUP_KEYS:
        if key not in startup:
            continue
        rows.append({"key": key, "label": label, "value": _startup_value(startup[key])})
    return rows


# -------------------------------------------------------------------------- the event commands

def command_rows(plan) -> list[dict]:
    """The four ``on*`` keys as rows: the command and, when the file states it, the sleep.

    A command is the schema's ``str_or_map``: a bare ``load:…`` string, or a map carrying ``command``
    and ``sleep``. A key the plan does not carry renders as "not set" — the row is kept so the four
    events are always listed, in the same order.
    """
    plan = plan if isinstance(plan, dict) else {}
    rows: list[dict] = []
    for key in ("onSimulationStart", "onSimulationStop", "onMissionEnd", "onShutdown"):
        value = plan.get(key)
        command = sleep = ""
        if isinstance(value, dict):
            command = readmodels.text(value.get("command"))
            sleep = readmodels.text(value.get("sleep"))
        else:
            command = readmodels.text(value)
        rows.append({"key": key, "label": KEY_LABELS[key], "command": command, "sleep": sleep})
    return rows


# ------------------------------------------------------------------------------ the landing view

def has_plan(effective) -> bool:
    """Whether the merged plan carries anything at all — the landing's own empty test."""
    return isinstance(effective, dict) and bool(effective)


def plan_view(effective, raw, default) -> dict:
    """Everything the landing renders, from the two reads and nothing else.

    ``effective`` is the plugin's merged plan (what the bot applies), ``raw`` the server's own block
    and ``default`` the shared one — the last two are read from the FILE and exist only to say where
    each key's value came from.
    """
    plan = effective if isinstance(effective, dict) else {}
    states = key_states(raw, default)
    present = [row for row in states if row["state"] != UNSET]
    return {
        "timezone": readmodels.text(plan.get("timezone")),
        # NOT ``keys``: a dict's ``keys`` attribute is a METHOD, and a template reading
        # ``row.keys`` would get the builtin instead of this list.
        "key_rows": states,
        "all_inherited": bool(present) and all(row["state"] == INHERITED for row in present),
        "inherited_count": sum(1 for row in present if row["state"] == INHERITED),
        "schedule": schedule_rows(plan.get("schedule")),
        "actions": action_rows(plan.get("action")),
        "warn": warn_view(plan.get("warn")),
        "startup": startup_rows(plan.get("startup")),
        "commands": command_rows(plan),
    }


# ===================================================================================== the wizard
# The wizard is ONE page whose STEP lives in the URL (``?tab=scheduler&step=N``), so a refresh keeps
# it, the back button works and the whole thing works with JavaScript off — the same rule every other
# console surface follows. Each content step is a GET form, so submitting one puts every plan value
# in the query string and the next step reads them back; the review step is the wizard's ONE POST and
# writes the plan (the write half is below).
#
# WHY A URL MAY CARRY THE WHOLE PLAN: a plan flattens to roughly 60 controls and a URL of about
# 1.5 KB. Starlette's form parser refuses a body past 1000 fields (the bound ``session.py``'s CSRF
# dependency declares for form routes), and a request line of this size is far below it — that bound
# is what makes state-in-the-URL safe here rather than merely convenient.

#: the landing (the plan as it stands) — the absence of a step, spelled as a value so one compare
#: decides between the landing and a step.
SCHEDULER_LANDING = "0"

#: the wizard's steps, in order. Membership is the whole validation: a step not listed here falls
#: back to the first one, exactly as an unknown tab falls back to the default tab.
SCHEDULER_STEPS: tuple[str, ...] = ("1", "2", "3", "4", "5")

#: the step that only reviews: it renders the override and posts it, so it OWNS no plan key and
#: carries every one of them.
SCHEDULER_REVIEW_STEP = "5"

#: the four ``on*`` event keys — step 3 owns them: they say what runs when an entry's events fire,
#: and they are written exactly as they are read.
COMMAND_KEYS: tuple[str, ...] = ("onSimulationStart", "onSimulationStop", "onMissionEnd", "onShutdown")

#: every ``action_map`` key the schema declares (``scheduler_schema.yaml`` ``action_map``), in the
#: schema's own order. The wizard renders all of them and carries the rest verbatim.
ACTION_KEYS: tuple[str, ...] = (
    "clear_maintenance", "local_times", "utc_times", "times", "cron", "mission_time",
    "max_mission_time", "real_time", "idle_time", "method", "mission_id", "mission_file",
    "populated", "mission_end", "coalition_win", "shutdown", "run_extensions", "no_reload",
    "use_orig", "presets", "password", "settings",
)

#: the ``action_map`` keys the schema still declares but the scheduler reads as deprecated. They are
#: refused WHERE THEY OCCUR (inside an action entry) and are never written; an existing occurrence in
#: the file is left alone because the mutate touches only the keys the plan names.
NEVER_WRITTEN: tuple[str, ...] = ("local_times", "utc_times")

#: the ``action_map`` keys step 3 draws as CONTROLS: every schema key except the deprecated pair,
#: which a form may not post, ``shutdown``, which is not offered any more (it means "stop the DCS
#: server process first", not "power the machine off"), and ``presets``, the configured file path a
#: bare pick name falls back to — the pick writes ``settings`` and never rewrites that path. A key
#: outside this set is carried as a hidden input exactly as the file states it, so no save can lose it.
RENDERED_ACTION_KEYS: tuple[str, ...] = tuple(
    key for key in ACTION_KEYS
    if key not in NEVER_WRITTEN and key not in ("shutdown", "presets"))

#: the keys that make an entry fire at all. An entry that states none of them is written but never
#: runs, which is what the step says beside a new entry.
ACTION_TRIGGER_KEYS: tuple[str, ...] = ("times", "cron", "mission_time", "max_mission_time",
                                        "real_time", "idle_time")

#: the method a NEW entry starts with. Nothing else is written into the plan: the trigger is the
#: operator's to choose, and the one the card shows selected is only a default (never a key).
NEW_ENTRY_METHOD = "restart"

#: the trigger a NEW entry opens with — selected on the page, but NOT stated in the plan until the
#: operator enters a time. An entry that already carries a trigger keeps its own.
ACTION_DEFAULT_TRIGGER = "times"

#: the entry row ops the step's GET form posts — ``addentry=1`` appends one entry and
#: ``removeentry=<index>`` drops that entry. Both are submit buttons of the same form, so both work
#: with the script off, and each removal names the entry it removes.
ACTION_ADD_FIELD = "addentry"
ACTION_REMOVE_FIELD = "removeentry"

ACTION_ADD_LABEL = i18n._("Add an entry")
ACTION_REMOVE_LABEL = i18n._("Remove entry %(number)s")

# ------------------------------------------------------------------- step 3: ONE trigger, chosen first
# An entry states AT MOST ONE trigger. The five are read in the BOT's own precedence order
# (``plugins/scheduler/commands.py``, ``_match_action``): the first trigger key present decides, so a
# file that carries two is shown with the one the bot keeps and the other leaves on the next save.

#: the radio group's field. The name carries the entry index (``action_trigger.<n>``) and is an AUX
#: carrier: it states the operator's CHOICE, never a plan value, so no save may read it as one.
ACTION_TRIGGER_FIELD = "action_trigger"

#: the five triggers, in the bot's precedence order. The order is both the radio group's order and
#: the order a file carrying several triggers is read in.
ACTION_TRIGGERS: tuple[str, ...] = ("times", "mission_time", "real_time", "idle_time", "cron")

#: the entry keys each trigger states, exhaustively. ``max_mission_time`` belongs to the mission-time
#: trigger: the hard cap only means anything beside a mission time.
ACTION_TRIGGER_KEYS_BY_KIND: dict[str, tuple[str, ...]] = {
    "times": ("times",),
    "mission_time": ("mission_time", "max_mission_time"),
    "real_time": ("real_time",),
    "idle_time": ("idle_time",),
    "cron": ("cron",),
}

ACTION_TRIGGER_LABELS: dict[str, str] = {
    "times": i18n._("Times of day"),
    "mission_time": i18n._("Minutes of mission time"),
    "real_time": i18n._("Minutes of server runtime"),
    "idle_time": i18n._("Minutes with nobody on"),
    "cron": i18n._("Cron expression"),
}

#: one sentence per trigger, said under the radio group while that trigger is the chosen one.
ACTION_TRIGGER_HINTS: dict[str, str] = {
    "times": i18n._("One row per time of day, read in this block's timezone. A row with no time "
                    "states nothing."),
    "mission_time": i18n._("Applied once the mission has run this many minutes."),
    "real_time": i18n._("Applied once the server has run this many minutes."),
    "idle_time": i18n._("Applied after this many minutes with nobody on the server."),
    "cron": i18n._("5 to 7 fields. The next three fires are shown under the field."),
}

ACTION_TRIGGER_QUESTION = i18n._("When does this run?")
ACTION_TRIGGER_LEAD = i18n._("An entry states ONE of these. Choosing another removes the others "
                             "from the plan, and the save deletes them from the file.")
ACTION_TRIGGER_DROPPED = i18n._("This entry states more than one trigger. Only the one shown is "
                                "kept: the bot reads times of day first, then mission time, then "
                                "server runtime, then idle time, and cron last — so the others are "
                                "removed from the plan and deleted from the file on save.")

#: an entry whose ONLY trigger is a deprecated key (``local_times`` / ``utc_times``): the card draws
#: its trigger block READ-ONLY. The bot still reads that key, so a trigger chosen here would be a
#: second one the old key overrides — an edit that changes nothing.
ACTION_LEGACY_TITLE = i18n._("This entry's trigger is a deprecated key")
ACTION_LEGACY_LEAD = i18n._("This key is deprecated and this tab does not write it, so this entry "
                            "cannot be retriggered here.")
ACTION_LEGACY_KEPT = i18n._("Everything else about this entry stays editable — its method, its "
                            "conditions and its mission picks — and a save leaves the key exactly "
                            "as the file states it.")

#: the times-of-day control is a ROW LIST, the language step 1's week editor and step 4's ladder
#: already use: one time input per row, named ``action_time.<entry>.<row>``, plus the two submits
#: ``addtime=<entry>`` and ``removetime=<entry>.<row>``. A row's name is never its value.
ACTION_TIME_FIELD = "action_time"
ACTION_TIME_ADD_FIELD = "addtime"
ACTION_TIME_REMOVE_FIELD = "removetime"

#: the five methods, and — exhaustively — the entry keys each one uses. ``restart`` and ``rotate``
#: take the presets, the two extension flags and ``password``, ``load`` adds the mission (one of the
#: two keys) with ``no_reload`` and ``password``, and ``stop`` / ``shutdown`` take none of them.
#: ``password`` belongs to the three methods that (re)start a mission: the bot sets it right after the
#: mission is loaded, so a method that loads nothing has no password to set.
ACTION_METHODS: tuple[str, ...] = ("restart", "rotate", "load", "stop", "shutdown")

ACTION_METHOD_KEYS: dict[str, tuple[str, ...]] = {
    "restart": ("settings", "presets", "run_extensions", "use_orig", "password"),
    "rotate": ("settings", "presets", "run_extensions", "use_orig", "password"),
    "load": ("mission_id", "mission_file", "settings", "presets", "run_extensions", "use_orig",
             "no_reload", "password"),
    "stop": (),
    "shutdown": (),
}

#: the keys a method does NOT use. They leave the plan, so the save's prune deletes them from the
#: file — stated here rather than derived in the template, so the four answers are readable at once.
_METHOD_OWNED: tuple[str, ...] = ("mission_id", "mission_file", "settings", "presets",
                                  "run_extensions", "no_reload", "use_orig", "password")
ACTION_METHOD_DROP: dict[str, tuple[str, ...]] = {
    method: tuple(key for key in _METHOD_OWNED if key not in ACTION_METHOD_KEYS[method])
    for method in ACTION_METHODS
}

#: which CONTROLS a method draws: ``mission`` (the two mutually exclusive pickers), ``presets`` (the
#: closed preset pick that writes ``settings``), ``extras`` (run_extensions, use_orig), ``load_only``
#: (no_reload) and ``password`` (the one password field). The trigger, the two "when nobody is
#: flying" radios and the conditions are drawn for every method.
ACTION_METHOD_CONTROLS: dict[str, tuple[str, ...]] = {
    "restart": ("presets", "extras", "password"),
    "rotate": ("presets", "extras", "password"),
    "load": ("mission", "presets", "extras", "load_only", "password"),
    "stop": (),
    "shutdown": (),
}

#: the password control's label and its one-sentence hint. The sentence says WHEN the value takes
#: effect (when the mission starts), never "restart password" — the value is the server's joiner
#: password, and the bot sets it after the mission is loaded.
ACTION_PASSWORD_LABEL = i18n._("Server password")
ACTION_PASSWORD_HINT = i18n._("The server password is set when the mission starts.")

#: the one leaf read and carried VERBATIM. A password is compared byte for byte, so it is never
#: trimmed, re-encoded or coerced on its way through a form: a padded value stays padded.
ACTION_PASSWORD_KEY = "password"

ACTION_METHOD_LEAD = i18n._("The options below follow this choice: a load names its mission, a "
                            "restart or rotate changes the presets, and stop or shutdown names no "
                            "mission at all.")

#: the load's two mission picks are mutually exclusive, exactly as step 2's are: one radio names the
#: one the entry states, the other picker is rendered ``disabled`` so it posts nothing, and the save
#: prunes the key the other pick carried.
ACTION_PICK_FIELD = "action_pick"
ACTION_PICK_LEAD = i18n._("A load names its mission one of the two ways, never both: the save "
                          "removes the value the other key carried.")

# ------------------------------------------------------------------------ step 4: the warning ladder
# A ladder row is TWO controls — a whole number of seconds and a text — and the plan's ``times``
# shape is COMPOSED from them. The row fields carry their own names (``warn_time.<n>``), like step 1's
# week rows, because a row is never named by its seconds: the operator types the number.
WARN_TIME = "warn_time"
WARN_TEXT = "warn_text"
WARN_ROW_FIELDS: tuple[str, ...] = (WARN_TIME, WARN_TEXT)

#: the row ops the step's GET form posts. ``addrow`` / ``removerow`` are the SAME two names step 1
#: uses and mean the same thing; a form is only read as a ladder form when it posts a row field, so
#: the two steps can never read each other's op.
WARN_ADD_FIELD = "addrow"
WARN_REMOVE_FIELD = "removerow"

#: the three names the bot substitutes in a warn text, each with one sentence saying what it puts
#: there. The bot fills them with ``str.format(item=..., what=..., when=...)``, which is also the
#: ONE rule :func:`warn_preview` applies — a preview must never be a second substitution.
WARN_PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("item", i18n._("either server or mission, whichever the change touches")),
    ("what", i18n._("what happens: restart, shutdown or rotate")),
    ("when", i18n._("how long is left, such as 10 minutes")),
)

#: the example words the preview substitutes, in the reader's language.
WARN_EXAMPLES: dict[str, str] = {
    "item": i18n._("server"),
    "what": i18n._("restart"),
    "when": i18n._("10 minutes"),
}

#: step 4's refusals, one sentence each. A refused row is never written, and a refused row op leaves
#: every row as it was.
WARN_SECONDS_NOT_A_NUMBER = i18n._("A warning time must be a whole number of seconds greater than "
                                   "zero, so this row is not written.")
WARN_DUPLICATE_SECONDS = i18n._("Another row already states these seconds, and a ladder holds one "
                                "text per moment, so this row is not written.")
WARN_ROW_WITHOUT_SECONDS = i18n._("This row states a text but no seconds, so there is no moment to "
                                  "show it at: state the seconds or clear the row.")

#: which shape the review step writes, said on the step so the operator is not surprised by it.
WARN_WRITES_MAP = i18n._("The review step writes this ladder as a map: each number of seconds to "
                         "the text shown at that moment.")
WARN_WRITES_LIST = i18n._("The review step writes this ladder as a list of seconds, with the shared "
                          "message at every moment.")
WARN_NO_LADDER = i18n._("This ladder states nothing, so the bot shows no warning before the change.")


#: which TOP-LEVEL plan keys each step OWNS (renders as a control). Everything else is carried in
#: hidden inputs, so a step never has to know about a block it does not edit. The steps read in the
#: order the plan is applied in: when it runs, how it starts, what it does, what it announces.
#: Step 2 owns the ``startup`` block ALONE — the four event commands ride with the actions, because
#: they say what an entry's events run.
STEP_KEYS: dict[str, tuple[str, ...]] = {
    "1": ("timezone", "schedule"),
    "2": ("startup",),
    "3": ("action",) + COMMAND_KEYS,
    "4": ("warn",),
    SCHEDULER_REVIEW_STEP: (),
}

#: the plan keys the wizard as a whole can carry. ``startup_delay`` is deliberately absent: it is a
#: DEFAULT-level, all-servers value, so it is never edited per server and never carried.
WIZARD_KEYS: tuple[str, ...] = ("timezone", "schedule", "warn", "action", "startup") + COMMAND_KEYS

#: the field names ONE week row posts: its start, its end and its seven-day pattern. They are the
#: step's own language — NOT ``schedule.<range>`` — because a row's range is COMPOSED from the two
#: times rather than named by the operator. Every other block keeps the dotted-name reader.
WEEK_START = "week_start"
WEEK_END = "week_end"
WEEK_DAYS = "week_days"
#: the range key the row was READ from, carried so an untouched row keeps the file's own spelling:
#: ``00-12`` and ``00:00-12:00`` are the same range to the plugin and two different keys to the file,
#: and a save must not rename a range the operator never moved.
WEEK_KEY = "week_key"
WEEK_ROW_FIELDS: tuple[str, ...] = (WEEK_START, WEEK_END, WEEK_DAYS, WEEK_KEY)

#: the row-op markers the step's GET form posts: ``addrow=1`` appends one empty row and
#: ``removerow=<index>`` drops that row. The route reads them, so both work with the script off.
WEEK_ADD_FIELD = "addrow"
WEEK_REMOVE_FIELD = "removerow"

#: the pattern a new row starts with — Y is the default state of a day.
DEFAULT_PATTERN = "YYYYYYY"

#: the day's end. ``<input type="time">`` admits 00:00–23:59 only, so a row that reaches midnight
#: is ENTERED and RENDERED as 00:00 and composed as ``24:00`` — the shape the file's own ``00-24``
#: states, and the only one the schema accepts for a whole day.
MIDNIGHT = "24:00"

#: the most fields one step form may carry, stated so the URL bound is measurable rather than assumed:
#: below the console's 1000-field form bound (``session.py``). A PICK adds one field per selection, so
#: this bound is about a realistic plan (eight missions, eight files, eight presets alongside the week,
#: the warnings and the actions — 100 fields) rather than about any plan at all: past roughly 35
#: selections in every pick the form outgrows it, which is a request-line question for the proxy in
#: front of the console. Nothing truncates a pick — every selection is posted.
FORM_FIELD_BOUND = 120

#: the two sentences a CLOSED pick falls back on. A pick is closed when the bot can enumerate the
#: choices; a read that answered nothing keeps the plain text box and says why, so the step never
#: draws an empty list of choices and a typed value never competes with a list the bot produced.
MISSION_LIST_UNAVAILABLE = i18n._("The mission list could not be read, so the missions are not "
                                  "offered here. Type the value instead.")
MISSION_FILES_UNAVAILABLE = i18n._("The missions directory could not be read, so the mission files "
                                   "are not offered here. Type the path instead.")
PRESET_LIST_UNAVAILABLE = i18n._("The preset files could not be read, so the presets are not offered "
                                 "here. Type the entries instead.")

#: the heading of the group that holds a picked entry the files no longer carry, so opening the step
#: never silently drops a name the bot still applies.
PICK_NOT_IN_FILES = i18n._("not in these files any more")

#: the ``startup`` keys step 2 draws as CONTROLS. ``presets`` is deliberately absent: it is the
#: configured file path a BARE pick name falls back to, the picker never rewrites it, so the step
#: carries it verbatim as a hidden input instead of offering it for editing. ``password`` is carried
#: the same way: the control belongs to an action entry, and a startup block that states one keeps
#: the value untouched through a save.
RENDERED_STARTUP_KEYS: tuple[str, ...] = ("mission_id", "mission_file", "settings",
                                          "clear_maintenance")

# ------------------------------------------------------------------------- step 2: the startup
# The step is OPTIONAL. ONE switch decides whether this server states anything about its start at
# all: switched off, the plan carries no ``startup`` block, so a save REMOVES the keys the block
# carries (the existing prune) and the server follows ``DEFAULT`` again.

#: the switch's field and its two values. It is the console's script-free boolean pair — a hidden
#: ``0`` (BEFORE the checkbox, so the browser posts the checkbox's value last) and the checkbox's
#: ``1`` under the SAME name — so a step form ALWAYS posts the name, ticked or not, and
#: :func:`plan_from_form` decides from the post rather than from the script.
STARTUP_FLAG = "startup_on"
STARTUP_FLAG_ON = "1"
STARTUP_FLAG_OFF = "0"

STARTUP_ON_LABEL = i18n._("Start a fixed mission every time this server starts")
STARTUP_OFF_WHAT = i18n._("Left off, the server starts the mission that ran last.")
STARTUP_OFF_DEED = i18n._("Saving with this off removes the startup keys this server's block "
                          "states, so the server follows DEFAULT again.")

#: the two mission picks are MUTUALLY EXCLUSIVE: one radio names the one this start states, and the
#: other pick's control is rendered ``disabled`` so it posts nothing — a key the block carried is
#: then pruned by the save. Both pickers are drawn either way, so switching costs one round trip,
#: never a lost value.
STARTUP_PICK = "startup_pick"
STARTUP_PICK_ID = "mission_id"
STARTUP_PICK_FILE = "mission_file"
STARTUP_PICK_KINDS: tuple[str, ...] = (STARTUP_PICK_ID, STARTUP_PICK_FILE)
STARTUP_PICK_LABELS: tuple[tuple[str, str], ...] = (
    (STARTUP_PICK_ID, i18n._("by mission ID")),
    (STARTUP_PICK_FILE, i18n._("by mission file")),
)
STARTUP_PICK_LEAD = i18n._("A start names its mission one of the two ways, never both: the save "
                           "removes the value the other key carried.")

#: refusals, each one sentence an operator can act on. ``CRON_NEVER_FIRES`` exists because a valid
#: expression that has no next date (30 February) must never read as "no upcoming runs".
CRON_NOT_AN_EXPRESSION = i18n._("This is not a cron expression — it needs 5 to 7 fields.")
CRON_NEVER_FIRES = i18n._("This expression is valid but never fires — there is no 30 February.")
CRON_EXTRA_FIELD = i18n._("This expression has more than 5 fields: the sixth field is SECONDS, so "
                          "it fires more often than the five fields suggest. That is accepted, not "
                          "refused — check it is what you meant.")
TIME_NOT_A_TIME = i18n._("A time of day is HH:MM, between 00:00 and 24:00.")
UNKNOWN_TIMEZONE = i18n._("That timezone is not a known IANA zone, so times cannot be read in it.")

#: step 1's refusals, one sentence each, rendered under the row they name. A range the bot cannot
#: read unambiguously is never written, so each of these leaves that row out of the plan.
WEEK_START_NOT_BEFORE_END = i18n._("The start must be before the end, or the range covers no time.")
WEEK_RANGE_OVERLAP = i18n._("This range overlaps an earlier row, so the week would be ambiguous.")
WEEK_BAD_DAYS = i18n._("The seven days must be seven letters, each Y, N or P.")

#: the schema's OWN time-of-day pattern (``scheduler_schema.yaml`` ``action_map.times``), applied to
#: a ``times`` entry exactly as the bot's validator applies it.
TIME_PATTERN = re.compile(r"^(0?[0-9]|1[0-9]|2[0-4])(:[0-5][0-9])")

#: leaves whose ``"true"``/``"false"`` text is a BOOLEAN in the plan.
_BOOL_LEAVES = frozenset({"populated", "mission_end", "coalition_win", "shutdown",
                          "run_extensions", "no_reload", "use_orig", "clear_maintenance"})

#: leaves whose digits are a NUMBER in the plan.
_INT_LEAVES = frozenset({"startup_delay", "mission_time", "max_mission_time", "real_time",
                         "idle_time", "sleep", "time", "times", "mission_id", "pause", "period"})

#: leaves the schema types as a SEQUENCE (always a list) and as ``str_or_list`` (a list only when
#: the file holds more than one). One control holds either comma-separated, so a list survives a
#: round trip through the URL and stays a list, and a single value stays a single value.
_SEQ_LEAVES = frozenset({"times", "settings"})
_LIST_LEAVES = frozenset({"presets", "mission_id", "mission_file"})

#: the plan key each command leaf sits under, for the shape marker of a ``str_or_map`` command.
_COMMAND_SHAPES = {key: ("shape." + key) for key in COMMAND_KEYS}

_INT_TEXT = re.compile(r"-?\d+\Z")


def normalise_step(value) -> str:
    """The wizard step in force: a member of :data:`SCHEDULER_STEPS`, :data:`SCHEDULER_LANDING`.

    An ABSENT ``step`` is the landing — the tab exists to be looked at. A ``step`` that is present
    but not a step (``99``, an empty string, a word) is the first step, so a stale link lands on the
    beginning of the wizard rather than on a dead page.
    """
    if value is None:
        return SCHEDULER_LANDING
    key = readmodels.text(value).strip()
    return key if key in SCHEDULER_STEPS else SCHEDULER_STEPS[0]


# ----------------------------------------------------------------------------------- cron and times

def _zone(name: str):
    """The plan's timezone, or ``None`` for "the machine's own local time" (no key stated)."""
    if not name:
        return None
    return ZoneInfo(name)


def _now(zone, now):
    """The reference instant: *now* when a caller passes one, else this minute in *zone*.

    Seconds and microseconds are dropped the way the plugin drops them
    (``plugins/scheduler/listener.py``), so two renders a second apart agree.
    """
    if now is not None:
        return now.astimezone(zone) if zone and now.tzinfo else now
    current = datetime.now(zone) if zone else datetime.now()
    return current.replace(second=0, microsecond=0)


def _utc(stamp: datetime) -> str:
    """One fire as an ISO stamp in UTC — the console renders real-world instants in UTC."""
    aware = stamp if stamp.tzinfo else stamp.replace(tzinfo=dt_timezone.utc)
    return aware.astimezone(dt_timezone.utc).replace(microsecond=0).isoformat()


def next_fires(expression, timezone_name: str = "", *, count: int = 3, now=None) -> dict:
    """The next *count* fires of a cron *expression*, read in the plan's timezone, as UTC stamps.

    Never an empty list on a refusal: an expression croniter cannot parse and one that parses but
    has no next date are BOTH sentences with no fires. A six- or seven-field expression is accepted
    and WARNS — the sixth field is seconds, which is legal and easy to misread.
    """
    text = readmodels.text(expression).strip()
    result = {"expression": text, "fires": [], "error": "", "warning": ""}
    if not text:
        return result
    try:
        zone = _zone(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return result | {"error": UNKNOWN_TIMEZONE}
    try:
        iterator = croniter(text, _now(zone, now))
        stamps = [iterator.get_next(datetime) for _ in range(count)]
    except CroniterBadDateError:
        return result | {"error": CRON_NEVER_FIRES}
    except CroniterBadCronError:
        return result | {"error": CRON_NOT_AN_EXPRESSION}
    except (CroniterError, ValueError, KeyError):
        return result | {"error": CRON_NOT_AN_EXPRESSION}
    warning = CRON_EXTRA_FIELD if len(text.split()) > 5 else ""
    return result | {"fires": [_utc(stamp) for stamp in stamps], "warning": warning}


def time_fires(value, timezone_name: str = "", *, count: int = 3, now=None) -> dict:
    """The next *count* occurrences of one ``times`` entry, read in the plan's timezone.

    The schema's own pattern decides whether *value* is a time of day at all; a value it refuses is
    its own sentence. Each occurrence is a UTC stamp, like a cron fire.
    """
    text = readmodels.text(value).strip()
    result = {"value": text, "fires": [], "error": ""}
    if not text:
        return result
    if not TIME_PATTERN.match(text):
        return result | {"error": TIME_NOT_A_TIME}
    try:
        zone = _zone(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return result | {"error": UNKNOWN_TIMEZONE}
    hours, minutes = (int(part) for part in text.split(":")[:2])
    reference = _now(zone, now)
    local = reference if zone else reference
    stamp = local.replace(hour=hours % 24, minute=minutes, second=0, microsecond=0)
    if stamp < local:
        stamp += timedelta(days=1)
    fires: list[str] = []
    for _ in range(count):
        fires.append(_utc(stamp))
        stamp += timedelta(days=1)
    return result | {"fires": fires}


def times_rows(times, timezone_name: str = "", *, now=None) -> list[dict]:
    """One row per ``times`` entry: its value and its next fires (or the reason there are none)."""
    if isinstance(times, dict):
        values = list(times)
    elif isinstance(times, (list, tuple)):
        values = list(times)
    else:
        return []
    rows: list[dict] = []
    for value in values:
        rows.append(time_fires(value, timezone_name, now=now))
    return rows


# --------------------------------------------------------------------------------- the form's words

def _is_password_name(name: str) -> bool:
    """Whether *name* is the password leaf — the one value read and carried verbatim."""
    return bool(name) and name.rsplit(".", 1)[-1] == ACTION_PASSWORD_KEY


def _scalar(value, *, verbatim: bool = False) -> str:
    """One plan value as the text a control carries; *verbatim* leaves a password untouched."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if verbatim and value is not None:
        return str(value)
    return readmodels.text(value)


def _leaves(prefix: str, value) -> list[tuple[str, str]]:
    """Every leaf of *value* as ``(dotted name, text)``, one entry per control.

    A map recurses and ``None`` states nothing rather than the word "None". A SEQUENCE yields one
    pair per item, all under the same name: that is how a multiple select posts, and
    :func:`plan_from_form` accumulates the repeats back into one list, so a pick a step does not own
    survives the next step exactly as it was made.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        found: list[tuple[str, str]] = []
        for key, inner in value.items():
            name = readmodels.text(key)
            if not name:
                continue
            found.extend(_leaves(f"{prefix}.{name}" if prefix else name, inner))
        return found
    verbatim = _is_password_name(prefix)
    if isinstance(value, (list, tuple)):
        return [(prefix, _scalar(item, verbatim=verbatim)) for item in value if _scalar(item)]
    return [(prefix, _scalar(value, verbatim=verbatim))]


def form_fields(plan) -> list[dict]:
    """Every plan value the wizard can carry, as ``name``/``value`` hidden inputs.

    The names are the plan's own paths, so reading them back needs no second table: ``timezone``,
    ``schedule.06-12``, ``action.0.method``, ``warn.times``, ``startup.presets``,
    ``onMissionEnd.command``. ``startup_delay`` is not in the plan the wizard carries at all.
    """
    plan = plan if isinstance(plan, dict) else {}
    fields: list[dict] = []

    def add(name: str, value: str) -> None:
        # a warn time's key IS its seconds, so an empty MESSAGE must still carry the time; every
        # other empty value states nothing and is left out
        if value or name.startswith("warn.times."):
            fields.append({"name": name, "value": value})

    add("timezone", readmodels.text(plan.get("timezone")))
    schedule = plan.get("schedule")
    if isinstance(schedule, dict):
        for period, pattern in schedule.items():
            name = readmodels.text(period)
            if name and name != "timezone":
                add(f"schedule.{name}", readmodels.text(pattern))
    warn = plan.get("warn")
    if isinstance(warn, dict):
        for name, value in _leaves("warn", warn):
            add(name, value)
    action = plan.get("action")
    if isinstance(action, dict):
        entries = [(0, action)]
    elif isinstance(action, (list, tuple)):
        entries = [(index, entry) for index, entry in enumerate(action)
                   if isinstance(entry, dict)]
    else:
        entries = []
    for index, entry in entries:
        for name, value in _leaves(f"action.{index}", entry):
            # the deprecated pair is never carried: a hidden input for it would make the save
            # refuse the page's own form, and the mutate leaves an existing occurrence alone
            if name.rsplit(".", 1)[-1] in NEVER_WRITTEN:
                continue
            add(name, value)
    startup = plan.get("startup")
    if isinstance(startup, dict):
        for name, value in _leaves("startup", startup):
            add(name, value)
    for key in COMMAND_KEYS:
        if key in plan:
            for name, value in _leaves(key, plan.get(key)):
                add(name, value)
    return fields


def shape_markers(plan) -> list[dict]:
    """The hidden fields that PRESERVE a value's shape across the steps.

    ``action`` is the schema's ``seq_or_map`` and ``warn.times`` is its other one; a command is its
    ``str_or_map``. A form that has to rebuild the plan from text must be told which shape the FILE
    holds, or a one-map block would come back as a one-item list. The marker is derived from the
    plan, never chosen.
    """
    plan = plan if isinstance(plan, dict) else {}
    markers: list[dict] = []
    action = plan.get("action")
    if isinstance(action, (list, tuple)):
        markers.append({"name": "shape.action", "value": "seq"})
    elif isinstance(action, dict):
        markers.append({"name": "shape.action", "value": "map"})
    warn = plan.get("warn")
    if isinstance(warn, dict) and "times" in warn:
        shape = "map" if isinstance(warn.get("times"), dict) else "seq"
        markers.append({"name": "shape.warn.times", "value": shape})
    for key in COMMAND_KEYS:
        if key in plan:
            shape = "str" if isinstance(plan.get(key), str) else "map"
            markers.append({"name": _COMMAND_SHAPES[key], "value": shape})
    return markers


def carried_fields(plan, step: str) -> list[dict]:
    """The hidden inputs one step carries: every plan value the step does NOT own."""
    owned = set(STEP_KEYS.get(step, ()))
    return [field for field in form_fields(plan)
            if field["name"].split(".", 1)[0] not in owned]


# ------------------------------------------------------------------------------- reading a form back

def _scalar_value(leaf: str, text: str):
    """One posted string as the plan's own scalar, by the leaf's schema type."""
    if leaf in _BOOL_LEAVES and text in ("true", "false"):
        return text == "true"
    if leaf in _INT_LEAVES and _INT_TEXT.match(text):
        return int(text)
    return text


def _pieces(text: str) -> list[str]:
    """A comma-separated control's pieces — or the WHOLE text when it is one pick entry.

    A file-qualified preset entry is ``<file label>\\n<preset name>`` and a preset name is operator
    data, so it may itself carry a comma: splitting such an entry would turn one preset into two names
    the bot refuses, and one refused entry discards the whole pick.
    """
    if PRESET_PAIR_SEPARATOR in text:
        return [text]
    return [piece.strip() for piece in text.split(",")]


def _leaf_value(leaf: str, text: str):
    """One posted string as the plan's own value — a list where the schema says sequence."""
    if leaf in _SEQ_LEAVES:
        return [_scalar_value(leaf, piece) for piece in _pieces(text) if piece]
    if leaf in _LIST_LEAVES and "," in text:
        return [_scalar_value(leaf, piece) for piece in _pieces(text) if piece]
    return _scalar_value(leaf, text)


def _indexed(tree: dict) -> list[dict]:
    """A map keyed by list INDEX as the list it is, in index order."""
    return [tree[key] for key in sorted(tree, key=lambda key: int(key) if _INT_TEXT.match(key) else 0)]


#: the marker for "this name has not been posted yet" — an absent dict key cannot be told from a key
#: stating ``None``.
_MISSING = object()


def _accumulate(current, value):
    """One name posted more than once becomes ONE list, in posted order; a single post stays a scalar.

    A multiple ``<select>`` posts its name once per selected entry, so the reader has to collect them
    instead of letting the last one win — otherwise a pick of three entries would be written as the
    last one alone. A leaf the schema types as a SEQUENCE already answers a list for one value, so its
    pieces are FLATTENED into the accumulated list rather than nested.
    """
    first = current if isinstance(current, list) else [current]
    return first + (value if isinstance(value, list) else [value])


def _plain(value):
    """A rebuilt subtree: an all-digit key set becomes a list, everything else stays a map."""
    if isinstance(value, dict):
        if value and all(_INT_TEXT.match(key) for key in value):
            return _indexed(value)
        return {key: _plain(item) for key, item in value.items()}
    return value


def _shape(tree: dict, markers: dict) -> dict:
    """Turn the rebuilt dotted tree into the plan, honouring every shape marker."""
    plan: dict = {}
    for key, value in tree.items():
        if key == "action":
            entries = _indexed(value) if isinstance(value, dict) else value
            if markers.get("action") == "map":
                plan["action"] = entries[0] if entries else {}
            else:
                plan["action"] = entries
            continue
        if key == "warn" and isinstance(value, dict):
            times = value.get("times")
            if isinstance(times, dict):
                if markers.get("warn.times") == "map":
                    value["times"] = {int(second) if _INT_TEXT.match(second) else second: text
                                      for second, text in times.items()}
                else:
                    value["times"] = _indexed(times)
            plan["warn"] = value
            continue
        if key in COMMAND_KEYS and isinstance(value, dict):
            only_command = set(value) <= {"command"}
            if markers.get(key) == "str" and only_command:
                plan[key] = value.get("command", "")
            else:
                plan[key] = value
            continue
        plan[key] = _plain(value)
    return plan


def _form_tree(pairs) -> tuple[dict, dict]:
    """The dotted tree one form posts, plus the ``shape.*`` markers it carries.

    The reading rules are the form's own: an EMPTY string states nothing (a control the operator
    cleared must not become an empty value), a name outside :data:`WIZARD_KEYS` is ignored, step 1's
    week rows and both steps' row ops have languages of their own, and a name posted twice
    accumulates into one list.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    markers: dict[str, str] = {}
    tree: dict = {}
    for name, raw in items:
        name = readmodels.text(name)
        head, _, rest = name.partition(".")
        # a value is TRIMMED on the way in, so a cleared control states nothing — except the
        # password leaf, which is taken as posted: a password is compared byte for byte.
        text = str(raw) if _is_password_name(name) and raw is not None else readmodels.text(raw)
        if (head in WEEK_ROW_FIELDS or head in WARN_ROW_FIELDS
                or head in (WEEK_ADD_FIELD, WEEK_REMOVE_FIELD,
                            ACTION_ADD_FIELD, ACTION_REMOVE_FIELD,
                            ACTION_TIME_FIELD, ACTION_TIME_ADD_FIELD, ACTION_TIME_REMOVE_FIELD)):
            continue
        if head == "shape":
            if rest and text:
                markers[rest] = text
            continue
        if head not in WIZARD_KEYS or (not text and not name.startswith("warn.times.")):
            continue
        node = tree
        segments = name.split(".")
        for segment in segments[:-1]:
            node = node.setdefault(segment, {})
            if not isinstance(node, dict):
                break
        else:
            leaf = segments[-1]
            value = _leaf_value(leaf, text)
            current = node.get(leaf, _MISSING)
            node[leaf] = value if current is _MISSING else _accumulate(current, value)
    return tree, markers


def _posted_entries(tree: dict) -> dict:
    """The ``action`` entries the form posted, keyed by the index its field names carry."""
    action = tree.get("action")
    return action if isinstance(action, dict) else {}


def action_row_refusal(pairs) -> str:
    """The refusal a posted entry-row op warrants by its own value, or ``""``.

    ``removeentry`` names a position the FORM posted, so a value that is not a number and a number no
    posted entry carries are both refused: applying either would drop a different entry than the one
    the operator named. ``addentry`` takes no argument and is never refused. Nothing here writes.
    """
    removed = _last(pairs, ACTION_REMOVE_FIELD).strip()
    if not removed:
        return ""
    if not _INT_TEXT.match(removed):
        return ACTION_REMOVE_NOT_A_POSITION
    tree, _markers = _form_tree(pairs)
    if removed not in _posted_entries(tree):
        return ACTION_REMOVE_NO_SUCH_ENTRY
    return ""


def _action_row_op(tree: dict, markers: dict, pairs) -> str:
    """Apply the posted row op to *tree* IN PLACE, renumbering the entries from zero.

    A refused op changes nothing at all, so the step re-renders the plan it was handed. An ADD
    appends one entry that states nothing but its method, and the marker turns to ``seq`` as soon as
    more than one entry is stated: a file that holds a single map cannot express two entries.
    """
    removed = _last(pairs, ACTION_REMOVE_FIELD).strip()
    addition = bool(_last(pairs, ACTION_ADD_FIELD).strip())
    if not removed and not addition:
        return ""
    refusal = action_row_refusal(pairs)
    if refusal:
        return refusal
    entries = dict(_posted_entries(tree))
    if removed:
        del entries[removed]
        entries = {str(number): entry for number, entry in enumerate(_indexed(entries))}
    if addition:
        entries[str(len(entries))] = {"method": NEW_ENTRY_METHOD}
    if entries:
        tree["action"] = entries
        if len(entries) > 1:
            markers["action"] = "seq"
    else:
        tree.pop("action", None)
    return ""


def _apply_startup_pick(tree: dict, pairs) -> None:
    """Drop the mission key the OTHER pick carried, when step 2's radio states a choice.

    The two picks are exclusive: the visible control posts its own key, the hidden one is disabled
    and posts nothing. A form that carries no radio (steps 3, 4 and 5) states no choice, so both keys
    stay as the file has them.
    """
    chosen = _posted(pairs, STARTUP_PICK)
    if chosen not in STARTUP_PICK_KINDS:
        return
    block = tree.get("startup")
    if not isinstance(block, dict):
        return
    block.pop(STARTUP_PICK_FILE if chosen == STARTUP_PICK_ID else STARTUP_PICK_ID, None)


def plan_from_form(pairs) -> dict:
    """The plan a step form states, rebuilt from its flattened fields.

    The rules are its own, spelled once here: an EMPTY string is the key ABSENT (a control the
    operator cleared must not become an empty value), a control that posts nothing states nothing —
    which is why the ``populated`` radios post ``false`` for "only when nobody is flying" and an
    empty value for "act even with players on", so the default writes no key at all — and a shape
    the file holds is kept by the ``shape.*`` markers rather than normalised.

    *pairs* is a query string's pairs (``QueryParams.multi_items()``) or any ``(name, value)``
    iterable; a name outside :data:`WIZARD_KEYS` (``tab``, ``step``, a CSRF token) is ignored. Step
    1's week rows REPLACE ``schedule`` whenever the form posts one, and step 3's row op is applied
    after the entries are read, so a removal takes that entry out of the plan the review and the save
    both read.

    Step 2's one switch decides whether a ``startup`` block is stated at all: a form that carries
    :data:`STARTUP_FLAG` and leaves it off states NOTHING about the start, so every ``startup.*``
    field it posts is dropped here — the fields are in the markup whether or not they are hidden.

    Step 3's per-entry rules apply only to entries whose own carriers the form posts: its
    ``action_time.<n>.<row>`` rows REPLACE that entry's ``times``, its ``action_trigger.<n>`` radio
    keeps ONE trigger and removes the other four kinds, and its ``action.<n>.method`` keeps only the
    keys that method uses. A step that draws none of those carriers states no choice, so nothing is
    dropped.
    """
    tree, markers = _form_tree(pairs)
    # THE ONE SWITCH of step 2: a form that carries it and does not turn it on states NOTHING about
    # the start, so every ``startup.*`` field it happens to post (a hidden one, or a control the
    # stylesheet hid) is dropped here rather than left in the plan to be written back.
    flag = _posted(pairs, STARTUP_FLAG)
    if flag is not None and flag.strip() != STARTUP_FLAG_ON:
        tree.pop("startup", None)
    _apply_startup_pick(tree, pairs)
    week = week_rows_from_pairs(pairs)
    if week is not None:
        # a posted week REPLACES the schedule: a row the operator removed must not survive, and a
        # row that states nothing writes no key at all
        schedule, _errors = schedule_from_week(week)
        tree.pop("schedule", None)
        if schedule:
            tree["schedule"] = schedule
    # the entry rules read the posted field names by the index the FORM posted, so they run BEFORE
    # the row op renumbers: a removal must not move one entry's trigger onto another
    _apply_entry_times(tree, pairs)
    _apply_entry_triggers(tree, pairs)
    _apply_entry_methods(tree, pairs)
    _action_row_op(tree, markers, pairs)
    warn_rows = warn_rows_from_pairs(pairs)
    if warn_rows is not None:
        _apply_warn_rows(tree, markers, warn_rows)
    return _shape(tree, markers)


def wizard_plan(effective, pairs) -> dict:
    """The plan the steps render: what the form states, else the plan the bot applies.

    A step with nothing posted (the first entry, or a refresh of it) shows the EFFECTIVE plan —
    the merge of ``DEFAULT`` and the server's own block — so nothing is promoted into an override
    by being displayed. Once a step has been submitted, its own values are the plan, because every
    step carries everything it does not own.
    """
    posted = plan_from_form(pairs)
    if posted or week_rows_from_pairs(pairs) is not None:
        # a step-1 form that posts rows STATES a week even when every row is empty — that is the
        # operator removing the schedule, not a page that has not been submitted
        return posted
    return dict(effective) if isinstance(effective, dict) else {}


# --------------------------------------------------------------------------------- the week editor
# A row is a start, an end and seven day letters. The RANGE KEY the file holds is composed from the
# two times — that is why this block has field names of its own (``week_start.<n>``) instead of the
# dotted ``schedule.<range>`` reader the other blocks use: the operator never types a key.

#: one clock time as ``<input type="time">`` posts it.
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")

#: the seven letters a row carries.
_DAY_PATTERN = re.compile(r"^[YNP]{7}$")


def timezone_options(current: str = "") -> list[str]:
    """Every IANA zone the interpreter reports, sorted, behind the empty "own local time" option.

    The list is CLOSED — the bot enumerates it — so the control is a picker and nothing here accepts
    a free-text zone. A *current* value the list does not carry (an old or aliased name) is added as
    its own option and stays selected, so opening the step never silently drops what the file holds.
    """
    zones = sorted(available_timezones())
    current = readmodels.text(current).strip()
    if current and current not in zones:
        zones.append(current)
        zones.sort()
    return [""] + zones


def _clock_text(value) -> str:
    """A clock time as ``HH:MM``; ``""`` when the value is not a time of day.

    ``24`` and ``24:00`` — the day's end — are read as :data:`MIDNIGHT`, which is the form the plan's
    own keys use.
    """
    text = readmodels.text(value).strip()
    if text in ("24", MIDNIGHT):
        return MIDNIGHT
    if ":" not in text:
        text = f"{text}:00"
    return text if _CLOCK.match(text) else ""


def _clock_minutes(text: str) -> int | None:
    """Minutes since midnight; ``None`` when the text is not a clock time."""
    if text == MIDNIGHT:
        return 24 * 60
    if not _CLOCK.match(text):
        return None
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def compose_range(start: str, end: str) -> str:
    """The schema's own key shape ``HH:MM-HH:MM`` from two clock times.

    An end of ``00:00`` is the day's end — the control cannot hold 24:00 — so it composes as
    ``24:00``, which both versions of the schema's regex accept.
    """
    return f"{readmodels.text(start)}-{MIDNIGHT if readmodels.text(end) == '00:00' else readmodels.text(end)}"


def _row(table: dict, index: str) -> dict:
    """The row a field name's index belongs to, created empty on first appearance."""
    return table.setdefault(index, {"key": "", "start": "", "end": "", "days": ""})


def _posted_week(pairs) -> list[dict] | None:
    """The week rows a step-1 form posts, in index order — ``None`` when it posts no row field.

    ``None`` and the empty list are different answers: a form that carries no week field at all is
    not step 1 (its rows then come from the plan), while a step-1 form always carries them.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    table: dict[str, dict] = {}
    seen = False
    for name, raw in items:
        head, _, index = readmodels.text(name).partition(".")
        if head not in WEEK_ROW_FIELDS:
            continue
        seen = True
        row = _row(table, index)
        if head == WEEK_START:
            row["start"] = _clock_text(raw)
        elif head == WEEK_END:
            row["end"] = _clock_text(raw)
        elif head == WEEK_KEY:
            # verbatim: it is the file's own key text, not a time this module normalises
            row["key"] = readmodels.text(raw).strip()
        else:
            row["days"] = readmodels.text(raw).strip().upper()
    if not seen:
        return None
    order = sorted(table, key=lambda key: int(key) if key.isdigit() else 0)
    return [dict(table[key], index=key) for key in order]


def _last(pairs, name: str) -> str:
    """The last value a form posts for *name* — the browser's own last-value-wins order."""
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    values = [readmodels.text(raw) for key, raw in items if readmodels.text(key) == name]
    return values[-1] if values else ""


def _posted(pairs, name: str):
    """The last value a form posts for *name*, or ``None`` when the form never carries the name.

    ``None`` and the empty string are different answers: a switch the form does not have is not a
    switch the operator turned off, so a step that does not draw the control must not decide with it.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    values = [readmodels.text(raw) for key, raw in items if readmodels.text(key) == name]
    return values[-1] if values else None


def added_week_row(rows) -> dict:
    """The row "Add a row" appends: it CONTINUES the week from the last row's end.

    The new range starts where the row above it ends, so consecutive ranges are typed once instead
    of twice, and both controls carry a whole hour (``HH:00``) so a time control never opens with an
    empty minute part. An end at midnight is the day's end, which the control cannot hold, so it is
    written as the next hour's ``00:00``.
    """
    previous = readmodels.text((rows[-1] if rows else {}).get("end")).strip()
    start = _clock_text(previous) or "00:00"
    if start == MIDNIGHT:
        start = "00:00"
    minutes = _clock_minutes(start)
    end_at = 0 if minutes is None else (minutes + 60) % (24 * 60)
    end = MIDNIGHT if end_at == 0 else f"{end_at // 60:02d}:{end_at % 60:02d}"
    return {"start": start, "end": end, "days": DEFAULT_PATTERN}


def week_rows_from_pairs(pairs) -> list[dict] | None:
    """The rows the step renders AND composes from: the posted rows with its row op applied.

    ``addrow=1`` appends one row (:func:`added_week_row`) and ``removerow=<index>`` drops that row.
    Both are submit buttons on the same GET form, so the round trip is the baseline and the script is
    only an enhancement. The indices are re-assigned after the op, so a re-rendered form always
    numbers its rows from zero.
    """
    rows = _posted_week(pairs)
    if rows is None:
        return None
    removed = _last(pairs, WEEK_REMOVE_FIELD).strip()
    if removed:
        rows = [row for row in rows if row["index"] != removed]
    if _last(pairs, WEEK_ADD_FIELD).strip():
        rows = rows + [added_week_row(rows)]
    return [dict(row, index=str(number)) for number, row in enumerate(rows)]


def _plan_week_rows(plan) -> list[dict]:
    """The rows the plan states, in the file's order; the always-up proposal when it states none."""
    schedule = plan.get("schedule")
    stated: list[dict] = []
    if isinstance(schedule, dict):
        for period, pattern in schedule.items():
            name = readmodels.text(period)
            if not name or name == "timezone":
                continue
            start, _, end = name.partition("-")
            stated.append({"key": name, "start": _clock_text(start), "end": _clock_text(end),
                           "days": readmodels.text(pattern).strip()})
    if not stated:
        return [{"start": "00:00", "end": MIDNIGHT, "days": DEFAULT_PATTERN}]
    return stated


def compose_key(start: str, end: str) -> str:
    """The ``schedule`` key for two clock times, in the style the schema's own example uses.

    A whole-hour boundary loses its ``:00`` — ``12:00-24:00`` composes as ``12-24`` — because
    ``00:00-12:00`` and ``00-12`` are two different map keys for one range, and a file that states
    one style must not collect the other beside it.
    """
    text = compose_range(start, end)
    head, _, tail = text.partition("-")
    # BOTH boundaries whole hours, or neither compacted: ``06:30-12`` would mix the two shapes
    if head.endswith(":00") and tail.endswith(":00"):
        return f"{head[:2]}-{tail[:2]}"
    return text


def _stated_key(row, start_at: int, end_at: int, start: str, end: str) -> str:
    """The map key for ONE row: the file's own spelling while the times still say that range.

    A row is drawn from a key the file already holds, so the key travels with it (:data:`WEEK_KEY`)
    and is reused verbatim: ``00-12`` stays ``00-12``, and a save never renames a range the operator
    did not move. A row whose times changed — or a row that was added — composes
    :func:`compose_key`.
    """
    stated = readmodels.text(row.get("key"))
    if stated:
        head, _, tail = stated.partition("-")
        stated_start = _clock_minutes(_clock_text(head))
        stated_end = _clock_minutes(_clock_text(tail))
        if stated_start == start_at and stated_end == end_at:
            return stated
    return compose_key(start, end)


def schedule_from_week(rows) -> tuple[dict, list[dict]]:
    """The ``schedule`` map *rows* state, plus one refusal per row it cannot state.

    The rules, each applied per row: a row missing its start or its end states nothing and is not
    written; a start that is not before its end, a pattern that is not seven Y/N/P letters, and a
    range that overlaps an earlier accepted row each refuse THAT row with its own sentence, so the
    plan written is never an ambiguous week. Ranges compose to the schema's ``HH:MM-HH:MM``.
    """
    schedule: dict[str, str] = {}
    spans: list[tuple[int, int]] = []
    errors: list[dict] = []
    for number, row in enumerate(rows, start=1):
        start, end = readmodels.text(row.get("start")), readmodels.text(row.get("end"))
        days = readmodels.text(row.get("days")).strip().upper()
        if not start or not end:
            continue
        start_at = _clock_minutes(start)
        end_at = 24 * 60 if end == "00:00" else _clock_minutes(end)
        if start_at is None or end_at is None:
            continue
        if not _DAY_PATTERN.match(days):
            errors.append({"row": number, "msgid": WEEK_BAD_DAYS})
            continue
        if start_at >= end_at:
            errors.append({"row": number, "msgid": WEEK_START_NOT_BEFORE_END})
            continue
        if any(start_at < other_end and other_start < end_at for other_start, other_end in spans):
            errors.append({"row": number, "msgid": WEEK_RANGE_OVERLAP})
            continue
        spans.append((start_at, end_at))
        schedule[_stated_key(row, start_at, end_at, start, end)] = days
    return schedule, errors


def _week_edit_rows(plan, pairs) -> tuple[list[dict], list[dict]]:
    """Step 1's rows as the page draws them, plus the refusals, from the plan or the posted form."""
    posted = week_rows_from_pairs(pairs)
    rows = posted if posted is not None else _plan_week_rows(plan)
    if posted is not None and not rows:
        # every row was removed: ONE empty row keeps the form's own language (a form with no week
        # field would read as "not submitted" and the plan would come back), and it states nothing
        rows = [{"start": "", "end": "", "days": DEFAULT_PATTERN}]
    _schedule, errors = schedule_from_week(rows)
    refusals = {error["row"]: error["msgid"] for error in errors}
    drawn: list[dict] = []
    for number, row in enumerate(rows, start=1):
        start, end = row.get("start", ""), row.get("end", "")
        drawn.append({
            "index": str(number - 1),
            "number": number,
            # the key the row was read from, posted back so an untouched row keeps its spelling
            "key": readmodels.text(row.get("key")),
            "start": "00:00" if start == MIDNIGHT else start,
            # the control cannot hold 24:00: midnight is ENTERED and RENDERED as 00:00
            "end": "00:00" if end == MIDNIGHT else end,
            "days": row.get("days") or DEFAULT_PATTERN,
            "range": compose_range(start, end) if start and end else "",
            "error": refusals.get(number, ""),
        })
    return drawn, errors


def _row_order(key: str) -> int:
    """A row index as a NUMBER for sorting, so row 10 comes after row 2."""
    return int(key) if _INT_TEXT.match(readmodels.text(key)) else 0


def _time_input(value) -> str:
    """A ``times`` entry as ``<input type="time">`` holds it: ``HH:MM``, hour zero-padded.

    The schema also accepts ``4:00``; the control requires the padded form, so the value is padded
    for the FIELD only. ``24:00`` — which the schema accepts for midnight — is the one value the
    control cannot hold, so it is ENTERED and RENDERED as ``00:00``: the same instant, the way step
    1's week editor renders its midnight.
    """
    text = readmodels.text(value).strip()
    hour, _, minute = text.partition(":")
    if hour.isdigit() and minute:
        return f"{int(hour) % 24:02d}:{minute}"
    return text


def entry_trigger(entry: dict, posted=None) -> str:
    """The ONE trigger an entry runs on, or ``""``.

    The posted radio wins when it names a trigger. Otherwise the file decides, in the BOT's own
    precedence order — times of day, mission time, server runtime, idle time, cron — so an entry that
    states two triggers is read the way the bot reads it.
    """
    if posted in ACTION_TRIGGERS:
        return posted
    entry = entry if isinstance(entry, dict) else {}
    for kind in ACTION_TRIGGERS:
        if any(key in entry for key in ACTION_TRIGGER_KEYS_BY_KIND[kind]):
            return kind
    return ""


def _legacy_value(value) -> str:
    """A deprecated ``local_times`` / ``utc_times`` value as the read-only block prints it, or ``""``."""
    if isinstance(value, dict):
        return ", ".join(f"{readmodels.text(name)}: {_legacy_value(inner)}"
                         for name, inner in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(readmodels.text(item) for item in value)
    return readmodels.text(value)


def entry_legacy(entry) -> list[dict]:
    """Every deprecated trigger key *entry* carries, the schema's order, each with its value.

    The pair is outside this tab's language — no plan can express it and no save writes it — so an
    entry that carries one and no supported trigger cannot be retriggered here: a trigger chosen on
    the page would be a SECOND one, and the bot reads the old key first.
    """
    entry = entry if isinstance(entry, dict) else {}
    return [{"key": key, "value": _legacy_value(entry[key])}
            for key in NEVER_WRITTEN if key in entry]


def entry_pick(entry: dict, posted=None) -> str:
    """Which of the two mission keys a ``load`` states: the posted radio, else the entry's shape.

    A ``mission_file`` without a ``mission_id`` is a file load; anything else — the id, both, or
    neither — is the id pick, the default a fresh load takes.
    """
    if posted in STARTUP_PICK_KINDS:
        return posted
    entry = entry if isinstance(entry, dict) else {}
    if "mission_file" in entry and "mission_id" not in entry:
        return STARTUP_PICK_FILE
    return STARTUP_PICK_ID


def posted_time_rows(pairs) -> dict[str, list[str]] | None:
    """The times-of-day rows a step-3 form posts, one value list per entry index.

    ``None`` means the form posts no row field at all — it is not the times control, so nothing may
    be replaced. ``addtime=<entry>`` appends one empty row to that entry and
    ``removetime=<entry>.<row>`` drops that row; both are submits of the same GET form, so both work
    with the script off. The rows are renumbered by position on every render, so a row is named by
    where it stands, never by the time it holds.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    table: dict[str, dict[str, str]] = {}
    seen = False
    for name, raw in items:
        head, _, rest = readmodels.text(name).partition(".")
        if head != ACTION_TIME_FIELD:
            continue
        entry, _, row = rest.partition(".")
        seen = True
        table.setdefault(entry, {})[row] = readmodels.text(raw)
    if not seen:
        return None
    rows = {entry: [values[key] for key in sorted(values, key=_row_order)]
            for entry, values in table.items()}
    removed = _last(pairs, ACTION_TIME_REMOVE_FIELD).strip()
    if removed:
        entry, _, row = removed.partition(".")
        order = sorted(table.get(entry, {}), key=_row_order)
        if row in order:
            at = order.index(row)
            rows[entry] = [value for position, value in enumerate(rows[entry]) if position != at]
    added = _last(pairs, ACTION_TIME_ADD_FIELD).strip()
    if added:
        rows.setdefault(added, []).append("")
    return rows


def _apply_entry_times(tree: dict, pairs) -> None:
    """Replace each entry's ``times`` with the rows the form posts for it, in row order.

    An entry the form posts no row for keeps its own value: steps 4 and 5 carry
    ``action.<n>.times`` as ONE comma-separated hidden value and draw no row.
    """
    posted = posted_time_rows(pairs)
    if posted is None:
        return
    entries = _posted_entries(tree)
    for index, values in posted.items():
        entry = entries.get(index)
        if not isinstance(entry, dict):
            continue
        stated = [value.strip() for value in values if value.strip()]
        if stated:
            entry["times"] = stated
        else:
            entry.pop("times", None)


def _apply_entry_triggers(tree: dict, pairs) -> None:
    """Keep only the trigger the operator chose, for every entry whose radio the form posts.

    The radio is the aux carrier ``action_trigger.<n>``. A form that does not carry it (steps 4 and
    5) states no choice, so nothing is dropped and the plan keeps every trigger the file has — a
    step that draws no control must not decide with it.
    """
    for index, entry in _posted_entries(tree).items():
        if not isinstance(entry, dict):
            continue
        chosen = _posted(pairs, f"{ACTION_TRIGGER_FIELD}.{index}")
        if chosen not in ACTION_TRIGGERS:
            continue
        keep = set(ACTION_TRIGGER_KEYS_BY_KIND[chosen])
        for kind in ACTION_TRIGGERS:
            for key in ACTION_TRIGGER_KEYS_BY_KIND[kind]:
                if key not in keep:
                    entry.pop(key, None)


def _apply_entry_methods(tree: dict, pairs) -> None:
    """Drop the keys the chosen method does not use, for every entry whose method the form posts.

    Read from the POSTED method, so a form that carries no method (a step that draws none) drops
    nothing. A ``load`` additionally keeps one of its two mission keys: the pick radio says which,
    and the other key is removed so the save prunes it from the file.
    """
    for index, entry in _posted_entries(tree).items():
        if not isinstance(entry, dict):
            continue
        posted = _posted(pairs, f"action.{index}.method")
        if posted is None:
            continue
        method = posted.strip()
        if method not in ACTION_METHOD_KEYS:
            continue
        for key in ACTION_METHOD_DROP[method]:
            entry.pop(key, None)
        if method == "load":
            # the pick radio names WHICH of the two mission keys this load states. A form that does
            # not carry the radio (steps 4 and 5) states no choice, so both keys stay as the file has
            # them rather than one being dropped on the operator's behalf.
            chosen = _posted(pairs, f"{ACTION_PICK_FIELD}.{index}")
            if chosen in STARTUP_PICK_KINDS:
                entry.pop(STARTUP_PICK_FILE if chosen == STARTUP_PICK_ID else STARTUP_PICK_ID, None)


def _action_entries(block) -> list[dict]:
    """The ``action`` entries a block states, in file order — a single map reads as one entry."""
    action = block.get("action") if isinstance(block, dict) else None
    if isinstance(action, dict):
        return [action]
    if isinstance(action, (list, tuple)):
        return [entry for entry in action if isinstance(entry, dict)]
    return []


def _action_edit_rows(plan, timezone_name: str, now, missions=None, mission_files=None,
                      presets=None, pairs=None, raw=None) -> list[dict]:
    """Step 3's cards: the entries the plan states, in order, with each trigger's computed fires.

    Every card also carries the same three closed picks step 2 draws (``startup_picks`` over that
    entry's own keys), and the keys the step does NOT draw as ``extra`` hidden inputs — ``shutdown``
    among them, so a save never loses a key the step does not offer.

    *raw* is the server's own block AS THE FILE STATES IT, read for the one thing the plan cannot
    carry: the deprecated ``local_times`` / ``utc_times``, which no form field ever posts, so a plan
    built from a previous step no longer states it. The two are aligned by POSITION and only while
    they state the same number of entries — an add or a removal renumbers the plan, and a card must
    never wear another entry's trigger.
    """
    action = plan.get("action")
    if isinstance(action, dict):
        entries = [(0, action)]
    elif isinstance(action, (list, tuple)):
        entries = [(index, entry) for index, entry in enumerate(action) if isinstance(entry, dict)]
    else:
        entries = []
    file_entries = _action_entries(raw)
    aligned = len(file_entries) == len(entries)
    rows: list[dict] = []
    posted_rows = posted_time_rows(pairs) if pairs is not None else None
    for number, (index, entry) in enumerate(entries, start=1):
        extra: list[dict] = []
        for name, value in entry.items():
            if name in RENDERED_ACTION_KEYS or name in NEVER_WRITTEN:
                continue
            for leaf, text in _leaves(f"action.{index}.{name}", value):
                extra.append({"name": leaf, "value": text})
        stated = [kind for kind in ACTION_TRIGGERS
                  if any(key in entry for key in ACTION_TRIGGER_KEYS_BY_KIND[kind])]
        # an entry whose only trigger is the deprecated pair draws NO radio and no trigger input:
        # its block is read-only, so a save cannot add a second trigger the old key would override
        file_entry = file_entries[index] if aligned else {}
        legacy = entry_legacy(file_entry)
        legacy_only = bool(legacy) and not any(
            key in file_entry for key in ACTION_TRIGGER_KEYS)
        # ONE trigger per entry: the posted radio wins, else the bot's precedence over the file. An
        # entry that states none shows the DEFAULT choice — selected on the card, never written as a
        # trigger key until the operator enters a value.
        trigger = "" if legacy_only else entry_trigger(
            entry, _posted(pairs, f"{ACTION_TRIGGER_FIELD}.{index}") if pairs is not None else None)
        shown = "" if legacy_only else (trigger or ACTION_DEFAULT_TRIGGER)
        if posted_rows is not None and str(index) in posted_rows:
            times = posted_rows[str(index)]
        else:
            stated_times = entry.get("times")
            times = list(stated_times) if isinstance(stated_times, (list, tuple)) else []
        if shown == "times" and not times:
            # the times control always holds one row, so the operator has somewhere to type
            times = [""]
        time_rows = [{"index": str(position), "value": _time_input(row["value"]),
                      "fires": row["fires"], "error": row["error"]}
                     for position, row in enumerate(times_rows(times, timezone_name, now=now))]
        method = readmodels.text(entry.get("method")).strip()
        rows.append({
            "index": index,
            "number": number,
            "entry": entry,
            "triggered": any(key in entry for key in ACTION_TRIGGER_KEYS),
            "trigger": trigger,
            "trigger_shown": shown,
            "trigger_dropped": len(stated) > 1,
            "legacy": legacy,
            "legacy_only": legacy_only,
            "time_rows": time_rows,
            "cron": next_fires(entry.get("cron"), timezone_name, now=now),
            "method": method,
            "controls": ACTION_METHOD_CONTROLS.get(method or NEW_ENTRY_METHOD, ()),
            "pick": entry_pick(entry, _posted(pairs, f"{ACTION_PICK_FIELD}.{index}")
                               if pairs is not None else None),
            "picks": startup_picks(entry, missions, mission_files, presets),
            "extra": extra,
        })
    return rows


def warn_preview(text) -> str:
    """The sentence a player reads: *text* filled the way the BOT fills it.

    The bot substitutes exactly three keywords (``item``, ``what``, ``when``), so this calls the same
    ``str.format`` with example words. A text carrying a name the bot cannot fill (``{player}``)
    raises there and would break this page, so the raw text stands instead of a half-rendered one.
    """
    source = readmodels.text(text)
    if not source:
        return ""
    try:
        return source.format(**WARN_EXAMPLES)
    except (KeyError, IndexError, ValueError):
        return source


def _posted_warn(pairs) -> list[dict] | None:
    """The ladder rows a step-4 form posts, in index order — ``None`` when it posts no row field.

    ``None`` and the empty list differ: a form with no row field is not step 4 (its rows then come
    from the plan), while a step-4 form always carries them, even after every row was removed.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    table: dict[str, dict] = {}
    seen = False
    for name, raw in items:
        head, _, index = readmodels.text(name).partition(".")
        if head not in WARN_ROW_FIELDS:
            continue
        seen = True
        row = table.setdefault(index, {"seconds": "", "message": ""})
        if head == WARN_TIME:
            row["seconds"] = readmodels.text(raw).strip()
        else:
            row["message"] = readmodels.text(raw)
    if not seen:
        return None
    order = sorted(table, key=lambda key: int(key) if key.isdigit() else 0)
    return [dict(table[key], index=key) for key in order]


def warn_rows_from_pairs(pairs) -> list[dict] | None:
    """The rows step 4 renders and composes from: the posted rows with its row op applied.

    ``addrow=1`` appends one empty row and ``removerow=<index>`` drops that row. Both are submit
    buttons of the same GET form, so both work with the script off. Removing every row leaves ONE
    empty row, so the form keeps its own language while stating no ladder. A refused op changes
    nothing, and the rows are renumbered from zero on every render.
    """
    rows = _posted_warn(pairs)
    if rows is None:
        return None
    removed = _last(pairs, WARN_REMOVE_FIELD).strip()
    if removed:
        rows = [row for row in rows if row["index"] != removed]
    if _last(pairs, WARN_ADD_FIELD).strip():
        rows = rows + [{"seconds": "", "message": ""}]
    if not rows:
        rows = [{"seconds": "", "message": ""}]
    return [dict(row, index=str(number)) for number, row in enumerate(rows)]


def warn_times_from_rows(rows) -> tuple[object, list[dict]]:
    """The ``times`` value *rows* state — a list of seconds or a map of seconds to text — with one
    refusal per row it cannot state.

    The rules, exhaustively: a row with NEITHER a number nor a text states nothing and is the empty
    row the operator types into; a row whose text stands without seconds is refused, because the
    ladder would then read as empty while a row still stands; a seconds value that is not a whole
    number above zero is refused; and a seconds value another accepted row already uses is refused,
    because a map holds one text per moment. The MAP is chosen as soon as one accepted row carries
    text, otherwise the LIST of seconds — the two shapes the schema declares. ``None`` means no
    ladder at all.
    """
    accepted: list[tuple[int, str]] = []
    errors: list[dict] = []
    for number, row in enumerate(rows, start=1):
        seconds = readmodels.text(row.get("seconds")).strip()
        message = readmodels.text(row.get("message"))
        if not seconds and not message:
            continue
        if not seconds:
            errors.append({"row": number, "msgid": WARN_ROW_WITHOUT_SECONDS})
            continue
        if not _INT_TEXT.match(seconds) or int(seconds) <= 0:
            errors.append({"row": number, "msgid": WARN_SECONDS_NOT_A_NUMBER})
            continue
        if any(int(seconds) == other for other, _text in accepted):
            errors.append({"row": number, "msgid": WARN_DUPLICATE_SECONDS})
            continue
        accepted.append((int(seconds), message))
    if not accepted:
        return None, errors
    if any(message for _seconds, message in accepted):
        # keys are STRINGS here, like every posted leaf; the ``warn.times`` marker turns them back
        # into the numbers the schema states
        return {str(seconds): message for seconds, message in accepted}, errors
    return [seconds for seconds, _message in accepted], errors


def _apply_warn_rows(tree: dict, markers: dict, rows) -> None:
    """Write the ladder *rows* state into *tree*, choosing the shape the review step will write.

    The ladder always REPLACES ``warn.times`` when the form posts rows, so a time the operator
    removed cannot survive, and the shape marker follows the composed value rather than the file's
    old shape. A ladder that states nothing drops the key, and a ``warn`` block left with no key at
    all leaves the plan, so clearing every control writes nothing.
    """
    warn = tree.get("warn")
    warn = dict(warn) if isinstance(warn, dict) else {}
    times, _errors = warn_times_from_rows(rows)
    warn.pop("times", None)
    if times is None:
        markers.pop("warn.times", None)
    else:
        warn["times"] = times
        markers["warn.times"] = "map" if isinstance(times, dict) else "seq"
    if warn:
        tree["warn"] = warn
    else:
        tree.pop("warn", None)


def _plan_warn_rows(warn) -> list[dict]:
    """The ladder rows a plan states, in the file's own order; a map keeps its per-row texts."""
    times = warn.get("times") if isinstance(warn, dict) else None
    if isinstance(times, dict):
        return [{"seconds": readmodels.text(second), "message": readmodels.text(text)}
                for second, text in times.items()]
    if isinstance(times, (list, tuple)):
        return [{"seconds": readmodels.text(second), "message": ""} for second in times]
    return []


def _warn_edit(plan, pairs) -> dict:
    """Step 4's ladder as the page draws it, plus the shape the review step writes and the refusals.

    A posted form is the ladder (with its row op applied); otherwise the plan's own ``times`` is, and
    an empty ladder keeps ONE empty row so there is a control to type into. The refusal lines come
    from the SAME :func:`warn_times_from_rows` the save's plan is composed from, so a row the page
    marks as refused is a row the plan does not carry.
    """
    plan = plan if isinstance(plan, dict) else {}
    posted = warn_rows_from_pairs(pairs)
    rows = posted if posted is not None else _plan_warn_rows(plan.get("warn"))
    stated = list(rows)
    if not rows:
        rows = [{"seconds": "", "message": ""}]
    times, errors = warn_times_from_rows(rows)
    refusals = {error["row"]: error["msgid"] for error in errors}
    drawn = [{"index": str(number), "number": number + 1,
              "seconds": readmodels.text(row.get("seconds")),
              "message": readmodels.text(row.get("message")),
              "error": refusals.get(number + 1, "")}
             for number, row in enumerate(rows)]
    block = plan.get("warn")
    warn = block if isinstance(block, dict) else {}
    inner = warn.get("countdown")
    countdown = inner if isinstance(inner, dict) else {}
    if times is None:
        shape, sentence = "", WARN_NO_LADDER
    elif isinstance(times, dict):
        shape, sentence = "map", WARN_WRITES_MAP
    else:
        shape, sentence = "list", WARN_WRITES_LIST
    return {
        "rows": drawn,
        "stated": [{"seconds": readmodels.text(row.get("seconds")),
                    "message": readmodels.text(row.get("message"))} for row in stated],
        "shape": shape,
        "sentence": sentence,
        "message": warn_preview(warn.get("message")),
        "countdown": warn_preview(countdown.get("message")),
    }



# ----------------------------------------------------------------------------- the closed picks
# A pick is CLOSED when the bot can enumerate the choices: the step then draws a multiple <select> and
# NO free-text box beside it, because a typed value must never override a list the bot produced. The
# text box comes back only when the read answered nothing, with one sentence saying why.
#
# The two multiple selects order their options differently, and the difference is the bot's:
#   * mission ids / mission files — nothing fixes their order, so the PICKED entries stand first, in
#     the plan's own order, and the rest follow in the server's; a re-post then keeps that order;
#   * the preset pick — the bot applies a pick in the order the presets stand in their FILES (one
#     refusal and the whole pick is dropped), so its options stand in the files' order, exactly as the
#     launch dialog states it.

def _stated(value) -> list[str]:
    """A plan value as the entries it states — one entry for a scalar, ``[]`` for nothing."""
    if isinstance(value, (list, tuple)):
        return [readmodels.text(item) for item in value if readmodels.text(item)]
    text = readmodels.text(value)
    return [text] if text else []


def _closed_pick(options, stated, reason) -> dict:
    """One closed pick: ``offered``, its option rows, and the sentence when it is not offered.

    *options* is the read's answer as ``[(value, label)]`` in its own order, *stated* what the plan
    states in the plan's own order. The stated entries are drawn FIRST and selected, so the plan's
    order survives the round trip; an entry the read does not carry keeps its own option (labelled with
    the value) rather than being dropped. No option at all — a failed read, or a server with no mission
    — is not offered, and *reason* is what the step says beside its text box, whose ``text`` is the
    stated value as the reader writes it back.
    """
    rows = [(readmodels.text(value), readmodels.text(label)) for value, label in options
            if readmodels.text(value)]
    text = ", ".join(_stated(stated))
    if not rows:
        return {"offered": False, "reason": reason, "options": [], "text": text}
    labels = {value: label for value, label in rows}
    drawn: list[dict] = []
    picked: set[str] = set()
    for value in _stated(stated):
        if value in picked:
            continue
        picked.add(value)
        drawn.append({"value": value, "label": labels.get(value, value), "selected": True})
    drawn += [{"value": value, "label": label, "selected": False}
              for value, label in rows if value not in picked]
    return {"offered": True, "reason": "", "options": drawn, "text": text}


def mission_id_pick(missions, current) -> dict:
    """The mission ID pick, from the DCS mission list: value is the 1-based index, label the name.

    *missions* is the ``get_mission_list`` answer — only the configured missions, in ``missionList``
    order. A file the directory scan finds but the server does not list is NOT an option here.
    """
    options = [(entry.get("index"), entry.get("name")) for entry in missions or ()
               if isinstance(entry, dict)]
    return _closed_pick(options, current, MISSION_LIST_UNAVAILABLE)


def mission_file_pick(missions, current) -> dict:
    """The mission FILE pick, from the directory scan: value and label are the relative path.

    *missions* is the ``list_missions`` answer — every ``.miz`` / ``.sav`` under the missions
    directory, recursive, whether or not the DCS mission list carries it. A failed scan is its own
    sentence, so it never blanks the id pick beside it.
    """
    options = [(entry.get("path"), entry.get("path")) for entry in missions or ()
               if isinstance(entry, dict)]
    return _closed_pick(options, current, MISSION_FILES_UNAVAILABLE)


def preset_pick(records, current) -> dict:
    """The PRESET pick: one ``<optgroup>`` per preset FILE, one option per preset it defines.

    *records* is the node read's ``[(file label, path, names)]``; an option's value is
    :func:`core.utils.presets.preset_option_value` of the file label and the name, which is exactly
    the entry ``_run_with_presets`` resolves by its own file label. The plan's entries are matched
    against those values and marked selected; an entry the files no longer carry (a BARE name, or one
    whose file is gone) is kept in its own group, so the step never silently drops it. No file that
    answered with a name means the read is not offered and the step falls back to its text box.
    """
    stated = _stated(current)
    groups: list[dict] = []
    known: set[str] = set()
    for label, _path, names in records or ():
        options: list[dict] = []
        for name in names or ():
            value = preset_option_value(readmodels.text(label), readmodels.text(name))
            known.add(value)
            options.append({"value": value, "label": readmodels.text(name),
                            "selected": value in stated})
        if options:
            groups.append({"label": readmodels.text(label), "options": options})
    if not groups:
        return {"offered": False, "reason": PRESET_LIST_UNAVAILABLE, "groups": [],
                "text": ", ".join(stated)}
    carried = [entry for entry in stated if entry not in known]
    if carried:
        groups.append({"label": i18n._(PICK_NOT_IN_FILES),
                       "options": [{"value": entry, "label": entry, "selected": True}
                                   for entry in carried]})
    return {"offered": True, "reason": "", "groups": groups, "text": ", ".join(stated)}


def startup_picks(startup, missions=None, mission_files=None, presets=None) -> dict:
    """One block's three closed picks, from its ``mission_id`` / ``mission_file``/ ``settings`` keys.

    The same three picks serve step 2's ``startup`` block and one entry of step 3 (an
    ``action_map`` states the same keys), so a page can never offer two different controls for one
    question. The two mission picks read DIFFERENT sources: ``missions`` is the DCS mission list
    (``get_mission_list``, index + name) and ``mission_files`` the missions-directory scan
    (``list_missions``, relative path). Each is ``None`` until the page has made its read, and a pick
    with no read behind it is NOT offered: a step rendered without it draws its text box and says why,
    so it never claims a list it did not read. One failed read leaves the other pick offered.
    """
    block = startup if isinstance(startup, dict) else {}
    return {
        "mission_id": mission_id_pick(missions, block.get("mission_id")),
        "mission_file": mission_file_pick(mission_files, block.get("mission_file")),
        "presets": preset_pick(presets, block.get("settings")),
    }


def startup_switch(pairs, startup) -> bool:
    """Whether this step states a ``startup`` block at all: the posted switch, else the block's own.

    A form that carries :data:`STARTUP_FLAG` decides — that is what makes a script-less browser
    work, because the checkbox is an ordinary field of the same GET form and Next re-renders the
    step with the fields in view. A form that does not carry it is not step 2, so the plan answers
    instead: a block the file states always opens the step with the switch ON, so nothing it says
    is hidden behind a control nobody ticked.
    """
    flag = _posted(pairs, STARTUP_FLAG)
    if flag is not None:
        return flag.strip() == STARTUP_FLAG_ON
    return isinstance(startup, dict) and bool(startup)


def startup_pick_kind(pairs, startup) -> str:
    """Which of the two mission picks this start states: the posted radio, else the block's shape.

    A block that carries ``mission_file`` and no ``mission_id`` is a file start; anything else —
    ``mission_id``, both, or neither — is the ID pick, which is the default a fresh start takes.
    """
    posted = _posted(pairs, STARTUP_PICK)
    if posted in STARTUP_PICK_KINDS:
        return posted
    block = startup if isinstance(startup, dict) else {}
    if "mission_file" in block and "mission_id" not in block:
        return STARTUP_PICK_FILE
    return STARTUP_PICK_ID


def wizard_view(effective, pairs, raw=None, default=None, *, step=None, now=None,
                missions=None, mission_files=None, presets=None) -> dict:
    """Everything one wizard step renders, from the plan and nothing else."""
    step = normalise_step(step)
    plan = wizard_plan(effective, pairs)
    states = {row["key"]: row["state"] for row in key_states(raw, default)}
    timezone_name = readmodels.text(plan.get("timezone"))
    warn = plan.get("warn") if isinstance(plan.get("warn"), dict) else None
    warn_edit = _warn_edit(plan, pairs)
    week_rows, week_errors = _week_edit_rows(plan, pairs)
    startup = plan.get("startup") if isinstance(plan.get("startup"), dict) else {}
    # every ``startup`` key the step does NOT render as a control rides along hidden — ``presets``
    # included, because the pick writes ``settings`` and never rewrites the configured file path
    startup_extra = [{"name": name, "value": value}
                     for name, value in _leaves("startup", startup)
                     if name.split(".", 1)[1] not in RENDERED_STARTUP_KEYS]
    commands: list[dict] = []
    for key in COMMAND_KEYS:
        value = plan.get(key)
        shape = "map" if isinstance(value, dict) else "str"
        command = sleep = ""
        if isinstance(value, dict):
            command = readmodels.text(value.get("command"))
            sleep = readmodels.text(value.get("sleep"))
        else:
            command = readmodels.text(value)
        commands.append({
            "key": key,
            "label": KEY_LABELS[key],
            "shape": shape,
            "command": command,
            "sleep": sleep,
            # only the mission start carries a delay; the other three keep a stated value untouched
            # by carrying it hidden, never by drawing a control for it
            "has_delay": key == "onSimulationStart",
        })
    return {
        "step": step,
        "plan": plan,
        "timezone": timezone_name,
        "timezone_options": timezone_options(timezone_name),
        "days": WEEKDAYS,
        "state": states,
        "week_rows": week_rows,
        "week_errors": week_errors,
        "week_start_field": WEEK_START,
        "week_end_field": WEEK_END,
        "week_days_field": WEEK_DAYS,
        "week_key_field": WEEK_KEY,
        "week_add_field": WEEK_ADD_FIELD,
        "week_remove_field": WEEK_REMOVE_FIELD,
        "actions": _action_edit_rows(plan, timezone_name, now, missions, mission_files, presets,
                                     pairs, raw),
        "action_add_field": ACTION_ADD_FIELD,
        "action_remove_field": ACTION_REMOVE_FIELD,
        "action_add_label": ACTION_ADD_LABEL,
        "action_remove_label": ACTION_REMOVE_LABEL,
        "action_no_trigger": ACTION_NO_TRIGGER,
        "action_op_refusal": action_row_refusal(pairs),
        # step 3's ONE trigger, the times row list and the method's own controls
        "action_trigger_field": ACTION_TRIGGER_FIELD,
        "action_trigger_question": ACTION_TRIGGER_QUESTION,
        "action_trigger_lead": ACTION_TRIGGER_LEAD,
        "action_trigger_dropped": ACTION_TRIGGER_DROPPED,
        # an entry whose only trigger is the deprecated pair: its block is read-only
        "action_legacy_title": ACTION_LEGACY_TITLE,
        "action_legacy_lead": ACTION_LEGACY_LEAD,
        "action_legacy_kept": ACTION_LEGACY_KEPT,
        "action_trigger_hints": ACTION_TRIGGER_HINTS,
        "action_triggers": [{"kind": kind, "label": ACTION_TRIGGER_LABELS[kind]}
                            for kind in ACTION_TRIGGERS],
        "action_default_trigger": ACTION_DEFAULT_TRIGGER,
        "action_time_field": ACTION_TIME_FIELD,
        "action_time_add_field": ACTION_TIME_ADD_FIELD,
        "action_time_remove_field": ACTION_TIME_REMOVE_FIELD,
        "action_methods": ACTION_METHODS,
        "action_method_lead": ACTION_METHOD_LEAD,
        "action_password_label": ACTION_PASSWORD_LABEL,
        "action_password_hint": ACTION_PASSWORD_HINT,
        "action_pick_field": ACTION_PICK_FIELD,
        "action_pick_labels": STARTUP_PICK_LABELS,
        "action_pick_lead": ACTION_PICK_LEAD,
        "warn": warn_view(warn),
        "warn_times": warn_edit["stated"],
        "warn_rows": warn_edit["rows"],
        "warn_write_shape": warn_edit["shape"],
        "warn_write_sentence": warn_edit["sentence"],
        "warn_message_preview": warn_edit["message"],
        "warn_countdown_preview": warn_edit["countdown"],
        "warn_placeholders": [{"name": "{" + key + "}", "meaning": meaning}
                              for key, meaning in WARN_PLACEHOLDERS],
        "warn_add_field": WARN_ADD_FIELD,
        "warn_remove_field": WARN_REMOVE_FIELD,
        "warn_time_field": WARN_TIME,
        "warn_text_field": WARN_TEXT,
        "startup": startup_rows(startup),
        "startup_extra": startup_extra,
        "picks": startup_picks(startup, missions, mission_files, presets),
        # step 2's switch and the one of the two mission picks it states
        "startup_on": startup_switch(pairs, startup),
        "startup_flag": STARTUP_FLAG,
        "startup_flag_on": STARTUP_FLAG_ON,
        "startup_flag_off": STARTUP_FLAG_OFF,
        "startup_on_label": STARTUP_ON_LABEL,
        "startup_off_what": STARTUP_OFF_WHAT,
        "startup_off_deed": STARTUP_OFF_DEED,
        "startup_pick_field": STARTUP_PICK,
        "startup_pick": startup_pick_kind(pairs, startup),
        "startup_pick_labels": STARTUP_PICK_LABELS,
        "startup_pick_lead": STARTUP_PICK_LEAD,
        "commands": commands,
        "carried": carried_fields(plan, step),
        "markers": shape_markers(plan),
        "action_keys": ACTION_KEYS,
        "command_keys": COMMAND_KEYS,
    }


# ===================================================================================== the write
# WHAT A SAVE MAY STATE, and what "the operator changed it" means per key. The wizard edits ONE
# server's own block; every key whose value equals what the block inherits is NOT written, and a key
# the block carries that the plan no longer states is DELETED, so "back to inherited" is a real
# outcome rather than a silently kept override. The override is computed ONCE, by
# :func:`minimal_override`, and both the review step and the save read that one answer.

#: the plan-key refusals, each one sentence an operator can act on. ``REFUSED_FOREIGN_KEY`` covers
#: every name outside the tab's own language at once, so it never discloses another block's contents.
REFUSED_STARTUP_DELAY = i18n._("The startup delay is one value shared by every server, so this tab "
                               "does not write it. Nothing was written.")
REFUSED_DEPRECATED_KEY = i18n._("local_times and utc_times are deprecated; this tab refuses to write "
                                "either of them. Nothing was written.")
REFUSED_FOREIGN_KEY = i18n._("That request names a value this tab does not write, so nothing was "
                             "written.")

#: step 3's refusals for a row op, one sentence each. A removal names a position the form itself
#: posted, so a value that is not a number and a number no posted entry carries are both refused:
#: a mis-click must never drop a different entry than the one the button named.
ACTION_REMOVE_NOT_A_POSITION = i18n._("A removal must name an entry by its position number, so "
                                      "nothing was removed.")
ACTION_REMOVE_NO_SUCH_ENTRY = i18n._("This plan has no entry at that position, so nothing was "
                                     "removed.")

#: what a card says about an entry that states no trigger. A new entry starts like that, so the
#: sentence has to be readable without knowing which entry is new.
ACTION_NO_TRIGGER = i18n._("Nothing happens for this entry until a trigger is stated: times of day, "
                           "a cron expression, minutes of mission time, minutes of server runtime, "
                           "or minutes with nobody on the server.")

#: the posted field an Expert Mode save states the server's WHOLE block in, beside the Expert flag.
BLOCK_YAML_FIELD = "block_yaml"

#: the form fields that are NOT plan values: the console's own carriers, the tab's scope marker, the
#: Expert Mode text (which is the block, not a plan — :func:`expert_block` reads it), step 2's two
#: carriers — its switch and the radio that names which mission pick the start states — and step 3's
#: two row submits. Every one of them is stated here, or a save refuses the page's own form.
_AUX_FIELDS = frozenset({"_scope", "_revision", "_csrf_token", "tab", "step", BLOCK_YAML_FIELD,
                         STARTUP_FLAG, STARTUP_PICK, ACTION_TIME_ADD_FIELD, ACTION_TIME_REMOVE_FIELD})

#: the aux names a step posts as a PREFIX (``name.<index>[.<row>]``): they carry the operator's
#: CHOICE — which trigger, which time row, which mission pick — and never a plan value.
_AUX_PREFIXES = frozenset({ACTION_TRIGGER_FIELD, ACTION_TIME_FIELD, ACTION_PICK_FIELD,
                           WEEK_START, WEEK_END, WEEK_DAYS, WEEK_KEY})

#: the keys a save may PRUNE from an ``action`` entry: the keys the step renders, plus ``presets`` —
#: the configured file path a bare pick name falls back to, which rides hidden but whose USE follows
#: the method, so a method that takes no presets must be able to delete it. ``shutdown`` is therefore
#: absent: the step carries it hidden, so the plan always states it when the file does.
PRUNABLE_ACTION_KEYS: tuple[str, ...] = RENDERED_ACTION_KEYS + ("presets",)

#: the ``startup`` block's keys the wizard renders, in the schema's order.
STARTUP_KEYS: tuple[str, ...] = tuple(key for key, _label in _STARTUP_KEYS)

#: the ``warn`` block's own keys, and the ``countdown`` map's sub-keys.
WARN_KEYS: tuple[str, ...] = ("message", "sound", "times", "countdown")
WARN_COUNTDOWN_KEYS: tuple[str, ...] = ("time", "message")


def posted_key_refusal(pairs) -> str:
    """The first refusal a POSTED form warrants by its field NAMES alone, or ``""``.

    A posted plan is a flat set of dotted names, so every name's FIRST segment states which top-level
    key it would write. Three readings, in this order, and no others:

    * ``startup_delay`` — a DEFAULT-level value this tab never writes, refused by its own sentence;
    * a name outside :data:`WIZARD_KEYS` (``DEFAULT``, another server's block, a node's block) — the
      request is asking to write something this tab does not own, refused without naming it;
    * ``action.<index>.local_times`` / ``utc_times`` — the deprecated pair, refused where it occurs.

    ``shape.*`` markers and the console's own ``_``-prefixed carriers are not plan values and are
    skipped. Nothing here writes: it is the same reading a save and a validation make.
    """
    items = pairs.multi_items() if hasattr(pairs, "multi_items") else list(pairs)
    for name, _value in items:
        name = readmodels.text(name)
        if (not name or name in _AUX_FIELDS or name.split(".", 1)[0] in _AUX_PREFIXES
                or name.startswith("_")):
            continue
        segments = name.split(".")
        if segments[0] == "shape":
            continue
        if segments[0] == "startup_delay":
            return REFUSED_STARTUP_DELAY
        if segments[0] not in WIZARD_KEYS:
            return REFUSED_FOREIGN_KEY
        if segments[0] == "action" and len(segments) > 2 and segments[2] in NEVER_WRITTEN:
            return REFUSED_DEPRECATED_KEY
    return ""


def _block_node(document, key):
    """The mapping *key* addresses in *document*, AS THE FILE HOLDS IT, or ``None``.

    The round-trip node is returned rather than a plain copy, so its comments survive a
    :func:`block_yaml` dump. See :func:`block_at` for the path's own rule.
    """
    node = document
    for token in tuple(key or ()):
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node if isinstance(node, dict) else None


def block_at(document, key) -> dict:
    """The block *key* addresses in *document*, or ``{}`` when the file carries none.

    The path is the one the guard built from the RESOLVED server (``(instance,)``, or
    ``(node, instance)`` when the instance name is ambiguous across nodes), so this reads the same
    block the plugin's ``get_base_config`` reads. Anything that is not a mapping is "no block".
    """
    node = _block_node(document, key)
    return dict(node) if node is not None else {}


def file_plan(document, key, default) -> dict:
    """``DEFAULT`` merged with the block at *key*, exactly as the FILE states them.

    The wizard reads its plan from here rather than from ``CPlugin.get_config``: that answer is the
    merge the RUNNING plugin cached when it loaded, so a page rendered after a save would still be
    showing the plan from before it. The file is what the save just wrote, so it is the truth the
    review step has to render.
    """
    shared = default if isinstance(default, dict) else {}
    if not key:
        return dict(shared)
    return dict(deep_merge(shared, block_at(document, key)))


def _plain_value(value):
    """*value* with every round-trip container turned into a plain dict/list — for COMPARISON only."""
    if isinstance(value, dict):
        return {name: _plain_value(inner) for name, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_value(inner) for inner in value]
    return value


def _aligned_entries(carried, wanted) -> list[tuple[dict, dict]]:
    """The ``(file entry, plan entry)`` pairs a write merges IN PLACE, or none.

    :func:`_assign` merges entry by entry only when both sides are the same shape and, for a
    sequence, the same length; anything else replaces the value wholesale, leaving no entry to
    prune.
    """
    if isinstance(carried, dict) and isinstance(wanted, dict):
        return [(carried, wanted)]
    if (isinstance(carried, list) and isinstance(wanted, list) and len(carried) == len(wanted)
            and all(isinstance(item, dict) for item in carried + wanted)):
        return list(zip(carried, wanted))
    return []


def _map_prune(carried, wanted, modelled) -> list[str]:
    """The keys *carried* states that *modelled* covers and *wanted* no longer states."""
    if not isinstance(carried, dict):
        return []
    stated = wanted if isinstance(wanted, dict) else {}
    return [key for key in modelled if key in carried and key not in stated]


def _entry_prune(carried, wanted, modelled) -> list[list[str]]:
    """The same answer per ENTRY of a ``seq_or_map`` value, aligned with the file's entries."""
    return [_map_prune(entry, other, modelled) for entry, other in _aligned_entries(carried, wanted)]


def _prune_spec(block, plan) -> dict:
    """The sub-keys a save REMOVES from one block, as ``{block key: ...}``.

    ``"action"`` carries one key list per entry, aligned with the file's entries; ``"startup"``,
    ``"warn"`` and ``"countdown"`` carry a plain list. A candidate is a key this tab MODELS
    (:data:`PRUNABLE_ACTION_KEYS`, :data:`STARTUP_KEYS`, :data:`WARN_KEYS`,
    :data:`WARN_COUNTDOWN_KEYS`) that the file carries and the plan no longer states — so an entry's
    key the wizard does not model, and the deprecated ``local_times`` / ``utc_times``, is never
    removed.
    """
    warn = block.get("warn")
    wanted_warn = plan.get("warn")
    return {
        "action": _entry_prune(block.get("action"), plan.get("action"), PRUNABLE_ACTION_KEYS),
        "startup": _map_prune(block.get("startup"), plan.get("startup"), STARTUP_KEYS),
        "warn": _map_prune(warn, wanted_warn, WARN_KEYS),
        "countdown": _map_prune(
            warn.get("countdown") if isinstance(warn, dict) else None,
            wanted_warn.get("countdown") if isinstance(wanted_warn, dict) else None,
            WARN_COUNTDOWN_KEYS),
    }


def minimal_override(inherited, block_raw, plan) -> dict:
    """The per-key override *plan* states over the block that already exists: ``{set, delete, prune}``.

    ``inherited`` is the ``DEFAULT`` block, ``block_raw`` the server's own block as the file states
    it and ``plan`` what the wizard states. The four rules, exhaustively:

    * a plan value EQUAL to the inherited one is not written (a key the block already carries is
      DELETED, so the value goes back to following ``DEFAULT``);
    * a key the block carries that the plan does not state is DELETED — for the plan's own keys only,
      so a key this tab never edits cannot be lost;
    * everything else the plan states is written;
    * inside an ``action`` entry, the ``startup`` block and the ``warn`` block, a MODELLED sub-key
      the file carries and the plan stops stating is PRUNED (see :func:`_prune_spec`): a write
      merges the maps and lists it is handed, so a value the operator switched off would otherwise
      survive the save and stay in force.

    Only :data:`WIZARD_KEYS` are considered: ``startup_delay`` and the deprecated pair are outside
    the tab's language, so no plan can express them and no override can delete them.
    """
    shared = inherited if isinstance(inherited, dict) else {}
    block = block_raw if isinstance(block_raw, dict) else {}
    wanted = plan if isinstance(plan, dict) else {}
    writes: dict = {}
    deletes: list[str] = []
    for key in WIZARD_KEYS:
        if key in wanted:
            if key in shared and _plain_value(wanted[key]) == _plain_value(shared[key]):
                if key in block:
                    deletes.append(key)
                continue
            writes[key] = _plain_value(wanted[key])
        elif key in block:
            deletes.append(key)
    return {"set": writes, "delete": deletes, "prune": _prune_spec(block, wanted)}


#: maps whose sub-keys are ONE statement, written whole rather than merged. A week is a partition of
#: the day: a range key the plan stops stating is not a value worth keeping, it is a leftover that
#: would run beside the new one. Merging ``schedule`` is how a save ends up with ``00-12`` AND
#: ``00:00-12:00`` in the same block, both matching the day.
ATOMIC_MAPS: tuple[str, ...] = ("schedule",)


def _assign(container, key: str, value) -> None:
    """Write *value* at *container[key]*, merging into the mapping/sequence already there.

    A mapping is merged KEY BY KEY and a sequence of mappings entry by entry, so the keys this tab
    does not state — an ``action`` entry's deprecated ``local_times``, a comment, a sibling key —
    survive a save. An :data:`ATOMIC_MAPS` key is the exception: it is replaced whole. Only an
    override's own TOP-LEVEL ``delete`` list removes anything else.
    """
    if key in ATOMIC_MAPS and isinstance(container, dict):
        container[key] = value
        return
    current = container.get(key) if isinstance(container, dict) else None
    if isinstance(current, dict) and isinstance(value, dict):
        for name in list(value):
            _assign(current, name, value[name])
        return
    if (isinstance(current, list) and isinstance(value, list) and len(current) == len(value)
            and all(isinstance(item, dict) for item in current + value)):
        for inner, written in zip(current, value):
            for name, leaf in written.items():
                _assign(inner, name, leaf)
        return
    container[key] = value


def _has_prune(prune) -> bool:
    """Whether a prune spec removes anything: a flat key list or a per-entry list of them."""
    for keys in (prune or {}).values():
        if any(item for item in (keys or ())):
            return True
    return False


def _prune_entry(entry, keys) -> None:
    """Delete *keys* from one mapping, leaving every other key of the entry untouched."""
    if not isinstance(entry, dict):
        return
    for key in keys or ():
        if key in entry:
            del entry[key]


def _prune_block(block, prune) -> None:
    """Apply a :func:`_prune_spec` answer to one block IN PLACE, one entry at a time.

    The block is never replaced: only the named sub-keys are removed, so an entry's unmodelled keys
    and the deprecated ``local_times`` / ``utc_times`` stay where they are.
    """
    if not prune:
        return
    _prune_entry(block.get("startup"), prune.get("startup"))
    warn = block.get("warn")
    _prune_entry(warn, prune.get("warn"))
    if isinstance(warn, dict):
        _prune_entry(warn.get("countdown"), prune.get("countdown"))
    action = block.get("action")
    entries = action if isinstance(action, list) else [action]
    for entry, keys in zip(entries, prune.get("action") or ()):
        _prune_entry(entry, keys)


#: the scheduler block file this tab writes, named in a refusal when the caller states no other.
DEFAULT_FILE_NAME = "scheduler.yaml"
#: a document that holds no server blocks: a list or a scalar has no key a block can be written under.
NOT_A_MAPPING = "{name} is not a mapping of server blocks."


def require_mapping(document, name: str) -> None:
    """Refuse *document* when it is not a mapping of server blocks.

    A list or a scalar has no key a block could be written under, and the round-trip containers raise
    a ``TypeError`` from somewhere inside their own indexing when handed one; refusing here names the
    file instead and leaves the caller's ``ConfigWriteError`` handling to answer.
    """
    if not isinstance(document, dict):
        raise configwrite.ConfigWriteError(NOT_A_MAPPING.format(name=name))


def _block_slot(document, key: tuple[str, ...], *, name: str = DEFAULT_FILE_NAME):
    """The mapping at *key* in *document*, created (with any missing parent) when it is absent.

    The path is the guard's — ``(instance,)`` or ``(node, instance)`` — and a parent that is not a
    mapping is replaced by one, so a malformed intermediate never silently swallows the write. A
    document that is not a mapping at all is refused: there is no slot to create in it.
    """
    from ruamel.yaml.comments import CommentedMap

    require_mapping(document, name)
    tokens = tuple(key or ())
    if not tokens:
        raise ValueError("a block path names at least one token")
    node = document
    for token in tokens[:-1]:
        child = node.get(token) if isinstance(node, dict) else None
        if not isinstance(child, dict):
            child = CommentedMap()
            node[token] = child
        node = child
    last = tokens[-1]
    if not isinstance(node.get(last), dict):
        node[last] = CommentedMap()
    return node[last]


def apply_block(document, key: tuple[str, ...], block, *, name: str = DEFAULT_FILE_NAME) -> None:
    """REPLACE the block at *key* in *document* with *block* — the Expert Mode write.

    The posted text IS the block: an entry the operator deleted is gone, a key they added is
    written, and the comments the text carries are the comments the file gets at that block. Every
    other block of the document is left as it was, which is what keeps ``DEFAULT`` and the other
    servers out of reach. A missing intermediate map is created the way a "+ Add block" creates one;
    a document that is not a mapping at all is refused before anything is assigned.
    """
    from ruamel.yaml.comments import CommentedMap

    require_mapping(document, name)
    tokens = tuple(key or ())
    node = document
    for token in tokens[:-1]:
        child = node.get(token) if isinstance(node, dict) else None
        if not isinstance(child, dict):
            child = CommentedMap()
            node[token] = child
        node = child
    node[tokens[-1]] = block


def apply_override(document, key, override, *, name: str = DEFAULT_FILE_NAME) -> None:
    """Apply a :func:`minimal_override` answer at *key* in *document*, in place.

    Missing intermediate maps are created the way a "+ Add block" creates one, so a server with no
    block of its own gets its block INSIDE this same write, carrying only the keys the operator
    actually changed. An override that neither sets, deletes nor prunes anything touches nothing —
    no empty block is created for a server that simply inherited. Order is deletes, then the
    sub-key prune, then the writes, so a pruned key can never be re-added by the same call. A
    document that is not a mapping at all is refused before anything is assigned.
    """
    require_mapping(document, name)
    writes = override.get("set") or {}
    deletes = override.get("delete") or []
    prune = override.get("prune") or {}
    if not writes and not deletes and not _has_prune(prune):
        return
    block = _block_slot(document, key, name=name)
    for dropped in deletes:
        if dropped in block:
            del block[dropped]
    _prune_block(block, prune)
    for written, value in writes.items():
        _assign(block, written, value)


# ------------------------------------------------------------ the block as YAML, written and posted
# ONE block, in the file's own words. :func:`block_yaml` is what the review step PRINTS (the block a
# save of the plan produces, read off a copy of the document, so DEFAULT and the other servers are
# never part of it); :func:`expert_block` is what an Expert Mode post MEANS (the text IS the block).
# Both speak the same key language: :data:`WIZARD_KEYS` — the keys a server's own block carries.

#: a block whose text does not parse. One sentence, and nothing is written.
EXPERT_NOT_YAML = i18n._("That text is not valid YAML, so nothing was written.")
#: a block that parses but is not a mapping — a list or a bare scalar cannot be a server's block.
EXPERT_NOT_A_MAPPING = i18n._("A server's block is a set of keys and values, and that text is not "
                              "one, so nothing was written.")
#: a key this tab does not own. Names the KEY only, never what it holds: ``DEFAULT``, ``commands``,
#: ``chat_commands`` and another server's block all fall under this one sentence.
EXPERT_FOREIGN_KEY = i18n._("That text states the key '%(key)s', which is not part of a server's own "
                            "block, so nothing was written.")


def expert_block(text) -> dict:
    """The block an Expert Mode post states, or the refusal it warrants — ``{block, refusal, params}``.

    ``block`` is the round-trip mapping the text holds (comments included, as typed) and ``refusal``
    is ``""``; otherwise ``block`` is ``None`` and ``refusal`` is one msgid with ``params`` for its
    interpolation. Three readings, in this order, and no others: the text must parse, it must be a
    mapping, and every top-level key must be one the server's own block may carry.
    """
    from core.utils import configwrite

    cleaned = "" if text is None else str(text)
    try:
        parsed = configwrite.load_config_text(cleaned, path=BLOCK_YAML_FIELD)
    except configwrite.ConfigWriteError:
        return {"block": None, "refusal": EXPERT_NOT_YAML, "params": {}}
    if not isinstance(parsed, dict):
        return {"block": None, "refusal": EXPERT_NOT_A_MAPPING, "params": {}}
    for key in parsed:
        if str(key) not in WIZARD_KEYS:
            return {"block": None, "refusal": EXPERT_FOREIGN_KEY, "params": {"key": str(key)}}
    return {"block": parsed, "refusal": "", "params": {}}


def saved_block(document, key, inherited, block_raw, plan):
    """The block a save of *plan* leaves at *key*, read off a COPY of *document*.

    The same override the save applies (:func:`minimal_override` + :func:`apply_override`), applied
    to a copy so the render cannot mutate what it is reading. The round-trip node is returned, so
    the comments inside the block survive :func:`block_yaml`; ``None`` when the save leaves the
    server without a block of its own.
    """
    written = copy.deepcopy(document)
    apply_override(written, key, minimal_override(inherited, block_raw, plan))
    return _block_node(written, key)


def block_yaml(document, key, inherited, block_raw, plan) -> str:
    """The block a save of *plan* writes for *key*, serialised as YAML — ``""`` when it writes none.

    The text an expert would have written for THIS server and no other: the file's own round trip
    produces it, so a comment inside the block is kept where the round trip can keep it. A block the
    save does not create (nothing changed, and the server had none) yields ``""``.
    """
    from core.utils import configwrite

    block = saved_block(document, key, inherited, block_raw, plan)
    if not block:
        return ""
    return configwrite.dump_config(block)


def _preview(value) -> str:
    """One written value as the review step prints it: a scalar verbatim, a list joined, a map as YAML-ish text."""
    plain = _plain_value(value)
    if isinstance(plain, bool):
        return i18n._("yes") if plain else i18n._("no")
    if isinstance(plain, (list, tuple)):
        return ", ".join(readmodels.text(item) for item in plain)
    if isinstance(plain, dict):
        return " · ".join(f"{name}: {_preview(inner)}" for name, inner in plain.items())
    return readmodels.text(plain)


#: the label each pruned sub-key group renders under on the review step.
PRUNE_LABELS: dict[str, str] = {
    "startup": KEY_LABELS["startup"],
    "warn": KEY_LABELS["warn"],
    "countdown": i18n._("Countdown"),
    "action": KEY_LABELS["action"],
}


def prune_rows(override) -> list[dict]:
    """The review step's rows for the sub-keys *override* removes, one row per group.

    Read straight from the SAME override the save applies, never recomputed. ``action`` yields one row
    per entry, labelled with the entry's 1-based position; ``startup``, ``warn`` and ``countdown``
    yield one flat row each. A group whose top-level key the override deletes outright is skipped:
    the whole block leaves, so naming its sub-keys would promise less than the save does.
    """
    prune = override.get("prune") or {}
    deletes = set(override.get("delete") or ())
    rows: list[dict] = []
    if "startup" not in deletes and prune.get("startup"):
        rows.append({"key": "startup", "label": PRUNE_LABELS["startup"], "entry": None,
                     "subkeys": list(prune["startup"])})
    if "warn" not in deletes:
        if prune.get("warn"):
            rows.append({"key": "warn", "label": PRUNE_LABELS["warn"], "entry": None,
                         "subkeys": list(prune["warn"])})
        if prune.get("countdown"):
            rows.append({"key": "warn.countdown", "label": PRUNE_LABELS["countdown"],
                         "entry": None, "subkeys": list(prune["countdown"])})
    if "action" not in deletes:
        for index, keys in enumerate(prune.get("action") or (), start=1):
            if keys:
                rows.append({"key": f"action.{index - 1}", "label": PRUNE_LABELS["action"],
                             "entry": index, "subkeys": list(keys)})
    return rows


def review_view(inherited, block_raw, plan) -> dict:
    """The review step's reading of the SAME override a save writes.

    ``override`` is :func:`minimal_override`'s answer, unchanged — the step renders it and the save
    writes it, so the page cannot promise a diff the writer does not make. One row per written key,
    one per deleted key and one per pruned sub-key group, each with the label the landing uses.
    """
    override = minimal_override(inherited, block_raw, plan)
    writes = [{"key": key, "label": KEY_LABELS.get(key, key), "value": _preview(value)}
              for key, value in override["set"].items()]
    deletes = [{"key": key, "label": KEY_LABELS.get(key, key)} for key in override["delete"]]
    prunes = prune_rows(override)
    return {
        "set": writes,
        "delete": deletes,
        "prune": prunes,
        "changed": bool(writes or deletes or prunes),
        "count": len(writes) + len(deletes) + len(prunes),
    }
