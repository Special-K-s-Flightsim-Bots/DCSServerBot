"""The Presets page: the MizEdit preset files this installation names, one card per preset.

A preset is a MizEdit mission-customizer document — ``config/presets.yaml``, any ``presets*.yaml``, or
``config/presets/`` — whose value is either a MAP (the preset's own weather and mission values) or a
LIST of other preset names (the COMBINATION mechanism). A preset file is global configuration, not a
server, so this page is Admin-only and carries NO manager scope grant: a manager's scope is a set of
servers, and "one of mine runs there" must never become "let me edit a preset".

The page is a standalone entry, built like the Bans page: one capability, one nav item and one page
route, each naming the SAME ``presets.view`` string, so the sidebar cannot offer a door the route
refuses. Two further GETs serve the node's own cloud presets (the vendor ``Config/Effects/clouds.lua``
list as JSON, and one plate per entry) under that same capability.

This module owns the AREA READ: it discovers the files the bot itself discovers, reads each through the
ONE config primitive (:func:`core.utils.configwrite.load_config_file` — never a second YAML reading),
and renders one card per top-level key carrying the preset's NAME, its KIND (its own values / a
combination / the expert DSL) and the top-level keys it writes, in the preset's own key names. Nothing
is written here — there is no write on this page.

The picture band is a placeholder until the field table lands, and only DCS's own plates will ever draw
a sky; this slice draws none. The card resolves its wizard URL from :data:`PRESETS_WIZARD_PATH`, the
URL shape the wizard's own route uses, so the link and the route can never drift.

The wizard (this module's tail) has its step AND its SHAPE in the URL — the four weather steps plus
the review, for a preset that is either its OWN values (a map the weather controls write) or a
COMBINATION (a list of other preset names merged in order). It reads the field table
(:mod:`services.webservice.weather_fields`) for every control, the node's own cloud presets for the
plates and its own halo list for the halo options and the crystal plates, resolves a combination
against the SAME reading the extension uses (:func:`expand_combination` / :func:`effective_weather`),
and dumps the produced document through the one config writer. The save is the ONE write route below.

Each step's controls are GATED by the modes the URL states (:func:`step_controls`): the cloud mode
decides which cloud values are offered, the dust-or-fog choice decides which of the two follows it,
and the halo preset decides whether the crystal plates are offered at all. A key a mode does not offer
is not drawn — it rides as a hidden field, so switching a mode never loses a value.
"""
from __future__ import annotations

import asyncio
import fnmatch
import itertools
import logging
import os
import re
import time
from collections.abc import Iterable, Mapping, MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from core.utils import configwrite

from .. import dcs_clouds, dcs_halo, dcs_install, i18n, permissions, session, weather_fields
from ..registry import NavItem
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import settings as settings_page

log = logging.getLogger(__name__)

__all__ = ["PRESETS_PATH", "PRESETS_CAPABILITY", "PRESETS_WRITE_CAPABILITY", "PRESETS_ROLES",
           "PRESETS_TEMPLATE", "PRESETS_STYLE", "PRESETS_TITLE", "PRESETS_LEAD", "PRESETS_WIZARD_PATH",
           "KIND_OWN", "KIND_COMBINATION", "KIND_MODIFY", "KIND_LABELS", "FILTER_ALL",
           "FILTER_CHOICES", "discover_preset_files", "preset_kind", "preset_keys", "read_presets",
           "read_preset_file", "PresetFileAnswer", "refusal_record", "forget_preset_documents",
           "PRESET_DOCUMENTS_MAX", "read_preset_files", "preset_tab_url", "removal_url",
           "PRESETS_KIND_LABEL", "PRESETS_ALL_FILES", "PRESETS_FILE_GONE", "PRESETS_REMOVE_LEAD",
           "PRESETS_REMOVE_DOCUMENT", "PRESETS_REMOVE_IN_USE", "PRESETS_REMOVAL_HEAD",
           "PRESETS_REMOVAL_SENTENCE", "PRESETS_REMOVAL_NOTE", "preset_removal",
           "PRESET_DOCUMENTS_MAX_BYTES", "PARSED_DOCUMENT", "UNREADABLE_FILE", "NOT_A_MAP_FILE",
           "EMPTY_FILE",
           "wizard_url", "capabilities", "nav_items", "add_routes",
           "PRESETS_CLOUDS_LIST_PATH", "PRESETS_CLOUD_IMAGE_PATH", "clouds_list_path",
           "cloud_image_path",
           "PRESETS_HALO_LIST_PATH", "PRESETS_HALO_IMAGE_PATH", "halo_list_path", "halo_image_path",
           "PRESETS_NEW_PATH", "PRESETS_WIZARD_TEMPLATE", "PRESETS_WIZARD_VALIDATE_PATH",
           "PRESETS_NEW_VALIDATE_PATH", "wizard_validate_url", "validate_preset_text",
           "WIZARD_STEPS", "WIZARD_STEP_VALUES",
           "STEP_FIELDS", "WIZARD_KEYS", "FIELD_HINTS", "new_preset_url", "wizard_base", "read_preset",
           "preset_destination", "preset_values", "preset_map_from_values", "produced_yaml",
           "start_time_display", "start_time_value",
           "wizard_rows", "sky_preview", "build_wizard_context", "step_controls", "CLOUD_VALUE_KEYS",
           "dialog_rows", "halo_row_choices", "picker_choices", "cloud_picker_choices",
           "read_node_choice_lists", "NAMED_DAYS", "row_gate", "step_controls",
           "NODE_LIST_TTL_SECONDS", "VENDOR_LIST", "PRESET_LIST", "node_list_cached",
           "remember_node_list", "forget_node_lists",
           "MIZEDIT_NAME", "MIZEDIT_DEFAULT_FILE", "mizedit_config", "mizedit_read_paths",
           "preset_read_key", "PRESET_FILE_PATTERNS", "preset_file_label", "node_preset_records",
           "node_preset_files", "node_preset_values",
           "parse_fog_rows", "serialize_fog_rows", "fog_rows_from_document", "fog_rows_from_form",
           "haze_choice", "fog_table_rows", "FOG_ADD_FIELD", "FOG_REMOVE_FIELD",
           "out_of_range_value", "PRESETS_REFUSED_OUT_OF_RANGE",
           "HALO_OFF_PRESET", "HALO_AUTO_PRESET", "MODE_KEYS", "is_real_halo",
           "WIZARD_FOG_ADD", "WIZARD_CRYSTALS_NOTE", "WIZARD_HALO_FALLBACK",
           "PRESETS_REFUSED_FOG_TIME",
           "PRESETS_SAVE_PATH", "PRESETS_REVISION_FIELD", "PRESETS_ORIGINAL_FIELD", "PRESETS_FILE_FIELD",
           "PRESETS_ACTION_FIELD", "PRESETS_ACTION_SAVE", "PRESETS_ACTION_REMOVE", "PRESETS_REMOVE_LABEL",
           "PRESET_OWNED_KEYS", "PRESETS_REFUSED_IN_USE", "PRESETS_REFUSED_DUPLICATE",
           "PRESETS_REFUSED_NO_FILE", "PRESETS_REFUSED_NO_NAME", "PRESETS_REFUSED_UNKNOWN",
           "PRESETS_REFUSED_BANNER", "PRESETS_STALE", "preset_schema_files", "preset_path",
           "PRESETS_EXPERT_FIELD", "PRESETS_YAML_FIELD", "PRESETS_REFUSED_EXPERT_YAML",
           "PRESETS_REFUSED_EXPERT_EMPTY", "PRESETS_REFUSED_EXPERT_SEVERAL",
           "PRESETS_REFUSED_EXPERT_RENAME", "WIZARD_EXPERT_LABEL", "WIZARD_EXPERT_NOTE",
           "find_preset_file", "preset_references", "preset_reference_map", "in_use_sentence",
           "mizedit_references", "combination_references",
           "scheduler_references", "SCHEDULER_CONFIG_FILE",
           "SHAPE_OWN", "SHAPE_COMBINATION", "SHAPE_FIELD", "SHAPE_CHOICES", "COMBINATION_FIELD",
           "preset_shape", "parse_combination", "combination_text", "has_preset", "expand_combination",
           "effective_weather", "combination_candidates", "preset_value_for",
           "combination_editor", "PRESETS_REFUSED_DANGLING", "PRESETS_REFUSED_EMPTY_COMBINATION",
           "PRESETS_REFUSED_UNNAMED_FILE", "PRESETS_REFUSED_NO_REVISION", "PRESETS_NO_CHANGE_CASE",
           "PRESETS_NEW_FILE_FIELD", "preset_new_file",
           "PRESETS_REFUSED_NEW_FILE_TRAVERSAL",
           "PRESETS_REFUSED_NEW_FILE_ABSOLUTE", "PRESETS_REFUSED_NEW_FILE_WINDOWS",
           "PRESETS_REFUSED_NEW_FILE_CONTROL", "PRESETS_REFUSED_NEW_FILE_SUFFIX",
           "PRESETS_REFUSED_NEW_FILE_SHAPE", "PRESETS_REFUSED_NEW_FILE_ESCAPE",
           "PRESETS_REFUSED_NEW_FILE_EXISTS",
           "WIZARD_DEST_NEW_CHOICE", "WIZARD_DEST_NEW_LABEL", "WIZARD_DEST_NEW_HELP",
           "WIZARD_DEST_NEW_ONE", "WIZARD_DEST_UNREAD", "WIZARD_DEST_UNREAD_DEFAULT",
           "mizedit_preset_files", "preset_destinations", "fog_keyframes",
           "WIZARD_DEST_NAMED", "WIZARD_DEST_NOT_YET", "WIZARD_FOG_KEEP_HEAD", "WIZARD_FOG_KEEP_NOTE",
           "PRESETS_YAML_PATH", "PRESETS_YAML_VALIDATE_PATH", "PRESETS_YAML_SAVE_PATH",
           "PRESETS_YAML_TEMPLATE", "PRESETS_YAML_TITLE", "PRESETS_YAML_LEAD",
           "PRESETS_YAML_EDITOR_LABEL", "PRESETS_YAML_HINT", "PRESETS_YAML_DOOR_LABEL",
           "PRESETS_YAML_CONVERT_HINT", "preset_yaml_url",
           "preset_yaml_validate_url", "preset_yaml_save_url", "preset_block_document",
           "preset_block_shape", "validate_preset_block", "PRESETS_REFUSED_BLOCK_NOT_A_MAP",
           "PRESETS_REFUSED_BLOCK_SEVERAL", "PRESETS_REFUSED_BLOCK_RENAME"]

PRESETS_PATH = "/presets"
PRESETS_CAPABILITY = "presets.view"
PRESETS_WRITE_CAPABILITY = "presets.write"

#: THE ONE WRITE. A preset file is written through ``configwrite.patch_config_file`` — the console's
#: single config-write primitive — so it is schema-validated, atomic, keeps a ``.bak`` and refuses a
#: stale form exactly as every other configuration write does.
PRESETS_SAVE_PATH = "/actions/presets/save"

#: the hidden fields the review step's form carries: the file revision it was rendered from (so a
#: stale form is refused), the name the wizard OPENED (``""`` for a new preset, which is what tells a
#: rename from an edit), the destination file, and which action the button names.
PRESETS_REVISION_FIELD = "_revision"
PRESETS_ORIGINAL_FIELD = "_original"
PRESETS_FILE_FIELD = "file"
#: the ONE text input the destination step's "New file…" choice reveals. A non-empty value IS the
#: destination of a new preset — the file the save creates; the offered ``file`` choice is then not
#: read. An edit/rename never reads it (it writes the file its own name was found in).
PRESETS_NEW_FILE_FIELD = "new_file"
PRESETS_ACTION_FIELD = "_action"
PRESETS_ACTION_SAVE = "save"
PRESETS_ACTION_REMOVE = "remove"
PRESETS_REMOVE_LABEL = i18n._("Remove preset")

#: THE EXPERT MODE'S TWO CARRIERS — the same convention the Settings area's own editor uses. ``_expert``
#: is a hidden companion the review step's toggle keeps at ``"0"`` (off) or ``"1"`` (on); ``_yaml``
#: carries the edited document. Both are ``_``-prefixed, so they never collide with a field the wizard
#: reads by key, and with JavaScript off neither is driven — the review stays the read-only view it was.
PRESETS_EXPERT_FIELD = "_expert"
PRESETS_YAML_FIELD = "_yaml"

#: THE KEYS THE WIZARD OWNS — the top-level preset keys its controls write, read from the ONE field
#: table (:mod:`services.webservice.weather_fields`). The save may change ONLY these; every other key
#: of a preset (``modify``, ``forcedOptions``, ``difficulty``, ``miscellaneous``, ``requiredModules``,
#: ``failures``, ``files``, ``RealWeather``, ``hidden``, ``terrain``, ``atmosphere_type``, …) comes
#: back untouched. ``name`` is the preset's KEY, not a value it carries, so it is not owned here.
PRESET_OWNED_KEYS: frozenset[str] = frozenset(
    {entry.key.split(".")[0] for entry in weather_fields.FIELDS if entry.key != "name"}
    #: ``enable_fog`` is written by the either-or control (nothing posts the key itself), so it is
    #: owned beside the keys that DO have a control: switching the choice to dust must clear it.
    | {"enable_fog"})

#: EVERY refusal is its own plain sentence, ``i18n._``-marked so the one-shot notice renders it in the
#: request's language. ``{name}``/``{who}``/``{file}`` are filled in after; the operator's own node,
#: instance, preset and file names stay verbatim (operator data, never translated).
PRESETS_REFUSED_BANNER = i18n._("The change was refused, and nothing was written.")
PRESETS_REFUSED_NO_NAME = i18n._(
    "No change was saved: the preset has no name. Give it a name — nothing was written.")
PRESETS_REFUSED_FOG_TIME = i18n._(
    "No change was saved: every fog row is keyed by whole seconds from the mission's start, and one "
    "row's time is not a whole number — nothing was written.")
#: a value outside its control's own range is refused HERE too: the browser refuses it, and a POST
#: that carries it anyway (a script, a stale page, a hand-built form) must not slip through. The range
#: is the field table's own, so the box and this sentence cannot disagree.
PRESETS_REFUSED_OUT_OF_RANGE = i18n._(
    "No change was saved: '%(label)s' accepts %(min)s to %(max)s, and the value entered is "
    "%(value)s — nothing was written.")
PRESETS_REFUSED_UNKNOWN = i18n._(
    "No change was saved: no preset named '%(name)s' is in this installation's preset files — "
    "nothing was written.")
PRESETS_REFUSED_NO_FILE = i18n._(
    "No change was saved: '%(file)s' is not a preset file this installation names, so there is "
    "nowhere to write a new preset. The destination step lists the files this installation names, "
    "and a file that is not there yet can be created with \"New file…\" — nothing was written.")
PRESETS_REFUSED_IN_USE = i18n._(
    "No change was saved: the preset '%(name)s' is still named by %(who)s, so removing or renaming "
    "it would break that reference. Remove the reference first — nothing was written.")
PRESETS_REFUSED_DUPLICATE = i18n._(
    "No change was saved: a preset named '%(name)s' already exists, so saving it would have "
    "overwritten that preset. Give it a different name — nothing was written.")
PRESETS_STALE = i18n._(
    "This page is stale — the preset file changed on disk since it was rendered. Reload and try "
    "again.")
#: a combination may only name presets the SAME file carries: a name the file does not have is a
#: member the extension silently ignores (``extension.py:110``), so the save refuses it rather than
#: write a preset that quietly does nothing.
PRESETS_REFUSED_DANGLING = i18n._(
    "No change was saved: the combination names %(missing)s, which this preset file does not carry. "
    "A name it does not have is ignored when the mission runs, so the combination would silently do "
    "nothing. Name a preset from the list — nothing was written.")
#: a combination that names nothing is the same defect: a preset that silently does nothing.
PRESETS_REFUSED_EMPTY_COMBINATION = i18n._(
    "No change was saved: the combination names no presets, so it would silently do nothing. "
    "Add at least one preset — nothing was written.")
#: a new preset may only be written into a file the extension READS. The extension reads ONLY the
#: files its own ``MizEdit.presets`` setting lists (``extensions/mizedit/extension.py:41-44``), so a
#: file no node names would never apply — the same "writes that never apply" defect the destination
#: rule exists to rule out. The sentence names the nodes whose setting governs the offer.
PRESETS_REFUSED_UNNAMED_FILE = i18n._(
    "No change was saved: '%(file)s' is not named by any node's MizEdit.presets, so this "
    "installation never reads it. The preset files it reads are named by %(who)s — nothing was "
    "written.")
#: THE NEW-FILE FAMILY. A file name typed into the destination step's "New file…" choice is
#: caller-supplied, so :func:`preset_new_file` is its ONE validation and each refusal states the one
#: thing wrong with the text, printing the operator's text verbatim (operator data, never translated).
#: A name that is not typed at all is not a request: the route then writes the destination the list
#: offers, exactly as before the choice existed.
PRESETS_REFUSED_NEW_FILE_TRAVERSAL = i18n._(
    "No change was saved: '%(file)s' carries a '..' segment, which would put the file outside the "
    "config directory — nothing was written.")
PRESETS_REFUSED_NEW_FILE_ABSOLUTE = i18n._(
    "No change was saved: '%(file)s' starts at the root of the drive, so it is not a path inside the "
    "config directory. Name it as 'config/presets.yaml' — nothing was written.")
PRESETS_REFUSED_NEW_FILE_WINDOWS = i18n._(
    "No change was saved: '%(file)s' carries a backslash or a drive letter, so it is not a path "
    "inside the config directory. Name it with forward slashes, as 'config/presets.yaml' — nothing "
    "was written.")
PRESETS_REFUSED_NEW_FILE_CONTROL = i18n._(
    "No change was saved: '%(file)s' carries a control character, which no file name may hold — "
    "nothing was written.")
PRESETS_REFUSED_NEW_FILE_SUFFIX = i18n._(
    "No change was saved: '%(file)s' does not end in '.yaml', so it is not a preset file. Preset "
    "files are YAML — nothing was written.")
PRESETS_REFUSED_NEW_FILE_SHAPE = i18n._(
    "No change was saved: '%(file)s' is not a preset file this page creates. It must be "
    "'config/presets.yaml', another 'config/presets*.yaml', or a '.yaml' file under "
    "'config/presets/' — nothing was written.")
PRESETS_REFUSED_NEW_FILE_ESCAPE = i18n._(
    "No change was saved: '%(file)s' resolves outside the config directory — nothing was written.")
PRESETS_REFUSED_NEW_FILE_EXISTS = i18n._(
    "No change was saved: '%(file)s' already exists. Pick it in the list instead — this page only "
    "creates a file that does not exist yet, and nothing was written.")
#: a form that carries no revision cannot be checked against the file it was rendered from: a POST
#: that omits the field would otherwise write after the file moved.
PRESETS_REFUSED_NO_REVISION = i18n._(
    "No change was saved: the form carried no revision, so the console cannot tell whether this "
    "page is stale. Reload the page and try again — nothing was written.")
#: a REMOVE whose posted name differs only in CASE is a no-op — the file's own key is the one that
#: has to be spelled. The sentence names the spelling the file actually carries.
PRESETS_NO_CHANGE_CASE = i18n._(
    "Nothing changed — this file carries no preset spelled '%(name)s'. The preset is spelled "
    "'%(actual)s'.")

#: THE EXPERT DOOR'S OWN REFUSALS. The review step's edited YAML is read, checked and written by the
#: one write route, so each way it can fail is a plain sentence with nothing written. The YAML error
#: carries the reader's own detail (the line and column, which is operator-usable); the schema error
#: reuses the banner above with the validator's detail appended.
PRESETS_REFUSED_EXPERT_YAML = i18n._(
    "No change was saved: the edited YAML could not be read as YAML (%(detail)s) — nothing was "
    "written.")
PRESETS_REFUSED_EXPERT_EMPTY = i18n._(
    "No change was saved: the edited YAML carries no preset at all, so there is nothing to write for "
    "'%(name)s' — nothing was written.")
PRESETS_REFUSED_EXPERT_SEVERAL = i18n._(
    "No change was saved: the edited YAML carries %(count)s preset keys (%(keys)s), and expert mode "
    "edits exactly the one preset this page opened — nothing was written.")
PRESETS_REFUSED_EXPERT_RENAME = i18n._(
    "No change was saved: the edited YAML is keyed '%(posted)s', and this page is editing '%(name)s'. "
    "Expert mode cannot rename a preset — nothing was written.")

#: THE ONE-BLOCK EDITOR'S OWN REFUSALS. The page edits ONE preset's block, so a document that is not
#: exactly that block is refused rather than written: a preset's name is changed through the guarded
#: wizard path, never here, and a second key is a whole-file document aimed at a one-preset page.
PRESETS_REFUSED_BLOCK_NOT_A_MAP = i18n._(
    "No change was saved: this document is not a map of one preset name to its value, so there is "
    "nothing to write for '%(name)s' — nothing was written.")
PRESETS_REFUSED_BLOCK_SEVERAL = i18n._(
    "No change was saved: this document carries %(count)s preset keys (%(keys)s), and this page "
    "edits exactly one preset, '%(name)s' — nothing was written.")
PRESETS_REFUSED_BLOCK_RENAME = i18n._(
    "No change was saved: this document is keyed '%(posted)s', and this page edits '%(name)s'. A "
    "preset's name is not changed here — nothing was written.")


#: the install's own preset files are ADMIN work and carry NO manager scope grant — the same rule the
#: Settings page states: a manager's scope is a set of SERVERS, and "one of mine runs there" must
#: never become "let me edit a preset".
PRESETS_ROLES: tuple[str, ...] = ("Admin",)

PRESETS_TEMPLATE = "presets.html"
PRESETS_STYLE = "presets.css"
#: The page's own name — the sidebar entry, the tab title, the heading and the breadcrumb all read
#: THIS string. It names presets FOR MISSIONS, never application settings, so a language renders it
#: with its own word for a mission preset rather than its word for settings or preferences.
PRESETS_TITLE = i18n._("Presets")
PRESETS_LEAD = i18n._("The MizEdit presets this installation names. A preset changes a mission's "
                      "weather and the time it flies at, without opening the Mission Editor.")

#: THE ONE-BLOCK EDITOR'S CHROME. The page's own name, the sentence that says what the editor holds,
#: the editor's accessible label, the note that only this preset is written, and the two buttons
#: (which reuse the wizard's own words so the area has ONE label per action).
PRESETS_YAML_TEMPLATE = "presets_yaml.html"
PRESETS_YAML_TITLE = i18n._("Edit preset")
PRESETS_YAML_LEAD = i18n._("This preset's own block, as YAML — the document the preset schema "
                           "validates.")
PRESETS_YAML_EDITOR_LABEL = i18n._("Preset YAML")
PRESETS_YAML_HINT = i18n._("Only this preset is written; every other preset in the file is kept as "
                           "it is.")
#: THE DOOR'S LABEL — the icon-only YAML link every preset tile carries, named in its tooltip and
#: its accessible name. One word for the one editor.
PRESETS_YAML_DOOR_LABEL = i18n._("Edit YAML")
#: THE CONVERSION SENTENCE — shown once, and only while the preset is not yet the expert DSL. It
#: names the edit that reaches the DSL and the consequence for the wizard.
PRESETS_YAML_CONVERT_HINT = i18n._(
    "Replacing this preset's value with 'modify: …' and saving makes it an expert-DSL preset, which "
    "the wizard no longer edits.")

CRUMB_GROUP = dashboard_page.CRUMB_GROUP

# ------------------------------------------------------------------- where the files are, and how
# THE BOT'S OWN DISCOVERY. ``extensions/mizedit`` reads the files a node lists (``presets.yaml`` by
# default) and this page lists the same tree, in the same order, so the page names what the bot reads.

#: the FLAT-file glob, which covers ``config/presets.yaml`` and any sibling ``config/presets_*.yaml``.
PRESET_FILE_GLOB = "presets*.yaml"
#: the directory form of the same thing (``config/presets/*.yaml``).
PRESET_DIR = "presets"
#: THE ONE PATTERN SET a NODE's config dir is listed with (:func:`node_preset_files`): both shapes
#: of a preset file, derived from the two literals above so neither is spelled a second time. It
#: names every file a preset NAME can live in under the config tree; it is NOT the set the extension
#: reads, which is only what a server's own ``presets`` setting names
#: (:func:`mizedit_read_paths`).
PRESET_FILE_PATTERNS: tuple[str, ...] = (PRESET_FILE_GLOB, f"{PRESET_DIR}/*.yaml")
#: the preset key whose presence marks a value as the expert DSL rather than its own values.
MODIFY_KEY = "modify"
#: the config tree's own name for itself, as the page prints a file (``config/presets.yaml``). A file
#: is never shown by its absolute install path: that path is not part of the console's vocabulary.
CONFIG_LABEL = "config"

# ------------------------------------ the kinds a preset can be, and the toolbar's filter values
# ONE DECLARATION per kind: the badge word and the ``?kind=`` value are the same string, so a chip
# cannot offer a filter the page does not know.

KIND_OWN = "own"
KIND_COMBINATION = "combination"
KIND_MODIFY = "modify"

#: The kind a preset value IS, printed as the card's badge. The words are chrome (translated); the
#: KEYS a card carries are the preset's own (never translated — they are the schema's identifiers).
KIND_LABELS: dict[str, str] = {
    KIND_OWN: i18n._("its own values"),
    KIND_COMBINATION: i18n._("a list of presets"),
    KIND_MODIFY: i18n._("expert DSL"),
}

#: the kinds the wizard may open. The expert DSL is LISTED and never opened by the wizard: it is
#: edited as raw YAML, not through weather controls.
WIZARD_KINDS: frozenset[str] = frozenset({KIND_OWN, KIND_COMBINATION})

# ---------------------------------------------------- the preset's SHAPE — the wizard's one switch
# A preset is ONE of two SHAPES: its OWN values (a map the weather controls write) or a COMBINATION
# (a list of other preset names). They are the same preset — one key in one file — so the wizard
# carries the shape as a URL/form FIELD (``shape``), never a second route: the switch stays inside the
# single wizard, survives in the URL, works with JavaScript off, and the save route reads it.
SHAPE_OWN = KIND_OWN
SHAPE_COMBINATION = KIND_COMBINATION
SHAPE_FIELD = "shape"
#: the two shapes as a segmented switch: (value, label). The value is the KIND string, so a shape and
#: a kind badge can never spell the same thing twice.
SHAPE_CHOICES: tuple[tuple[str, str], ...] = (
    (SHAPE_OWN, i18n._("Its own values")),
    (SHAPE_COMBINATION, i18n._("A combination")),
)
#: the field the ordered member names ride in (a comma-separated list, the spelling the extension
#: itself splits a preset list on — ``extension.py:101-102``).
COMBINATION_FIELD = "combination"

#: the toolbar's filter values — the ``?kind=`` a chip carries, plus the one that means "every kind".
FILTER_ALL = "all"
FILTER_CHOICES: tuple[tuple[str, str], ...] = (
    (FILTER_ALL, i18n._("All")),
    (KIND_OWN, i18n._("Own values")),
    (KIND_COMBINATION, i18n._("Combinations")),
    (KIND_MODIFY, i18n._("Expert DSL")),
)

#: THE WIZARD'S URL SHAPE. The route lands in a later slice; declaring it here is what keeps the
#: card's link and that route one string. The name is ONE path segment (percent-encoded), never a
#: path.
PRESETS_WIZARD_PATH = "/presets/preset/{name}/wizard"

#: THE WIZARD EDITOR'S OWN CHECK. The review step's YAML panel POSTs its text here for the ONE preset
#: the page has OPEN (``{name}`` as ONE percent-encoded segment), and the route answers the SAME JSON
#: verdict the Settings editor's check answers with — nothing is validated in the browser and nothing
#: is written. It is a sibling of the wizard path, so the two can never drift.
PRESETS_WIZARD_VALIDATE_PATH = PRESETS_WIZARD_PATH + "/validate"

#: THE EXPERT-DSL PRESET'S OWN EDITOR. One page per preset: the preset's name and the file it lives
#: in, ONE YAML editor holding that preset's own block (its top-level key plus its value), a Save and
#: a Cancel. Not the Settings area's file editor — that one edits a whole file and brings the file
#: tabs, the schema form and the Expert Mode toggle with it, none of which belong to one preset.
PRESETS_YAML_PATH = "/presets/preset/{name}/yaml"
#: the editor's check: a READ that answers the same JSON verdict the other editors answer with.
PRESETS_YAML_VALIDATE_PATH = PRESETS_YAML_PATH + "/validate"
#: THE WRITE: the ONE preset's block, through ``configwrite.patch_config_file`` — schema-validated,
#: atomic, ``.bak``-keeping and revision-guarded, exactly like every other config write.
PRESETS_YAML_SAVE_PATH = PRESETS_YAML_PATH + "/save"


# ------------------------------------------------------------ the node's cloud presets (DCS's own)
# The cloud presets a DCS installation ships are read from ``Config/Effects/clouds.lua`` ON the node
# (``dcs_clouds`` parses it; ``dcs_install`` resolves the path). The picker consumes them as JSON and
# draws each plate through the second route below.

#: the cloud-preset LIST for one node — a JSON read of that node's vendor file.
PRESETS_CLOUDS_LIST_PATH = "/presets/clouds/{node}.json"
#: ONE cloud-preset PLATE. The URL names a PRESET and a NODE, never a path: the route looks the name
#: up in that node's own parsed list and serves the thumbnail the entry carries.
PRESETS_CLOUD_IMAGE_PATH = "/presets/clouds/{node}/{name}.png"


def clouds_list_path(node: str) -> str:
    """The cloud-preset list URL of *node* — the node as ONE percent-encoded path segment."""
    return f"/presets/clouds/{quote(str(node), safe='')}.json"


def cloud_image_path(node: str, name: str) -> str:
    """The plate URL of cloud preset *name* on *node* — both as ONE percent-encoded segment each."""
    return f"/presets/clouds/{quote(str(node), safe='')}/{quote(str(name), safe='')}.png"


#: the halo-preset LIST for one node — a JSON read of that node's vendor file, answering BOTH its
#: ``presets`` (the halo) and its ``crystalsPresets`` (the ice crystals).
PRESETS_HALO_LIST_PATH = "/presets/halo/{node}.json"
#: ONE crystal-preset PLATE. The URL names a PRESET and a NODE, never a path: the route looks the name
#: up in that node's own parsed lists and serves the thumbnail the entry carries.
PRESETS_HALO_IMAGE_PATH = "/presets/halo/{node}/{name}.png"


def halo_list_path(node: str) -> str:
    """The halo-preset list URL of *node* — the node as ONE percent-encoded path segment."""
    return f"/presets/halo/{quote(str(node), safe='')}.json"


def halo_image_path(node: str, name: str) -> str:
    """The plate URL of halo preset *name* on *node* — both as ONE percent-encoded segment each."""
    return f"/presets/halo/{quote(str(node), safe='')}/{quote(str(name), safe='')}.png"


#: THE HALO'S OFF VALUE. ``core/mizfile.py`` defaults ``weather.halo`` to ``{"preset": "off"}``, so
#: ``off`` is a real value a preset carries — the halo control offers it beside ``auto`` and the
#: file's own entries. The VALUE lives in the ONE field table (``weather_fields``), which is where the
#: control's own choices are declared; this name reads it there so the two cannot drift.
HALO_OFF_PRESET = weather_fields.HALO_OFF
HALO_AUTO_PRESET = weather_fields.HALO_AUTO


def halo_row_choices(entries: Iterable[Any]) -> list[dict]:
    """The halo control's options: the table's own ``off``/``auto``, then the node's entries in order.

    ONE home: the closed pair is read from the field table (``weather_fields``), so a row built for the
    wizard's step and a row built for the launch dialog carry the SAME options. ``entries`` are the
    node's ``icehalo.lua`` presets (``name``/``label``).
    """
    options = [{"value": value, "label": label}
               for value, label in weather_fields.field("halo.preset").choices]
    options += [{"value": preset.name, "label": preset.label} for preset in entries]
    return options


def picker_choices(entries: Iterable[Any]) -> list[dict]:
    """A picker's options — the node's own entry names, in file order (``name``/``label``)."""
    return [{"value": preset.name, "label": preset.label} for preset in entries]


def cloud_picker_choices(entries: Iterable[Any]) -> list[dict]:
    """A cloud picker's options: the node's preset names, each with its OWN base in metres.

    The base is what the launch dialog pre-fills into ``clouds.base`` when a preset is chosen — the
    dialog's twin of the wizard's plate link, which carries the same figure. An entry with no layer
    altitude carries an empty base, so no number is invented.
    """
    return [{"value": preset.name, "label": preset.label, "base": _plain_number(preset.base)}
            for preset in entries]


async def read_node_choice_lists(node: Any) -> dict:
    """The node's cloud-plate and halo lists for the temporary preset's controls, as ``choices`` data.

    ONE read: the cloud presets (the ``clouds.lua`` plates) and the halo presets and crystals (the
    ``icehalo.lua`` entries), through the SAME remembered lists every wizard step and plate route reads
    (:func:`_remembered_vendor_list`), so a dialog opened twice reads the node once. A list that cannot
    be read answers ``[]`` for its part, so the control falls back to its free-text shape exactly as the
    wizard's own step does — the read never refuses. Nothing is written.
    """
    data: dict[str, list] = {"clouds": [], "halo": [], "crystals": []}
    if node is None:
        return data
    name = str(getattr(node, "name", "") or "")
    try:
        clouds, _path = await _read_cloud_presets(node, name)
    except _NodeFileUnreadable:
        clouds = []
    try:
        halo, crystals, _halo_path = await _read_halo_presets(node, name)
    except _NodeFileUnreadable:
        halo = crystals = []
    data["clouds"], data["halo"], data["crystals"] = clouds, halo, crystals
    return data


#: EVERY failure is its OWN plain sentence, and they are NOT translated: a picture has no page to
#: render its failure into, so the sentence IS the answer — the same class of body as the console's
#: other download refusals. ``{node}``/``{name}``/``{path}`` are filled in after, so the operator's
#: own node, preset and file names stay verbatim.
PRESETS_NODE_OFFLINE = ("Node '{node}' is offline or unknown, so its cloud presets cannot be read "
                        "from this console.")
PRESETS_NO_CLOUD_LIST = "Node '{node}' has no readable cloud-preset list (looked for '{path}')."
PRESETS_CLOUD_NAME_UNUSABLE = "Cloud preset name '{name}' is not usable."
PRESETS_CLOUD_UNKNOWN = "Node '{node}' has no cloud preset named '{name}'."
PRESETS_CLOUD_NO_PICTURE = "Cloud preset '{name}' on node '{node}' has no picture."
PRESETS_CLOUD_PICTURE_UNUSABLE = ("Cloud preset '{name}' reports an unusable picture name "
                                  "('{thumbnail}').")
PRESETS_PICTURE_MISSING = "'{thumbnail}' is not in this node's DCS installation (looked for '{path}')."
PRESETS_NODE_PERMISSION = "Node '{node}' cannot read '{path}' (permission denied)."
PRESETS_NODE_TIMEOUT = "Node '{node}' did not answer in time."
PRESETS_NODE_RAISED = "Node '{node}' could not hand over '{path}' ({error})."
PRESETS_NODE_FAILURE_CODE = "Node '{node}' answered with a failure code instead of '{path}'."
PRESETS_PICTURE_OVER_CAP = ("'{thumbnail}' is {size} bytes, above the {limit}-byte limit this console "
                            "hands over. Fetch it from the host: {path}")
PRESETS_CLOUD_LIST_OVER_CAP = ("Node '{node}' answered {size} bytes for '{path}', above the "
                               "{limit}-byte limit this console reads as a cloud-preset list.")
PRESETS_NO_HALO_LIST = "Node '{node}' has no readable halo-preset list (looked for '{path}')."
PRESETS_HALO_NAME_UNUSABLE = "Halo preset name '{name}' is not usable."
PRESETS_HALO_UNKNOWN = "Node '{node}' has no halo preset named '{name}'."
PRESETS_HALO_NO_PICTURE = "Halo preset '{name}' on node '{node}' has no picture."
PRESETS_HALO_PICTURE_UNUSABLE = ("Halo preset '{name}' reports an unusable picture name "
                                 "('{thumbnail}').")
PRESETS_HALO_LIST_OVER_CAP = ("Node '{node}' answered {size} bytes for '{path}', above the "
                              "{limit}-byte limit this console reads as a halo-preset list.")

#: the page's own copy. The key chips are DATA — the preset's own top-level keys — and are never
#: translated; these are the words around them.
PRESETS_FILTER_LABEL = i18n._("Filter")
PRESETS_FILTER_PLACEHOLDER = i18n._("Filter by name or key")
PRESETS_KEYS_LABEL = i18n._("keys this preset writes")
#: the tile's kind WORD — the head badge every tile of the shared component carries.
PRESETS_KIND_LABEL = i18n._("Preset")
PRESETS_NEW_LABEL = i18n._("New preset")
PRESETS_MODIFY_NOTE = i18n._("expert mode — the modify DSL")
#: the tab strip: the one tab that is not a file, and the sentence for a ``?file=`` this install no
#: longer carries. A tab LABEL is the file's own ``config/…`` label — operator data, never translated.
PRESETS_ALL_FILES = i18n._("All files")
PRESETS_FILE_GONE = i18n._("The preset file %(file)s is not here any more — showing every file.")
#: the confirm page's own copy. The three "Removal" sentences it also carries are the ones the
#: wizard's review step used to state (``PRESETS_REMOVAL_HEAD`` / ``_SENTENCE`` / ``_NOTE``).
PRESETS_REMOVE_LEAD = i18n._("Remove the preset %(name)s from %(file)s?")
PRESETS_REMOVE_DOCUMENT = i18n._("What this preset carries")
PRESETS_REMOVE_IN_USE = i18n._("%(name)s is still referenced, so it cannot be removed:")
#: the two honest empty states, plus the third shape: a file that is not a map of preset names.
PRESETS_NO_FILE = i18n._("No preset file was found — looked for config/presets.yaml, "
                         "config/presets*.yaml and config/presets/*.yaml.")
PRESETS_UNREADABLE = i18n._("The preset file %(path)s could not be read as YAML — %(detail)s")
PRESETS_NOT_A_MAP = i18n._("The preset file %(path)s is not a map of preset names.")
PRESETS_EMPTY_FILE = i18n._("The preset file %(path)s carries no presets yet.")
#: the sentence under a filter that matched nothing.
PRESETS_NO_MATCH = i18n._("No preset matches this filter.")


def declare() -> None:
    """Declare both capabilities. Idempotent for the same roles (and NO scope grant)."""
    permissions.declare_capability(PRESETS_CAPABILITY, PRESETS_ROLES)
    permissions.declare_capability(PRESETS_WRITE_CAPABILITY, PRESETS_ROLES)


declare()


# ------------------------------------------------------------------------- the read, without a page

def discover_preset_files(config: Path) -> list[Path]:
    """Every preset file under *config*, in the order the bot's own discovery reads them.

    ``config/presets/*.yaml``, then ``config/presets.yaml`` and any other ``config/presets*.yaml`` —
    a directory first, then the flat files, each set in file-name order, and a file that appears in
    both is listed once. A directory that does not exist contributes nothing, so the list is a
    truthful reading of the install rather than a wish-list.
    """
    config = Path(config)
    paths: list[Path] = []
    presets_dir = config / PRESET_DIR
    if presets_dir.is_dir():
        paths.extend(sorted(presets_dir.glob("*.yaml")))
    paths.extend(sorted(path for path in config.glob(PRESET_FILE_GLOB) if path not in paths))
    return paths


def preset_kind(value: Any) -> str:
    """The KIND of a preset's own value: a combination, the expert DSL, or its own values.

    A LIST value is the combination mechanism (the names of other presets to merge). A MAP value that
    carries :data:`MODIFY_KEY` is the expert DSL. Every other value is read as the preset's own
    values: a map's keys are what it writes, and a scalar (a shape the schema does not accept) writes
    no keys at all rather than being reported as some third thing.
    """
    if isinstance(value, list):
        return KIND_COMBINATION
    if isinstance(value, Mapping) and MODIFY_KEY in value:
        return KIND_MODIFY
    return KIND_OWN


def preset_keys(value: Any) -> list[str]:
    """The keys the preset writes: a map's OWN top-level keys, or a combination's member names.

    These are the schema's own identifiers (``date``, ``clouds``, ``wind``, …), read from the document
    itself — the page never invents a label here. A combination writes no keys of its own: its value
    IS the list of names it merges, so those names are what its card shows.
    """
    if isinstance(value, list):
        return [str(name) for name in value]
    if isinstance(value, Mapping):
        return [str(key) for key in value]
    return []


def preset_shape(value: Any) -> str:
    """The SHAPE of a preset's own value: a combination (a LIST) or its own values (anything else)."""
    return SHAPE_COMBINATION if isinstance(value, list) else SHAPE_OWN


def parse_combination(text: Any) -> list[str]:
    """The ordered member names in *text* — a comma-separated list, the spelling the extension splits.

    Every token is stripped and an empty one dropped, so ``"Summer, , Morning"`` is ``[Summer,
    Morning]``. This is the SAME reading ``MizEdit.get_presets`` performs on a preset list
    (``extension.py:101-102``), so the wizard posts the list the extension would read.
    """
    if not isinstance(text, str):
        return []
    return [token.strip() for token in text.split(",") if token.strip()]


def combination_text(value: Any) -> str:
    """The ordered member names of a preset *value* joined for the wizard's field — ``""`` otherwise."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(name) for name in value)
    return ""


def has_preset(document: Any, name: str) -> bool:
    """Whether *document* carries a preset keyed EXACTLY *name*.

    EXACT, never tolerant: the extension resolves a combination member with a plain ``in``
    (``extension.py:109,116``) and the shared reader with ``dict.get`` (``helper.py:513``), so a name
    in the wrong CASE is a member that silently does nothing — the defect the save guard refuses.
    """
    return isinstance(document, Mapping) and name in document


def expand_combination(document: Any, names: Any) -> list[tuple[str, Any]]:
    """The members of the combination *names*, in order, as ``(name, value)`` pairs read from *document*.

    This is the SAME expansion ``MizEdit.get_presets`` performs on a list value
    (``extension.py:113-121``): each name present is looked up; a name the document does not carry is
    DROPPED — the extension only logs ``Preset X not found, ignored.`` (``extension.py:110,117``) —
    so the pair list matches what the extension would hand ``apply_presets``. A member whose value is
    ITSELF a list is expanded ONE LEVEL, in order: the extension appends each inner member's own
    value (``extension.py:115-119``), and an inner name the document lacks is dropped the same way.
    """
    members: list[tuple[str, Any]] = []
    if not isinstance(document, Mapping):
        return members
    for name in names:
        if name not in document:
            continue
        value = document[name]
        if isinstance(value, list):
            for inner in value:
                if inner in document:
                    members.append((str(inner), document[inner]))
        else:
            members.append((str(name), value))
    return members


def effective_weather(members: Any) -> dict:
    """The effective weather of *members*: the preset maps folded in order, LATER winning per key.

    A preset map's keys are its own; folding them in the order ``apply_presets`` applies them
    (``extension.py:128-181``, ``mizfile.py:178-223``) shows which value wins. ``wind`` is merged one
    level deeper — ``MizFile``'s wind setter UNIONS each altitude in place
    (``mizfile.py:266-273``: ``wind['atGround'] |= values['atGround']``) — so two members that set
    DIFFERENT sub-keys at the SAME altitude both contribute, exactly as the mission ends up. Every
    other key is replaced whole.
    """
    effective: dict = {}
    for _name, value in members:
        if not isinstance(value, Mapping):
            continue
        for key, item in value.items():
            if key == "wind" and isinstance(item, Mapping):
                merged = dict(effective[key]) if isinstance(effective.get(key), Mapping) else {}
                for altitude, row in item.items():
                    if isinstance(row, Mapping) and isinstance(merged.get(altitude), Mapping):
                        merged[altitude] = {**merged[altitude], **row}
                    else:
                        merged[altitude] = row
                effective[key] = merged
            else:
                effective[key] = item
    return effective


def combination_candidates(document: Any, exclude: str) -> list[str]:
    """Every preset NAME in *document* a combination could name, in file order — bar *exclude*.

    A combination names presets in the SAME file; the preset being edited cannot sensibly name itself,
    so it is left out. Nothing else is filtered: a member may be an own-value preset, another
    combination or the expert DSL — the extension resolves any of them by name.
    """
    if not isinstance(document, Mapping):
        return []
    return [str(key) for key in document if str(key) != exclude]


def preset_value_for(shape: str, values: Mapping[str, str], combination: str,
                     existing: Any = None, *, fog_rows_stated: bool = False) -> Any:
    """The preset VALUE the wizard produces for *shape* — a combination LIST or its OWN map.

    ONE home for "what the wizard writes": the review step's produced YAML and the save both read it,
    so a review can never show a different document from the one the save writes. ``existing`` is the
    preset's current value and ``fog_rows_stated`` whether the form carried the fog table, both
    forwarded to :func:`preset_map_from_values`.
    """
    if shape == SHAPE_COMBINATION:
        return parse_combination(combination)
    return preset_map_from_values(values, existing, fog_rows_stated=fog_rows_stated)


def wizard_url(name: str) -> str:
    """The preset's wizard URL — the name as ONE percent-encoded path segment."""
    return PRESETS_WIZARD_PATH.format(name=quote(name, safe=""))


def display_path(config: Path, path: Path) -> str:
    """The file as the config tree names it (``config/presets.yaml``), never an absolute install path."""
    try:
        return f"{CONFIG_LABEL}/{Path(path).relative_to(Path(config)).as_posix()}"
    except ValueError:
        return path.name


# ------------------------------------------------- the preset files, parsed and remembered

#: The state of ONE preset file's read: a parsed map of presets, or the reason it carries none.
PARSED_DOCUMENT = "parsed"
UNREADABLE_FILE = "unreadable"
NOT_A_MAP_FILE = "not-a-map"
EMPTY_FILE = "empty"


class PresetFileAnswer(NamedTuple):
    """ONE preset file, read: its parsed document, or the state that says why it carries no presets.

    ``detail`` carries the parser's own sentence for :data:`UNREADABLE_FILE` and is empty otherwise.
    """

    state: str
    document: Any = None
    detail: str = ""

    @property
    def parsed(self) -> bool:
        """Whether this answer holds a document that carries at least one preset."""
        return self.state == PARSED_DOCUMENT


#: The most preset FILES whose answer this cache holds at once.
PRESET_DOCUMENTS_MAX = 64

#: The most bytes of preset source this cache holds at once. The held weight of an entry is the byte
#: size of the file its document was parsed from — the source the document stands for. A refusal is a
#: sentence, not a document, so it is held at zero weight and bounded by the file count alone.
PRESET_DOCUMENTS_MAX_BYTES = 32 * 1024 * 1024

#: ``(resolved path, st_mtime_ns, st_size) -> (answer, held bytes, last use)``.
#:
#: ONE cache for the whole process. The key is the file's IDENTITY as the filesystem states it, so a
#: write from ANYWHERE — this page, the Discord ``/mission save preset`` command, a hand edit, another
#: tool — moves the mtime or the size and the next render re-parses; nothing the page alone can see is
#: part of the key. At most one entry is held per file: a new stamp drops the older one.
_PRESET_DOCUMENTS: dict[tuple[str, int, int], tuple[PresetFileAnswer, int, int]] = {}

#: The use counter that orders the entries, so eviction drops the least recently read one first.
_PRESET_DOCUMENT_USES = itertools.count(1)


def _preset_key(path: Path) -> tuple[str, int, int] | None:
    """The cache key of *path* — its resolved path, mtime in ns and size — or ``None`` when absent."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size


def _preset_answer_now(path: Path) -> PresetFileAnswer:
    """Read ONE preset file through the tree's ONE config reader — the read behind the cache."""
    try:
        document = configwrite.load_config_file(path)
    except configwrite.ConfigWriteError as ex:
        return PresetFileAnswer(UNREADABLE_FILE, None, str(ex))
    if document is None or not isinstance(document, Mapping):
        return PresetFileAnswer(NOT_A_MAP_FILE)
    if not document:
        return PresetFileAnswer(EMPTY_FILE)
    return PresetFileAnswer(PARSED_DOCUMENT, document)


def _trim_preset_documents() -> None:
    """Drop the least recently read entries until both bounds hold."""
    while _PRESET_DOCUMENTS:
        held = sum(entry[1] for entry in _PRESET_DOCUMENTS.values())
        if len(_PRESET_DOCUMENTS) <= PRESET_DOCUMENTS_MAX and held <= PRESET_DOCUMENTS_MAX_BYTES:
            return
        oldest = min(_PRESET_DOCUMENTS, key=lambda key: _PRESET_DOCUMENTS[key][2])
        _PRESET_DOCUMENTS.pop(oldest, None)


def _remember_preset_answer(key: tuple[str, int, int], answer: PresetFileAnswer) -> None:
    """Remember *answer* for *key*, replacing any older generation of the same file, then trim."""
    for stale in [other for other in _PRESET_DOCUMENTS if other[0] == key[0] and other != key]:
        _PRESET_DOCUMENTS.pop(stale, None)
    held = key[2] if answer.state == PARSED_DOCUMENT else 0
    _PRESET_DOCUMENTS[key] = (answer, held, next(_PRESET_DOCUMENT_USES))
    _trim_preset_documents()


def read_preset_file(path: str | Path) -> PresetFileAnswer:
    """ONE preset file's answer, parsed once per generation of the file — remembered across renders.

    The key is ``(resolved path, st_mtime_ns, st_size)``, so any write to the file — from this page,
    from the Discord ``/mission save preset`` command, from a hand edit — re-parses it on the next
    read. A file that is absent answers as it reads now and is never served from the cache; a file
    that does not parse is remembered as its REFUSAL (its state and the parser's sentence), so a
    broken file is not re-parsed on every render.

    The document an answer holds is SHARED. A caller that MUTATES a document must read the file
    itself through ``configwrite.load_config_file``: the save path keeps parsing fresh for its
    ``before`` / ``candidate`` copies, which ``_merged_preset`` mutates in place.
    """
    path = Path(path)
    key = _preset_key(path)
    if key is not None:
        entry = _PRESET_DOCUMENTS.get(key)
        if entry is not None:
            _PRESET_DOCUMENTS[key] = (entry[0], entry[1], next(_PRESET_DOCUMENT_USES))
            return entry[0]
    answer = _preset_answer_now(path)
    if key is not None:
        _remember_preset_answer(key, answer)
    return answer


def forget_preset_documents() -> None:
    """Forget every remembered preset document — a save has just written one.

    A console save calls this so the next render reads the file it just wrote, rather than trusting
    that the write moved the file's mtime or size. The sibling config documents the page reads
    wholesale (``nodes.yaml``, the Scheduler's config) are forgotten with them: a save that rewrote a
    preset file may have rewritten a reference to it as well.
    """
    _PRESET_DOCUMENTS.clear()
    _CONFIG_DOCUMENTS.clear()


#: ``(resolved path, st_mtime_ns, st_size) -> document`` for the WHOLE config documents the page reads
#: beside the preset files (``nodes.yaml`` for the reference scan and the destination rule, and the
#: Scheduler's own config). Same identity as the preset cache, so any write re-reads; bounded by count
#: because the page reads a handful.
_CONFIG_DOCUMENTS: dict[tuple[str, int, int], Any] = {}
CONFIG_DOCUMENTS_MAX = 8


def _config_document(path: Path) -> Any:
    """ONE config document, parsed once per generation of the file — ``None`` when it carries none.

    A missing, unreadable or unparsable file answers ``None``: the reference scan and the destination
    rule only READ the document, so an absent plugin config must never refuse a preset write or break
    a render. The document is SHARED — a caller that mutates it must read the file itself.
    """
    try:
        stat = os.stat(path)
        key = (str(Path(path).resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    if key in _CONFIG_DOCUMENTS:
        return _CONFIG_DOCUMENTS[key]
    try:
        document = configwrite.load_config_file(path)
    except (configwrite.ConfigWriteError, OSError, ValueError):
        document = None
    for stale in [other for other in _CONFIG_DOCUMENTS if other[0] == key[0] and other != key]:
        _CONFIG_DOCUMENTS.pop(stale, None)
    _CONFIG_DOCUMENTS[key] = document
    while len(_CONFIG_DOCUMENTS) > CONFIG_DOCUMENTS_MAX:
        _CONFIG_DOCUMENTS.pop(next(iter(_CONFIG_DOCUMENTS)))
    return document


def refusal_record(answer: PresetFileAnswer, label: str) -> dict:
    """The page's sentence for a file that carries no presets — untranslated, the page translates it."""
    if answer.state == UNREADABLE_FILE:
        return {"msgid": PRESETS_UNREADABLE, "params": {"path": label, "detail": answer.detail}}
    if answer.state == EMPTY_FILE:
        return {"msgid": PRESETS_EMPTY_FILE, "params": {"path": label}}
    return {"msgid": PRESETS_NOT_A_MAP, "params": {"path": label}}


def read_preset_files(config: Path) -> list[dict]:
    """One record per DISCOVERED preset file, in the TAB STRIP's order.

    Each record is ``{label, path, answer, names, notice}``: the file's ``config/…`` label, its path,
    the remembered answer (:func:`read_preset_file`), the preset NAMES it carries (empty for a file
    that carries none) and, for such a file, the sentence that says why (:func:`refusal_record`) —
    untranslated, so the page translates it in the request's own language.

    ``discover_preset_files`` stays the source of the key list and keeps its own order; the STRIP is
    ordered here: ``presets.yaml`` first, then the other flat ``presets*.yaml``, then
    ``config/presets/*.yaml``, each set in file-name order. A file that is absent between the listing
    and the read contributes nothing.
    """
    config = Path(config)
    records: list[dict] = []
    for path in discover_preset_files(config):
        if not path.is_file():
            continue
        label = display_path(config, path)
        answer = read_preset_file(path)
        records.append({
            "label": label,
            "path": str(path),
            "answer": answer,
            "names": [str(name) for name in answer.document] if answer.parsed else [],
            "notice": None if answer.parsed else refusal_record(answer, label),
        })
    return sorted(records, key=_strip_order)


def _strip_order(record: dict) -> tuple[int, str]:
    """The tab strip's sort key: ``presets.yaml``, the other flat files, then the ``presets/`` files."""
    label = record["label"]
    flat = label[len(CONFIG_LABEL) + 1:] if label.startswith(f"{CONFIG_LABEL}/") else label
    if flat == "presets.yaml":
        return 0, flat
    if "/" not in flat:
        return 1, flat
    return 2, flat


def read_presets(config: Path) -> tuple[list[dict], list[dict], bool]:
    """One card per top-level key across every discovered file, plus one record per unreadable file.

    Returns ``(cards, unreadable, found)`` — ``found`` says whether the install names ANY preset file
    at all, which is what tells the page's empty state apart from a filter that matched nothing.

    Each card is ``{name, kind, kind_label, writes, file, wizard_url}``: the preset's name, its kind and
    its badge word, the keys it writes, the file it comes from, and the wizard URL (``None`` for a
    preset the wizard does not open). Each record is ``{"msgid": …, "params": …}`` — the sentence the
    page states for a file it could not read, left UNTRANSLATED so the page translates it in the
    request's own language.

    A file that does not parse, or that is not a map of preset names, is skipped WITH its own sentence:
    the other files' presets still render. A file that is absent between the listing and the read
    contributes nothing. Nothing here needs a request, so the whole read is unit-testable.

    Each file is read through :func:`read_preset_file`, so a second render of unchanged files parses
    nothing; ``discover_preset_files`` stays the source of the keys, so a file it no longer lists is
    not rendered from anything remembered.
    """
    config = Path(config)
    cards: list[dict] = []
    unreadable: list[dict] = []
    records = read_preset_files(config)
    for record in records:
        label = record["label"]
        if record["notice"] is not None:
            unreadable.append(record["notice"])
            continue
        for name, value in record["answer"].document.items():
            kind = preset_kind(value)
            cards.append({
                "name": str(name),
                "kind": kind,
                "kind_label": KIND_LABELS[kind],
                "writes": preset_keys(value),
                "file": label,
                "wizard_url": wizard_url(str(name)) if kind in WIZARD_KINDS else None,
            })
    return cards, unreadable, bool(records)


def _matches(card: dict, query: str) -> bool:
    """Whether *card* answers a search for *query* — its name, or any key it writes (case-insensitive)."""
    if not query:
        return True
    needle = query.casefold()
    return (needle in card["name"].casefold()
            or any(needle in key.casefold() for key in card["writes"]))


def _filter_url(value: str, query: str, file: str = "") -> str:
    """A filter chip's URL: the ``?kind=`` it carries, the search and the file tab it keeps.

    The ``file`` rides even when it is empty: an empty one is the "All files" tab, and omitting the
    parameter would fall back to the page's default tab (``presets.yaml``).
    """
    url = f"{PRESETS_PATH}?kind={quote(value, safe='')}&file={quote(file, safe='/')}"
    if query:
        url += f"&q={quote(query, safe='')}"
    return url


def removal_url(name: str) -> str:
    """The confirm page's URL for *name* — a GET on this page that writes nothing."""
    return f"{PRESETS_PATH}?remove={quote(name, safe='')}"


def preset_tab_url(file: str, *, kind: str = FILTER_ALL, query: str = "") -> str:
    """A tab's URL: the file it opens (``""`` for "All files"), the kind filter and the search."""
    params: list[str] = [f"file={quote(file, safe='/')}"]
    if kind and kind != FILTER_ALL:
        params.append(f"kind={quote(kind, safe='')}")
    if query:
        params.append(f"q={quote(query, safe='')}")
    return PRESETS_PATH + (f"?{'&'.join(params)}" if params else "")


# ------------------------------------------------------------------- the node's cloud presets, read

class _NodeFileUnreadable(Exception):
    """A node's vendor file could not be read. The route answers :attr:`sentence`."""

    def __init__(self, status_code: int, sentence: str):
        super().__init__(sentence)
        self.status_code = status_code
        self.sentence = sentence


def _refuse(status_code: int, sentence: str) -> PlainTextResponse:
    """The honest refusal a node read answers with: ONE sentence in plain text."""
    log.info("Preset read answered %d: %s", status_code, sentence)
    return PlainTextResponse(sentence + "\n", status_code=status_code)


def _reachable_node(request: Request, node_name: str):
    """The node *node_name* names, read from the SCOPED source — or ``None`` when unreachable.

    The SAME rule the per-server log routes use: the source is the request's own (so a node the
    caller cannot see is not resolvable here either), the lookup falls back to a case-insensitive
    match of the name, and an entry that is ``None`` is OFFLINE — an unknown name answers the same
    way, because a console that cannot tell the two apart must not claim which one it is.
    """
    entries = getattr(dashboard_page.request_source(request), "nodes", None)
    if not isinstance(entries, dict) or not node_name:
        return None
    if node_name in entries:
        return entries.get(node_name)
    wanted = node_name.casefold()
    for key, entry in entries.items():
        if str(key).casefold() == wanted:
            return entry
    return None


# --------------------------------------------------------- the node's own lists, remembered

#: How long a list read off a node is trusted before the node is asked again, in seconds.
#:
#: The two vendor files (``Config/Effects/clouds.lua``, ``Config/Effects/icehalo.lua``) change only
#: with a DCS update, and a node's preset files change only when a preset is saved — so the window can
#: be long, and it is the same 300 s the plate route already lets a browser keep a picture for: the
#: console trusts a list no longer than it lets a browser trust a picture cut from that list.
NODE_LIST_TTL_SECONDS = 300.0

#: The TWO kinds of node list this cache remembers. The kind is part of the key because one node
#: answers both and they are forgotten independently: saving a preset forgets the preset lists and
#: leaves the vendor lists alone.
VENDOR_LIST = "vendor"
PRESET_LIST = "presets"

#: ``(kind, node name, what it was read from) -> (expiry, the answer)``.
#:
#: ONE cache. The launch dialog's fieldset, every wizard step, the plate and crystal routes and the
#: dialog's preset pick all read a node's list through the two readers below, so a list any one of them
#: has read is not read again by another. The key carries the node NAME and the resolved path: a node
#: re-pointed at another installation, or a renamed one, is a different key and is read afresh without
#: anything having to be invalidated.
_NODE_LISTS: dict[tuple[str, str, str], tuple[float, Any]] = {}

#: The most entries this cache keeps. A key is one node's one file, so this sits far above any real
#: cluster; past it the expired entries go first.
_NODE_LISTS_MAX = 64

#: The refusals a node's answer may be REMEMBERED as. The file is absent, unreadable or larger than
#: the read cap: the next render gets the same sentence, because none of the three can change by
#: itself. A transport failure (the node did not answer — 502/504) is NOT here: it may clear on the
#: next attempt, so it is never remembered.
_REMEMBERABLE_REFUSALS = frozenset({403, 404, 413})


def node_list_cached(kind: str, node_name: str, what: str) -> tuple[bool, Any]:
    """``(found, answer)`` for a node's *what*. An expired entry is dropped and answers ``False``.

    ``found`` is what a caller reads: a remembered ``[]`` or ``False`` is a hit like any other, so the
    answer is never confused with "not remembered".
    """
    key = (str(kind), str(node_name or ""), str(what or ""))
    entry = _NODE_LISTS.get(key)
    if entry is None:
        return False, None
    expires, answer = entry
    if expires <= time.monotonic():
        _NODE_LISTS.pop(key, None)
        return False, None
    return True, answer


def remember_node_list(kind: str, node_name: str, what: str, answer: Any) -> None:
    """Remember *answer* for a node's *what* for ``NODE_LIST_TTL_SECONDS``."""
    key = (str(kind), str(node_name or ""), str(what or ""))
    if key not in _NODE_LISTS and len(_NODE_LISTS) >= _NODE_LISTS_MAX:
        now = time.monotonic()
        for stale in [key for key, (expires, _a) in _NODE_LISTS.items() if expires <= now]:
            _NODE_LISTS.pop(stale, None)
        if len(_NODE_LISTS) >= _NODE_LISTS_MAX:
            del _NODE_LISTS[next(iter(_NODE_LISTS))]
    _NODE_LISTS[key] = (time.monotonic() + NODE_LIST_TTL_SECONDS, answer)


def forget_node_lists(kind: str | None = None) -> None:
    """Forget every remembered node list, or only the *kind* named.

    A preset save calls this for :data:`PRESET_LIST`: a name the operator has just created must be in
    the dialog's pick on the next open, not after the window elapses.
    """
    if kind is None:
        _NODE_LISTS.clear()
        return
    for key in [key for key in _NODE_LISTS if key[0] == str(kind)]:
        _NODE_LISTS.pop(key, None)


# ------------------------------------- the preset files ONE server's MizEdit reads

#: The extension name a server's preset-file setting sits under, spelled as the runtime loads it
#: (``ServerImpl.load_extension`` looks the block up by this exact name).
MIZEDIT_NAME = "MizEdit"

#: The file a server that names no ``presets`` reads: beside the NODE's own config dir, which is the
#: default ``MizEdit._init_presets`` falls back to.
MIZEDIT_DEFAULT_FILE = "presets.yaml"


def _extension_block(locals_map: Any, name: str) -> Mapping:
    """``locals_map['extensions'][name]`` as a mapping, or ``{}`` for anything else."""
    if not isinstance(locals_map, Mapping):
        return {}
    extensions = locals_map.get("extensions")
    if not isinstance(extensions, Mapping):
        return {}
    block = extensions.get(name)
    return block if isinstance(block, Mapping) else {}


def mizedit_config(server: Any) -> Mapping:
    """The ``MizEdit`` block this server's runtime would hand its extension.

    The three IN-PROCESS snapshots the runtime's own merge reads, in its order: the node-wide block
    (``node.locals``), the instance block (``instance.locals``, which ``ServerImpl`` already folds into
    its own ``locals``) and the server's block (``server.locals``, the ``servers.yaml`` entry). Nothing
    is awaited and no RPC is issued, so reading it costs a page render nothing; a remote server whose
    master-side snapshot carries no block answers ``{}``, which resolves to the extension's default —
    the same answer an instance that names no ``presets`` gets.

    A server that declares no block answers ``{}``; every holder is read through ``getattr`` because a
    page renders over duck-typed read models as well as over the live cluster.
    """
    node = getattr(server, "node", None)
    instance = getattr(server, "instance", None)
    merged: dict = {}
    for holder in (node, instance, server):
        merged |= dict(_extension_block(getattr(holder, "locals", None), MIZEDIT_NAME))
    return merged


def mizedit_read_paths(config: Mapping | None, config_dir: str) -> list[str]:
    """The preset files ONE server's ``MizEdit`` extension reads, in the order it reads them.

    THE ONE HOME of "which files this server reads". ``presets`` is a path or a list of paths
    (``extensions/mizedit/extension.py`` ``_init_presets`` reads either and merges them into ONE name
    map, later files overriding earlier names); a server that names none reads
    :data:`MIZEDIT_DEFAULT_FILE` beside the node's own config dir. The paths are returned AS THE
    SETTING SPELLS THEM, because that is how the extension opens them — a relative entry is relative
    to the process that runs the extension, which is this same process on a master node.

    An explicitly EMPTY list reads nothing, exactly as the extension's own loop does; a value that is
    neither a path nor a list (an omitted or null setting) names none and takes the default.
    """
    named = config.get("presets") if isinstance(config, Mapping) else None
    if isinstance(named, (list, tuple)):
        return [str(item) for item in named if isinstance(item, str) and item.strip()]
    if isinstance(named, str) and named.strip():
        return [named]
    if not config_dir:
        return []
    return [os.path.join(config_dir, MIZEDIT_DEFAULT_FILE)]


def preset_read_key(config_dir: str, paths: Iterable[str]) -> str:
    """The node-list cache's ``what`` for ONE preset read: the config dir plus the file set read.

    The file set is part of the key because the files that answer a read change with the node: an entry
    can therefore only be served to a caller whose own set resolves to the same files. ``\\x1f``
    separates the parts because a path may contain any printable character, ``|`` included. A member
    may carry a file's VERSION beside its path (:func:`_listed_identity`), so a caller that wants a
    file rewritten on the node to invalidate the entry spells it that way; a bare path is a key that
    only a path set can invalidate.
    """
    return "\x1f".join([str(config_dir or ""), *[str(path) for path in paths]])


def _listed_identity(path: str, size: Any, mtime: Any) -> str:
    """One listed file as the cache's name for its current CONTENT: path, size and modification time.

    ``list_files`` answers each file with its size and mtime, so a preset file edited on the node
    outside the console is a different key and is parsed on the very next open. The values are taken
    as the node spells them, unrounded, because the mtime's fraction is what separates two edits inside
    one second. ``\\x1e`` is not a character a path carries on either platform the bot runs on.
    """
    return f"{path}\x1e{size}\x1e{mtime}"


def preset_file_label(config_dir: Any, path: Any) -> str:
    """A node-side preset file as the config tree names it (``config/presets_winter.yaml``).

    A file is never shown by its absolute install path: that path is not part of the console's
    vocabulary. The node answers its own absolute paths, so the config dir is stripped as a STRING
    (forward or back slashes) rather than with a path type, and a path outside it keeps its own name.
    """
    base = str(config_dir or "").replace("\\", "/").rstrip("/")
    shown = str(path or "").replace("\\", "/")
    if base and shown.startswith(base + "/"):
        shown = shown[len(base) + 1:]
    else:
        shown = shown.rsplit("/", 1)[-1]
    return f"{CONFIG_LABEL}/{shown}"


async def node_preset_records(
        node: Any) -> tuple[list[tuple[str, str, list[str], dict[str, Any]]], bool]:
    """THE ONE READ of a node's preset files: ``[(label, path, names, values)]`` and readability.

    ``node.list_files`` enumerates :data:`PRESET_FILE_PATTERNS` under the node's OWN ``config_dir``;
    each listed file is read through ``node.read_file`` once and parsed once. A record carries the
    file's ``config/…`` label, its path, the names its OWN document spells, in the file's order (the
    ONE key rule, :func:`actions.preset_names_of`), and the ``name -> value`` map behind them. Two
    files may define the same name and nothing is merged here.

    BOTH halves of a launch read this answer, which is why it carries both: the dialog that draws the
    file pick and the name pick (``actions._weather_fields``) and the POST that resolves them
    (``actions._stated_presets``). One call lists the node once, so the files the dialog offers and
    the files the POST accepts cannot be two different sets.

    A file the node cannot deliver — an exception, or an ``int`` (a failure CODE) instead of bytes —
    is skipped and marks the answer PARTIAL; it never hides the others. A file that does not parse,
    or that is not a mapping, carries no preset: it is skipped WITHOUT marking the answer partial,
    because the content's own version (:func:`_listed_identity`) already makes it a different cache
    key when it is rewritten.

    The second answer says whether anything was read at all — ``False`` for a node with no config
    dir, no ``list_files``/``read_file``, a listing that failed, or a listing whose every file
    contributed nothing. The caller then offers its free-text fallback.

    A COMPLETE answer is REMEMBERED (:mod:`presets`' one node-list cache) under a key carrying the
    NODE and the versioned file set (:func:`preset_read_key` / :func:`_listed_identity`), so a second
    read re-lists (the file set must be current) but parses nothing. The listing itself is never
    remembered: a preset file added, removed or rewritten on the node has to reach the very next
    read, and only the LISTING can tell. A partial answer is not remembered either, so the next read
    asks again; a save through the console forgets the kind outright.
    """
    node_name = str(getattr(node, "name", "") or "")
    config_dir = str(getattr(node, "config_dir", "") or "")
    lister = getattr(node, "list_files", None)
    reader = getattr(node, "read_file", None)
    if not config_dir or lister is None or reader is None:
        return [], False
    try:
        entries = await lister(config_dir, pattern=list(PRESET_FILE_PATTERNS))
    except Exception as ex:  # noqa: BLE001 - every transport failure is the free-text fallback
        log.warning("Preset files: node '%s' could not list '%s' (%s)",
                    node_name, config_dir, type(ex).__name__)
        return [], False
    listed: list[tuple[str, str]] = []
    for entry in entries or []:
        path, size, mtime = _listing_entry(entry)
        if path:
            listed.append((path, _listed_identity(path, size, mtime)))
    listed.sort(key=lambda item: preset_file_label(config_dir, item[0]))
    key = preset_read_key(config_dir, [identity for _path, identity in listed])
    found, remembered = node_list_cached(PRESET_LIST, node_name, key)
    if found:
        return [(label, path, list(names), dict(values))
                for label, path, names, values in remembered], True
    records: list[tuple[str, str, tuple[str, ...], dict[str, Any]]] = []
    failed = False
    for path, _identity in listed:
        try:
            payload = await reader(path)
        except Exception as ex:  # noqa: BLE001 - one unreadable file must not hide the others
            log.warning("Preset files: node '%s' could not read '%s' (%s)",
                        node_name, path, type(ex).__name__)
            failed = True
            continue
        if not isinstance(payload, (bytes, bytearray)):
            log.warning("Preset files: node '%s' answered '%s' without content", node_name, path)
            failed = True
            continue
        try:
            document = configwrite.load_config_text(bytes(payload).decode("utf-8"), path=path)
        except configwrite.ConfigWriteError as ex:
            log.warning("Preset files: node '%s' answered unparsable content for '%s' (%s)",
                        node_name, path, type(ex).__name__)
            continue
        if not isinstance(document, Mapping):
            continue
        records.append((preset_file_label(config_dir, path), path,
                        tuple(actions_page.preset_names_of(document)), dict(document)))
    answer = [(label, path, list(names), dict(values)) for label, path, names, values in records]
    if failed:
        return answer, bool(records)
    remember_node_list(PRESET_LIST, node_name, key, tuple(records))
    return answer, True


async def node_preset_files(node: Any) -> tuple[list[tuple[str, str, list[str]]], bool]:
    """The preset FILES the node carries, each with the names it offers: ``[(label, path, names)]``.

    A view of :func:`node_preset_records`, the ONE read, for a caller that needs the names alone.
    """
    records, readable = await node_preset_records(node)
    return [(label, path, list(names)) for label, path, names, _values in records], readable


async def node_preset_values(node: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Every preset FILE the node carries, with the ``name -> value`` map that file spells.

    A view of :func:`node_preset_records`, the ONE read, for a caller that needs the values. The maps
    are NOT merged here: two files may define the same name, and telling them apart is the caller's
    job (``config/presets_winter.yaml`` is the label the operator picked).
    """
    records, _readable = await node_preset_records(node)
    return [(label, path, dict(values)) for label, path, _names, values in records]


def _listing_entry(entry: Any) -> tuple[str, Any, Any]:
    """One ``list_files`` entry as ``(path, size, mtime)``, with ``0`` for an absent size or mtime.

    ``list_files`` answers ``(path, size, mtime)`` triples, and a double or an older node build may
    answer the path alone — that file is still a preset file, so it is kept with no version.
    """
    if isinstance(entry, (list, tuple)):
        path = str(entry[0]).strip() if entry else ""
        size = entry[1] if len(entry) > 1 else 0
        mtime = entry[2] if len(entry) > 2 else 0
        return path, size, mtime
    return str(entry or "").strip(), 0, 0


async def _remembered_vendor_list(node_name: str, path: str, read) -> tuple[Any, str]:
    """``read``'s answer for the node file at *path*, and *path* — remembered across renders.

    The ONE seam both vendor files read through, so the cache is written in one place and read by the
    dialog's fieldset, every wizard step and both plate routes alike. A remembered refusal is re-raised
    as a fresh exception (never the stored one, whose traceback belongs to the read that produced it);
    a refusal that may not be remembered passes through untouched.
    """
    key = (VENDOR_LIST, str(node_name or ""), path)
    found, answer = node_list_cached(*key)
    if found:
        if isinstance(answer, _NodeFileUnreadable):
            raise _NodeFileUnreadable(answer.status_code, answer.sentence)
        return answer, path
    try:
        value = await read()
    except _NodeFileUnreadable as refusal:
        if refusal.status_code in _REMEMBERABLE_REFUSALS:
            remember_node_list(*key, refusal)
        raise
    remember_node_list(*key, value)
    return value, path


async def _read_cloud_presets(node, node_name: str) -> tuple[list[dcs_clouds.CloudPreset], str]:
    """The node's VISIBLE cloud presets, and the path they were read from.

    The answer is REMEMBERED per node file for ``NODE_LIST_TTL_SECONDS`` (:func:`_remembered_vendor_list`),
    so a render that has read the list once costs the node nothing. The path is resolved on every call
    and is part of the cache key: a node pointed at another installation is read afresh.

    Raises :class:`_NodeFileUnreadable` — one sentence per failure — rather than answering a partial
    read. ``read_file`` answers ``bytes | int`` and an ``int`` is a FAILURE CODE, never content. A
    vendor file that is absent, unreadable or carries nothing to offer raises the SAME 404: the
    consumer's answer is identical in all three cases (no plates, free text), and the sentence names
    the path that was looked for.
    """
    relative = dcs_clouds.CLOUDS_LUA_RELATIVE_PATH
    path = dcs_install.installation_file(node, relative)
    if not path:
        raise _NodeFileUnreadable(404, PRESETS_NO_CLOUD_LIST.format(node=node_name, path=relative))
    return await _remembered_vendor_list(
        node_name, path, lambda: _cloud_presets_from_node(node, node_name, path))


async def _cloud_presets_from_node(node, node_name: str,
                                   path: str) -> list[dcs_clouds.CloudPreset]:
    """The node's ``clouds.lua`` AT *path*, read, capped and parsed — the read behind the cache."""
    try:
        data = await node.read_file(path)
    except FileNotFoundError:
        raise _NodeFileUnreadable(404, PRESETS_NO_CLOUD_LIST.format(node=node_name, path=path))
    except PermissionError:
        raise _NodeFileUnreadable(403, PRESETS_NODE_PERMISSION.format(node=node_name, path=path))
    except (TimeoutError, asyncio.TimeoutError):
        raise _NodeFileUnreadable(504, PRESETS_NODE_TIMEOUT.format(node=node_name))
    except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
        log.warning("Cloud preset list: node '%s' raised %s reading '%s'", node_name,
                    type(ex).__name__, path, exc_info=True)
        raise _NodeFileUnreadable(502, PRESETS_NODE_RAISED.format(node=node_name, path=path,
                                                                error=type(ex).__name__))
    if not isinstance(data, (bytes, bytearray)):
        raise _NodeFileUnreadable(502, PRESETS_NODE_FAILURE_CODE.format(node=node_name, path=path))
    payload = bytes(data)
    if len(payload) > dcs_clouds.CLOUDS_LUA_MAX_BYTES:
        raise _NodeFileUnreadable(413, PRESETS_CLOUD_LIST_OVER_CAP.format(
            node=node_name, size=len(payload), limit=dcs_clouds.CLOUDS_LUA_MAX_BYTES, path=path))
    presets = dcs_clouds.visible_presets(dcs_clouds.parse_cloud_presets(
        payload.decode("utf-8", errors="replace")))
    if not presets:
        raise _NodeFileUnreadable(404, PRESETS_NO_CLOUD_LIST.format(node=node_name, path=path))
    return presets


async def _read_halo_presets(node, node_name: str) -> tuple[list[dcs_halo.HaloPreset],
                                                            list[dcs_halo.HaloPreset], str]:
    """The node's VISIBLE halo presets and crystal presets, and the path they were read from.

    The SAME read as :func:`_read_cloud_presets`, over the halo vendor file: the same path resolution,
    the same one-sentence failures, the same read cap, and the SAME remembered answer per node file.
    A file that is absent, unreadable or carries nothing VISIBLE in either table raises the SAME 404,
    and the sentence names the path looked for.
    """
    relative = dcs_halo.HALO_LUA_RELATIVE_PATH
    path = dcs_install.installation_file(node, relative)
    if not path:
        raise _NodeFileUnreadable(404, PRESETS_NO_HALO_LIST.format(node=node_name, path=relative))
    (halo, crystals), path = await _remembered_vendor_list(
        node_name, path, lambda: _halo_presets_from_node(node, node_name, path))
    return halo, crystals, path


async def _halo_presets_from_node(node, node_name: str, path: str) -> tuple[list[dcs_halo.HaloPreset],
                                                                           list[dcs_halo.HaloPreset]]:
    """The node's ``icehalo.lua`` AT *path*, read, capped and parsed — the read behind the cache."""
    try:
        data = await node.read_file(path)
    except FileNotFoundError:
        raise _NodeFileUnreadable(404, PRESETS_NO_HALO_LIST.format(node=node_name, path=path))
    except PermissionError:
        raise _NodeFileUnreadable(403, PRESETS_NODE_PERMISSION.format(node=node_name, path=path))
    except (TimeoutError, asyncio.TimeoutError):
        raise _NodeFileUnreadable(504, PRESETS_NODE_TIMEOUT.format(node=node_name))
    except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
        log.warning("Halo preset list: node '%s' raised %s reading '%s'", node_name,
                    type(ex).__name__, path, exc_info=True)
        raise _NodeFileUnreadable(502, PRESETS_NODE_RAISED.format(node=node_name, path=path,
                                                                  error=type(ex).__name__))
    if not isinstance(data, (bytes, bytearray)):
        raise _NodeFileUnreadable(502, PRESETS_NODE_FAILURE_CODE.format(node=node_name, path=path))
    payload = bytes(data)
    if len(payload) > dcs_halo.HALO_LUA_MAX_BYTES:
        raise _NodeFileUnreadable(413, PRESETS_HALO_LIST_OVER_CAP.format(
            node=node_name, size=len(payload), limit=dcs_halo.HALO_LUA_MAX_BYTES, path=path))
    text = payload.decode("utf-8", errors="replace")
    halo = dcs_halo.visible_presets(dcs_halo.parse_halo_presets(text))
    crystals = dcs_halo.visible_presets(dcs_halo.parse_crystal_presets(text))
    if not halo and not crystals:
        raise _NodeFileUnreadable(404, PRESETS_NO_HALO_LIST.format(node=node_name, path=path))
    return halo, crystals


def _usable_preset_name(name: str) -> bool:
    """Whether *name* is a NAME: one path segment, never a path.

    An absolute lead, either separator, an empty name or any ``..`` is refused before the node is
    asked for anything, which is what keeps the plate route from ever resolving a caller-supplied
    path.
    """
    if not name or name in (".", "..") or ".." in name:
        return False
    return "/" not in name and dcs_install.BACKSLASH not in name


def _named_entry(presets: list, name: str):
    """The parsed entry *name* names, matched exactly and then case-insensitively; else ``None``."""
    for preset in presets:
        if preset.name == name:
            return preset
    wanted = name.casefold()
    for preset in presets:
        if preset.name.casefold() == wanted:
            return preset
    return None


# ------------------------------------------------------------------------- the wizard (render only)
# The wizard is ONE page whose STEP lives in the URL (?step=…), so a refresh keeps it and the whole
# thing works with JavaScript off — the same rule the rest of the console follows. There is NO WRITE
# on this page: the form is a GET, so submitting a step puts the values in the URL and the sky preview
# redraws from them; the save is a later slice.

#: the entry point for a NEW preset — the SAME wizard, empty, step 1 first (which carries the name).
PRESETS_NEW_PATH = "/presets/new"
#: the check for a NEW preset: the SAME editor, but there is no open name to hold the document to, so
#: the document's own single key IS the name (there is nothing to rename away from).
PRESETS_NEW_VALIDATE_PATH = PRESETS_NEW_PATH + "/validate"
PRESETS_WIZARD_TEMPLATE = "presets_wizard.html"

WIZARD_STEP_TIME = "time"
WIZARD_STEP_CLOUDS = "clouds"
WIZARD_STEP_WIND = "wind"
WIZARD_STEP_REVIEW = "review"

#: the four steps the wizard presents, in order: (value, label, blurb). The options & files step is
#: deliberately OUT of v1.
WIZARD_STEPS: tuple[tuple[str, str, str], ...] = (
    (WIZARD_STEP_TIME, i18n._("Date & time"), i18n._("when the mission starts")),
    (WIZARD_STEP_CLOUDS, i18n._("The sky"), i18n._("clouds, thermal layers")),
    (WIZARD_STEP_WIND, i18n._("Wind & fog"), i18n._("wind, air, ground")),
    (WIZARD_STEP_REVIEW, i18n._("Review"), i18n._("the YAML and the save")),
)
WIZARD_STEP_VALUES: tuple[str, ...] = tuple(value for value, _label, _blurb in WIZARD_STEPS)

#: which of the table's controls each step carries. A step's list is its FULL membership in table
#: order; :func:`step_controls` narrows it to what the current values GATE in (the cloud mode decides
#: which cloud values are offered, the dust-or-fog choice decides which of the two, and the halo
#: preset decides whether the crystal plate grid is offered at all). The review step presents none —
#: it reads the produced document back.
STEP_FIELDS: dict[str, tuple[str, ...]] = {
    WIZARD_STEP_TIME: ("name", "date", "start_time", "terrains"),
    WIZARD_STEP_CLOUDS: ("clouds", "clouds.preset", "clouds.density", "clouds.base",
                         "clouds.thickness", "clouds.iprecptns", "halo.preset",
                         "halo.crystalsPreset"),
    WIZARD_STEP_WIND: ("temperature", "wind.atGround.speed", "wind.atGround.dir",
                       "wind.at2000.speed", "wind.at2000.dir", "wind.at8000.speed",
                       "wind.at8000.dir", "groundTurbulence", "qnh", "enable_dust", "dust_density",
                       "fog.mode", "fog.rows"),
    WIZARD_STEP_REVIEW: (),
}

#: the keys the CLOUD MODE decides: a DCS preset offers the plates and the base, a dynamic layer the
#: density/base/thickness/precipitation, and clear none of them.
CLOUD_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "preset": ("clouds.preset", "clouds.base"),
    "dynamic": ("clouds.density", "clouds.base", "clouds.thickness", "clouds.iprecptns"),
    "clear": (),
}

#: THE MODE KEYS: changing one of these controls changes WHICH ROWS a step draws, so the step must be
#: re-submitted for the gate to take effect. They carry ``data-pw-mode`` in the render and the page's
#: own script submits the step's form on a change (the form is a GET, so a submit is the whole fix).
MODE_KEYS: tuple[str, ...] = ("clouds", weather_fields.HAZE_KEY, weather_fields.FOG_MODE_KEY,
                              "halo.preset")


def is_real_halo(value: Any) -> bool:
    """Whether *value* names a halo PRESET — not empty, not ``off`` and not ``auto``.

    The crystal control and the halo entry plates are offered only for a real preset: ``off`` means no
    halo and ``auto`` leaves the halo (and its crystals) to DCS, so neither carries a crystal choice.
    """
    text = str(value or "").strip()
    return bool(text) and text not in (HALO_OFF_PRESET, HALO_AUTO_PRESET)

#: the fog ROWS' control: drawn only while the fog is MANUAL. ``auto`` is DCS's own build and carries
#: no rows (``core/mizfile.py`` writes the ``fog2`` mode with no values), so the table is not offered.
FOG_ROW_KEYS: tuple[str, ...] = (weather_fields.FOG_ROWS_KEY,)

#: the fog table's two SUBMIT buttons. Each is a control of the step's GET form, so adding and removing
#: a row carries the typed cells with it and works with JavaScript off.
FOG_ADD_FIELD = "fog_add"
FOG_REMOVE_FIELD = "fog_remove"


def row_gate(key: str, *, plates_readable: bool = True) -> dict:
    """The gate for *key* as DATA — the ONE gate the wizard's step and the launch dialog both read.

    A key the gate does not name is offered unconditionally. The parts:

    * ``cloud`` — the cloud modes that offer a cloud value (empty = every mode). A key the current
      mode does not name is not offered;
    * ``haze`` — the dust-or-fog values a key follows (``dust_density`` under ``dust``; ``fog.mode``
      and ``fog.rows`` under ``fog``);
    * ``fog_mode`` — the fog mode a key follows (``fog.rows`` under ``manual`` only);
    * ``halo_real`` — offered only for a real halo preset, never for ``off`` or ``auto``.

    A list the node could not be read (``plates_readable`` False) leaves the cloud preset's OWN key
    offered, which is its free-text fallback — the same rule :func:`step_controls` applies.
    """
    gate: dict = {}
    if key in ("clouds.preset", "clouds.density", "clouds.base", "clouds.thickness",
               "clouds.iprecptns"):
        modes = tuple(mode for mode, keys in CLOUD_VALUE_KEYS.items() if key in keys)
        if key == "clouds.preset" and not plates_readable:
            modes = ()
        gate["cloud"] = modes
    elif key == "halo.crystalsPreset":
        gate["halo_real"] = True
    elif key == "dust_density":
        gate["haze"] = ("dust",)
    elif key == "fog.mode":
        gate["haze"] = ("fog",)
    elif key == "fog.rows":
        gate["haze"] = ("fog",)
        gate["fog_mode"] = ("manual",)
    return gate


def _offered_in(key: str, *, cloud_mode: str, haze: str, halo: str, fog_mode: str,
                plates_readable: bool = True, halo_readable: bool = True) -> bool:
    """Whether the gate offers *key* for one state, read from :func:`row_gate`. Never a second list."""
    gate = row_gate(key, plates_readable=plates_readable)
    if gate.get("cloud") and cloud_mode not in gate["cloud"]:
        return False
    if gate.get("haze") and haze not in gate["haze"]:
        return False
    if gate.get("fog_mode") and fog_mode not in gate["fog_mode"]:
        return False
    if gate.get("halo_real") and (not is_real_halo(halo) or not halo_readable):
        return False
    return True


def step_controls(step: str, values: Mapping[str, str], *,
                  plates_readable: bool = True, halo_readable: bool = True) -> tuple[str, ...]:
    """The table controls *step* presents for *values* — the step's membership, GATED by the modes.

    The gating is DATA-IN: the cloud mode decides its value controls, the dust-or-fog choice decides
    whether the visibility or the fog controls follow it, and the halo preset decides whether the
    crystal grid is offered at all (``auto`` leaves the crystals to DCS). A key a mode does not offer
    is NOT drawn; it is carried as a hidden field elsewhere, so no step loses a value the URL held.

    ``plates_readable`` is the node's cloud-preset read and ``halo_readable`` its halo read: with no
    readable list the key falls back to its free-text box, which is the one control kept beside (never
    instead of) the pictures, and the crystal grid is not offered at all.

    The per-key conditions live in :func:`row_gate`, so the step and the launch dialog cannot drift.
    """
    cloud_mode = values.get("clouds", "")
    haze = values.get(weather_fields.HAZE_KEY, "")
    halo = values.get("halo.preset", "")
    fog_mode = values.get(weather_fields.FOG_MODE_KEY, "")
    out = [key for key in STEP_FIELDS.get(step, ())
           if _offered_in(key, cloud_mode=cloud_mode, haze=haze, halo=halo, fog_mode=fog_mode,
                          plates_readable=plates_readable, halo_readable=halo_readable)]
    return tuple(out)

#: every key the wizard renders, in table order — what the "other steps" carry as hidden fields.
WIZARD_KEYS: tuple[str, ...] = tuple(entry.key for entry in weather_fields.FIELDS)

#: a per-key hint — the wizard's OWN prose (the table stays prose-free). A key may carry SEVERAL
#: sentences and each renders as its own line; a key with no entry renders no hint.
FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "name": (i18n._("The key this preset gets in the file. A node names it in its MizEdit settings."),),
    "date": (i18n._("YYYY-MM-DD, or one of today / yesterday / tomorrow. The named moments are "
                    "computed from the theatre and the date, so set the date first."),),
    "start_time": (i18n._("An absolute time, a named moment, or a named moment plus an offset "
                          "(morning +02:00)."),),
    "terrains": (i18n._("Leave empty to let the preset apply on any map. A preset that names "
                        "theatres is skipped on the others."),),
    "clouds": (i18n._("A DCS preset is a named sky shipped with DCS; dynamic lets you set density "
                      "and base yourself; clear writes an empty cloud layer."),),
    "clouds.preset": (i18n._("A cloud preset from the node's DCS installation (its own clouds.lua)."),),
    "clouds.density": (i18n._("0 is clear sky, 10 is overcast."),),
    "clouds.base": (i18n._("How high the cloud layer starts."),),
    "clouds.thickness": (i18n._("The depth of the layer."),),
    "clouds.iprecptns": (i18n._("What the layer drops."),),
    "halo.preset": (i18n._("The sun/moon halo preset."),),
    "halo.crystalsPreset": (i18n._("The ice-crystal appearance behind the halo."),),
    "groundTurbulence": (i18n._("How rough the air is near the ground."),),
    "temperature": (i18n._("The ambient air temperature."),),
    "qnh": (i18n._("The barometric setting, in mmHg."),),
    #: the either-or control STATES the exclusivity; the visibility's own line explains the scale.
    "enable_dust": (i18n._("DCS applies dust or fog, not both: enabling one turns the other off."),),
    "dust_density": (i18n._("Dust haze is a separate switch with its own density."),
                     i18n._("0 is none, 10 is thick.")),
    "fog.mode": (i18n._("auto lets DCS build the fog from the mission's own time; manual uses the "
                        "keyframes below."),),
    "fog.rows": (i18n._("One row per time offset, in seconds from the mission's start."),),
}

#: the page's own sentences around the wizard.
WIZARD_TITLE = i18n._("Weather preset")
WIZARD_NEW_TITLE = i18n._("New preset")
WIZARD_LEAD = i18n._("A preset is a named block of mission overrides. This wizard writes the weather "
                     "half; the values land under the preset's own name in a preset file.")
WIZARD_RAIL_HEAD = i18n._("Build a preset")
WIZARD_STEP_OF = i18n._("Step %(step)d of %(total)d")
WIZARD_BACK = i18n._("Back")
WIZARD_NEXT = i18n._("Next")
WIZARD_CANCEL = i18n._("Cancel")
WIZARD_SAVE = i18n._("Save preset")
WIZARD_WRITE_NOTE = i18n._("The form never touches the file until you press Save preset.")
WIZARD_SKY_HEAD = i18n._("sky preview")
WIZARD_SKY_NOTE = i18n._("The picture is the wizard's own reading of your numbers — it is not DCS's "
                         "render. Only the cloud plates are DCS's own pictures.")
WIZARD_PLATES_HEAD = i18n._("Cloud preset")
WIZARD_PLATES_NOTE = i18n._("The plates are the DCS cloud presets, read from the node's own "
                            "installation.")
WIZARD_PLATES_FALLBACK = i18n._("The node's cloud-preset list could not be read, so type the cloud "
                                "preset's name by hand. The plates are only pictures.")
#: the crystal plates' own note and the sentence a halo list that cannot be read is answered with.
#: The list a node's ``icehalo.lua`` carries feeds BOTH the halo preset's options and the crystal
#: plates, so an unreadable file falls back to a typed name for the preset and no plates at all.
WIZARD_CRYSTALS_NOTE = i18n._("The plates are the DCS ice-crystal presets, read from the node's own "
                              "installation.")
WIZARD_HALO_FALLBACK = i18n._("The node's halo-preset list could not be read, so type the halo "
                              "preset's name by hand; the ice-crystal plates are not offered. The "
                              "plates are only pictures.")
WIZARD_MOMENTS_NOTE = i18n._("The moments are computed from the theatre and the date — so set the "
                             "date first.")
WIZARD_DEST_HEAD = i18n._("Where this preset is written")
WIZARD_DEST_ONE = i18n._("This preset is written into %(file)s.")
WIZARD_DEST_MANY = i18n._("This installation names more than one preset file — choose which one this "
                          "preset is written into.")
WIZARD_DEST_NONE = i18n._("No preset file is named by this installation, so there is nowhere to "
                          "write a new preset yet.")
WIZARD_DEST_SELECT = i18n._("Preset file")
#: the destinations a NEW preset may be written into are the files the nodes' own ``MizEdit.presets``
#: setting names (the extension reads no others). A named file that does not exist yet is offered
#: too — the save creates it — so the note says which, per entry.
WIZARD_DEST_NAMED = i18n._("These are the preset files this installation's nodes name in their "
                           "MizEdit.presets setting. A file named there that does not exist yet is "
                           "still offered — the save creates it.")
WIZARD_DEST_NOT_YET = i18n._("does not exist yet — the save creates it")
#: THE "New file…" CHOICE. It sits AFTER the offered files and reveals ONE text input, because a file
#: no node names is never read (the extension reads only what ``MizEdit.presets`` lists) — so the
#: choice says so, and the review step names the file before the Save button is pressed.
WIZARD_DEST_NEW_CHOICE = i18n._("New file…")
WIZARD_DEST_NEW_LABEL = i18n._("New preset file")
WIZARD_DEST_NEW_HELP = i18n._("A path inside the config directory: 'config/presets.yaml', another "
                              "'config/presets_*.yaml', or a name under 'config/presets/', as "
                              "'config/presets/night.yaml'. The save creates the file; a file that "
                              "already exists is picked from the list instead.")
WIZARD_DEST_NEW_ONE = i18n._("The save creates the preset file %(file)s and writes this preset "
                             "into it.")
#: the honest half: a file no node's ``MizEdit.presets`` names is never read by anything — the
#: sentence names the setting and where it is added, so a new file cannot look like it worked when
#: nothing reads it. The DEFAULT file is the one exception and gets its own sentence: an instance that
#: lists no preset files reads ``presets.yaml`` from its own config directory
#: (``extensions/mizedit/extension.py:41``), so for that file the claim "nothing reads it" would be
#: false.
WIZARD_DEST_UNREAD = i18n._("No node's MizEdit.presets names %(file)s, so nothing reads it — add it "
                            "to that setting on the Settings page for the node that should use this "
                            "preset.")
WIZARD_DEST_UNREAD_DEFAULT = i18n._("No node's MizEdit.presets names %(file)s. A node that lists no "
                                    "preset files reads the presets.yaml in its own config directory, so "
                                    "this file is read only by such a node — add it to that setting on "
                                    "the Settings page to have a node use it.")
WIZARD_REVIEW_WRITES = i18n._("What this preset writes")
WIZARD_REVIEW_YAML = i18n._("The produced YAML")
WIZARD_REVIEW_YAML_NOTE = i18n._("Read-only. This is the document the save will produce, dumped by "
                                 "the same writer every configuration page uses.")
#: the review's fog-rows panel copy. The fog TABLE now draws every time-keyed row as its own control,
#: so the panel that used to list the rows the wizard did not render has no rows to list; the copy
#: keeps its msgid.
WIZARD_FOG_KEEP_HEAD = i18n._("Fog keyframes kept")
WIZARD_FOG_KEEP_NOTE = i18n._("These time-keyed fog rows are already in the preset. The wizard does "
                              "not render them, so they are written back unchanged; the wizard's own "
                              "keyframe is written at 0 s.")

#: THE FOG TABLE'S OWN ROW CONTROL. A row is added and removed WITHOUT script: both are SUBMIT
#: buttons on the wizard's GET form, so clicking one re-posts every row's cells and the step renders
#: the list with that row appended (or dropped) — the same URL-driven shape the combination editor's
#: links use, but carrying the typed cells with it.
WIZARD_FOG_ADD = i18n._("Add a row")
#: the fog TABLE's first-row tag, in words: a row whose time is 0 is the moment the mission starts
#: (``preset_map_from_values`` writes it under ``fog["0"]``), so the tag names that time instead of a
#: bare ``0`` no reader can place. Emitted ONLY on a row whose time is 0.
KEYFRAME_START_LABEL = i18n._("at 0 seconds (mission start)")
PRESETS_REMOVAL_HEAD = i18n._("Removal")
PRESETS_REMOVAL_SENTENCE = i18n._("A preset a server uses cannot be removed — or renamed. The save "
                                  "reads the node settings that name presets and refuses the change, "
                                  "writing nothing, while any of them still spells this preset's name.")
PRESETS_REMOVAL_NOTE = i18n._("Once no reference names this preset the remove control appears — "
                              "remove the reference first.")
WIZARD_REVIEW_RESTART = i18n._("This is a config write, and it applies on the next bot restart. The "
                               "save goes through the same write path every other configuration page "
                               "uses: schema-validated first, written atomically, with a backup "
                               "beside it.")
#: THE EXPERT MODE'S OWN WORDS. The toggle's label and the sentence that tells the operator what an
#: expert save DOES: it writes the edited document and does not apply the steps' pending values.
WIZARD_EXPERT_LABEL = i18n._("Expert mode")
WIZARD_EXPERT_NOTE = i18n._("An expert-mode save writes the edited document for this one preset; the "
                            "values typed on the wizard's steps are not applied.")
WIZARD_NO_SKY = i18n._("this preset sets no weather")
WIZARD_PRESET_MISSING = i18n._("No preset named '%(name)s' is openable by the wizard.")
WIZARD_PRESET_EXPERT = i18n._("The preset '%(name)s' is the expert DSL — it is edited as raw YAML, "
                              "not through the weather wizard.")
#: the SHAPE switch and the combination editor. A combination is a preset whose value is a list of
#: other preset names — the mechanism that makes presets reusable — merged in order.
WIZARD_SHAPE_HEAD = i18n._("This preset is")
WIZARD_SHAPE_NOTE = i18n._("One preset, one shape: its own weather values, or a combination of "
                           "other presets merged in the order you list them.")
WIZARD_COMBO_HEAD = i18n._("Combination")
WIZARD_COMBO_NOTE = i18n._("These presets are merged in order; where two set the same value, the "
                           "LATER one wins.")
WIZARD_COMBO_EMPTY = i18n._("This combination names no presets yet — add one below.")
WIZARD_COMBO_ADD = i18n._("Add a preset")
WIZARD_COMBO_ORDER = i18n._("Order (first applied first — the last wins)")
WIZARD_COMBO_MOVE_UP = i18n._("Move up")
WIZARD_COMBO_MOVE_DOWN = i18n._("Move down")
WIZARD_COMBO_REMOVE = i18n._("Remove")
WIZARD_COMBO_UNKNOWN = i18n._("no such preset in this file")
WIZARD_COMBO_UNKNOWN_SHORT = i18n._("unknown")
WIZARD_EFFECTIVE_HEAD = i18n._("Effective weather")
WIZARD_EFFECTIVE_NOTE = i18n._("What the combination resolves to — the wizard's reading of the "
                               "names above, merged in order.")
WIZARD_EFFECTIVE_EMPTY = i18n._("The combination resolves to nothing yet.")


def new_preset_url(file: str = "") -> str:
    """The wizard URL for a NEW preset — the SAME wizard, empty, step 1 first.

    *file* is a file label the page's tab strip shows: the wizard opens its destination step on that
    file when the install offers it, and on its usual default when it does not.
    """
    if not file:
        return PRESETS_NEW_PATH
    return f"{PRESETS_NEW_PATH}?file={quote(file, safe='/')}"


def wizard_base(name: str) -> str:
    """The wizard's own URL for *name*, or the new-preset URL when *name* is empty."""
    return PRESETS_WIZARD_PATH.format(name=quote(name, safe="")) if name else PRESETS_NEW_PATH


def wizard_validate_url(name: str) -> str:
    """The wizard editor's validate URL for *name* — the new-preset URL when *name* is empty."""
    return (PRESETS_WIZARD_VALIDATE_PATH.format(name=quote(name, safe=""))
            if name else PRESETS_NEW_VALIDATE_PATH)


def preset_yaml_url(name: str) -> str:
    """The one-block editor's URL for preset *name* — the name as ONE percent-encoded segment."""
    return PRESETS_YAML_PATH.format(name=quote(str(name), safe=""))


def preset_yaml_validate_url(name: str) -> str:
    """That page's own check URL — a READ that answers the editor's JSON verdict."""
    return PRESETS_YAML_VALIDATE_PATH.format(name=quote(str(name), safe=""))


def preset_yaml_save_url(name: str) -> str:
    """That page's own save URL — the ONE write that replaces this preset's block."""
    return PRESETS_YAML_SAVE_PATH.format(name=quote(str(name), safe=""))


def preset_block_document(config: Path, name: str) -> dict | None:
    """The one-block editor's data for *name*, or ``None`` when no preset file carries it.

    ``{name, file, revision, document, kind}``: the KEY the file spells (the name is matched exactly,
    then case-insensitively — the same tolerant read :func:`read_preset` uses), the file's ``config/…``
    label, that file's revision (what the form posts back so a stale page writes nothing), this
    preset's OWN block — its top-level key plus its value — dumped through the one config writer, and
    the preset's KIND, which decides whether the page states the DSL conversion sentence.
    """
    found = find_preset_file(config, name)
    if found is None:
        return None
    path, label, key = found
    answer = read_preset_file(path)
    value = answer.document.get(key) if answer.parsed else None
    try:
        text = configwrite.dump_config({key: value})
    except configwrite.ConfigWriteError:
        return None
    return {"name": key, "file": label, "revision": configwrite.config_revision(path),
            "document": text, "kind": preset_kind(value)}


def preset_block_shape(name: str, document: Any) -> tuple[str, dict] | None:
    """The refusal a document that is not EXACTLY ``{name: value}`` earns, or ``None``.

    The page edits one preset's block, so the document must be a mapping of that one preset's own name
    to its value. A document that is not a mapping (or is empty), one carrying several top-level keys,
    and one keyed with a different name are each their own sentence and their own params — the name a
    preset carries is changed through the guarded wizard path, never here.
    """
    if not isinstance(document, Mapping) or not document:
        return PRESETS_REFUSED_BLOCK_NOT_A_MAP, {"name": name}
    if len(document) != 1:
        return PRESETS_REFUSED_BLOCK_SEVERAL, {"count": len(document),
                                               "keys": ", ".join(f"'{entry}'" for entry in document),
                                               "name": name}
    key = str(next(iter(document)))
    if key != name:
        return PRESETS_REFUSED_BLOCK_RENAME, {"posted": key, "name": name}
    return None


def validate_preset_block(request: Request, name: str, text: str, path: Path,
                          schema_files: list[str]) -> dict:
    """Check ONE preset's edited block and answer the editor's verdict — a READ, writing nothing.

    The structural rule the SAVE enforces comes first (:func:`preset_block_shape`); a document that
    passes it is checked the way a save checks it — merged into the file's CURRENT document and
    validated WHOLE against the same schema pair — so the editor and the write can never disagree.
    """
    try:
        posted = configwrite.load_config_text(text, path=path)
    except configwrite.ConfigWriteError:
        # bad YAML: let the shared check name the syntax fault with its line and column.
        return settings_page.validate_text(request, text, path, schema_files=schema_files)
    refusal = preset_block_shape(name, posted)
    if refusal is not None:
        msgid, params = refusal
        return _verdict(ok=False, message=i18n.translate(request, msgid, **params))
    # THE WHOLE DOCUMENT: the file's current text with THIS preset's key replaced by the posted block,
    # validated against the schema pair — the same merge a save performs, located in the editor's own
    # coordinates.
    return settings_page.validate_slice(request, text, path, list(schema_files), [name])



def read_preset(config: Path, name: str) -> tuple[Any, str] | None:
    """The value of the preset *name* and the file it came from, or ``None``.

    The SAME discovery and the SAME remembered read the area read uses (:func:`read_preset_file`), so
    the wizard reads a preset the way the page lists it. The match is exact and then
    case-insensitive; a name the install does not carry answers ``None``.

    The returned value is the document's own object, not a copy: every reader of it (the wizard's
    values, its shape, its combination) only reads.
    """
    config = Path(config)
    for path in discover_preset_files(config):
        answer = read_preset_file(path)
        if not answer.parsed:
            continue
        if name in answer.document:
            return answer.document[name], display_path(config, path)
        wanted = name.casefold()
        for key, value in answer.document.items():
            if str(key).casefold() == wanted:
                return value, display_path(config, path)
    return None


# -------------------------------------------------------------- the write: schema, path, the guard
# The save reads the SAME schema pair ``extensions/mizedit/extension.py`` validates a preset file
# against (``schemas/presets_schema.yaml`` plus the realweather partial its ``include:`` needs), the
# SAME discovery the area read uses, and the SAME round-trip primitive every config write uses.

#: the two schema files a preset file is validated against — the extension's OWN pair. Spelled
#: absolutely from THIS file so the console never depends on the process working directory.
_PRESET_SCHEMAS: tuple[str, ...] = ("schemas/presets_schema.yaml",
                                    "extensions/realweather/schemas/realweather_schema.yaml")


def preset_schema_files() -> list[str]:
    """The schema files a preset file is validated against — the pair the extension names.

    A file that does not ship contributes nothing, exactly as the bot's own validator reads the set.
    """
    root = Path(__file__).resolve().parents[3]
    return [str(root / relative) for relative in _PRESET_SCHEMAS if (root / relative).is_file()]


def preset_path(config: Path, file_label: str) -> Path | None:
    """The file a ``config/…`` label names under *config*, or ``None`` for a label that is not one.

    A label is what the page PRINTS (``config/presets.yaml``); this turns it back into a path under the
    install's config directory. A label that is not under ``config/`` is refused (``None``) rather than
    resolved, so a posted label can never name a file outside the config tree.
    """
    if not file_label or not file_label.startswith(f"{CONFIG_LABEL}/"):
        return None
    return Path(config) / file_label[len(CONFIG_LABEL) + 1:]


def _preset_file_shape(relative: str) -> bool:
    """Whether *relative* (below the config directory) is one of the forms discovery reads.

    Either a flat ``presets*.yaml`` at the config root, or ``presets/<name>.yaml`` — the two forms
    :data:`PRESET_FILE_GLOB` and :data:`PRESET_DIR` make up. Anything deeper, and anything carrying
    the separator twice, is not a form the page's own discovery would ever list.
    """
    parts = relative.split("/")
    if len(parts) == 1:
        return fnmatch.fnmatchcase(parts[0], PRESET_FILE_GLOB)
    if len(parts) == 2:
        return parts[0] == PRESET_DIR and parts[1].endswith(".yaml") and len(parts[1]) > len(".yaml")
    return False


#: a Windows drive prefix (``C:``) — a name that carries one is not a path inside the config tree.
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def preset_new_file(config: Path, text: str) -> tuple[Path | None, str, dict]:
    """THE ONE VALIDATION of a preset file name typed into the wizard's "New file…" choice.

    *text* is the name as typed, read as a path RELATIVE to the config directory in the form the page
    prints its files (``config/presets.yaml``). It answers ``(path, "", {})`` for a name the save may
    CREATE — the file itself is not created here — and ``(None, msgid, params)`` for one it must
    refuse, where *msgid* is the refusal's own sentence and *params* carry the text verbatim.

    The checks, in the order that names the FIRST real defect:
    the name is not blank (a blank name is no request — the route writes the offered destination
    instead, so this branch is a backstop only); it carries no control character (a NUL included); no
    backslash and no drive letter; it is not absolute; no ``..`` segment; it ends in ``.yaml``; it is
    ``config/presets*.yaml`` or ``config/presets/<name>.yaml`` (:func:`_preset_file_shape`); it
    resolves INSIDE the config directory (a symlinked ``presets/`` is caught here, which no textual
    check can see); and it does not exist — an existing file is SELECTED from the offered list, never
    overwritten.
    """
    name = "" if text is None else str(text).strip()
    if not name:
        return None, PRESETS_REFUSED_NEW_FILE_SHAPE, {"file": name}
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        return None, PRESETS_REFUSED_NEW_FILE_CONTROL, {"file": name}
    if "\\" in name or _DRIVE_LETTER.match(name):
        return None, PRESETS_REFUSED_NEW_FILE_WINDOWS, {"file": name}
    if name.startswith("/") or Path(name).is_absolute():
        return None, PRESETS_REFUSED_NEW_FILE_ABSOLUTE, {"file": name}
    if ".." in name.split("/"):
        return None, PRESETS_REFUSED_NEW_FILE_TRAVERSAL, {"file": name}
    if not name.endswith(".yaml"):
        return None, PRESETS_REFUSED_NEW_FILE_SUFFIX, {"file": name}
    if not name.startswith(f"{CONFIG_LABEL}/"):
        return None, PRESETS_REFUSED_NEW_FILE_SHAPE, {"file": name}
    relative = name[len(CONFIG_LABEL) + 1:]
    if not _preset_file_shape(relative):
        return None, PRESETS_REFUSED_NEW_FILE_SHAPE, {"file": name}
    config = Path(config)
    path = config / relative
    try:
        root = config.resolve()
        resolved = path.resolve()
    except OSError:
        return None, PRESETS_REFUSED_NEW_FILE_ESCAPE, {"file": name}
    if not resolved.is_relative_to(root):
        return None, PRESETS_REFUSED_NEW_FILE_ESCAPE, {"file": name}
    if path.exists():
        return None, PRESETS_REFUSED_NEW_FILE_EXISTS, {"file": name}
    return path, "", {}


def find_preset_file(config: Path, name: str) -> tuple[Path, str, str] | None:
    """The file carrying the preset *name*, its ``config/…`` label and the KEY it matched, or ``None``.

    The SAME discovery and the SAME tolerant match :func:`read_preset` uses (exact, then
    case-insensitive), so the save writes the file the wizard read. The matched key is returned so
    the whole handler acts on ONE key — the one the lookup found — rather than the posted spelling,
    which can differ only in case.

    A LOOKUP only: it reads keys, never a value, so it reads through the remembered answer
    (:func:`read_preset_file`). The write itself re-reads the file afresh.
    """
    config = Path(config)
    for path in discover_preset_files(config):
        answer = read_preset_file(path)
        if not answer.parsed:
            continue
        if name in answer.document:
            return path, display_path(config, path), str(name)
        wanted = name.casefold()
        for key in answer.document:
            if str(key).casefold() == wanted:
                return path, display_path(config, path), str(key)
    return None


# ---------------------------------------------------------------- the editor's check (nothing written)
# The review step's YAML panel is edited through the SHARED editor (``static/settings-yaml.js``): on
# every change it POSTs its text to this page's own validate URL and renders the JSON verdict it gets
# back. The check is the SERVER's — the same schema pair the save validates against — so the editor
# and the write can never disagree about what is wrong.

def _verdict(*, ok: bool, message: str, kind: str = "schema", line=None, column=None, block=None,
             block_line=None, block_end_line=None) -> dict:
    """The JSON body the wizard editor reads — the SAME shape the Settings editor's check answers with.

    One shape for a valid and an invalid document alike, so the one shared script renders either.
    """
    return {"ok": ok, "kind": kind, "line": line, "column": column, "block": block,
            "block_line": block_line, "block_end_line": block_end_line, "message": message}


def _document_path(config: Path, name: str) -> Path:
    """The file the editor's document would be written into — for the verdict's own file name.

    An EXISTING preset answers the file its name was found in; a NEW one (or a name the install does
    not carry) answers the destination rule's first file, else ``config/presets.yaml``. Only the
    verdict's wording reads this; nothing here resolves a caller-supplied path.
    """
    if name:
        found = find_preset_file(config, name)
        if found is not None:
            return found[0]
    destination = preset_destinations(config, _nodes_document(config))
    for label in destination.get("files", ()):
        path = preset_path(config, label)
        if path is not None:
            return path
    return Path(config) / "presets.yaml"


def validate_preset_text(request: Request, name: str, text: str, path: Path,
                         schema_files: list[str]) -> dict:
    """Check ONE preset's edited document and answer the editor's verdict — a READ, writing nothing.

    The structural rule the SAVE enforces comes FIRST: the document must be exactly the preset this
    page has OPEN — one top-level key, spelled as the file spells it (no rename, no second key, no
    empty document). Only a document that passes that rule is handed to the BOT'S OWN validator
    (:func:`services.webservice.pages.settings.validate_text`, the same call the Settings editor and a
    save make) with the SAME schema pair the save validates against. ``name`` is the open preset's
    name, ``""`` for a new preset (whose own single key IS the name). The summary sentence is rendered
    in the REQUEST's language.
    """
    try:
        posted = configwrite.load_config_text(text, path=path)
    except configwrite.ConfigWriteError:
        # bad YAML: let the shared check name the syntax fault with its line and column.
        return settings_page.validate_text(request, text, path, schema_files=schema_files)
    if posted is None or (isinstance(posted, Mapping) and not posted):
        return _verdict(ok=False, message=i18n.translate(
            request, PRESETS_REFUSED_EXPERT_EMPTY, name=name or ""))
    if isinstance(posted, Mapping):
        if len(posted) != 1:
            return _verdict(ok=False, message=i18n.translate(
                request, PRESETS_REFUSED_EXPERT_SEVERAL, count=len(posted),
                keys=", ".join(f"'{entry}'" for entry in posted)))
        doc_key = str(next(iter(posted)))
        if name and doc_key != name:
            return _verdict(ok=False, message=i18n.translate(
                request, PRESETS_REFUSED_EXPERT_RENAME, posted=doc_key, name=name))
    return settings_page.validate_text(request, text, path, schema_files=schema_files)


def _preset_file_label(named: Any, config: Path) -> str:
    """A ``MizEdit.presets`` entry as the ``config/…`` label the page prints, or ``""``.

    The setting is a path (``str_or_list``, ``extensions/mizedit/schemas/mizedit_schema.yaml``); the
    page prints a path under the config tree as ``config/<relative>``, so an entry already spelled
    that way is kept and one spelled relative to the install root is labelled the same way.
    """
    text = str(named).strip()
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        return display_path(config, path)
    if text.startswith(f"{CONFIG_LABEL}/"):
        return text
    return f"{CONFIG_LABEL}/{text}"


def mizedit_preset_files(nodes_document: Any, config: Path) -> list[tuple[str, str]]:
    """Every preset file a node/instance's ``MizEdit.presets`` names, as ``(label, who)`` pairs.

    ``MizEdit`` is an INSTANCE extension, so the walk is
    ``nodes.<node>.instances.<instance>.extensions.MizEdit``. ``presets`` is a path or a list of paths
    (``extensions/mizedit/extension.py:41-44`` reads either); ``who`` is the node+instance label that
    names it. In document order, deduplicated by label (the FIRST naming node wins), so the offered
    list is deterministic and a refusal can name the setting that governs it.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    if not isinstance(nodes_document, Mapping):
        return pairs
    for node_name, node in nodes_document.items():
        if not isinstance(node, Mapping):
            continue
        instances = node.get("instances")
        if not isinstance(instances, Mapping):
            continue
        for instance_name, instance in instances.items():
            if not isinstance(instance, Mapping):
                continue
            extensions = instance.get("extensions")
            if not isinstance(extensions, Mapping):
                continue
            mizedit = extensions.get("MizEdit")
            if not isinstance(mizedit, Mapping):
                continue
            named = mizedit.get("presets")
            if named is None:
                continue
            if not isinstance(named, (list, tuple)):
                named = [named]
            who = f"node '{node_name}', instance '{instance_name}'"
            for item in named:
                label = _preset_file_label(item, config)
                if label and label not in seen:
                    seen.add(label)
                    pairs.append((label, who))
    return pairs


def _names_in(value: Any) -> list[str]:
    """Every preset NAME a MizEdit ``settings`` value spells, in any of the shapes the schema allows.

    A string is a COMMA LIST (the extension splits it, ``extension.py:101-102``); a list is either a
    random pick (a list of comma strings) or a PERMUTATION (a list of lists); a mapping is a
    TIME-WINDOW map whose every value is one of those. Every string token is split on ``","`` and
    stripped, so all three collapse to the same reading — the one the extension itself performs.
    """
    if isinstance(value, str):
        return [token.strip() for token in value.split(",") if token.strip()]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_names_in(item))
        return out
    if isinstance(value, Mapping):
        out = []
        for item in value.values():
            out.extend(_names_in(item))
        return out
    return []


def _mizedit_reference_map(nodes_document: Any) -> dict[str, list[str]]:
    """Every preset NAME a node's ``MizEdit`` settings state, mapped to the labels that name it.

    ``MizEdit`` is an INSTANCE extension (``extensions/schemas/extensions_schema.yaml:39``), so the
    walk is ``nodes.<node>.instances.<instance>.extensions.MizEdit`` and never the node-level
    ``extensions``. A name is read out of the block's ``settings`` (a time-window map, a list, a
    permutation or a bare comma string) and out of ``terrains.<map>.settings`` in any of those shapes;
    a label is listed ONCE for a name however many of those places spell it.
    ``filter:`` is a REGEX matched against the mission FILE NAME (``extensions/mizedit`` calls
    ``re.search`` on the basename) and is deliberately NOT read. The result is a pure function
    of the document, so it is unit-testable without a bot.
    """
    index: dict[str, list[str]] = {}
    if not isinstance(nodes_document, Mapping):
        return index
    for node_name, node in nodes_document.items():
        if not isinstance(node, Mapping):
            continue
        instances = node.get("instances")
        if not isinstance(instances, Mapping):
            continue
        for instance_name, instance in instances.items():
            if not isinstance(instance, Mapping):
                continue
            extensions = instance.get("extensions")
            if not isinstance(extensions, Mapping):
                continue
            mizedit = extensions.get("MizEdit")
            if not isinstance(mizedit, Mapping):
                continue
            names = _names_in(mizedit.get("settings"))
            terrains = mizedit.get("terrains")
            if isinstance(terrains, Mapping):
                for terrain in terrains.values():
                    if isinstance(terrain, Mapping):
                        names.extend(_names_in(terrain.get("settings")))
            label = f"node '{node_name}', instance '{instance_name}'"
            for preset_name in dict.fromkeys(names):
                index.setdefault(preset_name, []).append(label)
    return index


def mizedit_references(nodes_document: Any, name: str) -> list[str]:
    """The node/instance labels whose ``MizEdit`` block names *name* — the single-name door onto
    :func:`_mizedit_reference_map`."""
    return _mizedit_reference_map(nodes_document).get(name, [])


def _combination_reference_map(preset_documents: Any) -> dict[str, list[str]]:
    """Every preset NAME a combination preset lists, mapped to the presets that list it.

    A combination preset's value is a LIST of other preset names (``preset_seq``); the extension
    merges them and only LOGS a missing member (``extension.py:110,117``), so renaming a member breaks
    the combination silently. Same hazard as a node reference, same refusal.
    """
    index: dict[str, list[str]] = {}
    for document in preset_documents:
        if not isinstance(document, Mapping):
            continue
        for key, value in document.items():
            if isinstance(value, (list, tuple)):
                for preset_name in dict.fromkeys(_names_in(value)):
                    index.setdefault(preset_name, []).append(f"the preset '{key}'")
    return index


def combination_references(documents: Any, name: str) -> list[str]:
    """The COMBINATION presets that list *name* — the single-name door onto
    :func:`_combination_reference_map`."""
    return _combination_reference_map(documents).get(name, [])


#: The Scheduler plugin's OWN per-server config, relative to the config tree
#: (``config/plugins/<name>.yaml``). Its actions name the presets a rotation APPLIES, so a preset it
#: names is in use even when ``nodes.yaml`` says nothing about it.
SCHEDULER_CONFIG_FILE = "plugins/scheduler.yaml"

#: The keys ONE element carries (``scheduler_schema.yaml``'s ``element_schema``) — what tells a
#: section holding one element from a node holding one element per instance.
SCHEDULER_ELEMENT_KEYS = frozenset({
    "timezone", "startup_delay", "warn", "schedule", "startup", "action",
    "onSimulationStart", "onSimulationStop", "onMissionEnd", "onShutdown",
})

#: The top-level keys of the Scheduler config that are NOT server sections (``commands`` and
#: ``chat_commands`` carry command definitions, ``scheduler_schema.yaml``'s root mapping).
SCHEDULER_NON_SERVER_KEYS = frozenset({"commands", "chat_commands"})


def _is_scheduler_element(value: Any) -> bool:
    """Whether *value* is ONE Scheduler element (a section) rather than a map of them.

    The root mapping allows a node entry to be an ``element_schema`` or an ``instance_schema`` (a map
    of instance name → element) and ``is_element`` separates the two by instance names. An element's
    keys are a FIXED set (``SCHEDULER_ELEMENT_KEYS``), so the same separation is drawn from those.
    """
    return isinstance(value, Mapping) and any(key in SCHEDULER_ELEMENT_KEYS for key in value)


def _scheduler_settings_names(action: Any) -> list[str]:
    """Every preset NAME one Scheduler action's ``settings`` states.

    ``settings`` is a SEQ of ``str_or_list`` entries (``scheduler_schema.yaml``'s ``action_map``),
    and the runtime hands each entry ON ITS OWN to ``utils.get_preset`` (``plugins/scheduler/commands.py``
    ``_run_with_presets``), which resolves a name with ``dict.get``. Exactly ONE shape resolves: a bare
    string entry, spelled exactly. A comma string is never split there and a list entry is not a
    hashable key — both MISS the lookup (the runtime only logs ``Preset "…" not found``), so neither
    is a reference and neither is read here.
    """
    if not isinstance(action, Mapping):
        return []
    settings = action.get("settings")
    if not isinstance(settings, (list, tuple)):
        return []
    return [str(entry) for entry in settings if isinstance(entry, str) and entry]


def _scheduler_element_names(element: Any) -> list[str]:
    """Every preset NAME one Scheduler element applies: its ``startup`` and every ``action``.

    ``startup`` is ONE action map; ``action`` is one map or a SEQ of them (``seq_or_map``). The
    runtime reads the same two keys (``config['startup']``, ``config['action']``).
    """
    if not isinstance(element, Mapping):
        return []
    names = _scheduler_settings_names(element.get("startup"))
    action = element.get("action")
    if isinstance(action, (list, tuple)):
        for entry in action:
            names.extend(_scheduler_settings_names(entry))
    else:
        names.extend(_scheduler_settings_names(action))
    return names


def _scheduler_reference_map(scheduler_document: Any) -> dict[str, list[str]]:
    """Every preset NAME the Scheduler applies, mapped to the sections that apply it.

    The Scheduler config (``<config>/plugins/scheduler.yaml``) is keyed by node and then by instance,
    with a top-level ``DEFAULT`` whose settings every server inherits (``core/plugin.py``
    ``get_base_config``: ``locals[DEFAULT]`` merged with ``locals[node][instance]``). A top-level key
    whose value is ONE element (``_is_scheduler_element``) is a single section — the shape a document
    uses for a node it does not name, and the shape the root mapping allows for a node entry too — so
    the label names the section KEY and prints verbatim either way. A preset is resolved there with
    the same EXACT lookup the extension uses, so a name spelled with another case is not a reference.
    A section is listed ONCE for a name however many of its actions spell it. The result is a pure
    function of the document, unit-testable without a bot.
    """
    index: dict[str, list[str]] = {}
    if not isinstance(scheduler_document, Mapping):
        return index

    def add(label: str, element: Any) -> None:
        for preset_name in dict.fromkeys(_scheduler_element_names(element)):
            labels = index.setdefault(preset_name, [])
            if label not in labels:
                labels.append(label)

    for section, value in scheduler_document.items():
        section_name = str(section)
        if section_name in SCHEDULER_NON_SERVER_KEYS:
            continue
        if section_name == "DEFAULT":
            add("the scheduler's default section", value)
        elif _is_scheduler_element(value):
            add(f"the scheduler's section '{section_name}'", value)
        elif isinstance(value, Mapping):
            for instance_name, element in value.items():
                add(f"the scheduler's section '{section_name}/{instance_name}'", element)
    return index


def scheduler_references(scheduler_document: Any, name: str) -> list[str]:
    """The Scheduler sections that apply *name* — the single-name door onto
    :func:`_scheduler_reference_map`."""
    return _scheduler_reference_map(scheduler_document).get(name, [])


def preset_reference_map(nodes_document: Any, preset_documents: Any,
                         scheduler_document: Any) -> dict[str, list[str]]:
    """Every preset NAME that is referenced, mapped to the labels that reference it.

    ONE walk of the three documents for the WHOLE page: a render that must know the state of every
    drawn tile pays one pass over ``nodes.yaml``, the parsed preset documents and
    ``plugins/scheduler.yaml``, never one pass per tile. The parts are concatenated in the order a
    single name's list has always carried them — node/instance labels, then the combination presets,
    then the Scheduler — so :func:`preset_references` answers exactly what it always did. A name
    nothing references has no entry.
    """
    index: dict[str, list[str]] = {}
    for part in (_mizedit_reference_map(nodes_document),
                 _combination_reference_map(preset_documents),
                 _scheduler_reference_map(scheduler_document)):
        for name, references in part.items():
            index.setdefault(name, []).extend(references)
    return index


def preset_references(nodes_document: Any, preset_documents: Any, name: str,
                      scheduler_document: Any) -> list[str]:
    """Every reference to *name*: node/instance labels, combination presets and the Scheduler.

    The single-name door onto :func:`preset_reference_map`, so the write guard, the removal confirm
    page and a tile's own note can never disagree about who holds the name. ``scheduler_document`` is
    the ``<config>/plugins/scheduler.yaml`` document, ``None`` when the install carries none.
    """
    return preset_reference_map(nodes_document, preset_documents,
                                scheduler_document).get(name, [])


def in_use_sentence(request: Request, name: str) -> str:
    """The ONE sentence that says *name* is still referenced, translated for the request.

    The removal confirm page prints it above the reference list and the tile that carries no trash
    prints it in its own note, so the two surfaces cannot spell the same fact two ways.
    """
    return i18n.translate(request, PRESETS_REMOVE_IN_USE, name=name)


def removal_card(card: dict, references: list[str], request: Request) -> dict:
    """*card* plus its removal state: the confirm URL, the references, or the sentence saying why not.

    A preset nothing references keeps the trash (:func:`removal_url`) and carries no note. One that is
    still referenced carries NO trash — the confirm page could only refuse it — and the sentence that
    says so with the referenced labels, so the foot never loses its control silently.
    """
    if not references:
        return {**card, "references": [], "remove_url": removal_url(card["name"]),
                "in_use_sentence": ""}
    return {**card, "references": list(references), "remove_url": "",
            "in_use_sentence": in_use_sentence(request, card["name"])}


def preset_removal(config: Path, name: str) -> dict | None:
    """The confirm page's own data for removing *name*, or ``None`` when no file carries it.

    ``{name, file, revision, document, references}``: the KEY the file spells, the file's ``config/…``
    label, the file's revision (what the form posts back so a stale page writes nothing), the preset's
    own document dumped through the one config writer, and EVERY reference to the name — the node and
    instance settings, the combination presets and the Scheduler's own sections.
    """
    found = find_preset_file(config, name)
    if found is None:
        return None
    path, label, key = found
    documents = [record["answer"].document for record in read_preset_files(config)
                 if record["answer"].parsed]
    references = preset_references(_nodes_document(config), documents, key,
                                   _scheduler_document(config))
    answer = read_preset_file(path)
    document = answer.document.get(key) if answer.parsed else None
    try:
        text = configwrite.dump_config({key: document})
    except configwrite.ConfigWriteError:
        text = ""
    return {"name": key, "file": label, "revision": configwrite.config_revision(path),
            "document": text, "references": references}


def _collision(document: Any, new_name: str, original: str) -> str:
    """The existing key a rename or a new preset would overwrite, or ``""`` when there is none.

    The key being renamed is not a collision with itself. The match is exact first (a YAML key is what
    would be overwritten), then case-insensitive — the SAME tolerant reading :func:`read_preset` uses,
    so a new name that resolves to an existing preset is refused too.
    """
    if not isinstance(document, Mapping) or new_name == original:
        return ""
    for key in document:
        if str(key) == new_name and str(key) != original:
            return str(key)
    wanted = new_name.casefold()
    for key in document:
        if str(key).casefold() == wanted and str(key) != original:
            return str(key)
    return ""


def _merged_preset(existing: Any, wizard_map: Mapping, owned: frozenset[str]) -> Any:
    """The preset map after the wizard's keys are applied to *existing*, leaving every other key.

    A mapping is changed IN PLACE — the wizard-owned top-level keys are set from *wizard_map*, a
    wizard-owned key the operator cleared is REMOVED, and every key the wizard does not render is left
    exactly as it was. A value that is not a mapping (a combination list, or nothing) is REPLACED by
    the wizard's map, because there are no keys of it to preserve.
    """
    if not isinstance(existing, MutableMapping):
        return wizard_map
    for key in owned:
        if key in wizard_map:
            existing[key] = wizard_map[key]
        elif key in existing:
            del existing[key]
    return existing


def preset_destinations(config: Path, nodes_document: Any = None) -> dict:
    """Where a NEW preset may be written: the files this install READS, and the rule that picks one.

    A preset a node does not name never applies: the extension reads ONLY the files its own
    ``MizEdit.presets`` setting lists (``extensions/mizedit/extension.py:41-44``). So when any
    node/instance names preset files, the destinations are EXACTLY those files — a named file that
    does not exist yet is still offered (the save creates it) and each entry says whether it exists.
    When NO node names one, the fallback is the page's own discovery, whose default is
    ``config/presets.yaml``.

    One file: the destination is STATED. More than one: the operator CHOOSES. None: there is nowhere
    to write yet, and the wizard says so rather than inventing a file.
    """
    config = Path(config)
    named = mizedit_preset_files(nodes_document, config)
    if named:
        entries = [{"label": label,
                    "exists": bool((preset_path(config, label) or Path()).is_file())}
                   for label, _who in named]
        who = ", ".join(dict.fromkeys(owner for _label, owner in named))
        from_setting = True
    else:
        labels = [display_path(config, path) for path in discover_preset_files(config)]
        entries = [{"label": label, "exists": True} for label in labels]
        who = ""
        from_setting = False
    labels = [entry["label"] for entry in entries]
    return {
        "files": labels,
        "entries": entries,
        "count": len(labels),
        "choose": len(labels) > 1,
        "single": labels[0] if len(labels) == 1 else "",
        "named": from_setting,
        "who": who,
    }


def preset_destination(config: Path, nodes_document: Any = None) -> dict:
    """The destination rule for *config* — the offered files and how one is picked."""
    return preset_destinations(config, nodes_document)


def preset_destination_one(file_label: str) -> dict:
    """The destination rule for an EXISTING preset: the ONE file it lives in, stated.

    An edit/rename/removal writes the file the preset is already in, so there is nothing to choose
    and nothing to create — the shape matches :func:`preset_destinations` for one existing file.
    """
    return {"files": [file_label], "entries": [{"label": file_label, "exists": True}], "count": 1,
            "choose": False, "single": file_label, "named": False, "who": ""}


def _preset_value(document: Any, key: str) -> str:
    """The string the preset *document* carries for the dotted *key*, or ``''``.

    A tolerant walk: a key the document does not carry yields ``''``, a list (the theatres) is joined
    with commas, a boolean is ``true``/``false``. The wizard's own ``name`` and the clouds MODE are
    derived, not stored, so they are answered elsewhere.
    """
    node: Any = document
    for part in key.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return ""
        node = node[part]
    if node is None:
        return ""
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, (list, tuple)):
        return ", ".join(str(item) for item in node)
    return str(node)


def fog_keyframes(fog: Any) -> list[tuple[Any, Any]]:
    """The preset's EXISTING time-keyed fog rows, in time order, as ``(time, row)`` pairs.

    The schema documents ``fog`` as time-keyed (``presets_schema.yaml``: ``regex;(\\d+)``), so a
    preset may carry a row per time offset. The wizard renders only ONE keyframe (0 s), so every other
    row is carried through untouched by :func:`preset_map_from_values` and shown on the review step. A
    value that is not a map, or carries no numeric key, contributes nothing. The time is returned as
    the document's OWN key, so a carried row is written back with the key type the file had.
    """
    if not isinstance(fog, Mapping):
        return []
    rows = [(key, value) for key, value in fog.items() if str(key).isdigit()]
    return sorted(rows, key=lambda pair: int(str(pair[0])))


def preset_values(name: str, document: Any, query: Mapping[str, str]) -> dict[str, str]:
    """The wizard's current values: the preset's own, overridden by what the form submitted.

    ``document`` is the preset's value (a map for its own values; a list for a combination). The
    submitted ``query`` wins for any key it names, so a step keeps its edits as the URL carries them.
    """
    values: dict[str, str] = {key: "" for key in WIZARD_KEYS}
    if isinstance(document, Mapping):
        for key in WIZARD_KEYS:
            if key in ("name", "clouds", weather_fields.HAZE_KEY):
                continue
            values[key] = _preset_value(document, key)
        # THE CLOCK is stored in seconds (the mission file, ``/mission save preset``) and read as
        # ``HH:MM`` — handed over in the shape the control holds, so the field, the time helper
        # beside it, the sky panel and the review all state the same readable time.
        values["start_time"] = start_time_display(values["start_time"])
        # the clouds MODE and the preset name are derived from the clouds value's SHAPE: a bare string
        # names a DCS preset, a map carrying ``preset`` does too, an EMPTY map is the clear layer, and
        # any other map is the dynamic layer.
        clouds = document.get("clouds")
        if isinstance(clouds, str):
            values["clouds"] = "preset"
            values["clouds.preset"] = clouds
        elif isinstance(clouds, Mapping):
            if clouds.get("preset") is not None:
                values["clouds"] = "preset"
                values["clouds.preset"] = str(clouds["preset"])
            else:
                values["clouds"] = "dynamic" if clouds else "clear"
        # the dust-or-fog CHOICE is derived from the PAIR (ONE control writes two keys), and the fog
        # table is read back from the preset's own time-keyed rows. A fog map with no mode but
        # time-keyed rows is read as manual, so those rows are carried through instead of dropped.
        values[weather_fields.HAZE_KEY] = haze_choice(document)
        fog = document.get("fog")
        if isinstance(fog, Mapping) and not values.get(weather_fields.FOG_MODE_KEY):
            values[weather_fields.FOG_MODE_KEY] = "manual" if fog_keyframes(fog) else ""
        values[weather_fields.FOG_ROWS_KEY] = serialize_fog_rows(fog_rows_from_document(document))
    if name:
        values["name"] = name
    for key, value in query.items():
        if key in values:
            values[key] = value
    return values


def _coerce_value(key: str, text: str) -> Any:
    """A posted string as the TYPE the preset schema expects for *key*.

    The form posts every control as a string, but ``schemas/presets_schema.yaml`` types them (``int``,
    ``float``, ``bool``, ``str``) and a string is refused where an ``int``/``bool`` is required, so the
    value is read from the ONE field table — a numeric control (slider/number/dial, and a fog table's
    own cell) becomes a number (an ``int`` when it is whole, else a ``float``), a toggle becomes a
    ``bool``, and a select whose every choice is a number (``clouds.iprecptns``) becomes an ``int``.
    Every other control is text. A key the table does not carry is text.
    """
    entry = weather_fields.FIELDS_BY_KEY.get(key) or weather_fields.FOG_COLUMNS_BY_NAME.get(key)
    if entry is None:
        return text
    if entry.kind == weather_fields.KIND_TOGGLE:
        return text == "true"
    if entry.kind in (weather_fields.KIND_SLIDER, weather_fields.KIND_NUMBER, weather_fields.KIND_DIAL):
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text
    if entry.kind == weather_fields.KIND_SELECT and entry.choices and all(
            value.lstrip("-").isdigit() for value, _label in entry.choices):
        try:
            return int(text)
        except ValueError:
            return text
    return text


# ------------------------------------------------------------------- the fog table's rows
# The mission's fog is ONE table keyed by SECONDS FROM MISSION START, each row ``{thickness,
# visibility}``. The wizard's control draws a row per entry, and the rows travel as a ``fog.rows``
# VALUE whose spelling is composed HERE — one place, so the page and the write cannot disagree.

def parse_fog_rows(text: Any) -> list[tuple[str, str, str]]:
    """The rows a ``fog.rows`` value carries, as ``(time, thickness, visibility)`` text triples.

    Rows are separated by ``,`` and a row's three cells by ``:`` — ``0:100:500, 300:200:2000``. A cell
    left empty stays empty (so a row may set a thickness only), a row that names no cell at all is
    dropped, and the text's own order is kept.
    """
    rows: list[tuple[str, str, str]] = []
    for chunk in str(text or "").split(","):
        cells = chunk.split(":")
        time = cells[0].strip()
        thickness = cells[1].strip() if len(cells) > 1 else ""
        visibility = cells[2].strip() if len(cells) > 2 else ""
        if not (time or thickness or visibility):
            continue
        rows.append((time, thickness, visibility))
    return rows


def serialize_fog_rows(rows: Any) -> str:
    """The ``fog.rows`` spelling of *rows* — the ONE place the value's text is composed."""
    return ", ".join(f"{time}:{thickness}:{visibility}" for time, thickness, visibility in rows)


def fog_rows_from_document(document: Any) -> list[tuple[str, str, str]]:
    """The fog table a preset's own value carries, in time order, as text triples.

    Read through :func:`fog_keyframes` (the schema's own time-keyed shape), so a row keeps the file's
    own key type in its TIME cell and a cell the row does not carry stays empty.
    """
    fog = document.get("fog") if isinstance(document, Mapping) else None
    rows: list[tuple[str, str, str]] = []
    for time, row in fog_keyframes(fog):
        thickness = row.get("thickness") if isinstance(row, Mapping) else None
        visibility = row.get("visibility") if isinstance(row, Mapping) else None
        rows.append((str(time), "" if thickness is None else str(thickness),
                     "" if visibility is None else str(visibility)))
    return rows


def fog_rows_from_form(form: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """The indexed fog table a submitted form carries, ordered by row index.

    Reads ONLY the ``fog_row_<index>_<cell>`` names the table's own controls use, so a value posted by
    any other control is ignored. No such field at all → no rows.
    """
    cells: dict[int, dict[str, str]] = {}
    for name, value in form.items():
        parsed = weather_fields.fog_row_cell(str(name))
        if parsed is None:
            continue
        index, cell = parsed
        cells.setdefault(index, {})[cell] = "" if value is None else str(value)
    return [(cells[index].get("time", ""), cells[index].get("thickness", ""),
             cells[index].get("visibility", "")) for index in sorted(cells)]


def haze_choice(document: Any) -> str:
    """The either-or choice a preset's own value states: ``dust``, ``fog`` or ``off``.

    DCS applies one of the two, so a preset that carries BOTH (a contradiction the pair is there to
    prevent) is read as the one holding the more: a fog map carries a mode and rows, where dust is a
    switch and a number, so the fog wins and the switch it drops costs least.
    """
    if not isinstance(document, Mapping):
        return ""
    fog = document.get("fog")
    if document.get("enable_fog") or isinstance(fog, Mapping):
        return "fog"
    if document.get("enable_dust"):
        return "dust"
    return "off"


def preset_map_from_values(values: Mapping[str, str], existing: Any = None, *,
                           fog_rows_stated: bool = False) -> dict:
    """The preset's OWN map built from the wizard's values — only the keys that are set.

    This is the MUTATION the save applies: the wizard owns the weather keys, and a key the operator
    left empty is not written (so MizEdit leaves it as the mission author wrote it). Every value is
    coerced to the schema's type by :func:`_coerce_value`, so a numeric control writes a number and a
    toggle writes a boolean — never the string the form posted.

    ``existing`` is the preset's value as the file carries it. ``fog_rows_stated`` says whether the
    form CARRIED the fog table's control (its ``fog.rows`` value, or the table's own indexed cells):
    a stated table is authoritative, so a row the operator REMOVED stays removed, while a form that
    left the table out never drops a row the preset already carries.
    """
    document: dict = {}

    def set_at(key: str, value: Any) -> None:
        node = document
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def is_set(key: str) -> bool:
        return str(values.get(key, "")).strip() != ""

    if is_set("date"):
        document["date"] = values["date"]
    if is_set("start_time"):
        # the control's text becomes the mission's own shape: ``HH:MM`` as the seconds the mission
        # file carries, a named moment as the text it is (``MizFile.apply_preset`` resolves it)
        document["start_time"] = start_time_value(values["start_time"])
    if is_set("terrains"):
        document["terrains"] = [part.strip() for part in values["terrains"].split(",") if part.strip()]
    if is_set("temperature"):
        document["temperature"] = _coerce_value("temperature", values["temperature"])
    if is_set("qnh"):
        document["qnh"] = _coerce_value("qnh", values["qnh"])
    if is_set("groundTurbulence"):
        document["groundTurbulence"] = _coerce_value("groundTurbulence", values["groundTurbulence"])
    mode = values.get("clouds", "")
    if mode == "preset" and is_set("clouds.preset"):
        document["clouds"] = values["clouds.preset"]
    elif mode == "dynamic":
        clouds: dict = {}
        for sub in ("density", "base", "thickness", "iprecptns"):
            if is_set(f"clouds.{sub}"):
                clouds[sub] = _coerce_value(f"clouds.{sub}", values[f"clouds.{sub}"])
        if clouds:
            document["clouds"] = clouds
    elif mode == "clear":
        document["clouds"] = {}
    for altitude in ("atGround", "at2000", "at8000"):
        speed, direction = f"wind.{altitude}.speed", f"wind.{altitude}.dir"
        if is_set(speed) or is_set(direction):
            row: dict = {}
            if is_set(speed):
                row["speed"] = _coerce_value(speed, values[speed])
            if is_set(direction):
                row["dir"] = _coerce_value(direction, values[direction])
            set_at(f"wind.{altitude}", row)
    if is_set("halo.preset"):
        set_at("halo.preset", values["halo.preset"])
        # the crystal appearance belongs to a NAMED preset: with ``auto`` DCS decides the crystals, so
        # the key is written only beside a name — and it is dropped with the halo map when the name
        # goes away, because ``halo`` is ONE owned top-level key.
        if is_set("halo.crystalsPreset") and is_real_halo(values["halo.preset"]):
            set_at("halo.crystalsPreset", values["halo.crystalsPreset"])
    # DUST OR FOG: ONE either-or choice writes the PAIR. ``dust`` sets the dust switch and its
    # visibility and writes neither fog key; ``fog`` sets ``enable_fog`` and its mode and writes no
    # dust key. The control posts one value, so the two keys cannot both be set.
    haze = values.get(weather_fields.HAZE_KEY, "")
    if haze in ("true", "dust"):
        document["enable_dust"] = True
        if is_set("dust_density"):
            document["dust_density"] = _coerce_value("dust_density", values["dust_density"])
    elif haze == "fog":
        document["enable_fog"] = True
        fog_mode = values.get(weather_fields.FOG_MODE_KEY, "") or "manual"
        fog: dict = {"mode": fog_mode}
        # ``auto`` is DCS's own build and carries NO rows (``core/mizfile.py`` writes the ``fog2``
        # mode with no values) — the table belongs to ``manual``.
        if fog_mode == "manual":
            rows = parse_fog_rows(values.get(weather_fields.FOG_ROWS_KEY, ""))
            if not rows and not fog_rows_stated:
                rows = fog_rows_from_document(existing)
            for time, thickness, visibility in rows:
                if not time.isdigit():
                    continue
                row: dict = {}
                if thickness.strip():
                    row["thickness"] = _coerce_value("thickness", thickness)
                if visibility.strip():
                    row["visibility"] = _coerce_value("visibility", visibility)
                if row:
                    fog[int(time)] = row
        document["fog"] = fog
    return document


def produced_yaml(name: str, values: Mapping[str, str], *, shape: str = SHAPE_OWN,
                  combination: str = "", existing: Any = None) -> str:
    """The document the save WILL produce for ONE preset, dumped through the ONE config writer.

    The document carries EXACTLY ONE top-level key — the preset this wizard is editing — so the review
    shows what the wizard changed, not the file it will be merged into. The value comes from
    :func:`preset_value_for`, the ONE home for "what the wizard writes", so a review can never show a
    document the save would not write. ``existing`` is the preset's CURRENT value (its own key, before
    any rename), which is what the produced map merges its unrendered keys from.

    This is the REVIEW's document: the SAVE loads the whole file and merges this ONE preset into it
    (``_presets_save``), so nothing here names the file or reads it. The dump goes through
    :func:`core.utils.configwrite.dump_config`, the same round-trip writer every configuration page
    uses. A preset with no name yet produces an EMPTY document — there is nothing to write.
    """
    document: dict = {}
    if name:
        document[str(name)] = preset_value_for(shape, values, combination, existing,
                                               fog_rows_stated=True)
    try:
        return configwrite.dump_config(document)
    except configwrite.ConfigWriteError as ex:
        return f"# {ex}"


def wizard_rows(keys: tuple[str, ...], values: Mapping[str, str]) -> list[dict]:
    """The field rows for *keys* — every number READ FROM THE TABLE, never typed here.

    Each row is ``{key, label, kind, unit, min, max, step, box_step, source, choices, value}``. The
    min/max/step, the box's own step and the unit come STRAIGHT from
    :mod:`services.webservice.weather_fields`, so a control cannot carry a number the table does not
    state. ``step`` is the SLIDER's granularity and ``box_step`` the typed-in box's — never the same
    figure, because a step emitted onto a box refuses a value a person typed.
    """
    rows: list[dict] = []
    for key in keys:
        entry = weather_fields.field(key)
        rows.append({
            "key": entry.key,
            "label": entry.label,
            "kind": entry.kind,
            "unit": entry.unit,
            "min": entry.min,
            "max": entry.max,
            "step": entry.step,
            "box_step": entry.box_step,
            "source": entry.source,
            "choices": [{"value": value, "label": label} for value, label in entry.choices],
            "value": values.get(key, ""),
        })
    return rows


#: the three NAMED DAYS a preset's ``date`` accepts beside a real date (``core/mizfile.parse_date``).
#: The wizard offers them as link pills; the launch dialog offers them as a ``<datalist>`` (a POST form
#: carries one value per name, so a second same-named control could never be read).
NAMED_DAYS: tuple[str, ...] = ("today", "yesterday", "tomorrow")


def dialog_rows(keys: tuple[str, ...], values: Mapping[str, str], *, clouds: Iterable[Any] = (),
                halo: Iterable[Any] = (), crystals: Iterable[Any] = ()) -> list[dict]:
    """The launch dialog's temporary rows: the table's rows with the node's own choice lists filled in.

    The SAME row builder (:func:`wizard_rows`) and the SAME choice builders the wizard's own step uses,
    so a row drawn in the launch dialog cannot carry options the wizard's row does not. A picker key
    whose list could not be read falls back to its free-text shape — never an EMPTY select, and never a
    refusal. The halo select is never empty: its ``off``/``auto`` pair comes from the field table.

    A ``fogtable`` row carries its OWN ``fog_table`` and the add/remove control names, because the
    dialog draws the table inside its row (the wizard draws it as a step-level panel). One blank row is
    offered when the preset names none, so the table is present and editable with no script.

    Every row also carries its GATE (``row["gate"]`` from :func:`row_gate`): the ONE gate the wizard's
    step reads, rendered as ``data-gate-*`` attributes. The dialog's own script shows/hides the row by
    the CURRENT mode values, so the rows follow the mode exactly as the wizard's step does; with no
    script every row stays visible and the route's own resolution is the backstop (never a second list).
    """
    rows = wizard_rows(keys, values)
    choices = {
        "halo.preset": halo_row_choices(halo),
        "clouds.preset": cloud_picker_choices(clouds),
        "halo.crystalsPreset": picker_choices(crystals),
    }
    # the gate the dialog's rows carry as DATA: the ONE gate (:func:`row_gate`) the wizard's step
    # reads too, so a row drawn here cannot follow a mode the wizard's step would not. It is rendered
    # as ``data-gate-*`` attributes; the dialog's own script shows/hides by them, and with script off
    # every row stays visible and the route's own resolution is the backstop.
    plates_readable = bool(choices["clouds.preset"])
    for row in rows:
        row["gate"] = row_gate(row["key"], plates_readable=plates_readable)
        if row["key"] in choices:
            row["choices"] = choices[row["key"]]
            row["free_text"] = not row["choices"]
        if row["kind"] == weather_fields.KIND_DATE:
            row["iso_date"] = not row["value"] or _is_iso_date(row["value"])
            row["named_days"] = NAMED_DAYS
        elif row["kind"] == weather_fields.KIND_MOMENT:
            row["time_value"] = _time_of(row["value"])
            row["moments"] = weather_fields.moments()
        elif row["kind"] == weather_fields.KIND_FOGTABLE:
            table = fog_table_rows(values, {})
            if not table:
                table = [{"index": 0, "cells": [
                    {"name": weather_fields.fog_row_field(0, column.key), "value": "",
                     "min": column.min, "max": column.max, "step": column.step,
                     "box_step": column.box_step,
                     "hint": weather_fields.FOG_HINTS.get(column.key, ""),
                     "start": False}
                    for column in weather_fields.FOG_COLUMNS]}]
            row["fog_table"] = table
            row["fog_columns"] = weather_fields.FOG_COLUMNS
            row["fog_add_field"] = FOG_ADD_FIELD
            row["fog_remove_field"] = FOG_REMOVE_FIELD
            row["fog_add_label"] = WIZARD_FOG_ADD
            row["fog_start_label"] = KEYFRAME_START_LABEL
            row["fog_remove_label"] = WIZARD_COMBO_REMOVE
    return rows


def sky_preview(values: Mapping[str, str]) -> dict:
    """The wizard's OWN sky reading, as numbers for the inline SVG — never a DCS render.

    The band's position lifts with the cloud base and its opacity thickens with density; the sky's
    colours follow the moment named in ``start_time``. It reads only the values, so it redraws as the
    form carries them.
    """
    def number(key: str, fallback: float) -> float:
        try:
            return float(str(values.get(key, "")).strip())
        except (TypeError, ValueError):
            return fallback

    density = number("clouds.density", 0.0)
    base = number("clouds.base", 0.0)
    moment = str(values.get("start_time", "")).split()[0].lower() if values.get("start_time") else ""
    palettes = {
        "dawn": ("#16233f", "#3d3a58", "#8d6a63"),
        "sunrise": ("#20355e", "#6d7fa8", "#e6b07a"),
        "morning": ("#2a5f96", "#5f93c4", "#a9c6de"),
        "noon": ("#1f6fc4", "#5ba0dd", "#b9d6ef"),
        "evening": ("#243a63", "#7a6f93", "#d79a76"),
        "sunset": ("#1c2a4d", "#5d4a6b", "#e08a5a"),
        "dusk": ("#141d38", "#38334f", "#7c5f63"),
        "night": ("#0b1226", "#1c2540", "#384a6b"),
    }
    top, mid, low = palettes.get(moment, ("#16233f", "#3d3a58", "#8d6a63"))
    opacity = round(min(0.9, 0.18 + density * 0.07), 2)
    band_y = int(max(6, min(150, 150 - base / 2520 * 120)))
    return {"top": top, "mid": mid, "low": low, "opacity": opacity, "band_y": band_y,
            "density": density}


def _wizard_steps(current: str, base: str, query: Mapping[str, str]) -> list[dict]:
    """The rail: each step a LINK that keeps the values the URL already carries and swaps the step."""
    carried = {key: value for key, value in query.items() if key != "step"}
    steps: list[dict] = []
    for index, (value, label, blurb) in enumerate(WIZARD_STEPS):
        params = dict(carried)
        params["step"] = value
        steps.append({
            "value": value,
            "label": label,
            "blurb": blurb,
            "url": f"{base}?{urlencode(params)}",
            "active": value == current,
            "done": WIZARD_STEP_VALUES.index(current) > index,
        })
    return steps


def out_of_range_value(values: Mapping[str, Any], rows: Iterable[tuple[str, str, str]]) -> tuple[Any, str] | None:
    """The first numeric control whose posted value falls outside its own range, with that text.

    ONE home for the range: every limit is read from the field table
    (:func:`weather_fields.out_of_range`), so the browser's ``min``/``max`` box and this check cannot
    disagree. The numeric controls are read in table order, then the fog keyframes' own thickness and
    visibility cells. The value comes back as the TEXT the form posted, so a refusal can quote the
    value verbatim. ``None`` when every value is inside its range.

    A combination's form writes no weather value, so its weather controls are not checked at all.
    """
    for key in weather_fields.NUMERIC_KEYS:
        text = str(values.get(key, "") or "")
        offence = weather_fields.out_of_range(key, text)
        if offence is not None:
            return offence[0], text.strip()
    for _time, thickness, visibility in rows:
        for column, text in (("thickness", thickness), ("visibility", visibility)):
            offence = weather_fields.out_of_range(column, text)
            if offence is not None:
                return offence[0], str(text).strip()
    return None


def _value_text(value: Any) -> str:
    """A preset value as the wizard prints it: ``true``/``false`` for a bool, dotted for a map."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping):
        return ", ".join(f"{key}: {_value_text(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_value_text(item) for item in value)
    return str(value)


def _wizard_shape(query: Mapping[str, str], document: Any) -> str:
    """The shape the wizard presents: the URL's ``shape=`` when it names one, else the document's own."""
    shape = (query.get(SHAPE_FIELD) or "").strip()
    if shape in (SHAPE_OWN, SHAPE_COMBINATION):
        return shape
    return preset_shape(document)


def _wizard_combination(query: Mapping[str, str], document: Any) -> str:
    """The ordered member names the wizard presents: the URL's ``combination=``, else the preset's list."""
    if COMBINATION_FIELD in query:
        return query.get(COMBINATION_FIELD) or ""
    return combination_text(document)


def _combination_url(base: str, step: str, query: Mapping[str, str], names: list[str]) -> str:
    """A wizard URL that keeps the current query and swaps the ordered combination list.

    ``step`` is re-set last so a move/remove/add never loses the step the operator is on. Nothing is
    written: this is the same URL-driven, JavaScript-off shape the rail and the moment pills use.
    """
    params = {key: value for key, value in query.items()
              if key not in (COMBINATION_FIELD, "step", SHAPE_FIELD)}
    params[SHAPE_FIELD] = SHAPE_COMBINATION
    if names:
        params[COMBINATION_FIELD] = combination_text(names)
    params["step"] = step
    return f"{base}?{urlencode(params)}"


def combination_editor(base: str, step: str, query: Mapping[str, str], document: Any, names: list[str],
                       *, exclude: str) -> dict:
    """The combination editor's context: ordered rows with move/remove/add links and the effective weather.

    Every move/remove/add is a LINK that recomputes the ordered list and carries it in the URL, so
    reordering is a GET that works with JavaScript off. The rows read the file to mark a name the file
    does not carry (``known`` is ``False``) — the SAME dangling name the save refuses. Nothing is
    written here.
    """
    rows: list[dict] = []
    for index, name in enumerate(names):
        def swapped(target: int) -> list[str]:
            moved = list(names)
            moved[index], moved[target] = moved[target], moved[index]
            return moved

        rows.append({
            "index": index,
            "name": name,
            "known": has_preset(document, name),
            "up_url": _combination_url(base, step, query, swapped(index - 1)) if index > 0 else "",
            "down_url": _combination_url(base, step, query, swapped(index + 1))
            if index < len(names) - 1 else "",
            "remove_url": _combination_url(
                base, step, query, [n for i, n in enumerate(names) if i != index]),
        })
    candidates = [{"name": candidate, "url": _combination_url(base, step, query, names + [candidate])}
                  for candidate in combination_candidates(document, exclude) if candidate not in names]
    effective = effective_weather(expand_combination(document, names))
    return {
        "combination_field": COMBINATION_FIELD,
        "combination": combination_text(names),
        "combination_rows": rows,
        "combination_candidates": candidates,
        "combination_empty": not names,
        # a member's own clock reads as the mission states it; the panel prints the time, not the count
        "effective_rows": [{"key": key,
                            "value": start_time_display(value) if key == "start_time"
                            else _value_text(value)}
                           for key, value in effective.items()],
    }


def _effective_sky_values(effective: Mapping) -> dict[str, str]:
    """The effective weather as the flat keys :func:`sky_preview` reads, so a combination redraws the sky."""
    clouds = effective.get("clouds")
    values = {"start_time": start_time_display(effective.get("start_time", "")),
              "clouds.density": "", "clouds.base": ""}
    if isinstance(clouds, Mapping):
        for sub in ("density", "base"):
            if clouds.get(sub) is not None:
                values[f"clouds.{sub}"] = str(clouds[sub])
    return values


def _plain_number(value: Any) -> str:
    """A number as the text a form control carries: ``2000`` for ``2000.0``, else the number's text."""
    if value is None:
        return ""
    numeric = float(value)
    return str(int(numeric)) if numeric.is_integer() else str(numeric)


def _is_iso_date(value: str) -> bool:
    """Whether *value* is a ``YYYY-MM-DD`` date — the shape a ``<input type="date">`` can hold."""
    try:
        datetime.strptime(str(value).strip(), "%Y-%m-%d")
        return True
    except ValueError:
        return False


#: the two shapes ONE start time is written in: the mission file (and ``/mission save preset``)
#: states the clock as SECONDS SINCE MIDNIGHT; every control and every panel states it ``HH:MM``.
#: A named moment (``morning``, ``dusk +00:30``) is text in both, and so is a count no clock can
#: spell (a day or more) — never a number invented out of it.
_CLOCK_TEXT = re.compile(r"(\d{1,2}):(\d{2})")


def start_time_display(value: Any) -> str:
    """The start time *value* as a control carries it: a bare second count becomes ``HH:MM``.

    The ONE direction a stored value takes on its way to a person: the mission file's ``28800``
    reads ``08:00``, so the wizard's field, the sky panel and the review's value rows follow, and
    a text value (a named moment, with or without an offset) passes through unchanged.
    """
    text = "" if value is None else str(value).strip()
    if not re.fullmatch(r"\d+", text):
        return text
    seconds = int(text)
    if seconds >= 24 * 60 * 60:
        return text
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def start_time_value(value: Any) -> Any:
    """The value a preset's ``start_time`` keeps for a control's text — the mission's own shape.

    The ONE direction a control's text takes on its way into a document: ``08:00`` becomes the
    ``28800`` the mission file carries, so a prefilled preset IS the document ``/mission save
    preset`` writes, and a named moment keeps the text ``MizFile.apply_preset`` resolves. An
    ``HH:MM`` no clock can hold is kept as the text it is rather than turned into a wrong number.
    """
    text = "" if value is None else str(value).strip()
    match = _CLOCK_TEXT.fullmatch(text)
    if not match:
        return text
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return text
    return hours * 3600 + minutes * 60


def _time_of(value: str) -> str:
    """The ``HH:MM`` a start time states, or ``''`` when it is a named moment (or an offset)."""
    text = str(value).strip()
    return text if re.fullmatch(r"\d{1,2}:\d{2}", text) else ""


def _pair_wind_rows(rows: list[dict]) -> list[dict]:
    """Fold each wind layer's SPEED row and its DIR row into ONE row: speed left, dial right.

    The table declares the two beside each other (``wind.<layer>.speed``, ``wind.<layer>.dir``); the
    control draws them as one row, so the shape is the card's while the posted field names and the
    values stay exactly what the table declares. The dial is the SAME pair the speed slider is — one
    range (never submitted) wired to one named number box — only drawn round. A dir row with no speed
    partner is kept on its own.
    """
    by_key = {row["key"]: row for row in rows}
    folded: list[dict] = []
    used: set[str] = set()
    for row in rows:
        key = row["key"]
        if key.endswith(".speed"):
            partner = by_key.get(key[:-len(".speed")] + ".dir")
            if partner is not None:
                row = {**row, "dial": partner}
                used.add(partner["key"])
        elif key in used:
            continue
        folded.append(row)
    return folded


def fog_table_rows(values: Mapping[str, str], query: Mapping[str, Any]) -> list[dict]:
    """The fog table's rows for ONE render, as ``{index, cells}`` with a cell per column.

    The rows come from the form's own INDEXED cells when the step posted them (so a typed edit is not
    lost), else from the carried ``fog.rows`` value — the preset's own rows, or the hidden field the
    review step holds. ``fog_add`` appends one empty row and ``fog_remove`` drops the row it names;
    both are SUBMIT buttons on the step's GET form, so the table is edited WITHOUT script.
    """
    posted = fog_rows_from_form(query) if any(
        weather_fields.fog_row_cell(str(key)) for key in query) else None
    rows = posted if posted is not None else parse_fog_rows(
        values.get(weather_fields.FOG_ROWS_KEY, ""))
    if query.get(FOG_ADD_FIELD):
        rows.append(("", "", ""))
    removal = query.get(FOG_REMOVE_FIELD)
    if removal is not None and str(removal).strip().isdigit():
        index = int(str(removal).strip())
        if 0 <= index < len(rows):
            del rows[index]
    return [{
        "index": index,
        "cells": [{"name": weather_fields.fog_row_field(index, column.key),
                   "value": row[position],
                   "min": column.min, "max": column.max, "step": column.step,
                   "box_step": column.box_step,
                   "hint": weather_fields.FOG_HINTS.get(column.key, ""),
                   "start": column.key == "time" and row[0].strip() == "0"}
                  for position, column in enumerate(weather_fields.FOG_COLUMNS)],
    } for index, row in enumerate(rows)]


def _write_rows(values: Mapping[str, str]) -> list[dict]:
    """The review's "what this preset writes" rows: ONE row per REAL preset key.

    A control that writes a TABLE (the fog rows) prints the key the file gets (``fog``) with the
    table's own spelling of its rows, so the panel never names the control's own key in place of a
    key the preset carries.
    """
    rows: list[dict] = []
    for entry in weather_fields.FIELDS:
        if entry.key in ("name", "clouds"):
            continue
        if entry.kind == weather_fields.KIND_FOGTABLE:
            rows.append({"key": "fog", "value": values.get(weather_fields.FOG_ROWS_KEY, "")})
        else:
            rows.append({"key": entry.key, "value": values.get(entry.key, "")})
    return rows


async def build_wizard_context(request: Request, config: Path, *, name: str, step: str,
                               document: Any, file_label: str, destination: dict) -> dict:
    """The wizard's render context for ONE step — the values, the rail, the plates and the preview.

    On the clouds step the node's OWN lists are read: the cloud presets (the plates) and the halo
    presets (the halo control's options and the crystal plates). A list that cannot be read falls back
    to a free-text box for its key and says so — the step is never refused. This is a READ: nothing is
    written.
    """
    query = dict(request.query_params)
    values = preset_values(name, document, query)
    base = wizard_base(name)
    steps = _wizard_steps(step, base, query)

    # THE FOG TABLE. Its rows are the preset's own time-keyed rows (or the cells the form just posted,
    # which is what a typed edit carries), and ADD/REMOVE are SUBMIT buttons on this step's own GET
    # form — so the table is edited WITHOUT script. Its value is re-composed HERE, once, so every step
    # carries the same text and the review can post it on.
    table = fog_table_rows(values, query)
    values[weather_fields.FOG_ROWS_KEY] = serialize_fog_rows(
        [tuple(cell["value"] for cell in entry["cells"]) for entry in table])

    # THE DESTINATION FILE. One file: it is the single one stated. Several: the operator's own choice
    # rides in the URL (``file=``) and is carried here; when none was posted the first is the default.
    # A typed ``new_file`` name IS the destination of a new preset — validated HERE by the same one
    # home the save uses, so the review step can name the file it will create; a name it refuses is
    # shown with its own sentence on the step instead of as a destination. The revision rendered into
    # the form is that file's, so a stale form is refused against the file the save will actually write.
    files = list(destination.get("files", ()))
    posted_file = (query.get("file") or "").strip()
    new_file_value = (query.get(PRESETS_NEW_FILE_FIELD) or "").strip()
    new_file_error = ""
    chosen_file = ""
    chosen_path: Path | None = None
    chosen_is_new = False
    if new_file_value:
        target, refusal, params = preset_new_file(config, new_file_value)
        if target is None:
            new_file_error = i18n.translate(request, refusal, **params)
        else:
            chosen_path = target
            chosen_file = display_path(config, target)
            chosen_is_new = True
    else:
        chosen_file = (posted_file if posted_file in files
                       else destination.get("single") or (files[0] if files else ""))
        chosen_path = preset_path(config, chosen_file) if chosen_file else None
    revision = configwrite.config_revision(chosen_path) if chosen_path is not None else ""
    # THE HONEST HALF: a file no node's ``MizEdit.presets`` names is read by nothing — except the
    # DEFAULT file, which an instance that lists no preset files reads from its own config directory,
    # so that one carries its own sentence instead. Rendered here, so the step and the review agree.
    named_files = {label for label, _who in mizedit_preset_files(_nodes_document(config), config)}
    unread_sentence = ""
    if chosen_file and chosen_file not in named_files:
        rule = (WIZARD_DEST_UNREAD_DEFAULT
                if chosen_file == f"{CONFIG_LABEL}/{MIZEDIT_DEFAULT_FILE}" else WIZARD_DEST_UNREAD)
        unread_sentence = i18n.translate(request, rule, file=chosen_file)

    # THE FILE a combination resolves against: its members and its candidate names are the presets of
    # the SAME file the preset is written into. ``document`` is the preset's OWN value (a map or a
    # list), NOT the file, so the file's document is loaded here and the members are read from it.
    # The remembered answer is enough: every reader below (the candidates, the expansion, the
    # effective weather) reads the document and none of them writes into it.
    presets_document: Any = {}
    if chosen_path is not None:
        answer = read_preset_file(chosen_path)
        presets_document = answer.document if answer.parsed else {}

    # THE SHAPE. A preset is its own values OR a combination, never both; the shape rides in the URL
    # and swaps the work panel between the four weather steps and the combination editor.
    shape = _wizard_shape(query, document)
    combination = _wizard_combination(query, document) if shape == SHAPE_COMBINATION else ""
    members = parse_combination(combination)
    combo = combination_editor(base, step, query, presets_document, members, exclude=name)
    if shape == SHAPE_COMBINATION:
        effective = effective_weather(expand_combination(presets_document, members))
        sky = sky_preview(_effective_sky_values(effective))
        writes = [{"key": row["name"], "value": ""} for row in combo["combination_rows"]]
    else:
        sky = sky_preview(values)
        writes = _write_rows(values)

    plates: list[dict] = []
    plate_notice = ""
    crystals: list[dict] = []
    crystal_notice = ""
    halo_choices: list[dict] = []
    halo_entries: list[dict] = []
    if step == WIZARD_STEP_CLOUDS:
        node_name = (query.get("node") or "").strip() or _default_node_name(request)
        if node_name:
            reachable = _reachable_node(request, node_name)
            if reachable is None:
                plate_notice = PRESETS_NODE_OFFLINE.format(node=node_name)
                crystal_notice = plate_notice
            else:
                try:
                    presets, _path = await _read_cloud_presets(reachable, node_name)
                except _NodeFileUnreadable as unavailable:
                    plate_notice = unavailable.sentence
                else:
                    plates = [{"name": preset.name, "label": preset.label,
                               "url": cloud_image_path(node_name, preset.name),
                               "base": _plain_number(preset.base)}
                              for preset in presets]
                try:
                    halo, crystal_entries, _halo_path = await _read_halo_presets(reachable, node_name)
                except _NodeFileUnreadable as unavailable:
                    crystal_notice = unavailable.sentence
                else:
                    # the halo control's options: ``off`` (no halo — DCS's own default) and ``auto``
                    # (DCS decides), then the file's own entries in file order. The entries are ALSO
                    # drawn as plate-style items, so a long name can carry its full text in a tooltip.
                    halo_choices = halo_row_choices(halo)
                    halo_entries = [{"name": preset.name, "label": preset.label}
                                    for preset in halo]
                    crystals = [{"name": preset.name, "label": preset.label,
                                 "url": halo_image_path(node_name, preset.name)}
                                for preset in crystal_entries]
        else:
            plate_notice = WIZARD_PLATES_FALLBACK
            crystal_notice = WIZARD_HALO_FALLBACK

    # THE STEP'S CONTROLS, GATED by the modes the values state: the cloud mode decides which cloud
    # values are offered, the dust-or-fog choice decides which of the two follows it, and the halo
    # preset decides whether the crystal grid is offered at all. A key a mode does not offer is NOT
    # drawn — it rides as a hidden field instead, so no step loses a value the URL held.
    rendered = step_controls(step, values, plates_readable=bool(plates), halo_readable=bool(halo_choices))
    rows = wizard_rows(rendered, values)
    carry_all = [{"name": key, "value": values.get(key, "")} for key in WIZARD_KEYS]
    for row in rows:
        key = row["key"]
        # the halo preset's options are the node's OWN list (its ``icehalo.lua``), so they come from
        # the read rather than the table; with no readable list the row is a free-text box.
        if key == "halo.preset":
            row["choices"] = halo_choices
            row["free_text"] = not halo_choices
            value = (row["value"] or "").strip()
            if value and value not in {choice["value"] for choice in halo_choices}:
                # the value the FILE carries is kept SELECTABLE even when the node's own list does not
                # name it: a select that cannot show it would post an empty string and drop the key.
                row["choices"] = [{"value": value, "label": value}] + halo_choices
            # THE ENTRIES AS PLATE-STYLE ITEMS, offered ONLY for a real preset: a native option cannot
            # carry a tooltip, and the long names are not shown for ``off`` or ``auto``.
            row["text_plates"] = halo_entries if is_real_halo(value) else []
        elif key == "clouds.preset":
            row["free_text"] = not plates
            row["plates"] = plates
            row["plates_note"] = WIZARD_PLATES_NOTE
            row["plates_notice"] = plate_notice
            # CLICKING A PICTURE IS THE CHOICE, and it is the DCS-preset mode the picture implies.
            row["mode_field"] = "clouds"
            row["mode_value"] = "preset"
        elif key == "halo.crystalsPreset":
            row["plates"] = crystals
            row["plates_note"] = WIZARD_CRYSTALS_NOTE
            row["plates_notice"] = crystal_notice
        if row["kind"] == weather_fields.KIND_DATE:
            # a picker holds a REAL date; a named day (today/yesterday/tomorrow) keeps the text box
            row["iso_date"] = not row["value"] or _is_iso_date(row["value"])
        elif row["kind"] == weather_fields.KIND_MOMENT:
            row["time_value"] = _time_of(row["value"])
        if key in MODE_KEYS:
            # a mode control re-renders the step on change (its own script submits the GET form).
            row["mode"] = True
        if row["kind"] in (weather_fields.KIND_PICKER, weather_fields.KIND_DATE,
                           weather_fields.KIND_MOMENT) or row.get("text_plates"):
            # every other value the render holds rides in a row's LINK, so switching a picture, a date
            # shortcut, a moment or a halo entry never drops what the URL already knew.
            row["carry"] = [entry for entry in carry_all if entry["name"] != key]
    rows = _pair_wind_rows(rows)
    hidden = [{"name": key, "value": values.get(key, "")}
              for key in WIZARD_KEYS if key not in rendered]
    if shape == SHAPE_COMBINATION:
        # the preset NAME control lives in the combination editor (so a NEW combination can be named
        # too); leaving it out of the weather hidden fields keeps exactly ONE ``name`` input per form.
        hidden = [entry for entry in hidden if entry["name"] != "name"]

    current_index = WIZARD_STEP_VALUES.index(step)
    step_number = current_index + 1
    next_step = WIZARD_STEP_VALUES[current_index + 1] if current_index + 1 < len(WIZARD_STEP_VALUES) \
        else ""
    prev_step = WIZARD_STEP_VALUES[current_index - 1] if current_index else ""
    next_params = {key: value for key, value in query.items() if key != "step"}
    if next_step:
        next_params["step"] = next_step
    prev_params = {key: value for key, value in query.items() if key != "step"}
    if prev_step:
        prev_params["step"] = prev_step

    return {
        "title": WIZARD_NEW_TITLE if not name else WIZARD_TITLE,
        "page_title": (WIZARD_NEW_TITLE if not name else name),
        "lead": WIZARD_LEAD,
        "action": base,
        "base": base,
        "step": step,
        "steps": steps,
        "step_number": step_number,
        "step_total": len(WIZARD_STEPS),
        "next_url": f"{base}?{urlencode(next_params)}" if next_step else "",
        "prev_url": f"{base}?{urlencode(prev_params)}" if prev_step else "",
        "rows": rows,
        "hidden": hidden,
        "values": values,
        "name_label": weather_fields.field("name").label,
        "hints": FIELD_HINTS,
        "moments": weather_fields.moments(),
        "plates": plates,
        "plate_notice": plate_notice,
        "crystals": crystals,
        "crystal_notice": crystal_notice,
        # THE FOG TABLE, as data: one row per entry with a cell per column, and the two controls that
        # add and remove a row. The columns carry the table's own min/max/step and unit.
        "fog_table": table,
        "fog_columns": [{"key": column.key, "label": column.label, "unit": column.unit,
                         "min": column.min, "max": column.max, "step": column.step,
                         "hint": weather_fields.FOG_HINTS.get(column.key, "")}
                        for column in weather_fields.FOG_COLUMNS],
        "fog_add_field": FOG_ADD_FIELD,
        "fog_remove_field": FOG_REMOVE_FIELD,
        "fog_add_label": WIZARD_FOG_ADD,
        "fog_start_label": KEYFRAME_START_LABEL,
        "fog_remove_label": WIZARD_COMBO_REMOVE,
        "sky": sky,
        "shape": shape,
        "shape_field": SHAPE_FIELD,
        "shape_choices": [{"value": value, "label": label} for value, label in SHAPE_CHOICES],
        "destination": destination,
        # THE REVIEW STEP'S SAVE FORM: a POST to the ONE write route, carrying the file revision it was
        # rendered from, the name the wizard OPENED, the destination file and the session's CSRF token.
        "save_path": PRESETS_SAVE_PATH,
        "form_method": "post" if step == WIZARD_STEP_REVIEW else "get",
        "form_action": PRESETS_SAVE_PATH if step == WIZARD_STEP_REVIEW else base,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "revision_field": PRESETS_REVISION_FIELD,
        "revision": revision,
        "original_field": PRESETS_ORIGINAL_FIELD,
        "original": name,
        "file_field": PRESETS_FILE_FIELD,
        "chosen_file": chosen_file,
        # THE NEW-FILE CARRIERS: the revealed text input's own field and the name it holds, whether
        # the destination is a file the save will CREATE, and the refusal the typed name earned (if
        # any) rendered as its own sentence on the step. The review step reads the same two values, so
        # the file it NAMES is the file the save validates again on the POST.
        "new_file_field": PRESETS_NEW_FILE_FIELD,
        "new_file_value": new_file_value,
        "new_file_error": new_file_error,
        "chosen_is_new": chosen_is_new,
        "unread_sentence": unread_sentence,
        # THE EXPERT MODE'S TWO CARRIERS beside the YAML: the hidden companion its toggle drives and
        # the text carrier the editor posts. Both ride the review step's own form.
        "expert_field": PRESETS_EXPERT_FIELD,
        "yaml_field": PRESETS_YAML_FIELD,
        "expert_label": WIZARD_EXPERT_LABEL,
        "expert_note": WIZARD_EXPERT_NOTE,
        # THE EDITOR'S OWN CHECK: the panel carries this URL, and the shared script POSTs to it.
        "validate_url": wizard_validate_url(name),
        "can_save": bool(chosen_file),
        "action_field": PRESETS_ACTION_FIELD,
        "action_save": PRESETS_ACTION_SAVE,
        "yaml": produced_yaml(values.get("name", name), values, shape=shape,
                              combination=combination, existing=document)
        if step == WIZARD_STEP_REVIEW else "",
        "writes": writes,
        **combo,
    }


def _nodes_document(config: Path) -> Any:
    """The ``nodes.yaml`` document of *config*, or ``None`` when it does not exist or does not parse.

    The SAME primitive every config read uses. The document is what says which preset files the
    extension reads (``MizEdit.presets``) and which presets are in use (``MizEdit.settings``).
    """
    return _config_document(Path(config) / "nodes.yaml")


def _scheduler_document(config: Path) -> Any:
    """The ``<config>/plugins/scheduler.yaml`` document, or ``None`` when it is absent or unreadable.

    An install without the Scheduler plugin has no such file, and a broken one must never stop a
    preset save: the guard only READS this document, so a missing, unparsable or unreadable file
    contributes no reference at all.
    """
    return _config_document(Path(config) / SCHEDULER_CONFIG_FILE)


def _default_node_name(request: Request) -> str:
    """The node the wizard reads cloud presets from when the URL names none: the master, else the first."""
    entries = getattr(dashboard_page.request_source(request), "nodes", None)
    if not isinstance(entries, dict):
        return ""
    for name, node in entries.items():
        if getattr(node, "master", False):
            return str(name)
    return next((str(name) for name in entries), "")


# ------------------------------------------------------------------------- the page

def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers.

    Every node read carries the PAGE's capability (``presets.view``) with NO scope grant: a preset and
    the vendor lists behind it are installation configuration, so a manager's server scope must not
    reach them — the same rule the page states for the preset files themselves.
    """
    declare()
    return {PRESETS_PATH: PRESETS_CAPABILITY,
            PRESETS_NEW_PATH: PRESETS_CAPABILITY,
            PRESETS_WIZARD_PATH: PRESETS_CAPABILITY,
            # the editor's check is a READ — it validates posted text and writes nothing — so it rides
            # the PAGE's capability, not the write capability. The save stays the ONE write route.
            PRESETS_WIZARD_VALIDATE_PATH: PRESETS_CAPABILITY,
            PRESETS_NEW_VALIDATE_PATH: PRESETS_CAPABILITY,
            # the one-block editor: the page and its check are reads of one preset; the save is the
            # area's write capability, the same one every other preset write requires.
            PRESETS_YAML_PATH: PRESETS_CAPABILITY,
            PRESETS_YAML_VALIDATE_PATH: PRESETS_CAPABILITY,
            PRESETS_YAML_SAVE_PATH: PRESETS_WRITE_CAPABILITY,
            PRESETS_CLOUDS_LIST_PATH: PRESETS_CAPABILITY,
            PRESETS_CLOUD_IMAGE_PATH: PRESETS_CAPABILITY,
            PRESETS_HALO_LIST_PATH: PRESETS_CAPABILITY,
            PRESETS_HALO_IMAGE_PATH: PRESETS_CAPABILITY,
            PRESETS_SAVE_PATH: PRESETS_WRITE_CAPABILITY}


def nav_items():
    """The sidebar entry for this page — the capability's own name, never a second spelling."""
    declare()
    return (NavItem(label=PRESETS_TITLE, url=PRESETS_PATH, capability=PRESETS_CAPABILITY),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Presets route to the shell's own router and return it."""
    declare()

    @router.get(PRESETS_PATH, response_class=HTMLResponse, name="page-presets")
    async def _presets_page(request: Request, kind: str = "", q: str = "", file: str | None = None,
                            remove: str = ""):
        # no-store: the preset list is a config READ that a later save invalidates, so the browser
        # must not serve the pre-save page from its cache (the same reason every console page sets it).
        registrar = getattr(request.app.state, "webui_registrar", None)
        config = settings_page.config_dir(request)
        shell = dict(
            title=PRESETS_TITLE,
            page_title=PRESETS_TITLE,
            lead=PRESETS_LEAD,
            crumb=dashboard_page.crumb(CRUMB_GROUP, PRESETS_TITLE),
            notice=actions_page.pop_notice(request),
            nav_groups=dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                 current=PRESETS_PATH,
                                                 manager=permissions.manages_console(request)),
            user=dashboard_page.identity_summary(request),
            style=PRESETS_STYLE,
            path=PRESETS_PATH,
        )

        # THE ONE REMOVAL DOOR: a GET that SHOWS a preset and every reference to it. Nothing is
        # written here — the form it draws posts to the existing write route.
        if remove.strip():
            removal = preset_removal(config, remove.strip())
            if removal is None:
                raise HTTPException(status_code=404,
                                    detail=WIZARD_PRESET_MISSING.format(name=remove.strip()))
            html = i18n.render(
                request, PRESETS_TEMPLATE, **shell,
                removal=removal,
                removal_head=PRESETS_REMOVAL_HEAD,
                removal_sentence=PRESETS_REMOVAL_SENTENCE,
                removal_note=PRESETS_REMOVAL_NOTE,
                removal_lead=i18n.translate(request, PRESETS_REMOVE_LEAD,
                                            name=removal["name"], file=removal["file"]),
                removal_document_label=PRESETS_REMOVE_DOCUMENT,
                removal_in_use=in_use_sentence(request, removal["name"]),
                remove_label=PRESETS_REMOVE_LABEL,
                cancel_label=WIZARD_CANCEL,
                save_path=PRESETS_SAVE_PATH,
                csrf_field=session.CSRF_FIELD,
                csrf_token=session.get_csrf_token(request),
                action_field=PRESETS_ACTION_FIELD,
                action_remove=PRESETS_ACTION_REMOVE,
                original_field=PRESETS_ORIGINAL_FIELD,
                revision_field=PRESETS_REVISION_FIELD,
            )
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})

        cards, unreadable, found = read_presets(config)
        records = read_preset_files(config)
        file_labels = [record["label"] for record in records]
        query = (q or "").strip()
        selected = kind if kind in {value for value, _label in FILTER_CHOICES} else FILTER_ALL
        # THE FILE TAB. No ``file`` at all is the FIRST tab (``presets.yaml``); an empty ``file`` is
        # the "All files" tab, which needs a URL of its own to be linkable; a label the install no
        # longer carries falls back to "All files" and says so, rather than rendering an empty page
        # for a bookmark that outlived its file.
        wanted = (file or "").strip()
        if file is None:
            active = file_labels[0] if file_labels else ""
            file_gone = ""
        elif wanted in file_labels:
            active = wanted
            file_gone = ""
        else:
            active = ""
            file_gone = i18n.translate(request, PRESETS_FILE_GONE, file=wanted) if wanted else ""
        matched = [card for card in cards if _matches(card, query)]
        in_file = [card for card in matched if not active or card["file"] == active]
        counts = {value: len([card for card in in_file
                              if value == FILTER_ALL or card["kind"] == value])
                  for value, _label in FILTER_CHOICES}
        shown = [card for card in in_file if selected == FILTER_ALL or card["kind"] == selected]
        # THE TRASH IS NOT DRAWN FOR A PRESET IN USE: the confirm page could only refuse it, so the
        # tile says WHY instead — the same sentence and the same reference labels that confirm page
        # prints. The references of EVERY tile come from ONE walk of the three documents (the nodes,
        # the parsed preset files and the Scheduler), never one walk per tile. The route's own guard
        # stays the backstop.
        nodes_document = _nodes_document(config)
        scheduler_document = _scheduler_document(config)
        documents = [record["answer"].document for record in records if record["answer"].parsed]
        references = preset_reference_map(nodes_document, documents, scheduler_document)
        shown = [removal_card(card, references.get(card["name"], []), request) for card in shown]
        # THE EXPERT-DSL PRESET'S ONE DOOR: the wizard refuses a ``modify`` preset, so its tile opens
        # THIS page's own one-block editor — the URL this module declares (``preset_yaml_url``), never
        # a hand-built string, so the cog and the page cannot drift. The URL names the PRESET, not the
        # file: two presets of one file are two distinct pages. A cog is drawn only for a caller the
        # editor admits (``presets.view`` is the page's own capability, so the links cannot answer 403
        # for a caller who can read this page).
        can_edit = permissions.allows(PRESETS_CAPABILITY, permissions.role_names_for(request))
        for card in shown:
            # EVERY preset carries the YAML door; the expert DSL has NO wizard cog, so its door is
            # the only editor the tile draws. The wizard's own kinds keep the cog beside the door.
            card["yaml_url"] = preset_yaml_url(card["name"]) if can_edit else ""
        # THE TABS' COUNTS come from the SAME read: a file's count is the number of its presets this
        # search matches, and "All files" is every match.
        tabs = [{"label": record["label"],
                 "count": len([card for card in matched if card["file"] == record["label"]]),
                 "url": preset_tab_url(record["label"], kind=selected, query=query),
                 "active": record["label"] == active}
                for record in records]
        tabs.append({"label": "", "all_files": True, "count": len(matched),
                     "url": preset_tab_url("", kind=selected, query=query),
                     "active": not active})
        # A FILE TAB states its own file's refusal; "All files" states every one of them.
        if active:
            notices = [i18n.translate(request, record["notice"]["msgid"], **record["notice"]["params"])
                       for record in records if record["label"] == active and record["notice"]]
        else:
            notices = [i18n.translate(request, record["msgid"], **record["params"])
                       for record in unreadable]
        html = i18n.render(
            request, PRESETS_TEMPLATE, **shell,
            cards=shown,
            total=len(in_file),
            found=found,
            removal=None,
            notices=notices,
            file_gone=file_gone,
            tabs=tabs,
            show_tabs=len(records) > 1,
            all_files_label=PRESETS_ALL_FILES,
            active_file=active,
            filters=[{"value": value, "label": label, "count": counts[value],
                      "url": _filter_url(value, query, active), "active": value == selected}
                     for value, label in FILTER_CHOICES],
            query=query,
            selected=selected,
            filter_label=PRESETS_FILTER_LABEL,
            filter_placeholder=PRESETS_FILTER_PLACEHOLDER,
            keys_label=PRESETS_KEYS_LABEL,
            preset_label=PRESETS_KIND_LABEL,
            cog_label=settings_page.COG_LABEL,
            remove_label=PRESETS_REMOVE_LABEL,
            new_url=new_preset_url(active),
            new_label=PRESETS_NEW_LABEL,
            modify_note=PRESETS_MODIFY_NOTE,
            yaml_label=PRESETS_YAML_DOOR_LABEL,
            no_file=PRESETS_NO_FILE,
            no_match=PRESETS_NO_MATCH,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.get(PRESETS_CLOUDS_LIST_PATH, name="presets-clouds-list")
    async def _presets_clouds_list(request: Request, node: str) -> Response:
        # A READ of the node's vendor file: it changes nothing, so it is a GET. A node the caller
        # cannot reach is refused with the same sentence as an unknown one, and a node whose list
        # carries nothing is NOT a refusal — the wizard falls back to free text, so the answer is an
        # empty list WITH the sentence that says so (HTTP 200), never an error to render.
        reachable = _reachable_node(request, node)
        if reachable is None:
            return _refuse(404, PRESETS_NODE_OFFLINE.format(node=node))
        try:
            presets, _path = await _read_cloud_presets(reachable, node)
        except _NodeFileUnreadable as unavailable:
            if unavailable.status_code == 404:
                return JSONResponse({"presets": [], "notice": unavailable.sentence})
            return _refuse(unavailable.status_code, unavailable.sentence)
        return JSONResponse({"presets": [preset.item() for preset in presets]})

    @router.get(PRESETS_CLOUD_IMAGE_PATH, name="presets-cloud-image")
    async def _presets_cloud_image(request: Request, node: str, name: str) -> Response:
        # THE PLATE. The URL carries a preset NAME and a NODE and never a path: the name is checked
        # as a name BEFORE anything is asked of the node, then looked up in that node's own parsed
        # list, and only the entry's own thumbnail is ever resolved. That is what keeps this route
        # from being a general file reader.
        reachable = _reachable_node(request, node)
        if reachable is None:
            return _refuse(404, PRESETS_NODE_OFFLINE.format(node=node))
        if not _usable_preset_name(name):
            return _refuse(400, PRESETS_CLOUD_NAME_UNUSABLE.format(name=name))
        try:
            presets, _path = await _read_cloud_presets(reachable, node)
        except _NodeFileUnreadable as unavailable:
            return _refuse(unavailable.status_code, unavailable.sentence)
        preset = _named_entry(presets, name)
        if preset is None:
            return _refuse(400, PRESETS_CLOUD_UNKNOWN.format(node=node, name=name))
        if not preset.thumbnail:
            return _refuse(404, PRESETS_CLOUD_NO_PICTURE.format(node=node, name=name))
        path = dcs_install.preset_image_file(reachable, preset.thumbnail)
        if path is None:
            return _refuse(400, PRESETS_CLOUD_PICTURE_UNUSABLE.format(name=name,
                                                                     thumbnail=preset.thumbnail))
        try:
            data = await reachable.read_file(path)
        except FileNotFoundError:
            return _refuse(404, PRESETS_PICTURE_MISSING.format(thumbnail=preset.thumbnail, path=path))
        except PermissionError:
            return _refuse(403, PRESETS_NODE_PERMISSION.format(node=node, path=path))
        except (TimeoutError, asyncio.TimeoutError):
            return _refuse(504, PRESETS_NODE_TIMEOUT.format(node=node))
        except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
            log.warning("Cloud preset plate: node '%s' raised %s reading '%s'", node,
                        type(ex).__name__, path, exc_info=True)
            return _refuse(502, PRESETS_NODE_RAISED.format(node=node, path=path,
                                                           error=type(ex).__name__))
        if not isinstance(data, (bytes, bytearray)):
            # ``read_file`` is ``bytes | int`` and an ``int`` is a FAILURE CODE, never content.
            return _refuse(502, PRESETS_NODE_FAILURE_CODE.format(node=node, path=path))
        payload = bytes(data)
        if len(payload) > dcs_install.PRESET_IMAGE_MAX_BYTES:
            return _refuse(413, PRESETS_PICTURE_OVER_CAP.format(
                thumbnail=preset.thumbnail, size=len(payload),
                limit=dcs_install.PRESET_IMAGE_MAX_BYTES, path=path))
        return Response(content=payload, media_type=dcs_install.PRESET_IMAGE_MEDIA_TYPE,
                        headers={"Cache-Control": "private, max-age=300"})

    @router.get(PRESETS_HALO_LIST_PATH, name="presets-halo-list")
    async def _presets_halo_list(request: Request, node: str) -> Response:
        # A READ of the node's vendor file: it changes nothing, so it is a GET. The answer carries
        # BOTH entry tables — the halo presets, with the caller-side "auto" choice prepended, and the
        # crystal presets, the only ones with pictures. A node whose list carries nothing VISIBLE is
        # NOT a refusal: the answer is empty lists WITH the sentence that says so (HTTP 200).
        reachable = _reachable_node(request, node)
        if reachable is None:
            return _refuse(404, PRESETS_NODE_OFFLINE.format(node=node))
        try:
            halo, crystals, _path = await _read_halo_presets(reachable, node)
        except _NodeFileUnreadable as unavailable:
            if unavailable.status_code == 404:
                return JSONResponse({"presets": [], "crystalsPresets": [],
                                     "notice": unavailable.sentence})
            return _refuse(unavailable.status_code, unavailable.sentence)
        return JSONResponse({
            "presets": [dcs_halo.auto_preset().item()] + [preset.item() for preset in halo],
            "crystalsPresets": [preset.item() for preset in crystals],
        })

    @router.get(PRESETS_HALO_IMAGE_PATH, name="presets-halo-image")
    async def _presets_halo_image(request: Request, node: str, name: str) -> Response:
        # THE CRYSTAL PLATE. The URL carries a preset NAME and a NODE and never a path: the name is
        # checked as a name BEFORE anything is asked of the node, then looked up in that node's own
        # parsed lists, and only the entry's own thumbnail is ever resolved. The path comes VERBATIM
        # out of ``thumbnailName`` — the stored names carry a space and a casing that does not always
        # match the key. A halo preset carries no thumbnail, so only a crystal entry has a plate.
        reachable = _reachable_node(request, node)
        if reachable is None:
            return _refuse(404, PRESETS_NODE_OFFLINE.format(node=node))
        if not _usable_preset_name(name):
            return _refuse(400, PRESETS_HALO_NAME_UNUSABLE.format(name=name))
        try:
            halo, crystals, _path = await _read_halo_presets(reachable, node)
        except _NodeFileUnreadable as unavailable:
            return _refuse(unavailable.status_code, unavailable.sentence)
        preset = _named_entry(crystals + halo, name)
        if preset is None:
            return _refuse(400, PRESETS_HALO_UNKNOWN.format(node=node, name=name))
        if not preset.thumbnail:
            return _refuse(404, PRESETS_HALO_NO_PICTURE.format(node=node, name=name))
        path = dcs_install.preset_image_file(reachable, preset.thumbnail)
        if path is None:
            return _refuse(400, PRESETS_HALO_PICTURE_UNUSABLE.format(name=name,
                                                                     thumbnail=preset.thumbnail))
        try:
            data = await reachable.read_file(path)
        except FileNotFoundError:
            return _refuse(404, PRESETS_PICTURE_MISSING.format(thumbnail=preset.thumbnail, path=path))
        except PermissionError:
            return _refuse(403, PRESETS_NODE_PERMISSION.format(node=node, path=path))
        except (TimeoutError, asyncio.TimeoutError):
            return _refuse(504, PRESETS_NODE_TIMEOUT.format(node=node))
        except Exception as ex:  # noqa: BLE001 - every transport failure is an honest sentence
            log.warning("Halo preset plate: node '%s' raised %s reading '%s'", node,
                        type(ex).__name__, path, exc_info=True)
            return _refuse(502, PRESETS_NODE_RAISED.format(node=node, path=path,
                                                           error=type(ex).__name__))
        if not isinstance(data, (bytes, bytearray)):
            # ``read_file`` is ``bytes | int`` and an ``int`` is a FAILURE CODE, never content.
            return _refuse(502, PRESETS_NODE_FAILURE_CODE.format(node=node, path=path))
        payload = bytes(data)
        if len(payload) > dcs_install.PRESET_IMAGE_MAX_BYTES:
            return _refuse(413, PRESETS_PICTURE_OVER_CAP.format(
                thumbnail=preset.thumbnail, size=len(payload),
                limit=dcs_install.PRESET_IMAGE_MAX_BYTES, path=path))
        return Response(content=payload, media_type=dcs_install.PRESET_IMAGE_MEDIA_TYPE,
                        headers={"Cache-Control": "private, max-age=300"})

    async def _render_wizard(request: Request, *, name: str, step: str, document: Any,
                             file_label: str, destination: dict) -> HTMLResponse:
        """Render ONE wizard step for *name* (empty = a new preset). A READ: nothing is written."""
        chosen = step if step in WIZARD_STEP_VALUES else WIZARD_STEP_VALUES[0]
        registrar = getattr(request.app.state, "webui_registrar", None)
        context = await build_wizard_context(request, settings_page.config_dir(request),
                                             name=name, step=chosen, document=document,
                                             file_label=file_label, destination=destination)
        html = i18n.render(
            request, PRESETS_WIZARD_TEMPLATE,
            crumb=dashboard_page.crumb(CRUMB_GROUP, PRESETS_TITLE,
                                       WIZARD_NEW_TITLE if not name else name),
            notice=actions_page.pop_notice(request),
            nav_groups=dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                 current=PRESETS_PATH,
                                                 manager=permissions.manages_console(request)),
            user=dashboard_page.identity_summary(request),
            style=PRESETS_STYLE,
            path=PRESETS_PATH,
            rail_head=WIZARD_RAIL_HEAD,
            back_label=WIZARD_BACK,
            next_label=WIZARD_NEXT,
            cancel_label=WIZARD_CANCEL,
            save_label=WIZARD_SAVE,
            write_note=WIZARD_WRITE_NOTE,
            sky_head=WIZARD_SKY_HEAD,
            sky_note=WIZARD_SKY_NOTE,
            plates_head=WIZARD_PLATES_HEAD,
            plates_note=WIZARD_PLATES_NOTE,
            crystals_note=WIZARD_CRYSTALS_NOTE,
            moments_note=WIZARD_MOMENTS_NOTE,
            dest_head=WIZARD_DEST_HEAD,
            dest_one=WIZARD_DEST_ONE,
            dest_many=WIZARD_DEST_MANY,
            dest_none=WIZARD_DEST_NONE,
            dest_select=WIZARD_DEST_SELECT,
            dest_named=WIZARD_DEST_NAMED,
            dest_not_yet=WIZARD_DEST_NOT_YET,
            dest_new_choice=WIZARD_DEST_NEW_CHOICE,
            dest_new_label=WIZARD_DEST_NEW_LABEL,
            dest_new_help=WIZARD_DEST_NEW_HELP,
            dest_new_one=WIZARD_DEST_NEW_ONE,
            review_writes=WIZARD_REVIEW_WRITES,
            review_yaml=WIZARD_REVIEW_YAML,
            review_yaml_note=WIZARD_REVIEW_YAML_NOTE,
            review_restart=WIZARD_REVIEW_RESTART,
            no_sky=WIZARD_NO_SKY,
            shape_head=WIZARD_SHAPE_HEAD,
            shape_note=WIZARD_SHAPE_NOTE,
            combo_head=WIZARD_COMBO_HEAD,
            combo_note=WIZARD_COMBO_NOTE,
            combo_empty=WIZARD_COMBO_EMPTY,
            combo_add=WIZARD_COMBO_ADD,
            combo_order=WIZARD_COMBO_ORDER,
            combo_move_up=WIZARD_COMBO_MOVE_UP,
            combo_move_down=WIZARD_COMBO_MOVE_DOWN,
            combo_remove=WIZARD_COMBO_REMOVE,
            combo_unknown=WIZARD_COMBO_UNKNOWN,
            combo_unknown_short=WIZARD_COMBO_UNKNOWN_SHORT,
            effective_head=WIZARD_EFFECTIVE_HEAD,
            effective_note=WIZARD_EFFECTIVE_NOTE,
            effective_empty=WIZARD_EFFECTIVE_EMPTY,
            step_of=WIZARD_STEP_OF,
            **context,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.get(PRESETS_NEW_PATH, response_class=HTMLResponse, name="page-presets-new")
    async def _presets_new(request: Request, step: str = ""):
        # A NEW preset: the SAME wizard, EMPTY, step 1 first (which carries the preset-name field).
        # The destination rule is stated for one file and offered for several; with none the wizard
        # says there is nowhere to write yet. The offer is the files the nodes' MizEdit.presets names
        # — the extension reads no others.
        config = settings_page.config_dir(request)
        destination = preset_destinations(config, _nodes_document(config))
        return await _render_wizard(request, name="", step=step, document=None, file_label="",
                                    destination=destination)

    @router.get(PRESETS_WIZARD_PATH, response_class=HTMLResponse, name="page-presets-wizard")
    async def _presets_wizard(request: Request, name: str, step: str = ""):
        # The wizard opens a preset's OWN values. The expert DSL is NOT openable here (S1 offers it
        # no link), so a request for one is refused the same way an unknown name is.
        found = read_preset(settings_page.config_dir(request), name)
        if found is None:
            raise HTTPException(status_code=404, detail=WIZARD_PRESET_MISSING.format(name=name))
        document, file_label = found
        if preset_kind(document) == KIND_MODIFY:
            raise HTTPException(status_code=404, detail=WIZARD_PRESET_EXPERT.format(name=name))
        destination = preset_destination_one(file_label)
        return await _render_wizard(request, name=name, step=step, document=document,
                                    file_label=file_label, destination=destination)

    @router.get(PRESETS_YAML_PATH, response_class=HTMLResponse, name="page-presets-yaml")
    async def _presets_yaml(request: Request, name: str):
        # THE ONE-BLOCK EDITOR — the preset's name, the file it lives in, ONE YAML editor holding that
        # preset's own block, a Save and a Cancel. No file tabs, no schema form, no read-only panel and
        # no Expert Mode toggle: this page edits one preset, so none of a whole-file editor's parts
        # belongs on it. A name no file carries is a 404, the same answer the wizard gives.
        config = settings_page.config_dir(request)
        block = preset_block_document(config, name)
        if block is None:
            raise HTTPException(status_code=404, detail=WIZARD_PRESET_MISSING.format(name=name))
        registrar = getattr(request.app.state, "webui_registrar", None)
        shell = dict(
            title=PRESETS_YAML_TITLE,
            page_title=PRESETS_YAML_TITLE,
            lead=PRESETS_YAML_LEAD,
            crumb=dashboard_page.crumb(CRUMB_GROUP, PRESETS_TITLE, PRESETS_YAML_TITLE),
            notice=actions_page.pop_notice(request),
            nav_groups=dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                 current=PRESETS_PATH,
                                                 manager=permissions.manages_console(request)),
            user=dashboard_page.identity_summary(request),
            style=PRESETS_STYLE,
            path=PRESETS_PATH,
        )
        html = i18n.render(
            request, PRESETS_YAML_TEMPLATE, **shell,
            preset=block,
            save_path=preset_yaml_save_url(block["name"]),
            validate_url=preset_yaml_validate_url(block["name"]),
            back_url=PRESETS_PATH,
            csrf_field=session.CSRF_FIELD,
            csrf_token=session.get_csrf_token(request),
            revision_field=PRESETS_REVISION_FIELD,
            yaml_field=PRESETS_YAML_FIELD,
            editor_label=PRESETS_YAML_EDITOR_LABEL,
            hint=PRESETS_YAML_HINT,
            convert_hint=(PRESETS_YAML_CONVERT_HINT if block["kind"] != KIND_MODIFY else ""),
            save_label=WIZARD_SAVE,
            cancel_label=WIZARD_CANCEL,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.post(PRESETS_YAML_VALIDATE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def _presets_yaml_validate(request: Request, name: str):
        """The one-block editor's live check — a JSON verdict, nothing written."""
        config = settings_page.config_dir(request)
        block = preset_block_document(config, name)
        if block is None:
            raise HTTPException(status_code=404, detail=WIZARD_PRESET_MISSING.format(name=name))
        form = await request.form()
        text = _text(form.get(PRESETS_YAML_FIELD))
        path = preset_path(config, block["file"])
        return JSONResponse(validate_preset_block(
            request, block["name"], text, path or Path(config), preset_schema_files()))

    @router.post(PRESETS_YAML_SAVE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def _presets_yaml_save(request: Request, name: str):
        """THE ONE WRITE of this page: replace THIS preset's entry and nothing else.

        Guard order matches every other config write: read → structural check → validate the merged
        document against the schema pair → ``patch_config_file`` with the revision the page rendered.
        Every way it can fail is ONE plain sentence with nothing written; the name a preset carries is
        not changed here, so a document keyed otherwise is refused rather than written.
        """
        config = settings_page.config_dir(request)
        schemas = preset_schema_files()
        form = await request.form()
        text = _text(form.get(PRESETS_YAML_FIELD))
        revision = _text(form.get(PRESETS_REVISION_FIELD))

        found = find_preset_file(config, name)
        if found is None:
            return _refuse_write(request, PRESETS_REFUSED_UNKNOWN, PRESETS_PATH, name=name)
        path, _label, key = found
        redirect = preset_yaml_url(key)

        try:
            posted = configwrite.load_config_text(text, path=path)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, PRESETS_REFUSED_EXPERT_YAML, redirect,
                                 detail=" ".join(str(ex).split()))
        refusal = preset_block_shape(key, posted)
        if refusal is not None:
            msgid, params = refusal
            return _refuse_write(request, msgid, redirect, **params)
        value = posted[key]

        # A form that carries no revision cannot be checked against the file it was rendered from.
        if not revision and path.is_file():
            return _refuse_write(request, PRESETS_REFUSED_NO_REVISION, redirect)

        candidate = configwrite.load_config_file(path)
        if candidate is None:
            from ruamel.yaml.comments import CommentedMap
            candidate = CommentedMap()
        candidate[key] = value
        try:
            configwrite.validate_against_schema(configwrite.dump_config(candidate), schemas, path=path)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        def mutate(document) -> None:
            document[key] = value

        try:
            result = configwrite.patch_config_file(path, mutate, schemas=schemas,
                                                   expect_revision=revision)
        except configwrite.StaleConfigRevision:
            return _refuse_write(request, PRESETS_STALE, redirect)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        if result.changed:
            message = (settings_page.SAVED_NOW if result.applies == configwrite.APPLIES_NOW
                       else settings_page.SAVED_RESTART)
        else:
            message = settings_page.SAVED_NO_CHANGE
        # the name this save produced must be in the launch dialog's pick on its next open
        forget_node_lists(PRESET_LIST)
        # and every remembered parse of these files is one generation old: the next render re-reads
        forget_preset_documents()
        _presets_notice(request, message, ok=True, applies=result.applies)
        return RedirectResponse(redirect, status_code=303)

    def _presets_notice(request: Request, message: str, *, ok: bool, applies: str = "") -> None:
        """Store the one-shot notice of this write — the console's OWN wording, translated at render."""
        request.session[actions_page.NOTICE_KEY] = {
            "message": message, "ok": ok, "audit": "", "audit_warning": False,
            "action": PRESETS_TITLE, "revert": "", "revert_omitted": "",
            "refusal": True, "applies": applies,
        }

    def _refuse_write(request: Request, msgid: str, redirect_to: str, **params) -> RedirectResponse:
        """Leave a refusal and go back — NOTHING is written. *msgid* is rendered in the request's language."""
        _presets_notice(request, i18n.translate(request, msgid, **params), ok=False)
        return RedirectResponse(redirect_to, status_code=303)

    def _new_preset_path(request: Request, config: Path, form, destination: dict
                         ) -> tuple[Path, str] | RedirectResponse:
        """Where a NEW preset is written — the ONE home for its destination, for both write doors.

        A posted ``new_file`` name is validated by :func:`preset_new_file` (a caller-supplied path's
        ONLY source of truth) and names a file the SAME ``patch_config_file`` call creates. Otherwise
        the posted ``file`` must be one :func:`preset_destinations` offers — the files the nodes'
        ``MizEdit.presets`` names, or discovery when no node names one. Answers ``(path, label)``, or
        a ``RedirectResponse`` carrying the refusal with nothing written.
        """
        posted_new = _text(form.get(PRESETS_NEW_FILE_FIELD)).strip()
        if posted_new:
            path, refusal, params = preset_new_file(config, posted_new)
            if path is None:
                if refusal == PRESETS_REFUSED_NEW_FILE_EXISTS:
                    # an existing file is SELECTED, never overwritten: the step re-opens on that file
                    select = urlencode({PRESETS_FILE_FIELD: posted_new})
                    return _refuse_write(request, refusal, f"{PRESETS_NEW_PATH}?{select}", **params)
                return _refuse_write(request, refusal, PRESETS_NEW_PATH, **params)
            return path, display_path(config, path)
        files = {entry: Path(config) / entry[len(CONFIG_LABEL) + 1:]
                 for entry in destination["files"] if entry.startswith(f"{CONFIG_LABEL}/")}
        posted_file = _text(form.get(PRESETS_FILE_FIELD))
        if posted_file not in files:
            if destination["named"]:
                return _refuse_write(request, PRESETS_REFUSED_UNNAMED_FILE, PRESETS_NEW_PATH,
                                     file=posted_file, who=destination["who"])
            return _refuse_write(request, PRESETS_REFUSED_NO_FILE, PRESETS_NEW_PATH,
                                 file=posted_file)
        return files[posted_file], posted_file

    def _text(value: Any) -> str:
        """A posted form value as text — ``""`` for an absent one, never ``"None"``."""
        return "" if value is None else str(value)

    @router.post(PRESETS_WIZARD_VALIDATE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def _presets_wizard_validate(request: Request, name: str):
        """The wizard editor's live check for the ONE open preset — a JSON verdict, nothing written."""
        config = settings_page.config_dir(request)
        form = await request.form()
        text = _text(form.get(PRESETS_YAML_FIELD))
        return JSONResponse(validate_preset_text(request, name, text,
                                                 _document_path(config, name),
                                                 preset_schema_files()))

    @router.post(PRESETS_NEW_VALIDATE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def _presets_new_validate(request: Request):
        """The wizard editor's check for a NEW preset — the document's own single key IS the name."""
        config = settings_page.config_dir(request)
        form = await request.form()
        text = _text(form.get(PRESETS_YAML_FIELD))
        return JSONResponse(validate_preset_text(request, "", text,
                                                 _document_path(config, ""),
                                                 preset_schema_files()))

    async def _save_expert(request: Request, config: Path, schemas: list[str],
                           form) -> RedirectResponse:
        """THE EXPERT DOOR of the ONE write: the review step's edited YAML for the ONE open preset.

        It writes the preset the wizard has OPEN, into the file that preset LIVES in, through the SAME
        primitive and the SAME revision guard as a plain save — and nothing else. The edited text is
        read by the tree's ONE reader (:func:`core.utils.configwrite.load_config_text`, the text entry
        point :func:`core.utils.configwrite.load_config_file` itself calls — never a second YAML
        reading), the merged candidate is validated against the SAME schema pair the extension names,
        and only then does ``patch_config_file`` run.

        The document must carry EXACTLY the one preset that is open, keyed as the file spells it: the
        value is what this page edits, so the door can neither RENAME nor DELETE the preset, and it
        writes the file the preset already lives in — a posted ``file`` cannot move it. A new preset
        (nothing open) writes the file the operator CHOSE, validated the way the plain save validates
        it. Every failure — unreadable YAML, no preset, several presets, a rename, a collision, a
        schema error, a stale form — is ONE plain sentence with nothing written.
        """
        text = _text(form.get(PRESETS_YAML_FIELD))
        original = _text(form.get(PRESETS_ORIGINAL_FIELD)).strip()
        posted_name = _text(form.get("name")).strip()
        revision = _text(form.get(PRESETS_REVISION_FIELD))

        # WHERE THE WRITE GOES. An EXISTING preset writes the file its own name was FOUND in, so the
        # posted ``file`` is never consulted. A NEW one writes the file the operator chose — one a
        # node's ``MizEdit.presets`` names, the only files the extension reads.
        if original:
            found = find_preset_file(config, original)
            if found is None:
                return _refuse_write(request, PRESETS_REFUSED_UNKNOWN, wizard_base(original),
                                     name=original)
            path, _label, key = found
            redirect = f"{wizard_base(key)}?step={WIZARD_STEP_REVIEW}"
        else:
            if not posted_name:
                return _refuse_write(request, PRESETS_REFUSED_NO_NAME, PRESETS_NEW_PATH)
            key = ""
            destination = preset_destinations(config, _nodes_document(config))
            placed = _new_preset_path(request, config, form, destination)
            if isinstance(placed, RedirectResponse):
                return placed
            path, _label = placed
            redirect = f"{wizard_base(posted_name)}?step={WIZARD_STEP_REVIEW}"

        # THE EDITED DOCUMENT, read by the tree's ONE reader. Bad YAML is a sentence, never a traceback.
        try:
            posted = configwrite.load_config_text(text, path=path)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, PRESETS_REFUSED_EXPERT_YAML, redirect,
                                 detail=" ".join(str(ex).split()))
        name = key or posted_name
        if not isinstance(posted, Mapping) or not posted:
            return _refuse_write(request, PRESETS_REFUSED_EXPERT_EMPTY, redirect, name=name)
        if len(posted) != 1:
            return _refuse_write(
                request, PRESETS_REFUSED_EXPERT_SEVERAL, redirect, count=len(posted),
                keys=", ".join(f"'{entry}'" for entry in posted))
        doc_key = next(iter(posted))
        if key and str(doc_key) != key:
            return _refuse_write(request, PRESETS_REFUSED_EXPERT_RENAME, redirect,
                                 posted=str(doc_key), name=key)
        key = key or str(doc_key)
        value = posted[doc_key]
        if not original:
            # a NEW preset is named by its own document's key, so the notice returns to THAT preset.
            redirect = f"{wizard_base(key)}?step={WIZARD_STEP_REVIEW}"

        if not revision and path.is_file():
            return _refuse_write(request, PRESETS_REFUSED_NO_REVISION, redirect)

        from ruamel.yaml.comments import CommentedMap
        before = configwrite.load_config_file(path)
        if before is None:
            before = CommentedMap()
        # a NEW preset may not take a name the file already carries (the same guard the plain save runs).
        if not original:
            clash = _collision(before, key, "")
            if clash:
                return _refuse_write(request, PRESETS_REFUSED_DUPLICATE, redirect, name=clash)
        candidate = configwrite.load_config_file(path)
        if candidate is None:
            candidate = CommentedMap()
        candidate[key] = value
        # VALIDATE the merged candidate against the SAME schema pair the extension names.
        try:
            configwrite.validate_against_schema(configwrite.dump_config(candidate), schemas, path=path)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        # WRITE the ONE key through the ONE primitive — validated, atomic, revision-guarded.
        def mutate(document) -> None:
            document[key] = value

        try:
            result = configwrite.patch_config_file(path, mutate, schemas=schemas,
                                                   expect_revision=revision)
        except configwrite.StaleConfigRevision:
            return _refuse_write(request, PRESETS_STALE, redirect)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        if result.changed:
            message = (settings_page.SAVED_NOW if result.applies == configwrite.APPLIES_NOW
                       else settings_page.SAVED_RESTART)
        else:
            message = settings_page.SAVED_NO_CHANGE
        # the name this save produced must be in the launch dialog's pick on its next open
        forget_node_lists(PRESET_LIST)
        # and every remembered parse of these files is one generation old: the next render re-reads
        forget_preset_documents()
        _presets_notice(request, message, ok=True, applies=result.applies)
        return RedirectResponse(redirect, status_code=303)

    @router.post(PRESETS_SAVE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def _presets_save(request: Request):
        """THE ONE WRITE. Guard order: read → build (mutate in place) → validate → guard → write.

        The document is loaded through the ONE round-trip primitive and mutated IN PLACE — only the
        keys the wizard owns — so every field it does not render round-trips untouched. The candidate
        is validated against the SAME schema pair the extension names, then the removal/rename guard
        runs (a preset in use, a name collision, a file no node names), and only then the write goes
        through ``patch_config_file`` with the form's own revision, so a stale form is refused and
        writes nothing.

        The review step's EXPERT mode is answered by :func:`_save_expert` FIRST: that form posts a
        whole document for the preset instead of the weather controls, so none of the control values
        below is read for it. A REMOVE is not expert work — it deletes the preset by name and never
        reads the edited document — so it keeps the plain path below.
        """
        config = settings_page.config_dir(request)
        schemas = preset_schema_files()
        form = await request.form()
        if (_text(form.get(PRESETS_EXPERT_FIELD)) == "1"
                and _text(form.get(PRESETS_ACTION_FIELD)) != PRESETS_ACTION_REMOVE):
            return await _save_expert(request, config, schemas, form)
        values = {key: _text(form.get(key)) for key in WIZARD_KEYS}
        # THE FOG TABLE the form states. The review step posts the table's VALUE; a form that carries
        # the table's INDEXED cells instead (a direct POST) is read the same way, so both spellings
        # reach one row list.
        posted_fog = fog_rows_from_form(form)
        if posted_fog:
            values[weather_fields.FOG_ROWS_KEY] = serialize_fog_rows(posted_fog)
        fog_rows_stated = bool(posted_fog) or weather_fields.FOG_ROWS_KEY in form
        # A fog row is keyed by SECONDS, so a time that is not a whole number cannot become one: it is
        # refused in plain words rather than silently dropped (which would lose the row).
        for time, _thickness, _visibility in parse_fog_rows(values.get(weather_fields.FOG_ROWS_KEY, "")):
            if not time.isdigit():
                return _refuse_write(
                    request, PRESETS_REFUSED_FOG_TIME,
                    f"{wizard_base(_text(form.get(PRESETS_ORIGINAL_FIELD)).strip())}"
                    f"?step={WIZARD_STEP_WIND}")
        original = _text(form.get(PRESETS_ORIGINAL_FIELD)).strip()
        action = _text(form.get(PRESETS_ACTION_FIELD)) or PRESETS_ACTION_SAVE
        revision = _text(form.get(PRESETS_REVISION_FIELD))
        new_name = values.get("name", "").strip()
        # THE SHAPE the form posted. A form that carries none is a wizard page from before the switch
        # (or a direct POST): it is read as the preset's OWN values, so the older form stays valid.
        shape = _text(form.get(SHAPE_FIELD)).strip()
        if shape not in (SHAPE_OWN, SHAPE_COMBINATION):
            shape = SHAPE_OWN
        combination = _text(form.get(COMBINATION_FIELD))

        # WHERE THE WRITE GOES. A removal and an edit/rename write the file the preset LIVES in (found
        # by its name, never trusted from the form); a NEW preset writes the file the operator CHOSE,
        # which must be one a node's ``MizEdit.presets`` names — the extension reads no other, so a
        # file no node names is refused rather than written where it would never apply. ``matched`` is
        # the KEY the file actually carries (matched the way the lookup matched).
        matched = ""
        if action == PRESETS_ACTION_REMOVE:
            if not original:
                return _refuse_write(request, PRESETS_REFUSED_NO_NAME, PRESETS_PATH)
            found = find_preset_file(config, original)
            if found is None:
                return _refuse_write(request, PRESETS_REFUSED_UNKNOWN, PRESETS_PATH, name=original)
            path, label, matched = found
            redirect = PRESETS_PATH
        else:
            if not new_name:
                return _refuse_write(request, PRESETS_REFUSED_NO_NAME, wizard_base(original))
            if original:
                found = find_preset_file(config, original)
                if found is None:
                    return _refuse_write(request, PRESETS_REFUSED_UNKNOWN, wizard_base(original),
                                         name=original)
                path, label, matched = found
                # ONE KEY: the handler mutates the key the lookup found, never the posted spelling,
                # so a name that differs only in case edits the preset instead of adding a twin.
                original = matched
            else:
                destination = preset_destinations(config, _nodes_document(config))
                placed = _new_preset_path(request, config, form, destination)
                if isinstance(placed, RedirectResponse):
                    return placed
                path, label = placed
            redirect = f"{wizard_base(new_name)}?step={WIZARD_STEP_REVIEW}"

        # THE RANGE. Every numeric control the form carries must state a value its own control accepts:
        # the box refuses an out-of-range figure in the browser, and a POST that carries one anyway (a
        # stale page, a script, a hand-built form) is refused here with its own sentence, so the two
        # ends agree. A removal writes no weather value, and a combination writes only its member list,
        # so neither is checked. The limits are the field table's (``weather_fields.out_of_range``).
        if action != PRESETS_ACTION_REMOVE and shape != SHAPE_COMBINATION:
            offence = out_of_range_value(values, parse_fog_rows(
                values.get(weather_fields.FOG_ROWS_KEY, "")))
            if offence is not None:
                entry, stated = offence
                return _refuse_write(
                    request, PRESETS_REFUSED_OUT_OF_RANGE, redirect,
                    label=i18n.translate(request, entry.label),
                    min=_plain_number(entry.min), max=_plain_number(entry.max), value=stated)

        # A form that carries no revision cannot be checked against the file it was rendered from: it
        # is refused, so a POST that omits the field can never write after the file moved. Only a file
        # that does not exist yet has no revision to carry.
        if not revision and path.is_file():
            return _refuse_write(request, PRESETS_REFUSED_NO_REVISION, redirect)

        def mutate(document) -> None:
            if action == PRESETS_ACTION_REMOVE:
                if original in document:
                    del document[original]
                return
            if original and shape != SHAPE_COMBINATION:
                # its own values: mutate the loaded map in place so unrendered keys survive.
                document[new_name] = _merged_preset(document.get(original), wizard_value,
                                                    PRESET_OWNED_KEYS)
            else:
                # a combination (or a new preset) REPLACES the value: a list carries no keys to keep.
                document[new_name] = wizard_value
            if original and new_name != original:
                del document[original]

        # BUILD the candidate by mutating a loaded copy in place; keep an UNMUTATED copy for the guard,
        # which reads the file's keys as they are (a rename must not collide with itself).
        before = configwrite.load_config_file(path)
        # WHAT THE WIZARD PRODUCES for its shape — a combination LIST or its own values MAP, the SAME
        # value the review step dumped (``preset_value_for``), so review and write cannot drift. The
        # preset's CURRENT value is passed in, so the fog keyframes it already carries are written back.
        existing = before.get(original) if (original and isinstance(before, Mapping)) else None
        wizard_value = preset_value_for(shape, values, combination, existing,
                                        fog_rows_stated=fog_rows_stated)
        candidate = configwrite.load_config_file(path)
        if candidate is None:
            from ruamel.yaml.comments import CommentedMap
            candidate = CommentedMap()
        mutate(candidate)

        # VALIDATE the candidate against the SAME schema pair the extension names.
        try:
            configwrite.validate_against_schema(configwrite.dump_config(candidate), schemas, path=path)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        # GUARD the removal/rename: a preset nothing names may be removed or renamed, a name that
        # already exists may not be overwritten, and the destination file must be one this install
        # names. On a refusal NOTHING is written.
        nodes_document = _nodes_document(config)
        preset_documents = []
        for preset_file in discover_preset_files(config):
            try:
                preset_documents.append(configwrite.load_config_file(preset_file))
            except configwrite.ConfigWriteError:
                continue
        # the reference scan runs against the KEY the file carries, never a posted spelling that
        # differs only in case — that is the name a node's settings would have to spell.
        guard_key = matched or original
        if action == PRESETS_ACTION_REMOVE or (original and new_name != original):
            who = preset_references(nodes_document, preset_documents, guard_key,
                                    _scheduler_document(config))
            if who:
                return _refuse_write(request, PRESETS_REFUSED_IN_USE, redirect, name=guard_key,
                                     who=", ".join(who))
        # a REMOVE never collides: its posted ``name`` is not the target, so the collision check is
        # skipped rather than refusing a remove form that carries another preset's name.
        if action != PRESETS_ACTION_REMOVE:
            clash = _collision(before, new_name, original)
            if clash:
                return _refuse_write(request, PRESETS_REFUSED_DUPLICATE, redirect, name=clash)

        # GUARD a combination: it must name at least one preset and EVERY name it lists must exist in
        # the file it is written into. The extension only LOGS a member it cannot find
        # (``extension.py:110``), so a dangling or empty name is a combination that silently does
        # nothing. The match is EXACT — the extension resolves a member with a plain ``in`` and the
        # shared reader with ``dict.get``, so a wrongly-cased name is the same silent no-op.
        if shape == SHAPE_COMBINATION and action != PRESETS_ACTION_REMOVE:
            if not wizard_value:
                return _refuse_write(request, PRESETS_REFUSED_EMPTY_COMBINATION, redirect)
            known = {str(key) for key in before} if isinstance(before, Mapping) else set()
            missing = [name for name in wizard_value if name not in known]
            if missing:
                return _refuse_write(request, PRESETS_REFUSED_DANGLING, redirect,
                                     missing=", ".join(f"'{name}'" for name in missing))

        # WRITE through the ONE primitive — validated, atomic, ``.bak``-keeping, revision-guarded.
        try:
            result = configwrite.patch_config_file(path, mutate, schemas=schemas,
                                                   expect_revision=revision)
        except configwrite.StaleConfigRevision:
            return _refuse_write(request, PRESETS_STALE, redirect)
        except configwrite.ConfigValidationError as ex:
            detail = " ".join((ex.errors or str(ex)).split())
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {detail}",
                                 redirect)
        except configwrite.ConfigWriteError as ex:
            return _refuse_write(request, f"{i18n.translate(request, PRESETS_REFUSED_BANNER)} {ex}",
                                 redirect)

        # THE BANNER reads the primitive's OWN apply story — never a hard-coded claim.
        if result.changed:
            message = (settings_page.SAVED_NOW if result.applies == configwrite.APPLIES_NOW
                       else settings_page.SAVED_RESTART)
        elif action == PRESETS_ACTION_REMOVE and matched and matched != original:
            # a remove whose posted spelling differs only in case is a no-op; name the spelling the
            # file carries so the operator can act on it.
            message = i18n.translate(request, PRESETS_NO_CHANGE_CASE, name=original, actual=matched)
        else:
            message = settings_page.SAVED_NO_CHANGE
        # the name this save produced must be in the launch dialog's pick on its next open
        forget_node_lists(PRESET_LIST)
        # and every remembered parse of these files is one generation old: the next render re-reads
        forget_preset_documents()
        _presets_notice(request, message, ok=True, applies=result.applies)
        return RedirectResponse(redirect, status_code=303)

    return router
