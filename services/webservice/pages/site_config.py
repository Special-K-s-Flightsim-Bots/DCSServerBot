"""The Site settings page: one card per public site, one save, one CSS check.

WHAT THIS PAGE OWNS
-------------------
* ``GET /settings/site`` — the generated card of every site in the visitor's scope. The card's
  ordinary fields are the schema's own leaves, rendered by :mod:`services.webservice.configgen` and
  posted through the console's ONE form path; the four groups a generated control would misdescribe
  (``enabled``, ``character``, ``sections``/``metrics``, the preset gallery and the two sheets) are
  hand-rendered from their code-side vocabulary so their DEFAULT is visible
  (:data:`services.webservice.sites.SECTION_IDS`, :data:`~.sites.CHARACTERS`,
  :data:`services.webservice.site_reads.LEADERBOARD_METRICS`, :data:`services.webservice.theme_css.PRESETS`).
  The three list blocks are hand-rendered too: ``root.nav`` and ``root.tiles`` are CLOSED SETS drawn
  from :data:`~.sites.NAV_PAGE_IDS` / :data:`~.sites.TILE_IDS` (every id always listed, ordered by the
  console's shared row drag in its LOCAL mode — the row moves, **Save** writes the order), and
  ``branding.footer_links`` is the one block that takes entries.
* ``POST /actions/settings/site/config`` — ONE save for one card: the posted leaves become a
  candidate document, the candidate is parsed by the loader's own parser
  (:func:`services.webservice.sites.parse_sites`) BEFORE anything is written, and only then is the
  file handed to ``configwrite.patch_config_file`` — the same primitive every other settings save
  uses, so there is no second YAML writer.
* ``POST /actions/settings/site/draft`` — the card's UNSAVED state, so the demo pane can render the
  values the form holds before Save. The body is read by the SAVE's reader (one field tree, one set
  of passes), only that site's block is kept, and the block lives in this process's memory
  (:mod:`services.webservice.site_pages`, one entry per session and site, idle TTL, hard cap): no
  file is opened, ``site.yaml`` is untouched, and a save or the operator's leaving the card drops it.
* ``POST /actions/settings/site/css/validate`` — the expert editor's live check; it answers the
  refusal (line and reason) or the composed sheet's size, and writes nothing.
* ``POST /actions/settings/site/avatar`` and ``POST /actions/settings/site/avatar/remove`` — the
  mark: the upload re-encodes the decoded image to the four sizes of
  :data:`services.webservice.sites.AVATAR_SIZES` as PNGs and sets the ``avatar`` flag through the
  same document writer the save uses; the removal clears the flag and deletes the four files.
* ``GET /settings/site/mark/{token}/{size}.png`` — ONE of those four files, at its own address and
  its own pixels, so the card's four previews are the files themselves and never a scaled copy.
* ``POST /settings/site/add`` — the "Add a site" block: one key, one ``group_role`` (the tokens
  ``servers.yaml`` declares, which the operator cannot read off this file) and one slug, written
  through the same primitive as a card save and validated by the loader's own parser inside the
  mutation, so a site the console would refuse to install is refused with the parser's sentence and
  ``site.yaml`` is byte-identical.
* ``POST /settings/site/yaml`` and ``POST /settings/site/yaml/validate`` — the file ITSELF, in the
  console's shipped Expert Mode panel: the document is parsed and its ``sites:`` block validated
  before anything is written, and the live check answers the refusal without writing at all.

STRUCTURE VERSUS CONTENT
------------------------
``slug``, ``group_role`` are fixed when the console installs its route table, and a site that is
switched off unregisters its routes; so those two plus ``enabled`` are STRUCTURAL
(:data:`services.webservice.sites.STRUCTURAL_FIELDS`). A structural save APPLIES its file: the
console re-reads ``site.yaml`` on the saving request's own application before answering, so the
change is live and the notice says so. Everything else — branding, character, nav, tiles, sections,
metrics, lang, theme — is read per request, so a content-only save is live on the next page load.

WHO MAY DO WHAT
---------------
``site.view`` opens the page and ``site.write`` saves it; both carry a manager-scope grant, and the
per-site narrowing is done HERE, on the posted site name, against the resolved scope. ``site.structure``
and ``site.expert`` are the Admin's alone and are checked inside the handler by KEY NAME, never by
hiding a control: a hidden control is a control a crafted post can still reach.
"""
from __future__ import annotations

import copy
import io
import json
import logging
import os
import tempfile
from urllib.parse import quote
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.utils import configwrite

from .. import (config_apply, configgen, i18n, mount, permissions, readmodels, session, site_pages,
                site_reads, sites, theme_css)
from ..registry import NavItem
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import settings as settings_page
from .site_leaderboard import METRIC_MSGIDS
from .site_player import COMBAT_LAYOUT, READOUT_ORDER

__all__ = [
    "SITE_CONFIG_PATH", "SITE_CONFIG_SAVE_PATH", "SITE_CONFIG_DRAFT_PATH", "SITE_CSS_VALIDATE_PATH",
    "SITE_ADD_PATH", "SITE_YAML_PATH", "SITE_YAML_VALIDATE_PATH",
    "SITE_ADD_KEY_FIELD", "SITE_ADD_ROLE_FIELD", "SITE_ADD_SLUG_FIELD", "SiteAddRefusal",
    "SITE_DRAFT_CLEAR_FIELD", "SITE_DRAFT_BODY_BYTES", "draft_size_guard",
    "SITE_AVATAR_UPLOAD_PATH", "SITE_AVATAR_REMOVE_PATH", "SITE_AVATAR_FIELD",
    "SITE_AVATAR_MAX_BYTES", "SITE_AVATAR_MAX_PIXELS", "SITE_AVATAR_FORM_ALLOWANCE",
    "SITE_SHEET_UPLOAD_PATH", "SITE_SHEET_REMOVE_PATH", "SITE_SHEET_FILE_FIELD",
    "SITE_SHEET_FORM_ALLOWANCE", "sheet_slot", "sheet_form_dependency",
    "SITE_MARK_PATH", "mark_slot", "mark_form_dependency", "SITE_MARK_STAGED_SUFFIX",
    "stage_mark_file", "stage_sheet_file",
    "SITE_VIEW_CAPABILITY", "SITE_WRITE_CAPABILITY", "SITE_STRUCTURE_CAPABILITY",
    "SITE_EXPERT_CAPABILITY", "SITE_ROLES", "SITE_CONFIG_TEMPLATE", "SITE_TITLE", "SITE_LEAD",
    "SITE_KEY_FIELD", "SITE_SHEET_FIELD", "SITE_STRUCTURAL_KEYS", "SECTION_LABELS",
    "CHARACTER_LABELS", "CHARACTER_NOTES", "CharacterSet", "CHARACTER_SETS",
    "READOUT_ORDER", "COMBAT_LAYOUT", "character_payload",
    "TILE_LABELS", "NAV_LABEL_SOURCE", "list_rows",
    "ExpertSheet", "expert_sheet", "WIZARD_STEPS", "WIZARD_BASE", "wizard_payload",
    "palette_state", "palette_payload",
    "BRANDING_ROWS", "BRANDING_PATHS", "BrandingRow", "branding_rows",
    "SITE_PANEL", "SITE_PANEL_DOC", "SITE_PANEL_OPEN", "SITE_PANEL_CAP", "SITE_PANEL_WRITES",
    "SITE_PANEL_NONE", "SITE_PALETTE", "SITE_LINKS", "SITE_EXPERT_MODE", "SITE_FIELDS",
    "SITE_IDENTITY", "SITE_IDENTITY_NOTE", "SITE_CHARACTER_BOX",
    "SITE_PREVIEW_TITLE", "SITE_PREVIEW_OFF",
    "SiteAccess", "site_access", "site_in_scope", "form_tree", "card_field",
    "site_catalog", "page_context", "capabilities", "nav_items", "declare", "add_routes",
    "known_roles", "site_document", "SiteYamlEmpty",
]

log = logging.getLogger(__name__)

#: THE PAGE and its two POST doors. Both live on ``/actions/`` so they never meet the settings area's
#: parameterised page routes.
SITE_CONFIG_PATH = "/settings/site"
SITE_CONFIG_SAVE_PATH = "/actions/settings/site/config"
SITE_CONFIG_DRAFT_PATH = "/actions/settings/site/draft"
SITE_CSS_VALIDATE_PATH = "/actions/settings/site/css/validate"
#: THE TWO ADMIN-ONLY BLOCKS AT THE FOOT OF THE PAGE, on the page's own prefix. The settings area's
#: parameterised page route (``/settings/{kind}/{name}``) is a GET, so a POST here is never shadowed
#: by it; the live check writes nothing and is the Expert Mode panel's ``data-validate-url``.
SITE_ADD_PATH = SITE_CONFIG_PATH + "/add"
#: THE CARD'S BOX, SERVED FROM THE CONSOLE. A site's own preview lives under its prefix, and a site
#: this process has not registered has none — so the box asks here instead: the console renders the
#: same document from the FILE (and the session's draft), for a site whose address answers on the
#: next restart. Same address for every site; the key travels in the query.
SITE_MOCK_PATH = SITE_CONFIG_PATH + "/mock"
SITE_YAML_PATH = SITE_CONFIG_PATH + "/yaml"
SITE_YAML_VALIDATE_PATH = SITE_CONFIG_PATH + "/yaml/validate"
#: the mark's two write doors: the upload and the removal. Both take the site key the save takes.
SITE_AVATAR_UPLOAD_PATH = "/actions/settings/site/avatar"
SITE_AVATAR_REMOVE_PATH = "/actions/settings/site/avatar/remove"
#: the uploaded palette sheet's two write doors, beside the mark's pair and gated the same way.
SITE_SHEET_UPLOAD_PATH = "/actions/settings/site/sheet"
SITE_SHEET_REMOVE_PATH = "/actions/settings/site/sheet/remove"
#: ONE of a site's four uploaded mark files, at its own address and its own pixel size — the card's
#: four previews are the actual files, never a scaled copy of the one the bar draws.
SITE_MARK_PATH = "/settings/site/mark/{token}/{size}.png"
#: the suffix a variant carries while it is staged beside its target, before the config write that
#: names it has landed. A rename onto the live name is the only thing that publishes a mark.
SITE_MARK_STAGED_SUFFIX = ".tmp"

#: the four capabilities. ``site.view``/``site.write`` are grantable to a managing scope (a group
#: admin edits their own site's CONTENT); ``site.structure``/``site.expert`` never are.
SITE_VIEW_CAPABILITY = "site.view"
SITE_WRITE_CAPABILITY = "site.write"
SITE_STRUCTURE_CAPABILITY = "site.structure"
SITE_EXPERT_CAPABILITY = "site.expert"
SITE_ROLES: tuple[str, ...] = ("Admin",)

SITE_CONFIG_TEMPLATE = "site_config.html"
#: the page's own query parameter naming the ONE card that must be open. A save answers with it, so
#: an operator who pressed Save on the third card lands on that card and not at the top of the list.
SITE_OPEN_PARAM = "site"
SITE_TITLE = i18n._("Public site")
SITE_LEAD = i18n._("Every public site this console serves: what it shows, what it looks like, and "
                   "whether it is on. A site's content shows on its next page load; its address, its "
                   "role and its switch need the console to reload.")

#: the hidden field naming the card a post belongs to — the one thing the save trusts from the form
#: before it resolves anything, and the value the per-site scope narrowing reads.
SITE_KEY_FIELD = "_site"
#: the expert editor's posted text (its live check) — never a document path.
SITE_SHEET_FIELD = "_sheet"
#: the add block's three fields: the site's key in the file, the role it serves and its slug.
SITE_ADD_KEY_FIELD = "_key"
SITE_ADD_ROLE_FIELD = "_role"
SITE_ADD_SLUG_FIELD = "_slug"
#: the draft door's own control: posted as ``1`` to DISCARD this session's draft (the operator left
#: the card, or the panel asked for a clean slate). A draft is otherwise only ever read.
SITE_DRAFT_CLEAR_FIELD = "_draft_clear"
#: the largest body the draft door reads, and the largest block it will store. The card posts its own
#: form, so the bound has to admit an expert sheet (:data:`services.webservice.sites.MAX_EXPERT_LENGTH`)
#: beside every other field while staying far below a body that could stall the single worker.
SITE_DRAFT_BODY_BYTES = 256 * 1024

#: the keys a running console cannot change without rebuilding its route table.
SITE_STRUCTURAL_KEYS: tuple[str, ...] = sites.STRUCTURAL_FIELDS

#: the shipped schema the ``sites:`` block is described by. The site file itself carries no schema of
#: its own: this page hands the block to the loader's own parser before writing, and the schema is
#: read here only for the FORM (labels, kinds, defaults, the closed ``sections:`` mapping).
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "webservice_schema.yaml"

#: the section ids' display names, in the order :data:`services.webservice.sites.SECTION_IDS` numbers
#: them. A LABEL table only — the vocabulary is the model's constant, pinned by a test.
SECTION_LABELS: dict[str, str] = {
    "community": i18n._("This community"),
    "season": i18n._("The season so far"),
    "standings": i18n._("Top pilots"),
    "aircraft": i18n._("Most flown"),
    "when_we_fly": i18n._("When we fly"),
    "squadrons": i18n._("Squadrons"),
    "servers": i18n._("Servers"),
}

#: one line per character, saying which figures it leads with. The ids are
#: :data:`services.webservice.sites.CHARACTERS`; this table adds words, never a second vocabulary.
CHARACTER_LABELS: dict[str, str] = {
    "pvp": i18n._("PvP"),
    "pve": i18n._("PvE"),
    "pvpve": i18n._("PvP and PvE"),
}
CHARACTER_NOTES: dict[str, str] = {
    "pvp": i18n._("The player-versus-player record leads."),
    "pve": i18n._("What a pilot flew leads."),
    "pvpve": i18n._("Both sides of the record lead, side by side."),
}


class CharacterSet(NamedTuple):
    """ONE character's decisions, as the card states them.

    ``sections`` and ``metrics`` are the sets a press writes; ``readout`` and ``combat`` are the
    player page's own symbols, read from :mod:`services.webservice.pages.site_player` rather than
    restated, so the card can never claim a page behaviour the page does not have. ``what`` is the
    one sentence the card prints under the pills; ``why_sections``/``why_metrics`` say why a row of
    each list is off (empty for the character that withdraws nothing).
    """

    sections: tuple[str, ...]
    metrics: tuple[str, ...]
    readout: tuple[str, ...]
    combat: tuple[tuple[str, int], ...]
    what: str
    why_sections: str
    why_metrics: str


#: THE CHARACTER TABLE — the ONE home of what a character decides. Keyed by
#: :data:`services.webservice.sites.CHARACTERS`; pinned against the design's table and against
#: ``site_player``'s readout order and combat layout.
CHARACTER_SETS: dict[str, CharacterSet] = {
    "pvp": CharacterSet(
        sections=("community", "standings", "aircraft", "when_we_fly", "servers"),
        metrics=("playtime", "air_targets", "kd_ratio", "pvp_kd_ratio"),
        readout=READOUT_ORDER["pvp"],
        combat=COMBAT_LAYOUT["pvp"],
        what=i18n._("A fight-night community: the site shows who is winning the fights — the "
                    "ladder, the airframes, the op nights and the servers — and it ranks fights, "
                    "not strike work."),
        why_sections=i18n._("Off for PvP: the season curve and the squadrons — a duel community's "
                            "story is the ladder, not a weekly hours curve, and a fight night runs "
                            "no campaign."),
        why_metrics=i18n._("Withdrawn for PvP: ships, air defence and ground targets — the site "
                           "ranks fights, not strike work."),
    ),
    "pve": CharacterSet(
        sections=("community", "season", "aircraft", "when_we_fly", "squadrons", "servers"),
        metrics=("playtime", "air_targets", "ships", "air_defence", "ground_targets", "kd_ratio"),
        readout=READOUT_ORDER["pve"],
        combat=COMBAT_LAYOUT["pve"],
        what=i18n._("A co-op community: the site shows what it flies and builds — the activity "
                    "curve, the airframes, the campaign and the op nights — and it does not rank "
                    "pilots against each other."),
        why_sections=i18n._("Off for PvE: top pilots — a co-op site does not rank its pilots "
                            "against each other."),
        why_metrics=i18n._("Withdrawn for PvE: the PvP ratio — there are no player-versus-player "
                           "kills to rank."),
    ),
    "pvpve": CharacterSet(
        sections=sites.SECTION_IDS,
        metrics=site_reads.LEADERBOARD_METRICS,
        readout=READOUT_ORDER["pvpve"],
        combat=COMBAT_LAYOUT["pvpve"],
        what=i18n._("A community that does both: every section the build has, and every metric it "
                    "can rank."),
        why_sections="",
        why_metrics="",
    ),
}


def _check_character_sets() -> None:
    """Refuse to serve a table that names an id the page cannot draw, or that misses a character."""
    if set(CHARACTER_SETS) != set(sites.CHARACTERS):
        raise RuntimeError(f"the character table is keyed by {sorted(CHARACTER_SETS)}, "
                           f"the model names {sorted(sites.CHARACTERS)}")
    for ident, spec in CHARACTER_SETS.items():
        unknown_sections = [ident_ for ident_ in spec.sections if ident_ not in sites.SECTION_IDS]
        unknown_metrics = [ident_ for ident_ in spec.metrics
                           if ident_ not in site_reads.LEADERBOARD_METRICS]
        if unknown_sections or unknown_metrics:
            raise RuntimeError(f"the {ident} set names unknown ids: "
                               f"{unknown_sections + unknown_metrics}")


_check_character_sets()

# ---------------------------------------------------------------- the page's own sentences
SITE_EMPTY = i18n._("No site is configured yet, so there is nothing to edit here.")
SITE_READ_FAILED = i18n._("The site settings could not be read.")
SITE_BRANDING = i18n._("Branding")
SITE_PAGES = i18n._("Pages")
SITE_SECTIONS = i18n._("What the overview shows")
SITE_METRICS = i18n._("What the leaderboard ranks")
SITE_CHARACTER_BEHAVIOUR = i18n._(
    "Pressing a character rewrites the two lists below, now, before anything is saved — Save keeps "
    "the result. A switch you moved by hand stays where you left it until a character is pressed "
    "again.")
SITE_CHARACTER_NOJS = i18n._(
    "With JavaScript off a press only saves the character: the two lists keep the values stored "
    "here, and the player page still follows the character.")
SITE_CHARACTER_CHANGED = i18n._("Edited by hand — these are no longer exactly the %(name)s set.")
SITE_CHARACTER_COUNT = i18n._("%(sections)s of %(sectionsTotal)s sections · "
                              "%(metrics)s of %(metricsTotal)s metrics")
SITE_PRESETS = i18n._("Presets")
SITE_PRESET_USE = i18n._("Use this preset")
SITE_PRESET_IN_USE = i18n._("In use")
SITE_EXPERT = i18n._("Expert sheet")
SITE_EXPERT_CHECK = i18n._("Check")
#: the bar's copy badge, and what it becomes the moment the sheet is edited. The name is the shipped
#: sheet's FILE, carried verbatim: it names a file the operator could point at.
SITE_EXPERT_COPY = i18n._("copy of %(name)s")
#: the copy's badge while the carrier is empty and the site stores no sheet: the transcription is
#: shown for reading and is not this site's palette until the operator changes it.
SITE_EXPERT_COPY_UNSTORED = i18n._("copy of %(name)s · not stored")
SITE_EXPERT_EDITED = i18n._("edited")
#: the line under the badge — the copy's own count, and what it is linked after.
SITE_EXPERT_WHEN = i18n._("%(declarations)s declarations · %(lines)s lines · linked after the "
                          "site's own sheet")
#: the same line while the copy is only SHOWN: the carrier is empty and the site stores no sheet.
SITE_EXPERT_WHEN_UNSTORED = i18n._(
    "%(declarations)s declarations · %(lines)s lines · shown, not stored — the shipped copy for "
    "reading; it is kept only when you change it")
#: the bar's restore control and the one question it asks before replacing operator work.
SITE_EXPERT_LOAD = i18n._("Load the shipped sheet")
SITE_EXPERT_REPLACE = i18n._("Replace this sheet with the %(declarations)s declarations of "
                             "%(name)s? This cannot be undone.")
#: §D4's three statements: where the sheet is read, the grammar in one line, and the bound with the
#: count beside it.
SITE_EXPERT_LINKED = i18n._("The sheet is linked after the site's own stylesheet, so a token "
                            "declared there wins.")
SITE_EXPERT_GRAMMAR = i18n._("One --name: value; declaration per line. The sheet is wrapped in one "
                             "rule, so a selector, a comment and an import are each refused by name.")
SITE_EXPERT_LIMIT = i18n._("%(allowed)s characters allowed · %(used)s used")
#: the accepted verdict: the editor's own answer, said with the counts it just measured.
SITE_EXPERT_ACCEPTED = i18n._("Accepted — %(declarations)s declarations, %(characters)s characters. "
                              "Saving writes them as this site's own sheet.")

# ------------------------------------------------------ §5 · the palette block's own two-way words
#: WHICH OF THE THREE palettes the site names, said ONCE in the block's head: the words, the key in
#: force, then its value when it has one. A preset's NAME is never repeated here — the gallery's tile
#: carries it, once, in its badge.
SITE_PALETTE_STATE_PRESET = i18n._("A shipped preset")
SITE_PALETTE_STATE_SHEET = i18n._("An uploaded sheet")
SITE_PALETTE_STATE_EXPERT = i18n._("An expert sheet")
#: the sheet's own value: how much it declares
SITE_PALETTE_EXPERT_VALUE = i18n._("%(declarations)s declarations")
#: the head's say-line: at rest, with the uploaded sheet in force, and with the sheet drawn at the
#: foot of the page
SITE_PALETTE_SAY = i18n._(
    "One palette, one choice — the box at the foot of the page shows it as you press a palette.")
SITE_PALETTE_SAY_SHEET = i18n._("Your uploaded sheet is in force — replace or remove it below.")
SITE_PALETTE_SAY_OPEN = i18n._("The sheet is open at the foot of this page.")
#: the head carries NO control: a site with a sheet is pointed at the block that draws it
SITE_PALETTE_FOOT = i18n._("Expert mode — at the foot of the page")
#: the sixth tile is a DOOR, not a sixth member of the exclusive set
SITE_PALETTE_DOOR = i18n._("Or build a palette")
SITE_PALETTE_DOOR_KEY = i18n._("three starting points →")
#: the block's one rule, stated once and always visible under the gallery
SITE_PALETTE_RULE_B = i18n._("One palette, one choice.")
SITE_PALETTE_RULE = i18n._(
    "A shipped preset, an uploaded sheet or the expert sheet — at most one, and the header above "
    "says which one this site names. A save that would name two is refused, and nothing is written.")
#: Expert Mode is a VIEW control: it carries no name, posts nothing and never writes the carrier
SITE_EXPERT_TOGGLE = i18n._("Expert Mode")
#: the closed state still STATES the sheet — a stored sheet is hidden, keeps its value, still posts
SITE_EXPERT_STORED = i18n._(
    "The expert sheet is stored — %(declarations)s declarations, %(used)s of %(allowed)s characters. "
    "Expert Mode is closed, so the sheet is not drawn: it still posts exactly as it stands, and the "
    "box being unticked clears nothing. Tick it to read or edit the sheet.")
#: …and a site with no sheet says so, rather than drawing an empty box. The sentence must not
#: claim that opening Expert Mode stores anything: the shown transcription is kept only when the
#: operator changes it and saves.
SITE_EXPERT_NONE = i18n._(
    "No expert sheet. The site's palette is whichever of the preset and the uploaded sheet the "
    "header above states. Tick Expert Mode above to read the shipped transcription — it is shown, "
    "not stored, and it is kept only when you change it and save. The sheet is read and checked "
    "here at the card's full width, because a sheet is code and code wants lines.")
SITE_EXPERT_SAY = i18n._("the sheet is code: read and checked here, at the card's full width")

# ---------------------------------------------------------- §2 · the palette wizard's own sentences
#: The wizard is a UI over the sheet above: it carries the six steps, the six constants it does not
#: own, and every sentence the screen prints. `static/site-palette-wizard.js` fills the numbers in.
SITE_WIZARD = i18n._("Palette wizard")
SITE_WIZARD_STEP1 = i18n._("Step 1 · the starting point")
SITE_WIZARD_STEP2 = i18n._("Step 2 · the six steps")
SITE_WIZARD_LEAD = i18n._(
    "A palette is twenty-three declarations over six steps — seventeen derived, six carried as they "
    "are. Pick what it starts from; the wizard derives the rest, tests every pair against AA, and "
    "hands the result to the sheet below — where it stays editable and where Check and Save already "
    "are.")
SITE_WIZARD_RULE = i18n._(
    "Every step is tested on the ground it lands on. A base that cannot reach 4.5:1 as the accent is "
    "moved, and the move is shown — never clamped in silence.")
SITE_WIZARD_DERIVE = i18n._("Derive six steps →")
#: the panel's one way back: the gallery, and the step before the six rows
SITE_WIZARD_CLOSE = i18n._("← Presets")
SITE_WIZARD_BASE = i18n._("Base colour")
SITE_WIZARD_SCHEME = i18n._("The page the palette is derived for")
SITE_WIZARD_DARK = i18n._("Dark page")
SITE_WIZARD_LIGHT = i18n._("Light page")
SITE_WIZARD_BACK = i18n._("← Step 1")
SITE_WIZARD_AGAIN = i18n._("← Back to the wizard")
SITE_WIZARD_USE = i18n._("Use these colours")
SITE_WIZARD_FOOT = i18n._(
    "Six steps, twenty-three declarations, ten pairs tested. Use these colours writes them into the "
    "sheet below — nothing is saved until the card's own Save.")
SITE_WIZARD_NOW_MISSING = i18n._(
    "The site's own preview is not loaded, so its palette cannot be read: the box above answers only "
    "when the site is enabled.")
SITE_WIZARD_NOW_SAY = i18n._(
    "Read from the site's own document: a sheet from another origin cannot be read, and then the "
    "tokens have to be reported by hand.")
#: a palette that was READ (a stored sheet, the page's own colours) is reported, never moved
SITE_WIZARD_REPORTED = i18n._(
    "These six steps are measured as they stand: nothing here is derived, nothing is moved, "
    "and nothing is rewritten until you press Use these colours.")
SITE_WIZARD_HANDED = i18n._(
    "The wizard's answer is in the sheet below. The four presets above are no longer applied, and no "
    "preset is marked in use — a site names one palette, and this is it.")
SITE_WIZARD_WARN_B = i18n._("The base cannot be used as it is.")
SITE_WIZARD_PICK = i18n._("Pick another")
SITE_WIZARD_USE_THIS = i18n._("Use %(hex)s")
SITE_WIZARD_CONSTS = i18n._(
    "Carried unchanged, and named so: --sage, --hero-sage and the three --pulse-* washes (green means "
    "live, whatever the palette), --black, the four medal declarations and the three font families.")
#: §3 · one editable step: its declarations, the followers, and the value a hand-set edit lands under AA
SITE_WIZARD_MORE = i18n._("The declarations this step writes")
SITE_WIZARD_OWN = i18n._("the page's own colour")
SITE_WIZARD_ACCENT = i18n._("the accent — eyebrow, links, the name")
SITE_WIZARD_FOLLOWS = i18n._("follows %(token)s · L %(offset)s")
SITE_WIZARD_MOVED_L = i18n._("moved L %(from)s → %(to)s")
SITE_WIZARD_STEP_EDITING = i18n._("the step you are editing")
SITE_WIZARD_UNDER_AA = i18n._("under AA")
SITE_WIZARD_EDIT_UNDER = i18n._(
    "The value you set is %(before)s. The nearest value that clears it is %(hex)s "
    "(%(ratio)s:1 on %(against)s, %(ground)s:1 on the ground), L %(from)s → %(to)s — it clears "
    "the other pairs this edit broke as well.")
SITE_WIZARD_EDIT_KEEP = i18n._("Keep my %(hex)s")
SITE_WIZARD_EDIT_STUCK = i18n._(
    "The value %(hex)s cannot be moved into AA at any lightness: the row keeps it and the verdict "
    "refuses it.")
SITE_WIZARD_EDIT_FOOT = i18n._(
    "Six steps, twenty-three declarations, ten pairs tested — %(count)s under AA. The value is the "
    "operator's and the sheet carries it; Use these colours is held until you answer.")
#: the six steps, in the order the panel draws them: the step's own name, and the pair its chip measures
SITE_WIZARD_STEP_GROUND = i18n._("ground")
SITE_WIZARD_STEP_PANEL = i18n._("panel")
SITE_WIZARD_STEP_LINE = i18n._("line")
SITE_WIZARD_STEP_ACCENT = i18n._("accent")
SITE_WIZARD_STEP_TEXT = i18n._("text")
SITE_WIZARD_STEP_STEPS = i18n._("text steps")
#: the three starting points, and the third's default base: each tile states its key and one sentence
SITE_WIZARD_START_PRESET = i18n._("A shipped palette")
#: the two tiles' KEY words are schema identifiers, not prose: they are carried verbatim
SITE_WIZARD_START_PRESET_KEY = "theme.preset"
SITE_WIZARD_START_PRESET_SAY = i18n._(
    "Reads %(file)s as the starting point — the sheet this build ships. ← Presets goes back to the "
    "gallery to choose another.")
SITE_WIZARD_START_NOW = i18n._("The palette this site wears now")
SITE_WIZARD_START_NOW_KEY = i18n._("the resolved sheet")
SITE_WIZARD_START_NOW_SAY = i18n._(
    "Read from the page itself: the colours a visitor is looking at today, as a starting point.")
SITE_WIZARD_START_ONE = i18n._("One colour")
SITE_WIZARD_START_ONE_KEY = i18n._("a base colour")
SITE_WIZARD_START_ONE_SAY = i18n._(
    "Pick the colour the site is about; the wizard derives the other seventeen from its hue.")
SITE_WIZARD_START_STORED = i18n._("The sheet below")
SITE_WIZARD_START_STORED_KEY = "theme.expert"
SITE_WIZARD_START_STORED_SAY = i18n._(
    "This site's own sheet, read back: the six steps are its values, and Back returns to them.")
#: the numbers the rule had to move, and the one case where the handoff stays closed
SITE_WIZARD_MOVED = i18n._(
    "%(token)s moved L %(from)s → %(to)s to reach %(ratio)s:1 on %(against)s (was %(was)s:1, %(role)s "
    "needs %(needs)s:1).")
SITE_WIZARD_STUCK = i18n._("%(token)s could not reach %(needs)s:1 on %(against)s at any lightness.")
SITE_WIZARD_USED = i18n._(
    "The base %(base)s was used as the accent (%(accent)s, lightness %(from)s → %(to)s to clear "
    "--panel).")
SITE_WIZARD_CHROMA = i18n._(
    "The accent's chroma was raised from %(from)s to %(to)s, so a grey still carries a hue.")
SITE_WIZARD_ADJUST = i18n._(
    "The base cannot be used as it is. %(base)s sits at OKLCH lightness %(from)s; an accent on this "
    "ground needs %(to)s to clear 4.5:1 on the panel, so every step below is built from %(accent)s "
    "instead — lightness %(from)s → %(to)s, a different colour and not a nudge.")
SITE_WIZARD_ACCEPTED = i18n._(
    "Accepted — %(declarations)s declarations, %(pairs)s pairs tested, every one at or above AA. "
    "Nothing is saved until Save.")
#: the group admin's row, where the wizard and the sheet would be
SITE_WIZARD_GROUP = i18n._("A palette of your own")
SITE_WIZARD_GROUP_FLAG = i18n._("Admin only")
SITE_WIZARD_GROUP_HINT = i18n._(
    "A palette is written into the site's file by an installation admin. Ask for one: the sheet it "
    "carries is theirs to edit.")
SITE_WIZARD_GROUP_RO = i18n._("Not available to a group admin")
#: the sheet's bar once the wizard has written it: its badge, and the line naming what it is
SITE_WIZARD_FROM_BADGE = i18n._("from the wizard")
SITE_WIZARD_WRITTEN = i18n._("written by the wizard · linked after the site's own sheet")
SITE_NEEDS_RELOAD = i18n._("needs a reload")
SITE_CHARACTER = i18n._("Character")
SITE_CHARACTER_Q = i18n._("What kind of community is this?")
SITE_ENABLED = i18n._("Enabled")
SITE_ENABLED_HINT = i18n._("A site that is switched off registers no routes: the console reloads and "
                           "its address answers \"not found\" until it is back on.")
SITE_ADDRESS = i18n._("Address")
SITE_SERVERS = i18n._("Servers")
SITE_SERVERS_HINT = i18n._("A site's servers are the ones whose managed_by names this token "
                           "(servers.yaml is the only authority).")
SITE_ROOT_SERVERS = i18n._("the servers that declare no managed_by")
SITE_ROOT_SLUG = i18n._("served at the site root")
SITE_ON = i18n._("on")
SITE_OFF = i18n._("off")
SITE_SAVED_LIVE = i18n._("Saved — shown on the site immediately; nothing is reloaded or restarted.")
SITE_SAVED_APPLIED = i18n._("Saved — the console re-read the site settings file, so the site's "
                            "address, role or switch is live now. Nothing is reloaded or restarted.")
SITE_REFUSED_SCOPE = i18n._("You can change only your own site's content, so nothing was written.")
SITE_REFUSED_STRUCTURE = i18n._("This site's address, its role and its switch are the Admin's to "
                                "change, so nothing was written.")
SITE_REFUSED_EXPERT = i18n._("The expert sheet is the Admin's to write, so nothing was written.")
SITE_REFUSED_FOREIGN = i18n._("This form names another site's fields, so nothing was written.")
SITE_REFUSED_UNKNOWN = i18n._("No such site.")
SITE_NOTHING_WRITTEN = i18n._("Nothing was written: ")
SITE_REFUSED_STALE = i18n._("This page is stale — the file changed on disk since it was rendered. "
                            "Reload and try again.")

# --------------------------------------- the two Admin-only blocks at the foot of the page
SITE_ADD = i18n._("Add a site")
SITE_ADD_LEAD = i18n._("A new site is one entry in this file: its name, the servers it shows and the "
                       "segment it answers on.")
SITE_ADD_KEY = i18n._("Name in the file")
SITE_ADD_KEY_HINT = i18n._("Lowercase letters, digits and inner dashes, starting with a letter or "
                           "digit. This is the site's key, not its address.")
SITE_ADD_ROLE = i18n._("The servers it shows")
SITE_ADD_ROLE_HINT = i18n._("Every value servers.yaml names in a server's managed_by, and nothing "
                            "else: a site's servers are the ones whose managed_by names its role.")
SITE_ADD_ROOT = i18n._("The site root")
SITE_ADD_SLUG_HINT = i18n._("The segment after \"/\" a visitor types. Empty for the site root; a site "
                            "other than the root cannot be saved without one.")
SITE_ADD_SUBMIT = i18n._("Add the site")
SITE_ADD_REFUSED_ROLE = i18n._("Adding a site is the installation Admin's, so nothing was written.")
SITE_ADD_REFUSED_KEY = i18n._("'%(key)s' is not a site name: lowercase letters, digits and inner "
                              "dashes, starting with a letter or digit. Nothing was written.")
SITE_ADD_REFUSED_EXISTS = i18n._("A site named '%(key)s' is already in this file, so nothing was "
                                 "written.")
SITE_ADD_REFUSED_SLUG = i18n._("A site other than the site root needs the segment it answers on, so "
                               "nothing was written.")
#: the YAML block's own words. The panel's chrome (its heading, its badge, Expert Mode, its hints and
#: Save) is the shipped Settings page's, reused verbatim; these two sentences are what the console
#: has to say about THIS file.
SITE_YAML_CHECKED = i18n._("Every change is checked by this file's own parser, and a document that "
                           "does not pass cannot be saved.")
SITE_YAML_APPLIES = i18n._("A save writes this file; the console reads it again on the next bot "
                           "restart.")
SITE_YAML_REFUSED_ROLE = i18n._("Editing this file is the installation Admin's, so nothing was "
                                "written.")

# ------------------------------------------------------- the mark (§E1): the upload and its bounds
#: the multipart field the upload's bytes arrive in, and the closed set of formats Pillow is allowed
#: to identify. The FORMAT is Pillow's answer, never the request's content type or the file's suffix.
SITE_AVATAR_FIELD = "_avatar"
SITE_AVATAR_FORMATS: tuple[str, ...] = ("PNG", "JPEG", "WEBP")
#: the request body's hard ceiling, checked before Pillow reads a byte. A 12-megapixel JPEG is a few
#: megabytes; 8 MiB leaves room for one and refuses a body that is not an image at all.
SITE_AVATAR_MAX_BYTES = 8 * 1024 * 1024
#: the decoded pixel ceiling. Pillow's own ``MAX_IMAGE_PIXELS`` is a second, independent cap and the
#: LOWER of the two governs; a 30 000 x 30 000 PNG (900 megapixels) is refused from its header.
SITE_AVATAR_MAX_PIXELS = 16_777_216
SITE_AVATAR_UPLOADED = i18n._("The mark was uploaded — the site shows it on its next page load.")
SITE_AVATAR_REMOVED = i18n._("The uploaded mark was removed — the site draws branding.logo, or the "
                             "shipped mark when none is set.")
SITE_AVATAR_REFUSED_NOT_IMAGE = i18n._("That file is not an image this page accepts: upload a PNG, "
                                       "a JPEG or a WebP.")
SITE_AVATAR_REFUSED_BYTES = i18n._("That file is larger than the %(limit)s this page accepts, so "
                                   "nothing was written.")
SITE_AVATAR_REFUSED_PIXELS = i18n._("That image is %(pixels)s pixels; the mark accepts at most "
                                    "%(limit)s, so nothing was written.")
#: the decompression bomb: Pillow refuses to decode it before this page can count its pixels, so the
#: sentence carries no count — there is none to be had without decoding the image.
SITE_AVATAR_REFUSED_BOMB = i18n._("That image decodes to more pixels than the mark accepts, so "
                                  "nothing was written.")
SITE_AVATAR_REFUSED_FILE = i18n._("This site's settings file cannot be read, so nothing was written.")
SITE_AVATAR_REFUSED_ROLE = i18n._("Your role cannot change this site's mark, so nothing was written.")
SITE_AVATAR_REFUSED_SCOPE = i18n._("You can upload a mark only for your own site, so nothing was "
                                   "written.")
#: a body Starlette's multipart parser refuses for a reason that is not this page's own ceiling (a
#: field over the bound, no boundary, unreadable data): the page answers in its own words regardless.
SITE_AVATAR_REFUSED_FORM = i18n._("That upload could not be read as a form, so nothing was written.")
#: the multipart ENVELOPE's headroom over the file's own bytes: boundaries, the CSRF field, the site
#: key and the revision. The body ceiling is the file ceiling plus this, so a file AT the page's own
#: cap is not refused for the few hundred bytes around it — the parser's part bound decides instead.
SITE_AVATAR_FORM_ALLOWANCE = 64 * 1024

# ------------------------------------------- the uploaded palette sheet: the second upload door
#: the multipart field the sheet's bytes arrive in. A sheet is TEXT and is stored under a FIXED leaf
#: (:data:`services.webservice.sites.SHEET_LEAF`), so the uploaded file's name is only ever read to
#: check its suffix — no operator-supplied name reaches a path.
SITE_SHEET_FILE_FIELD = "_sheet"
#: the accepted suffix, matched case-insensitively on the uploaded file's name.
SITE_SHEET_SUFFIX = ".css"
#: the multipart envelope's headroom, the mark's own figure: the same handful of fields around it.
SITE_SHEET_FORM_ALLOWANCE = 64 * 1024
SITE_SHEET_UPLOADED = i18n._("The sheet was uploaded — the site's preview reloads with it.")
SITE_SHEET_REMOVED = i18n._("The uploaded sheet was removed — the site falls back to its preset.")
SITE_SHEET_REFUSED_NOT_CSS = i18n._("That file is not a .css sheet: this page accepts a CSS file.")
SITE_SHEET_REFUSED_BYTES = i18n._("That sheet is larger than the %(limit)s this page accepts, so "
                                  "nothing was written.")
SITE_SHEET_REFUSED_ENCODING = i18n._("That file is not UTF-8 text, so nothing was written.")
SITE_SHEET_REFUSED_ROLE = i18n._("Your role cannot change this site's palette, so nothing was "
                                 "written.")
SITE_SHEET_REFUSED_SCOPE = i18n._("You can upload a sheet only for your own site, so nothing was "
                                  "written.")
#: one refusal sentence, two doors: the site file cannot be read, whatever is being written into it.
SITE_SHEET_REFUSED_FILE = SITE_AVATAR_REFUSED_FILE
#: …and one more: a body the multipart parser refused is the page's words, never the parser's.
SITE_SHEET_REFUSED_FORM = SITE_AVATAR_REFUSED_FORM

# ------------------------------------- §5 · the sheet's row in the palette block
SITE_SHEET = i18n._("Your own sheet")
SITE_SHEET_DESC = i18n._("A .css file the site serves as its palette, stored with the site's assets.")
#: SAID PLAINLY: no validation, and what follows from that. The sheet is the operator's own CSS.
SITE_SHEET_NOTE = i18n._("The sheet is served exactly as you wrote it — nothing is checked. It may "
                         "carry @font-face, url() and component rules, and the browser loads what "
                         "it names from this installation's own origin. The expert sheet below is "
                         "the validated path.")
SITE_SHEET_STORED = i18n._("The site serves the uploaded file %(name)s.")
SITE_SHEET_NONE = i18n._("No sheet uploaded. The site serves the palette the header above states.")
SITE_SHEET_UPLOAD_LABEL = i18n._("Upload a sheet")
SITE_SHEET_REPLACE_LABEL = i18n._("Replace the sheet")
SITE_SHEET_CHOOSE = i18n._("Choose a sheet")
SITE_SHEET_INPUT = i18n._("A .css file, UTF-8, at most %(limit)s.")
SITE_SHEET_CONFIRM = i18n._("Remove this site's uploaded sheet? The site falls back to its preset.")

# ---------------------------------------------------- §E · the mark's slot on the card
SITE_MARK = i18n._("The mark")
SITE_MARK_DESC = i18n._("The image beside the site's name in the top bar.")
SITE_MARK_ALT = i18n._("The site's current mark")
#: the two no-upload states' sentences, then the one line that states the order now that the resolver
#: reads the uploaded files. §E1's "until the resolver reads these files" caveat is retired.
SITE_MARK_SHIPPED = i18n._("branding.logo is not set, so the shipped mark is drawn until one is "
                           "uploaded.")
SITE_MARK_LOGO = i18n._("branding.logo names the file the site draws today; uploading an image "
                        "replaces it.")
SITE_MARK_ORDER = i18n._("The site draws the uploaded mark first, then branding.logo, then the "
                         "shipped mark.")
SITE_MARK_FROM_LOGO = i18n._("from branding.logo")
SITE_MARK_UPLOAD = i18n._("Upload an image")
SITE_MARK_REPLACE = i18n._("Replace")
#: the picker's VISIBLE trigger — a `label` bound to the clipped native input — and the line the
#: control states a chosen file with. The browser's own "no file chosen" is never drawn: the input
#: is clipped, not removed, so it stays focusable and the form still posts it.
SITE_MARK_CHOOSE = i18n._("Choose an image")
SITE_MARK_CHOSEN = i18n._("Selected: %(name)s")
#: what the upload accepts, and what it does with it — the page's own ceiling, said with its number.
SITE_MARK_ACCEPT = i18n._("PNG, JPEG or WebP, up to %(limit)s; the upload re-encodes it to the four "
                          "sizes below.")
SITE_MARK_FILES = i18n._("Written beside the site's settings: %(files)s.")
SITE_MARK_CONFIRM = i18n._("Remove the uploaded mark? The site falls back to branding.logo.")
SITE_MARK_SIZE_ALT = i18n._("The uploaded mark, %(size)s pixels")
#: the hero's placement row: the two sides the mark may sit on, and the sentence the row prints.
SITE_MARK_SIDE = i18n._("Placement")
SITE_MARK_SIDE_Q = i18n._("Which side of the hero's headline the mark sits on. The bar's small "
                          "mark never moves.")
SITE_MARK_SIDE_LEFT = i18n._("Left")
SITE_MARK_SIDE_RIGHT = i18n._("Right")
SITE_MARK_SIDE_NOTE = i18n._("The hero draws the mark at 128 px on a desktop, 96 on a tablet and "
                             "72 on a phone, where it takes a row of its own.")

# ------------------------------------------- the three list blocks (§C): the drawing and the rows
#: the block heading the two closed sets share. `branding.footer_links` keeps its place in the
#: branding card, beside the footer it draws; these two are the site's own menu and tile strip.
SITE_MENU_TILES = i18n._("The menu and the tiles")
SITE_NAV_WHAT = i18n._("The links in the site's top bar, left to right, after the brand.")
SITE_NAV_NEED = i18n._(
    "An entry is one of the pages the build serves — %(ids)s. There is nothing to add: the list is "
    "the set, so the block sets the order and which of them appear.")
SITE_NAV_DRAW = i18n._("the site's top bar")
SITE_NAV_OFF = i18n._(
    "Every page switched off: the bar carries the brand alone. The site still serves the overview "
    "at its own address, so a visitor is never stranded.")
SITE_TILES_WHAT = i18n._("The strip of figures under the hero, before the %(count)s sections.")
SITE_TILES_NEED = i18n._(
    "An entry is one of the figures the build can compute — %(ids)s. There is nothing to add "
    "either; the block sets the order and which appear. A tile switched off closes its gap instead "
    "of leaving a hole.")
SITE_TILES_DRAW = i18n._("the overview's tile strip")
SITE_TILES_OFF = i18n._(
    "Every tile switched off: the strip is dropped, and the first section follows the hero directly.")
SITE_LINKS_WHAT = i18n._("The anchors in the footer strip, right of the attribution.")
SITE_LINKS_NEED = i18n._(
    "Each entry needs a label and an address — a full http(s)://, a mailto:, or a path on this "
    "installation.")
SITE_LINKS_EMPTY = i18n._(
    "No footer links yet. The footer shows the attribution alone — a Discord invite or the "
    "mission board belongs here.")
SITE_LINKS_ADD = i18n._("+ Add a link")
SITE_LINKS_ADD_NOTE = i18n._("a label, then an address")
SITE_SHOWN = i18n._("shown")
SITE_HIDDEN = i18n._("hidden")
SITE_ENTRY_LABEL = i18n._("Label")
SITE_ENTRY_ADDRESS_HINT = i18n._("https://…, mailto:… or /path")
SITE_REMOVE = i18n._("Remove")

#: the menu's own labels, taken from the module that draws the bar: the card prints the same words the
#: site's menu prints, and there is no second table of page names.
NAV_LABEL_SOURCE = site_pages.NAV_LABELS

#: the display name of every tile id, keyed by :data:`services.webservice.sites.TILE_IDS`. A LABEL
#: table only; a test pins its keys against the model's tuple.
TILE_LABELS: dict[str, str] = {
    "active_week": i18n._("Active this week"),
    "online_now": i18n._("Flying now"),
    "playtime_total": i18n._("Hours in the air"),
    "avg_session": i18n._("Average session"),
    "peak_hour": i18n._("Busiest hour"),
}


def _check_tile_labels() -> None:
    """Refuse to serve a label table that misses a tile id or names one the model does not carry."""
    if set(TILE_LABELS) != set(sites.TILE_IDS):
        raise RuntimeError(f"the tile labels are keyed by {sorted(TILE_LABELS)}, "
                           f"the model names {sorted(sites.TILE_IDS)}")


_check_tile_labels()


def list_rows(stored: tuple[str, ...], vocabulary: tuple[str, ...],
              labels: Mapping[str, str]) -> list[dict]:
    """One row per id of *vocabulary*: the SHOWN ones in the site's own order, then the rest.

    The stored sequence holds the ids that are shown, so it holds no position for a hidden one — a
    switched-off row is therefore drawn after the shown rows, in vocabulary order, which is the same
    place a reload puts it. Every id appears exactly once (a duplicate in the file is drawn once) and
    a row's ``on`` is whether the site states it at all.
    """
    shown = list(dict.fromkeys(ident for ident in stored if ident in vocabulary))
    order = shown + [ident for ident in vocabulary if ident not in shown]
    return [{"id": ident, "label": labels.get(ident, ident), "on": ident in shown} for ident in order]


def _closed_set(request: Request, token: str, leaf: str, rows: list[dict], *, kind: str,
                key: str, what: str, need: str, caption: str, off: str,
                drives: str = "", order: Mapping[str, str] | None = None, **params) -> dict:
    """ONE closed-set block: its sentences, its drawing and its rows, in ONE order.

    The rows post ONE checkbox each, named ``f.sites.<token>.<leaf>`` with the id as the value — the
    metric idiom, so the DOM order IS the posted order. One empty hidden value of the same name
    states the block, so switching every row off posts an empty list instead of nothing at all.

    ``drives`` names the element in the site's own document the block's ORDER writes (the bar's nav),
    and ``order`` is that block's id → address map, rendered once so the page's script can move the
    document's own anchors as the rows are dragged — no second copy of the nav's addresses in JS.
    """
    return {"name": leaf, "kind": kind, "key": key, "field": f"f.sites.{token}.{leaf}", "rows": rows,
            "drives": drives,
            "order": json.dumps(order, ensure_ascii=False) if order else "",
            "what": i18n.translate(request, what, **params),
            "need": i18n.translate(request, need, **params),
            "caption": i18n.translate(request, caption),
            "off": i18n.translate(request, off)}


# ---------------------------------------------------- each card's own preview, and the page's order
#: THE PREVIEW IS THE CARD'S, not a block's: one `<iframe>` of that site's own document, in the panel
#: beside the card's editing column. The panel states once what the box is, what feeds it, and the one
#: block that feeds it nothing.
SITE_PANEL = i18n._("The site")
SITE_PANEL_DOC = i18n._("its own document · %(path)s")
SITE_PANEL_OPEN = i18n._("Open ↗")
SITE_PANEL_CAP = i18n._("The site's own document at the box's width — bar, hero, footer. Not a "
                        "drawing: the page's blocks write into this one frame, and it is the only "
                        "preview here.")
SITE_PANEL_WRITES = i18n._("Writes into it: %(branding)s — the mark, the bar's name, the hero's "
                           "headline and paragraph · %(palette)s — every colour in it · %(links)s — "
                           "the footer's anchors, beside the attribution · %(menu)s — the bar's nav, "
                           "in this order.")
SITE_PANEL_NONE = i18n._("Character changes nothing here: it decides the bands below the hero, "
                         "which the box's height does not reach.")
SITE_PREVIEW_TITLE = i18n._("The site's top bar, hero and footer, drawn from the values beside it")
SITE_PREVIEW_OFF = i18n._("This site is switched off, so its own page is not served — the box comes "
                          "back with it.")
#: THE CHIP BESIDE THE BOX WHEN THE SITE HAS NO ADDRESS YET — the drawing comes from the file, not
#: from a document the site serves.
SITE_PANEL_MOCK = i18n._("drawn from your file")
#: THE SENTENCE UNDER THE MOCK. Addresses are registered when the bot starts, so a site this file
#: just gained — or one whose address changed — is drawn from the file until then, and the drawing
#: cannot say that by itself.
SITE_PREVIEW_MOCK = i18n._("This site has no address of its own yet — this is what it will look "
                           "like. It answers once the bot restarts.")
#: the box's own caveat: what it draws follows the form BEFORE Save, and nothing here is written.
SITE_PANEL_DRAFT = i18n._("A draft: the box follows the values in the form, and nothing is written "
                          "until you save.")
#: the block headings the page's own order introduces, and the count chip the Branding block wears.
SITE_PALETTE = i18n._("Palette")
SITE_LINKS = i18n._("Footer links")
SITE_EXPERT_MODE = i18n._("Expert mode")
SITE_FIELDS = i18n._("fields")
#: THE SITE ITSELF: the one structural key that changes no pixel — the address and the switch are on
#: the card's own header row.
SITE_IDENTITY = i18n._("The site itself")
SITE_IDENTITY_NOTE = i18n._("not part of the look")
#: the Character block's honest last line: what a character decides, and what it does NOT draw.
SITE_CHARACTER_BOX = i18n._("Nothing in the box changes: the character decides the bands below the "
                            "hero, and which figures the leaderboard ranks. The box draws the bar, "
                            "the hero and the footer — and no band.")


def declare() -> None:
    """Declare the four site capabilities. Idempotent for the same roles and grants.

    ``site.view``/``site.write`` carry the manager-scope grant because a group admin edits their own
    site's content; the handler narrows the grant to one site. ``site.structure``/``site.expert`` are
    the Admin's alone: a structural key moves the console's route table, and expert CSS is published
    on the installation's own origin.
    """
    permissions.declare_capability(SITE_VIEW_CAPABILITY, SITE_ROLES, scope_grants=True)
    permissions.declare_capability(SITE_WRITE_CAPABILITY, SITE_ROLES, scope_grants=True)
    permissions.declare_capability(SITE_STRUCTURE_CAPABILITY, SITE_ROLES)
    permissions.declare_capability(SITE_EXPERT_CAPABILITY, SITE_ROLES)


declare()


@dataclass(frozen=True)
class SiteAccess:
    """What ONE identity may do on this page, read once per request from the one predicate."""

    view: bool
    write: bool
    structure: bool
    expert: bool


def site_access(request: Request) -> SiteAccess:
    """THE capability decision for this request: view, write, structure and the expert sheet."""
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    return SiteAccess(
        view=permissions.allows(SITE_VIEW_CAPABILITY, roles, manager=manager),
        write=permissions.allows(SITE_WRITE_CAPABILITY, roles, manager=manager),
        structure=permissions.allows(SITE_STRUCTURE_CAPABILITY, roles, manager=manager),
        expert=permissions.allows(SITE_EXPERT_CAPABILITY, roles, manager=manager),
    )


def site_in_scope(request: Request, site: "sites.Site") -> bool:
    """Whether *site* is inside this request's scope: the narrow rule a group admin is held to.

    An unrestricted scope (an Admin, break-glass) sees every site. A restricted one sees a site only
    when the site declares a ``group_role`` its tokens hold: the root site takes no token, so a
    restricted identity never owns it.
    """
    scope = permissions.scope_for(request)
    if scope.unrestricted:
        return True
    return bool(site.group_role) and scope.allows((site.group_role,))


# ------------------------------------------------------------------------------ the form tree

def form_tree() -> configgen.Field:
    """The form tree for the ``sites:`` block, built from the shipped schema's own rule.

    The block lives inside the webservice service schema (``DEFAULT.sites``); it is lifted into a
    root of its own so the generated paths are ``sites.<key>.<leaf>`` — the document's own paths, and
    the names the form posts.
    """
    schema = configgen.load_schema(str(_SCHEMA_PATH))
    default = (schema.root.get("mapping") or {}).get("DEFAULT") or {}
    rule = (default.get("mapping") or {}).get("sites")
    if not isinstance(rule, dict):
        raise RuntimeError("the webservice schema carries no sites block")
    lifted = configgen.Schema(root={"type": "map", "nullable": True, "mapping": {"sites": rule}},
                              partials=schema.partials, included_files=schema.included_files,
                              primary=schema.primary)
    return configgen.describe(lifted)


def card_field(tree: configgen.Field, key: str) -> configgen.Field:
    """ONE site's sub-form: the schema's per-site element with its placeholder key made concrete.

    The container's element carries the ``*`` token for the site key, which is the LAST token of its
    own path (``sites.*``) and the first of every leaf below it. Only that ONE token is replaced, so a
    nested ``*`` (a sequence's element, a deeper regex map) keeps its own shape.
    """
    sites_field = next((child for child in tree.children if child.key == "sites"), None)
    container = next((child for child in (sites_field.children if sites_field else ())
                      if child.kind == configgen.KIND_REGEX), None)
    if container is None or container.element is None:  # pragma: no cover - the schema ships one
        raise RuntimeError("the sites block carries no per-site mapping")

    def one(node: configgen.Field) -> configgen.Field:
        tokens = list(node.path)
        if "*" in tokens:
            tokens[tokens.index("*")] = key
        return replace(node,
                       path=tuple(tokens),
                       children=tuple(one(child) for child in node.children),
                       element=one(node.element) if node.element is not None else None)

    return one(container.element)


def _leaf(field: configgen.Field, *path: str) -> configgen.Field | None:
    """The field at *path* inside *field*, or ``None`` when the schema does not carry it."""
    return configgen.find_field(field, tuple(path))


# ------------------------------------------------------------------------- the source of truth

def _legacy_block(request: Request) -> Mapping | None:
    """The ``sites:`` block of the legacy home, read only when the site file does not exist.

    The page renders and saves ``config/site.yaml``; until that file exists the block the console
    installs from is the one inside ``config/services/webservice.yaml``, so the operator's existing
    sites are the ones the page shows — and the first save writes them where the page owns them.
    """
    config_dir = settings_page.config_dir(request)
    if sites.site_file(config_dir).is_file():
        return None
    path = config_dir / "services" / "webservice.yaml"
    if not path.is_file():
        return None
    try:
        from core.utils.yaml_helper import new_yaml

        document = new_yaml().load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - an unreadable legacy file states no sites
        log.debug("The legacy sites block could not be read from %s", path, exc_info=True)
        return None
    if not isinstance(document, Mapping):
        return None
    for block in (document.get("DEFAULT"), document):
        if isinstance(block, Mapping) and isinstance(block.get("sites"), Mapping):
            return block["sites"]
    return None


def site_catalog(request: Request) -> "sites.SiteCatalog":
    """The catalogue the page renders from: the site file first, the legacy block until it exists."""
    config_dir = settings_page.config_dir(request)
    legacy = _legacy_block(request)
    return sites.load_sites({"sites": legacy} if legacy is not None else {}, config_dir=config_dir)


def _document(request: Request) -> tuple[Any, Path, str]:
    """The document the cards read: the site file's own, or the legacy block until it exists.

    Returns the document, the file a save writes and the refusal sentence when the source cannot be
    read — a page that cannot see the file says so instead of rendering empty cards.
    """
    config_dir = settings_page.config_dir(request)
    path = sites.site_file(config_dir)
    if path.is_file():
        try:
            return configgen.read_document(path), path, ""
        except configwrite.ConfigWriteError as ex:
            return None, path, str(ex)
    legacy = _legacy_block(request)
    if legacy is None:
        return None, path, ""
    from ruamel.yaml.comments import CommentedMap

    document = CommentedMap()
    document["sites"] = legacy
    return document, path, ""


# ------------------------------------------------------------------------------- the page model

def _values(document, field: configgen.Field) -> dict:
    """The current value of every leaf of *field*, keyed by the field's own ``name``."""
    return {leaf.name: configgen.value_at(document, leaf.path)
            for leaf in configgen.leaves(field)}


def _row_context(document, field: configgen.Field) -> tuple[dict, dict]:
    """The rows and element leaves of the sequence controls inside *field*."""
    rows: dict[str, list] = {}
    elements: dict[str, list] = {}
    for child in configgen.flatten(field):
        if child.kind != configgen.KIND_SEQ:
            continue
        rows[child.name] = configgen.rows_for(child, configgen.value_at(document, child.path))
        elements[child.name] = configgen.form_leaves(child)
    return rows, elements


def _field_list(field: configgen.Field, paths: tuple[tuple[str, ...], ...]) -> list:
    """The fields at *paths*, skipping any the schema does not carry."""
    found = []
    for path in paths:
        leaf = _leaf(field, *path)
        if leaf is not None:
            found.append(leaf)
    return found


#: the tokens a preset tile previews its palette with: the ground, the panel, the accent and the
#: text colour of the sheet it names.
_PREVIEW_TOKENS: tuple[str, ...] = ("--ground", "--panel", "--brass", "--fg")


def _declared_values(text: str) -> dict[str, str]:
    """Every ``--token: value;`` line a token sheet states, keyed by the token's name.

    ONE reader for every sheet this page reads — a shipped preset's file and a sheet the composer
    answered alike — so a swatch and the value a tile paints can never come from two parses.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("--") or ":" not in stripped:
            continue
        name, _, raw = stripped.partition(":")
        values[name.strip()] = raw.rstrip(";").strip()
    return values


def _preset_text(filename: str) -> str:
    """A shipped preset sheet's own text; an empty string when it cannot be read."""
    try:
        return (theme_css.PRESET_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        log.debug("The preset sheet %s could not be read", filename, exc_info=True)
        return ""


def preset_palette(filename: str) -> list[str]:
    """The four colours a preset's own sheet declares, in :data:`_PREVIEW_TOKENS`' order.

    Read from the shipped file, so the tile previews the sheet the site would actually be served.
    A token the sheet does not carry answers an empty string and the tile simply draws no swatch.
    """
    values = _declared_values(_preset_text(filename))
    return [values.get(token, "") for token in _PREVIEW_TOKENS]


def preset_paint(preset: theme_css.Preset) -> dict:
    """What a press of *preset*'s tile paints: its sheet's full declaration map, and its scheme.

    The map is the file's own lines, so the frame is repainted with the sheet it would be served —
    tokens the gallery's four swatches do not name included (the display faces, the medal washes).
    """
    return {"scheme": preset.scheme,
            "declarations": _declared_values(_preset_text(preset.filename))}


class ExpertSheet(NamedTuple):
    """The expert block's own text: the transcription, and what it counts.

    ``text`` is the body of a shipped preset sheet WITHOUT its ``:root`` wrapper and its opening
    comment — the validator refuses both, so a sheet that carried them would be refused at line 1.
    ``declarations`` and ``lines`` are read from that text, never stated twice.
    """

    text: str
    declarations: int
    lines: int

    @property
    def characters(self) -> int:
        """The transcription's length in characters — the number the card prints beside the bound."""
        return len(self.text)


def expert_sheet(preset_id: str | None = None) -> ExpertSheet:
    """A shipped preset's declarations as the expert block takes them: a transcription, not the file.

    Each declaration is re-spelled ``--name: value;`` (one space after the colon, as the design
    states), and the sheet's blank lines are kept as the group separators they are in the file. An
    unreadable preset answers an empty sheet rather than raising into the render.
    """
    preset = theme_css.PRESET_BY_ID.get(str(preset_id or theme_css.DEFAULT_PRESET))
    filename = preset.filename if preset is not None else theme_css.PRESET_BY_ID[
        theme_css.DEFAULT_PRESET].filename
    try:
        raw = (theme_css.PRESET_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        log.debug("The preset sheet %s could not be read", filename, exc_info=True)
        return ExpertSheet(text="", declarations=0, lines=0)
    lines: list[str] = []
    body = False
    for line in raw.splitlines():
        stripped = line.strip()
        if not body:
            body = stripped.endswith("{")
            continue
        if stripped == "}":
            break
        if not stripped:
            lines.append("")
            continue
        name, _, value = stripped.partition(":")
        lines.append(f"{name.strip()}: {value.strip().rstrip(';')};")
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines)
    return ExpertSheet(text=text, declarations=sum(1 for line in lines if line),
                       lines=len(lines))


#: the wizard's six steps, in the order the panel draws them: the id the row is keyed on, the step's
#: own name, the declarations it emits, and the pair the step's chip measures. One table, so the rows
#: the template draws and the pairs the rule tests cannot disagree.
WIZARD_STEPS: tuple[tuple[str, str, tuple[str, ...], tuple[str, str]], ...] = (
    ("ground", SITE_WIZARD_STEP_GROUND, ("--ground", "--stage"), ("--brass", "--ground")),
    ("panel", SITE_WIZARD_STEP_PANEL, ("--panel", "--panel-2", "--band"), ("--fg", "--panel")),
    ("line", SITE_WIZARD_STEP_LINE, ("--line", "--line-2"), ("--line-2", "--panel")),
    ("accent", SITE_WIZARD_STEP_ACCENT, ("--brass", "--brass-hi", "--brass-dim"),
     ("--brass", "--panel")),
    ("text", SITE_WIZARD_STEP_TEXT, ("--fg",), ("--fg", "--ground")),
    ("steps", SITE_WIZARD_STEP_STEPS, ("--fg-2", "--fg-3"), ("--fg-3", "--ground")),
)

#: the colour the wizard's third starting point opens on: the design's own worked example
WIZARD_BASE = "#4a7fd1"


def _composed_colours(theme: Any, config_dir=None) -> list[str]:
    """The four colours the site is served with today, read from the sheet the site resolves to.

    The wizard's "the palette this site wears now" tile previews what a visitor loads, so it is read
    through the same composer the public route uses — a preset, an uploaded sheet or an expert block,
    whichever this site names. A sheet the composer cannot answer falls back to the default.
    """
    try:
        text = theme_css.compose(theme, config_dir)
    except (ValueError, OSError) as ex:
        log.debug("The palette of the site's own sheet could not be read: %s", ex)
        return preset_palette(theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET].filename)
    values = _declared_values(text)
    return [values.get(token, "") for token in _PREVIEW_TOKENS]


def _declarations(text: str) -> int:
    """The number of non-empty lines a sheet's text carries — what the block counts as declarations."""
    return sum(1 for line in (text or "").splitlines() if line.strip())


def palette_state(request: Request, site: "sites.Site") -> dict:
    """WHICH OF THE THREE palettes this site names: the words, the key in force, its value if any.

    A site names a shipped preset, an uploaded sheet or an expert sheet — at most one — and the block
    says which one, once. A preset's NAME is not repeated here: the gallery's tile carries it.
    """
    stored = (site.theme.expert or "").strip()
    if stored:
        return {"id": "expert", "words": i18n.translate(request, SITE_PALETTE_STATE_EXPERT),
                "key": "theme.expert",
                "value": i18n.translate(request, SITE_PALETTE_EXPERT_VALUE,
                                        declarations=_declarations(stored))}
    sheet = (getattr(site.theme, "sheet", None) or "").strip()
    if sheet:
        return {"id": "sheet", "words": i18n.translate(request, SITE_PALETTE_STATE_SHEET),
                "key": "theme.sheet", "value": sheet}
    return {"id": "preset", "words": i18n.translate(request, SITE_PALETTE_STATE_PRESET),
            "key": "theme.preset", "value": ""}


def palette_payload(request: Request) -> dict:
    """The three states' words and the head's say-lines, for the controls that repaint them.

    One payload, rendered once: the head states the stored truth, and a press that changes which
    palette the site names (the handoff) reads the same table instead of keeping a second copy.
    """
    return {
        "states": {
            "preset": {"words": i18n.translate(request, SITE_PALETTE_STATE_PRESET),
                       "key": "theme.preset", "value": ""},
            "sheet": {"words": i18n.translate(request, SITE_PALETTE_STATE_SHEET),
                      "key": "theme.sheet", "value": ""},
            "expert": {"words": i18n.translate(request, SITE_PALETTE_STATE_EXPERT),
                       "key": "theme.expert",
                       "value": i18n.translate(request, SITE_PALETTE_EXPERT_VALUE,
                                               declarations="%(declarations)s")},
        },
        "say": {"rest": i18n.translate(request, SITE_PALETTE_SAY),
                "open": i18n.translate(request, SITE_PALETTE_SAY_OPEN)},
    }


def wizard_payload(request: Request, site: "sites.Site", sheet: ExpertSheet) -> dict:
    """The palette wizard's table, its starting points and every sentence its script prints.

    One payload, rendered once: the script fills the numbers (hexes, ratios, moves) into slots the
    template draws, and every word on the screen is translated here for this request. Nothing in it
    decides anything the save does not: the sheet it hands over is the sheet's own grammar.
    """
    shipped = preset_palette(theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET].filename)
    stored = bool((site.theme.expert or "").strip())
    config_dir = settings_page.config_dir(request)
    tiles = [
        {"id": "preset", "name": i18n.translate(request, SITE_WIZARD_START_PRESET),
         "key": i18n.translate(request, SITE_WIZARD_START_PRESET_KEY),
         "say": i18n.translate(request, SITE_WIZARD_START_PRESET_SAY,
                               file=theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET].filename),
         "swatches": shipped, "on": not stored},
        {"id": "now", "name": i18n.translate(request, SITE_WIZARD_START_NOW),
         "key": i18n.translate(request, SITE_WIZARD_START_NOW_KEY),
         "say": i18n.translate(request, SITE_WIZARD_START_NOW_SAY),
         "swatches": _composed_colours(site.theme, config_dir), "on": False},
        {"id": "one", "name": i18n.translate(request, SITE_WIZARD_START_ONE),
         "key": i18n.translate(request, SITE_WIZARD_START_ONE_KEY),
         "say": i18n.translate(request, SITE_WIZARD_START_ONE_SAY),
         "swatches": [WIZARD_BASE], "on": not stored},
    ]
    if stored:
        tiles.append({"id": "stored", "name": i18n.translate(request, SITE_WIZARD_START_STORED),
                      "key": i18n.translate(request, SITE_WIZARD_START_STORED_KEY),
                      "say": i18n.translate(request, SITE_WIZARD_START_STORED_SAY),
                      "swatches": shipped, "on": True})
    return {
        "step1": i18n.translate(request, SITE_WIZARD_STEP1),
        "step2": i18n.translate(request, SITE_WIZARD_STEP2),
        "lead": i18n.translate(request, SITE_WIZARD_LEAD),
        "rule": i18n.translate(request, SITE_WIZARD_RULE),
        "foot": i18n.translate(request, SITE_WIZARD_FOOT),
        "consts": i18n.translate(request, SITE_WIZARD_CONSTS),
        "now_missing": i18n.translate(request, SITE_WIZARD_NOW_MISSING),
        "now_say": i18n.translate(request, SITE_WIZARD_NOW_SAY),
        "handed": i18n.translate(request, SITE_WIZARD_HANDED),
        "base": WIZARD_BASE,
        "scheme": theme_css.scheme(site.theme),
        "start": "stored" if stored else "preset",
        "prefill": sheet.text,
        "tiles": tiles,
        "schemes": [("dark", i18n.translate(request, SITE_WIZARD_DARK)),
                    ("light", i18n.translate(request, SITE_WIZARD_LIGHT))],
        "steps": [{"id": ident, "no": f"{index:02d}",
                   "name": i18n.translate(request, name),
                   "tokens": " · ".join(tokens)}
                  for index, (ident, name, tokens, _pair) in enumerate(WIZARD_STEPS, start=1)],
        # what the script prints, and the holes it fills
        "words": {
            "step1": i18n.translate(request, SITE_WIZARD_STEP1),
            "step2": i18n.translate(request, SITE_WIZARD_STEP2),
            "adjust": i18n.translate(request, SITE_WIZARD_ADJUST),
            "used": i18n.translate(request, SITE_WIZARD_USED),
            "chroma": i18n.translate(request, SITE_WIZARD_CHROMA),
            "moved": i18n.translate(request, SITE_WIZARD_MOVED),
            "stuck": i18n.translate(request, SITE_WIZARD_STUCK),
            "accepted": i18n.translate(request, SITE_WIZARD_ACCEPTED),
            "use_this": i18n.translate(request, SITE_WIZARD_USE_THIS),
            "consts": i18n.translate(request, SITE_WIZARD_CONSTS),
            "reported": i18n.translate(request, SITE_WIZARD_REPORTED),
            "own": i18n.translate(request, SITE_WIZARD_OWN),
            "accent": i18n.translate(request, SITE_WIZARD_ACCENT),
            "follows": i18n.translate(request, SITE_WIZARD_FOLLOWS),
            "moved_l": i18n.translate(request, SITE_WIZARD_MOVED_L),
            "step_editing": i18n.translate(request, SITE_WIZARD_STEP_EDITING),
            "under_aa": i18n.translate(request, SITE_WIZARD_UNDER_AA),
            "edit_under": i18n.translate(request, SITE_WIZARD_EDIT_UNDER),
            "edit_keep": i18n.translate(request, SITE_WIZARD_EDIT_KEEP),
            "edit_stuck": i18n.translate(request, SITE_WIZARD_EDIT_STUCK),
            "edit_foot": i18n.translate(request, SITE_WIZARD_EDIT_FOOT),
        },
    }


class BrandingRow(NamedTuple):
    """ONE branding leaf, as the card's row states it.

    ``selector`` is the element in the site's own document the value writes, in the site's own class
    names; the row carries it as ``data-sc-drives`` and ``static/site-config.js`` resolves THAT — so
    the binding keeps no second copy of the selectors and a renamed element fails a test instead of
    silently writing nowhere. ``""`` means no shipped page draws the value. ``kind`` says how the
    value is written (``text``, ``links`` rebuilt from the entry rows).
    """

    path: tuple[str, ...]
    selector: str
    kind: str
    line: str
    flag: str = ""


#: THE FIELD → ELEMENT TABLE, in the order the card draws it. One line per row naming the element the
#: value changes — the sentence the operator was missing. It is also the list of leaves the card
#: renders (:data:`BRANDING_PATHS`), so a row and its line can never disagree.
BRANDING_ROWS: tuple[BrandingRow, ...] = (
    BrandingRow(
        ("branding", "title"), ".brand b", "text",
        i18n._("The brand line in the site's top bar, on every page — and the tab's title.")),
    BrandingRow(
        ("branding", "community_name"), ".hero h1 em", "text",
        i18n._("The hero's headline: the name on its own line in the accent colour. Empty, the hero "
               "falls back to the Discord guild's name, then the title.")),
    BrandingRow(
        ("branding", "welcome"), "", "",
        i18n._("The hero's paragraph under the headline. %(community)s is filled with the "
               "community's name; empty, the hero prints its shipped sentence.")),
    BrandingRow(
        ("branding", "footer_links"), ".foot .in", "links",
        i18n._("The anchors in the footer, right of the attribution — one per entry: a label and "
               "an address.")),
    BrandingRow(
        ("branding", "logo"), "", "",
        i18n._("The image in the top bar, left of the title — served from the saved file, so it "
               "changes on Save, not as you type.")),
)

#: the leaves the card renders, in the table's order.
BRANDING_PATHS: tuple[tuple[str, ...], ...] = tuple(row.path for row in BRANDING_ROWS)

#: the generated leaves of one card, grouped by the block they render in. ``enabled``, ``character``,
#: ``sections``, ``metrics``, ``nav``, ``tiles``, ``branding.footer_links``, ``theme.preset`` and
#: ``theme.expert`` are NOT here: they are hand-rendered so their default is visible.
_PAGES_PATHS: tuple[tuple[str, ...], ...] = (("lang",),)
#: the deprecated ``theme.light`` alias keeps its generated row; ``theme.preset`` and
#: ``theme.expert`` are hand-rendered by the palette block.
_LIGHT_PATHS: tuple[tuple[str, ...], ...] = (("theme", "light"),)


def branding_rows(field: configgen.Field) -> list[dict]:
    """The branding block's rows: the leaf, the element it drives and the line the row prints.

    Built from :data:`BRANDING_ROWS`'s ONE table, so the row's control, its sentence and the selector
    the binding resolves cannot be kept in three places. A leaf the schema does not carry is dropped
    rather than rendered as a row with nothing behind it.
    """
    rows: list[dict] = []
    for entry in BRANDING_ROWS:
        leaf = _leaf(field, *entry.path)
        if leaf is None:
            continue
        rows.append({"leaf": ".".join(entry.path), "field": leaf, "selector": entry.selector,
                     "kind": entry.kind, "line": entry.line, "flag": entry.flag})
    return rows


def _say(request: Request, message: str) -> str:
    """*message* in the request's language, or ``""`` for ``""`` — ``gettext("")`` is the header."""
    return i18n.translate(request, message) if message else ""


def _count(value: int) -> str:
    """*value* with a space as its thousands separator — the way the card reads a bound (``8 192``)."""
    return f"{value:,}".replace(",", " ")


def character_payload(request: Request) -> dict:
    """The character table as the card's script reads it: labels, sentences and the two totals.

    Every sentence goes through :func:`services.webservice.i18n.translate`, because the payload is
    text the page will print — a press repaints the card without a request, so the words have to be
    translated when the page is rendered.
    """
    return {
        "labels": {ident: i18n.translate(request, CHARACTER_LABELS[ident])
                   for ident in sites.CHARACTERS},
        "count": i18n.translate(request, SITE_CHARACTER_COUNT),
        "changed": i18n.translate(request, SITE_CHARACTER_CHANGED),
        "sectionsTotal": len(sites.SECTION_IDS),
        "metricsTotal": len(site_reads.LEADERBOARD_METRICS),
        "sets": {ident: {"sections": list(spec.sections), "metrics": list(spec.metrics),
                         "what": _say(request, spec.what),
                         "whySections": _say(request, spec.why_sections),
                         "whyMetrics": _say(request, spec.why_metrics)}
                 for ident, spec in CHARACTER_SETS.items()},
    }


def _footer_links(request: Request, site: "sites.Site", token: str) -> dict:
    """The one block that takes entries: its two lines, its rows and the empty state's sentence.

    The rows are hand-rendered from the site's own entries, because the shape the design states —
    a 150 px label, an address across the rest and the entry's `✕` — is not the generated
    sequence's stacked sub-fields. They keep the generated contract (`data-cfg-seq` + `data-cfg-row`
    + `__n` + `data-cfg-template`), so `static/configgen.js` still adds, removes and renumbers them
    and the save still reads `…footer_links.<index>.label|href`.
    """
    prefix = f"f.sites.{token}.branding.footer_links"
    return {
        "leaf": "branding.footer_links",
        # the element the entries write, read off the ONE field→element table: the block is drawn
        # outside the branding rows loop, so it carries the row's own selector rather than a copy
        "selector": next(row.selector for row in BRANDING_ROWS if row.kind == "links"),
        "name": prefix,
        "count": prefix + ".__n",
        "seq": f"sites.{token}.branding.footer_links",
        "rows": [{"label": link.label, "href": link.href} for link in site.branding.footer_links],
        "what": i18n.translate(request, SITE_LINKS_WHAT),
        "need": i18n.translate(request, SITE_LINKS_NEED),
        "empty": i18n.translate(request, SITE_LINKS_EMPTY),
        "add": i18n.translate(request, SITE_LINKS_ADD),
        "note": i18n.translate(request, SITE_LINKS_ADD_NOTE),
        "entry_label": i18n.translate(request, SITE_ENTRY_LABEL),
    }


def mark_stamp(site: "sites.Site", config_dir) -> str:
    """The version the box's address carries: it changes exactly when the box's content does.

    The card's frame points at the site's OWN preview document, and that document's mark and palette
    are whatever the site resolves — neither address carries a version of its own, so after a write
    the page would ask the browser for the addresses it already holds and a cached copy could answer.
    The stamp is the uploaded mark's own modification time and, when the site names an uploaded
    sheet, that file's too (``0`` for the component the site does not have), so a new mark or a new
    sheet IS a new address and the box cannot outlive either file.
    """
    stamps: list[str] = []
    uploaded = site_pages.uploaded_mark(site, config_dir)
    if uploaded is not None:
        stamps.append(_mtime_stamp(uploaded))
    leaf = (getattr(site.theme, "sheet", None) or "").strip()
    if leaf:
        try:
            stamps.append(_mtime_stamp(sites.sheet_path(leaf, config_dir)))
        except ValueError:
            stamps.append("0")
    return "-".join(stamps) if stamps else "0"


def _mtime_stamp(path: Path) -> str:
    """*path*'s modification time in nanoseconds as a string, ``0`` when it cannot be read."""
    try:
        return str(int(path.stat().st_mtime_ns))
    except OSError:
        return "0"


def _card(request: Request, tree: configgen.Field, site: "sites.Site", document,
          access: SiteAccess, config_dir, installed: frozenset) -> dict:
    """ONE site's card: its generated leaves, its hand-rendered groups and what may be edited.

    ``installed`` is the ``(key, prefix)`` set this process registered at startup. A site the file
    carries but the process does not is a card whose address nothing answers yet: its box is a MOCK
    drawn from the file instead of a frame at that address. The PREFIX is part of the pair on purpose
    — a site whose address was changed is installed under the old one, and a frame would point at a
    path that is gone.
    """
    live = (site.key, site.prefix) in installed
    field = card_field(tree, site.key)
    rows, elements = _row_context(document, field)
    token = configgen.encode_token(site.key)
    # §D: the shipped sheet this page transcribes, and whether the stored sheet IS that transcription
    shipped = theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET]
    sheet = expert_sheet(shipped.id)
    stored_sheet = site.theme.expert or ""
    expert_copy = not stored_sheet.strip() or stored_sheet.strip() == sheet.text.strip()
    # §5: which of the three palettes this site names, said once in the block's head — and the
    # table the controls that change it read back
    state = palette_state(request, site)
    wizard = wizard_payload(request, site, sheet)
    palette_say = (SITE_PALETTE_SAY_OPEN if stored_sheet.strip()
                   else SITE_PALETTE_SAY_SHEET if state["id"] == "sheet" else SITE_PALETTE_SAY)
    nav_labels = {ident: i18n.translate(request, NAV_LABEL_SOURCE[ident])
                  for ident in sites.NAV_PAGE_IDS}
    tile_labels = {ident: i18n.translate(request, TILE_LABELS[ident]) for ident in sites.TILE_IDS}
    brand_rows = branding_rows(field)
    return {
        "site": site,
        "key": site.key,
        "token": token,
        "prefix": site.prefix,
        # the brand the menu's drawing prints: the site's own title, else the site's key
        "brand_name": site.branding.title or site.key,
        "branding_rows": brand_rows,
        # the one chip the block wears: the number of rows it draws (footer links has its own block)
        "branding_count": len([row for row in brand_rows if row["kind"] != "links"]),
        # §E: the mark's slot — the ONE state the site is in, its four files and its controls
        "mark": mark_slot(request, site, token, config_dir),
        # the hero's placement: the stored side, the two pills and the row's own words
        "mark_side": site.branding.mark_side,
        "mark_sides": [(ident, i18n.translate(request, SITE_MARK_SIDE_LEFT if ident == "left"
                                              else SITE_MARK_SIDE_RIGHT))
                       for ident in sites.MARK_SIDES],
        "mark_side_label": i18n.translate(request, SITE_MARK_SIDE),
        "mark_side_q": i18n.translate(request, SITE_MARK_SIDE_Q),
        "mark_side_note": i18n.translate(request, SITE_MARK_SIDE_NOTE),
        # the box's own document: the SITE's address (its prefix in front of the preview path), never
        # a root-absolute path — a group site's console page must point at that site's preview. The
        # `v` is the mark's stamp, so an upload is a NEW address and no cached document can answer.
        # A site with no address yet gets the console's DEMO document instead (SITE_MOCK_PATH): the
        # box then shows what the file describes, drawn in place, rather than a frame at an address
        # nothing answers.
        "preview_url": (f"{site_pages.site_url(site, site_pages.CHROME_PREVIEW_PATH)}"
                        f"?v={mark_stamp(site, config_dir)}" if live
                        else f"{mount.url(SITE_MOCK_PATH)}?{SITE_OPEN_PARAM}={quote(site.key, safe='')}"
                             f"&v={mark_stamp(site, config_dir)}"),
        # does anything answer that address in THIS process? Only an installed (key, prefix) does;
        # when nothing does the box is a MOCK, drawn from the file, and it says so under itself.
        "live": live,
        "mock": not live,
        "pages_fields": _field_list(field, _PAGES_PATHS),
        # §C: the three list blocks. The two closed sets carry every id of the vocabulary and post
        # ONE checkbox per row under the block's own name; the footer keeps the generated sequence
        # contract, hand-drawn as the design's row.
        "footer_links": _footer_links(request, site, token),
        "closed_sets": [
            _closed_set(request, token, "nav",
                        list_rows(site.nav, sites.NAV_PAGE_IDS, nav_labels),
                        kind="bar", key=f"{site.key}.nav", what=SITE_NAV_WHAT, need=SITE_NAV_NEED,
                        caption=SITE_NAV_DRAW, off=SITE_NAV_OFF,
                        ids=", ".join(sites.NAV_PAGE_IDS),
                        # the bar's own menu is the one thing on this page whose ORDER the frame
                        # draws: the block carries the element and each id's address, so a drag
                        # repaints the document's nav before Save
                        drives=".top .nav",
                        order={ident: site_pages.site_url(site, site_pages.NAV_PATHS[ident])
                               for ident in sites.NAV_PAGE_IDS
                               if ident in site_pages.NAV_PATHS}),
            _closed_set(request, token, "tiles",
                        list_rows(site.tiles, sites.TILE_IDS, tile_labels),
                        kind="tiles", key=f"{site.key}.tiles", what=SITE_TILES_WHAT,
                        need=SITE_TILES_NEED, caption=SITE_TILES_DRAW, off=SITE_TILES_OFF,
                        ids=", ".join(sites.TILE_IDS), count=len(sites.SECTION_IDS)),
        ],
        "vals": _values(document, field),
        "rows": rows,
        "elements": elements,
        "customs": {}, "choices": {}, "sections": {}, "hidden": frozenset(), "picks": {},
        "removable": frozenset(),
        # the hand-rendered state, read from the EFFECTIVE model (an absent key means its default)
        "enabled": site.enabled,
        "character": site.character,
        "characters": [(ident, CHARACTER_LABELS.get(ident, ident),
                        CHARACTER_NOTES.get(ident, "")) for ident in sites.CHARACTERS],
        # the character card: the stored character's own words, its counts, and the table the script
        # repaints from — one home, rendered once per card
        "character_label": CHARACTER_LABELS.get(site.character, site.character),
        "character_what": i18n.translate(request, CHARACTER_SETS[site.character].what),
        "why_sections": _say(request, CHARACTER_SETS[site.character].why_sections),
        "why_metrics": _say(request, CHARACTER_SETS[site.character].why_metrics),
        # the card's own chip counts the SWITCHES as they stand, so it can never contradict the two
        # lists beside it — on load that is the stored set, after a press the character's set
        "character_count": i18n.translate(
            request, SITE_CHARACTER_COUNT, sections=len(site.sections),
            sectionsTotal=len(sites.SECTION_IDS), metrics=len(site.metrics),
            metricsTotal=len(site_reads.LEADERBOARD_METRICS)),
        "character_sets": json.dumps(character_payload(request), ensure_ascii=False),
        "section_switches": [(ident, SECTION_LABELS.get(ident, ident), ident in site.sections)
                             for ident in sites.SECTION_IDS],
        "metric_switches": [(ident, METRIC_MSGIDS.get(ident, ident), ident in site.metrics)
                            for ident in site_reads.LEADERBOARD_METRICS],
        "preset": site.theme.preset or "",
        # §5: the head's one statement of which of the three palettes this site names
        "palette_state": state,
        "palette_payload": json.dumps(palette_payload(request), ensure_ascii=False),
        "palette_say": i18n.translate(request, palette_say),
        # §5: the uploaded sheet's row — the file in force, its bounds and the picker's controls
        "sheet": sheet_slot(request, site),
        "light_fields": _field_list(field, _LIGHT_PATHS),
        "presets": [{"id": preset.id, "label": preset.label, "scheme": preset.scheme,
                     "swatches": preset_palette(preset.filename),
                     # the tile's own press payload: its sheet's declaration map and its scheme
                     "paint": json.dumps(preset_paint(preset), ensure_ascii=False)}
                    for preset in theme_css.PRESETS],
        # the shipped palette's tile names no preset: it carries the default preset's own sheet
        "shipped_paint": json.dumps(
            preset_paint(theme_css.PRESET_BY_ID[theme_css.DEFAULT_PRESET]), ensure_ascii=False),
        # §D: the expert sheet. `expert` is the CONTROL's own value — the stored sheet, empty when the
        # site states none, so an untouched card never writes a transcription of the shipped sheet.
        # The transcription is the editor's starting text and the restore target, carried beside it.
        "expert": site.theme.expert or "",
        "expert_badge": (
            i18n.translate(request, SITE_EXPERT_COPY_UNSTORED, name=shipped.filename)
            if not stored_sheet.strip()
            else i18n.translate(request, SITE_EXPERT_COPY, name=shipped.filename)
            if expert_copy else i18n.translate(request, SITE_EXPERT_EDITED)),
        "expert_copy": expert_copy,
        "expert_when": (
            i18n.translate(request, SITE_EXPERT_WHEN_UNSTORED,
                           declarations=sheet.declarations, lines=sheet.lines)
            if not stored_sheet.strip()
            else i18n.translate(request, SITE_EXPERT_WHEN,
                                declarations=sheet.declarations, lines=sheet.lines)),
        "expert_count": i18n.translate(request, SITE_EXPERT_LIMIT,
                                       allowed=_count(sites.MAX_EXPERT_LENGTH),
                                       used=_count(len(site.theme.expert or sheet.text))),
        # §4.1/§4.2: the box is ticked exactly when the site's palette IS an expert sheet, and the
        # closed state still STATES the sheet — a stored sheet is hidden on screen, never cleared
        "expert_ticked": bool(stored_sheet.strip()),
        "expert_stored": (i18n.translate(request, SITE_EXPERT_STORED,
                                         declarations=_declarations(stored_sheet),
                                         used=_count(len(stored_sheet)),
                                         allowed=_count(sites.MAX_EXPERT_LENGTH))
                          if stored_sheet.strip() else ""),
        "expert_none": i18n.translate(request, SITE_EXPERT_NONE),
        "expert_data": json.dumps({
            "prefill": sheet.text,
            "stored": bool(stored_sheet.strip()),
            "badgeCopy": i18n.translate(request, SITE_EXPERT_COPY, name=shipped.filename),
            "badgeUnstored": i18n.translate(request, SITE_EXPERT_COPY_UNSTORED,
                                            name=shipped.filename),
            "badgeEdited": i18n.translate(request, SITE_EXPERT_EDITED),
            "badgeWizard": i18n.translate(request, SITE_WIZARD_FROM_BADGE),
            "whenWizard": i18n.translate(request, SITE_WIZARD_WRITTEN),
            "when": i18n.translate(request, SITE_EXPERT_WHEN),
            "whenUnstored": i18n.translate(request, SITE_EXPERT_WHEN_UNSTORED),
            "count": i18n.translate(request, SITE_EXPERT_LIMIT),
            "accepted": i18n.translate(request, SITE_EXPERT_ACCEPTED),
            "replace": i18n.translate(request, SITE_EXPERT_REPLACE,
                                      declarations=sheet.declarations, name=shipped.id),
            "declarations": sheet.declarations,
            "limit": sites.MAX_EXPERT_LENGTH,
        }, ensure_ascii=False),
        "structure": access.structure,
        "expert_allowed": access.expert,
        # §2: the palette wizard — its table, its three starting points and the words it prints
        "wizard": wizard,
        "wizard_json": json.dumps(wizard, ensure_ascii=False),
        "wizard_group_hint": i18n.translate(request, SITE_WIZARD_GROUP_HINT),
        "write": access.write,
    }


def page_context(request: Request) -> dict:
    """Everything the template reads, in one value."""
    access = site_access(request)
    registrar = getattr(request.app.state, "webui_registrar", None)
    config_dir = settings_page.config_dir(request)
    document, path, error = _document(request)
    catalog = sites.SiteCatalog()
    if not error:
        try:
            catalog = site_catalog(request)
        except sites.SiteConfigError as ex:
            error = str(ex)
    tree = None
    cards: list[dict] = []
    if not error and document is not None:
        tree = form_tree()
        installed = frozenset((candidate.key, candidate.prefix)
                              for candidate in sites.sites_for(request.app).sites)
        cards = [_card(request, tree, site, document, access, config_dir, installed)
                 for site in catalog.sites
                 if site_in_scope(request, site)]
    return {
        "title": SITE_TITLE,
        "page_title": SITE_TITLE,
        "lead": SITE_LEAD,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, SITE_TITLE),
        "notice": actions_page.pop_notice(request),
        "nav_groups": dashboard_page.nav_groups(registrar,
                                                permissions.role_names_for(request),
                                                current=SITE_CONFIG_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "cards": cards,
        "open_site": readmodels.text(request.query_params.get(SITE_OPEN_PARAM, "")),
        "error": error,
        "empty": SITE_EMPTY,
        "file_name": path.name,
        "revision": configwrite.config_revision(path) if path.is_file() else "",
        "save_path": mount.url(SITE_CONFIG_SAVE_PATH),
        "draft_path": mount.url(SITE_CONFIG_DRAFT_PATH),
        # the two Admin-only blocks at the foot of the page. Each is rendered only for a request
        # that has the capability, and each route checks it again — a hidden block is not a guard.
        "add_path": mount.url(SITE_ADD_PATH),
        "yaml_path": mount.url(SITE_YAML_PATH),
        "yaml_validate_path": mount.url(SITE_YAML_VALIDATE_PATH),
        "structure_allowed": access.structure,
        "expert_file_allowed": access.expert,
        "roles": known_roles(config_dir) if access.structure else [],
        "add_key_field": SITE_ADD_KEY_FIELD,
        "add_role_field": SITE_ADD_ROLE_FIELD,
        "add_slug_field": SITE_ADD_SLUG_FIELD,
        "yaml_field": settings_page.SETTINGS_YAML_FIELD,
        "expert_field": settings_page.SETTINGS_EXPERT_FIELD,
        # the file ITSELF, byte for byte: the panel's read-only view is the file the save writes
        "yaml": _file_text(path),
        "draft_clear_field": SITE_DRAFT_CLEAR_FIELD,
        "validate_path": mount.url(SITE_CSS_VALIDATE_PATH),
        "upload_path": mount.url(SITE_AVATAR_UPLOAD_PATH),
        "remove_path": mount.url(SITE_AVATAR_REMOVE_PATH),
        "sheet_upload_path": mount.url(SITE_SHEET_UPLOAD_PATH),
        "sheet_remove_path": mount.url(SITE_SHEET_REMOVE_PATH),
        "avatar_field": SITE_AVATAR_FIELD,
        "sheet_file_field": SITE_SHEET_FILE_FIELD,
        "key_field": SITE_KEY_FIELD,
        "sheet_field": SITE_SHEET_FIELD,
        "revision_field": settings_page.SETTINGS_REVISION_FIELD,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        "labels": {
            "branding": SITE_BRANDING, "pages": SITE_PAGES,
            "palette": SITE_PALETTE, "links": SITE_LINKS, "fields": SITE_FIELDS,
            "expert_mode": SITE_EXPERT_MODE,
            "sections": SITE_SECTIONS, "metrics": SITE_METRICS,
            "character_behaviour": SITE_CHARACTER_BEHAVIOUR,
            "character_nojs": SITE_CHARACTER_NOJS,
            "character_box": SITE_CHARACTER_BOX,
            "presets": SITE_PRESETS, "use": SITE_PRESET_USE, "in_use": SITE_PRESET_IN_USE,
            "expert": SITE_EXPERT, "check": SITE_EXPERT_CHECK,
            "expert_load": SITE_EXPERT_LOAD, "expert_linked": SITE_EXPERT_LINKED,
            "expert_grammar": SITE_EXPERT_GRAMMAR, "expert_say": SITE_EXPERT_SAY,
            # §5: the palette block's own words — the two-way truth, the door, the rule
            "palette_door": SITE_PALETTE_DOOR,
            "palette_door_key": SITE_PALETTE_DOOR_KEY,
            "palette_rule_b": SITE_PALETTE_RULE_B, "palette_rule": SITE_PALETTE_RULE,
            "palette_foot": SITE_PALETTE_FOOT,
            "sheet": SITE_SHEET, "sheet_desc": SITE_SHEET_DESC,
            "expert_toggle": SITE_EXPERT_TOGGLE,
            # §2: the palette wizard's chrome, beside the sheet it writes
            "wizard": SITE_WIZARD, "wizard_close": SITE_WIZARD_CLOSE,
            "wizard_derive": SITE_WIZARD_DERIVE, "wizard_base": SITE_WIZARD_BASE,
            "wizard_scheme": SITE_WIZARD_SCHEME, "wizard_back": SITE_WIZARD_BACK,
            "wizard_again": SITE_WIZARD_AGAIN, "wizard_use": SITE_WIZARD_USE,
            "wizard_pick": SITE_WIZARD_PICK, "wizard_warn_b": SITE_WIZARD_WARN_B,
            "wizard_more": SITE_WIZARD_MORE,
            "wizard_group": SITE_WIZARD_GROUP, "wizard_group_flag": SITE_WIZARD_GROUP_FLAG,
            "wizard_group_ro": SITE_WIZARD_GROUP_RO,
            "needs_reload": SITE_NEEDS_RELOAD, "character": SITE_CHARACTER,
            "character_q": SITE_CHARACTER_Q,
            "enabled": SITE_ENABLED, "enabled_hint": SITE_ENABLED_HINT,
            "address": SITE_ADDRESS, "servers": SITE_SERVERS,
            "servers_hint": SITE_SERVERS_HINT, "root_servers": SITE_ROOT_SERVERS,
            "root_slug": SITE_ROOT_SLUG, "on": SITE_ON, "off": SITE_OFF,
            "read_failed": SITE_READ_FAILED,
            # the page's ONE preview: its title bar, its caption and the line naming its writers
            "panel": SITE_PANEL, "panel_open": SITE_PANEL_OPEN, "panel_cap": SITE_PANEL_CAP,
            "panel_doc": i18n.translate(request, SITE_PANEL_DOC,
                                        path=site_pages.CHROME_PREVIEW_PATH),
            "panel_writes": i18n.translate(request, SITE_PANEL_WRITES,
                                           branding=i18n.translate(request, SITE_BRANDING),
                                           palette=i18n.translate(request, SITE_PALETTE),
                                           links=i18n.translate(request, SITE_LINKS),
                                           menu=i18n.translate(request, SITE_MENU_TILES)),
            "panel_none": SITE_PANEL_NONE,
            "preview_title": SITE_PREVIEW_TITLE, "preview_off": SITE_PREVIEW_OFF,
            "preview_mock": SITE_PREVIEW_MOCK, "panel_mock": SITE_PANEL_MOCK,
            "panel_draft": SITE_PANEL_DRAFT,
            # the identity block's folded line
            "identity": SITE_IDENTITY,
            "identity_note": SITE_IDENTITY_NOTE,
            "menu_tiles": SITE_MENU_TILES,
            "entry_address": SITE_ADDRESS,
            "entry_address_hint": SITE_ENTRY_ADDRESS_HINT,
            "shown": SITE_SHOWN, "hidden": SITE_HIDDEN,
            "remove": SITE_REMOVE,
            "mark": SITE_MARK, "mark_desc": SITE_MARK_DESC,
            # the two Admin-only blocks at the foot of the page
            "add": SITE_ADD, "add_lead": SITE_ADD_LEAD,
            "add_key": SITE_ADD_KEY, "add_key_hint": SITE_ADD_KEY_HINT,
            "add_role": SITE_ADD_ROLE, "add_role_hint": SITE_ADD_ROLE_HINT,
            "add_root": SITE_ADD_ROOT,
            "add_slug_hint": SITE_ADD_SLUG_HINT, "add_submit": SITE_ADD_SUBMIT,
            "yaml_checked": SITE_YAML_CHECKED, "yaml_applies": SITE_YAML_APPLIES,
        },
        # the schema identifiers the card prints VERBATIM (a key is an identifier, never prose)
        "keys": {"slug": "slug", "group_role": "group_role", "enabled": "enabled",
                 "character": "character", "sections": "sections", "metrics": "metrics",
                 "theme_preset": "theme.preset", "theme_expert": "theme.expert",
                 "avatar": "avatar"},
    }


# --------------------------------------------------------------------------------- the save

def _posted_site_fields(form) -> list[str]:
    """Every posted field name that addresses a site's block, in post order."""
    return [name for name in form.keys() if name.startswith("f.sites.")]


def _site_token(name: str) -> str:
    """The site key a posted ``f.sites.<token>.<leaf>`` name addresses, decoded."""
    parts = name.split(".")
    return configgen.decode_token(parts[2]) if len(parts) > 2 else ""


def _foreign_site_field(form, key: str) -> str | None:
    """The first posted site field that addresses a site OTHER than *key*, or ``None``.

    The tree's per-site container matches ANY key, so without this check a crafted post could write a
    site the caller cannot even see. The card a form belongs to is stated by its hidden field, and
    every field of that form must address that one site.
    """
    for name in _posted_site_fields(form):
        if _site_token(name) != key:
            return name
    return None


def _structural_field(form, key: str) -> str | None:
    """The first posted field naming a STRUCTURAL key of *key*, or ``None``."""
    for name in _posted_site_fields(form):
        tokens = [configgen.decode_token(part) for part in name.split(".")]
        if len(tokens) > 3 and tokens[2] == key and tokens[3] in SITE_STRUCTURAL_KEYS:
            return name
    return None


def _expert_field(form, key: str) -> str | None:
    """The first posted field naming *key*'s expert sheet, or ``None``."""
    for name in _posted_site_fields(form):
        tokens = [configgen.decode_token(part) for part in name.split(".")]
        if len(tokens) > 4 and tokens[2] == key and tokens[3:5] == ["theme", "expert"]:
            return name
    return None


def _seed(document, legacy: Mapping | None) -> None:
    """Give a document that carries no ``sites:`` block the legacy one, before the form is applied.

    A save that starts from a file the page is about to create must not lose the sites the console is
    still installing from: the whole block is carried over and the posted leaves are applied on top.
    """
    if legacy is None or isinstance(document.get("sites"), Mapping):
        return
    document["sites"] = copy.deepcopy(legacy)


def _apply_absent_false(document, form, key: str) -> None:
    """Write the ``false`` a switch posted for a key the site does not state yet.

    ``enabled`` and every section id default to ON, and the shared form machinery skips a boolean
    whose value equals the field's default when the path is missing — which is right for a key that
    is merely being shown and wrong for a switch the operator turned OFF. Only a false on an ABSENT
    path is written here; every other value is the shared path's business.
    """
    block = document.get("sites")
    entry: Any = block.get(key) if isinstance(block, Mapping) else None
    if not isinstance(entry, Mapping):
        return
    token = configgen.encode_token(key)
    switches: list[tuple[str, tuple[str, ...]]] = [("enabled", ("enabled",))]
    switches += [(f"sections.{ident}", ("sections", ident)) for ident in sites.SECTION_IDS]
    for name, at in switches:
        posted = form.getlist(f"f.sites.{token}.{name}")
        if not posted or readmodels.text(posted[-1]) not in ("", "0", "false", "False"):
            continue
        if len(at) == 1:
            if at[0] not in entry:
                entry[at[0]] = False
            continue
        current = entry.get(at[0])
        if not isinstance(current, Mapping):
            if at[0] in entry:
                continue
            from ruamel.yaml.comments import CommentedMap

            current = CommentedMap()
            entry[at[0]] = current
        if at[1] not in current:
            current[at[1]] = False


#: the closed sets whose posted list IS the whole setting: an all-off post of one states an empty
#: sequence, and the shared form path refuses to CREATE a key merely because its control was shown.
_EMPTY_LIST_FIELDS: tuple[str, ...] = ("nav", "tiles")


def _apply_empty_lists(document, form, key: str) -> None:
    """Write the empty sequence an all-off closed set posts for a key the site does not state yet.

    Without this, switching every page off on a site that never declared ``nav:`` would write
    nothing and the site would keep showing them all — the one post whose whole content is "the list
    is empty" must be honoured. Only that shape is written: a block the operator left alone posts its
    shown ids, so it never reaches this.
    """
    block = document.get("sites")
    if not isinstance(block, Mapping):
        return
    entry = block.get(key)
    if not isinstance(entry, dict):
        return
    token = configgen.encode_token(key)
    for leaf in _EMPTY_LIST_FIELDS:
        if leaf in entry:
            continue
        posted = form.getlist(f"f.sites.{token}.{leaf}")
        if not posted or any(readmodels.text(value) for value in posted):
            continue
        from ruamel.yaml.comments import CommentedSeq

        entry[leaf] = CommentedSeq()


def _restore_expert(block, document, key: str) -> None:
    """Put *block*'s expert sheet back to the one the site serves today.

    A draft is not held to the sheet's grammar: a half-typed sheet would refuse every keystroke's
    draft and freeze the demo, while the saved text is what the site actually serves and is what the
    demo should show until the operator types a sheet the validator accepts.
    """
    sites_block = document.get("sites") if isinstance(document, Mapping) else None
    saved = sites_block.get(key) if isinstance(sites_block, Mapping) else None
    saved_theme = saved.get("theme") if isinstance(saved, Mapping) else None
    stored = saved_theme.get("expert") if isinstance(saved_theme, Mapping) else None
    theme = block.get("theme") if isinstance(block, Mapping) else None
    if not isinstance(theme, dict):
        return
    if isinstance(stored, str) and stored:
        theme["expert"] = stored
    else:
        theme.pop("expert", None)


def _page_url(site: str = "") -> str:
    """The page's address, naming the card to open when *site* is known.

    A key is a config key, not a URL fragment, so it is quoted rather than trusted.
    """
    target = mount.url(SITE_CONFIG_PATH)
    if not site:
        return target
    return f"{target}?{SITE_OPEN_PARAM}={quote(site, safe='')}"


def _back(request: Request, site: str = ""):
    """Back to the page, its saved card open, one-shot notice already stored.

    The destination travels as an ANSWER, not as a fixed 303: the console's background submit reads
    its page with ``fetch(redirect: "manual")``, which makes a 303's ``Location`` unreadable, so a
    route that only 303s sends that submit to the page it was already on — the first card, at the
    top. Both faces therefore carry the same target, and it names the card whose Save was pressed.
    """
    return actions_page._redirect_or_json(request, _page_url(site))


def _refuse(request: Request, msgid: str, **params: Any):
    """Store one refusal sentence and go back — nothing was written."""
    settings_page._notice(request, i18n.translate(request, msgid, **params), ok=False, refusal=True)
    return _back(request)


def _refuse_detail(request: Request, detail: str):
    """A refusal that quotes the parser's OWN sentence after the page's prefix, never a 500."""
    settings_page._notice(request, i18n.translate(request, SITE_NOTHING_WRITTEN) + detail,
                          ok=False, refusal=True)
    return _back(request)


# ------------------------------------------------------- the add block and the file's own editor

class SiteAddRefusal(Exception):
    """A refusal raised INSIDE the mutation: the add form's own msgid, translated by the caller.

    Raised from the ``mutate`` callable so the write never reaches ``_save_locked`` — an exit that
    leaves ``site.yaml`` byte-identical rather than writing a site the operator cannot have.
    """

    def __init__(self, msgid: str) -> None:
        super().__init__(msgid)
        self.msgid = msgid


class SiteYamlEmpty(Exception):
    """The posted document parsed to nothing: an empty file would be written as ``null``."""


def known_roles(config_dir) -> list[str]:
    """Every ``managed_by`` token ``servers.yaml`` declares, sorted and deduplicated.

    The one piece of knowledge the operator cannot read off the site file. An unreadable
    ``servers.yaml`` answers an empty list rather than raising into the render: the parser then
    refuses a site naming a role, with its own sentence, and the page still opens.
    """
    try:
        return sorted(sites.known_managed_by(sites.load_servers_config(config_dir)))
    except sites.SiteConfigError as ex:
        log.warning("Site settings: the role set could not be read: %s", ex)
        return []


def _file_text(path: Path) -> str:
    """The site file's own text, or ``""`` when it does not exist or cannot be read.

    The panel's read-only view is the file the save writes, comments included — never a re-dump.
    """
    try:
        return configwrite.read_config_text(path)
    except configwrite.ConfigWriteError as ex:
        log.warning("Site settings: the site file could not be read: %s", ex)
        return ""


def site_document(text: str, path: Path, config_dir, roles: list[str]):
    """The posted text as a document the console would install, or a refusal by exception.

    Raises :class:`configwrite.ConfigParseError` for YAML that does not parse,
    :class:`SiteYamlEmpty` for an empty document and :class:`sites.SiteConfigError` for a ``sites:``
    block the loader's own parser refuses. ONE reader for the save and the live check, so the two
    can never disagree about a document.
    """
    document = configwrite.load_config_form_text(text, path=path)
    if document is None:
        raise SiteYamlEmpty()
    if not isinstance(document, Mapping):
        raise sites.SiteConfigError(f"{path.name}: must be a map carrying a 'sites:' block")
    sites.parse_sites(document.get("sites"), known_roles=roles, config_dir=config_dir)
    return document


def _site_refusal(request: Request, ex: Exception) -> str:
    """The sentence a refused document answers with: the page's own words for an empty one."""
    if isinstance(ex, SiteYamlEmpty):
        return i18n.translate(request, settings_page.SAVE_REFUSED_EMPTY_DOCUMENT)
    return str(ex)


# ------------------------------------------------------------------------------- the mark (§E1)
class AvatarRefusal(Exception):
    """A refusal of the uploaded image: the notice's msgid and the holes it is translated with."""

    def __init__(self, msgid: str, **params: Any) -> None:
        super().__init__(msgid)
        self.msgid = msgid
        self.params = params


def avatar_pixel_limit() -> int:
    """The decoded pixel ceiling in force: this page's bound, or Pillow's own when that is lower."""
    configured = Image.MAX_IMAGE_PIXELS
    limit = SITE_AVATAR_MAX_PIXELS if configured is None else min(SITE_AVATAR_MAX_PIXELS,
                                                                 int(configured))
    return int(limit)


def avatar_paths(slug: str, config_dir) -> list[Path]:
    """The four files an uploaded mark lives in, through :func:`services.webservice.sites.avatar_path`.

    The ONLY way a slug reaches the disk. Every resolved path is asserted to sit inside the site's
    own asset folder, so a slug this build's own parser would refuse still cannot address a file
    outside it.
    """
    folder = (Path(config_dir) / sites.ASSET_DIR).resolve()
    paths = [sites.avatar_path(slug, size, config_dir) for size in sites.AVATAR_SIZES]
    for path in paths:
        # the RESOLVED FILE must sit directly in that folder: a slug carrying ``..`` collapses past it
        if path.resolve().parent != folder:
            raise AvatarRefusal(SITE_AVATAR_REFUSED_FILE)
    return paths


def _avatar_byte_limit() -> str:
    """The page's own ceiling as the card states it (``8 MiB``)."""
    return f"{SITE_AVATAR_MAX_BYTES // (1024 * 1024)} MiB"


def _sheet_byte_limit() -> str:
    """The sheet's ceiling as the card states it (``256 KiB``)."""
    return f"{sites.MAX_SHEET_LENGTH // 1024} KiB"


def sheet_slot(request: Request, site: "sites.Site") -> dict:
    """§5's row: the palette sheet the site serves today, its bounds and the picker's words.

    The state is read from the MODEL — ``theme.sheet`` is what the route answers — never from the
    disk, so the row cannot claim a file the site does not serve. The bounds and the accepted suffix
    are this module's own constants, so the row cannot promise something the route refuses.
    """
    leaf = (getattr(site.theme, "sheet", None) or "").strip()
    return {
        "state": "uploaded" if leaf else "absent",
        "uploaded": bool(leaf),
        "name": leaf,
        "sentence": (i18n.translate(request, SITE_SHEET_STORED, name=leaf) if leaf
                     else i18n.translate(request, SITE_SHEET_NONE)),
        "note": i18n.translate(request, SITE_SHEET_NOTE),
        "upload_label": i18n.translate(request, SITE_SHEET_REPLACE_LABEL if leaf
                                       else SITE_SHEET_UPLOAD_LABEL),
        "choose": i18n.translate(request, SITE_SHEET_CHOOSE),
        "chosen": i18n.translate(request, SITE_MARK_CHOSEN),
        "input": i18n.translate(request, SITE_SHEET_INPUT, limit=_sheet_byte_limit()),
        "confirm": i18n.translate(request, SITE_SHEET_CONFIRM),
        "accept": f"{SITE_SHEET_SUFFIX},text/css",
    }


def mark_slot(request: Request, site: "sites.Site", token: str, config_dir) -> dict:
    """§E's slot: the ONE mark the site draws today, the four files behind it, and the controls.

    The state is read from the shipped resolver (:func:`services.webservice.site_pages.uploaded_mark`
    and :func:`~services.webservice.site_pages.logo_url`), so the slot cannot claim an order the site
    does not follow: uploaded mark, then ``branding.logo``, then the shipped mark. The four previews
    address the four FILES at their own pixel sizes through :data:`SITE_MARK_PATH`, and carry each
    file's modification time so a replaced mark is a different address.
    """
    uploaded = site_pages.uploaded_mark(site, config_dir)
    declared = site_pages.logo_url(site, config_dir)
    state = "uploaded" if uploaded is not None else ("logo" if declared else "shipped")
    limit = _avatar_byte_limit()
    previews: list[dict] = []
    for size in sites.AVATAR_SIZES:
        path = sites.avatar_path(site.slug, size, config_dir)
        stamp = int(path.stat().st_mtime_ns) if path.is_file() else 0
        previews.append({
            "size": size,
            "name": path.name,
            "src": f"{mount.url(SITE_MARK_PATH.format(token=token, size=size))}?v={stamp}",
            "alt": i18n.translate(request, SITE_MARK_SIZE_ALT, size=size),
        })
    sentence = {"uploaded": SITE_MARK_ORDER, "logo": SITE_MARK_LOGO,
                "shipped": SITE_MARK_SHIPPED}[state]
    return {
        "state": state,
        "uploaded": state == "uploaded",
        # the slot draws the 256 file when one was uploaded, the declared file when one is named, and
        # the shipped mark otherwise — the same three answers the site's own bar gets
        "src": (previews[-1]["src"] if state == "uploaded"
                else declared or "/static/logo-mark.png"),
        "alt": i18n.translate(request, SITE_MARK_ALT),
        "sentence": i18n.translate(request, sentence),
        "order": i18n.translate(request, SITE_MARK_ORDER),
        "from_logo": i18n.translate(request, SITE_MARK_FROM_LOGO),
        "upload_label": i18n.translate(request, SITE_MARK_REPLACE if state == "uploaded"
                                       else SITE_MARK_UPLOAD),
        "accept": ",".join(f"image/{kind.lower()}" for kind in SITE_AVATAR_FORMATS),
        # the picker's own two words: the trigger beside the clipped input, and the line that states
        # the chosen file's name in place of the accepted formats
        "choose": i18n.translate(request, SITE_MARK_CHOOSE),
        "chosen": i18n.translate(request, SITE_MARK_CHOSEN),
        "input": i18n.translate(request, SITE_MARK_ACCEPT, limit=limit),
        "files": i18n.translate(request, SITE_MARK_FILES,
                                files=", ".join(preview["name"] for preview in previews)),
        "previews": previews,
        "confirm": i18n.translate(request, SITE_MARK_CONFIRM),
    }


def upload_form_dependency(max_fields: int | float, *, max_bytes: int, allowance: int,
                           refusal_attr: str, over: str, unreadable: str) -> Callable:
    """The console's CSRF predicate over a multipart body parsed under ONE door's own bounds.

    ``session.csrf_protect_with`` parses the body itself, and Starlette's parser bounds every non-file
    part at 1 MiB unless the caller says otherwise — and it CACHES that parse, so a later
    ``request.form(max_part_size=…)`` never runs. An upload door therefore does the one parse itself,
    with its own file ceiling as the part bound, and runs the SAME predicate
    (:func:`services.webservice.session.validate_csrf_token`) on the token it reads. A body the
    parser refuses is recorded on the request under *refusal_attr* and answered by the handler in the
    card's own words — the parser's text never reaches the operator.
    """
    async def _dependency(request: Request) -> None:
        try:
            form = await request.form(max_fields=max_fields, max_part_size=max_bytes)
        except StarletteHTTPException:
            declared = str(request.headers.get("content-length") or "")
            too_big = not declared.isdigit() or int(declared) > max_bytes + allowance
            setattr(request.state, refusal_attr, over if too_big else unreadable)
            return
        token = request.headers.get(session.CSRF_HEADER) or form.get(session.CSRF_FIELD)
        if not session.validate_csrf_token(request, token if isinstance(token, str) else None):
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")

    # the marker ``tests/test_webui_auth_routes`` reads: this dependency runs the console's ONE CSRF
    # predicate — only the parse bound differs, exactly as ``session.csrf_protect_with`` states
    setattr(_dependency, "dcssb_csrf_dependency", True)
    return _dependency


def mark_form_dependency(max_fields: int | float) -> Callable:
    """The mark's door: the shared predicate, bounded by :data:`SITE_AVATAR_MAX_BYTES`."""
    return upload_form_dependency(max_fields, max_bytes=SITE_AVATAR_MAX_BYTES,
                                  allowance=SITE_AVATAR_FORM_ALLOWANCE,
                                  refusal_attr="site_mark_refusal",
                                  over=SITE_AVATAR_REFUSED_BYTES,
                                  unreadable=SITE_AVATAR_REFUSED_FORM)


def sheet_form_dependency(max_fields: int | float) -> Callable:
    """The sheet's door: the shared predicate, bounded by :data:`sites.MAX_SHEET_LENGTH`."""
    return upload_form_dependency(max_fields, max_bytes=sites.MAX_SHEET_LENGTH,
                                  allowance=SITE_SHEET_FORM_ALLOWANCE,
                                  refusal_attr="site_sheet_refusal",
                                  over=SITE_SHEET_REFUSED_BYTES,
                                  unreadable=SITE_SHEET_REFUSED_FORM)


def _sheet_refusal(request: Request) -> str:
    """The msgid a refused parse recorded on the request, or ``""`` when the body parsed."""
    stored = getattr(request.state, "site_sheet_refusal", "")
    return stored if isinstance(stored, str) else ""


def _mark_refusal(request: Request) -> str:
    """The msgid a refused parse recorded on the request, or ``""`` when the body parsed."""
    stored = getattr(request.state, "site_mark_refusal", "")
    return stored if isinstance(stored, str) else ""


def _mark_image(payload: bytes) -> Image.Image:
    """The uploaded bytes as one RGBA image, or :class:`AvatarRefusal` — Pillow decides everything.

    The FORMAT is the decoder's answer, never the request's content type or the file's suffix, and
    the pixel count is checked from the header BEFORE the image is decoded, so a decompression bomb
    is refused rather than loaded. The returned image carries no metadata: it is a fresh RGBA copy of
    the decoded pixels, so no EXIF, no ICC profile and no trailing bytes can survive into it.
    """
    if not payload:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_NOT_IMAGE)
    limit = avatar_pixel_limit()
    try:
        with Image.open(io.BytesIO(payload)) as probe:
            image_format = str(probe.format or "").upper()
            width, height = probe.size
    except Image.DecompressionBombError as ex:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_BOMB) from ex
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as ex:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_NOT_IMAGE) from ex
    if image_format not in SITE_AVATAR_FORMATS:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_NOT_IMAGE)
    if width * height > limit:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_PIXELS,
                            pixels=_count(width * height), limit=_count(limit))
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            decoded = image.convert("RGBA")
    except Image.DecompressionBombError as ex:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_BOMB) from ex
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as ex:
        raise AvatarRefusal(SITE_AVATAR_REFUSED_NOT_IMAGE) from ex
    decoded.info = {}
    return decoded


def avatar_variants(payload: bytes) -> dict[int, bytes]:
    """The four PNG byte strings an upload publishes, keyed by size, or :class:`AvatarRefusal`.

    Each variant is *size* x *size* — the mark is contained in a transparent square, so a wide image
    keeps its proportions and the site's bar still gets the box it reserved. Nothing but what Pillow
    encoded is in the bytes: the image is a fresh canvas, never the uploaded file.
    """
    source = _mark_image(payload)
    variants: dict[int, bytes] = {}
    previous = source
    for size in sorted(sites.AVATAR_SIZES, reverse=True):
        fitted = ImageOps.contain(previous, (size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2), fitted)
        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        variants[size] = buffer.getvalue()
        previous = fitted
    return variants


def stage_mark_file(payload: bytes, target: Path) -> Path:
    """Write ONE encoded variant under a temporary name in *target*'s folder; return that name.

    The staged file is the whole variant — the bytes Pillow produced — and it is NOT the file the
    site draws: nothing is published until :func:`os.replace` puts it in place, so a refusal that
    arrives after it exists cannot have replaced the mark.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + ".",
                                        suffix=SITE_MARK_STAGED_SUFFIX, delete=False)
    try:
        with handle as stream:
            stream.write(payload)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


def stage_sheet_file(payload: bytes, target: Path) -> Path:
    """Stage a sheet's bytes beside *target*; the rename onto it is the only thing that publishes it.

    One file, the same staged-then-renamed shape the mark's four variants use: nothing is served from
    the staged name, so a refusal that arrives after it exists cannot have replaced the sheet in use.
    """
    return stage_mark_file(payload, target)


def _restore_sheet(target: Path, previous: bytes | None) -> None:
    """Put the sheet back as it was after a config write that did not land.

    ``previous`` is the file's own bytes before the upload replaced it, or ``None`` when there was no
    file: then the restore is the deletion. A restore that fails is logged and nothing else — the
    operator has already been told nothing was written.
    """
    try:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(previous)
    except OSError:
        log.warning("Site settings: could not restore %s", target, exc_info=True)


def _avatar_entry(document, key: str) -> dict | None:
    """The site's own block inside *document*, or ``None`` when the document carries none."""
    block = document.get("sites") if isinstance(document, Mapping) else None
    entry = block.get(key) if isinstance(block, Mapping) else None
    return entry if isinstance(entry, dict) else None


def _write_avatar_flag(document, key: str, legacy: Mapping | None, value: bool) -> None:
    """Set (or clear) the ``avatar`` flag through the document the save writes, never by hand.

    ``legacy`` is the block the console installs from until ``site.yaml`` exists, seeded the way the
    save seeds it, so the first upload writes the site where the page owns it. Clearing DELETES the
    key: an absent flag is the default, and a removed mark leaves no line behind.
    """
    _seed(document, legacy)
    entry = _avatar_entry(document, key)
    if entry is None:
        return
    if value:
        if entry.get("avatar") is not True:
            entry["avatar"] = True
    elif "avatar" in entry:
        del entry["avatar"]


def _write_sheet_flag(document, key: str, legacy: Mapping | None, leaf: str | None) -> None:
    """Set (or clear) ``theme.sheet`` through the document the save writes, never by hand.

    ``legacy`` is the block the console installs from until ``site.yaml`` exists, seeded the way the
    save seeds it. A sheet is one of THREE exclusive sources, so naming one DELETES the other two: the
    uploaded file is the palette from that save on, and a block that kept a preset or an expert block
    beside it would be refused by the parser on the next read. Clearing deletes the key, and a theme
    block it leaves empty is dropped with it.
    """
    _seed(document, legacy)
    entry = _avatar_entry(document, key)
    if entry is None:
        return
    theme = entry.get("theme")
    theme = dict(theme) if isinstance(theme, dict) else {}
    if leaf:
        theme["sheet"] = leaf
        theme.pop("preset", None)
        theme.pop("expert", None)
        entry["theme"] = theme
        return
    theme.pop("sheet", None)
    if theme:
        entry["theme"] = theme
    elif "theme" in entry:
        del entry["theme"]


def _mark_site(request: Request, form, scope_msgid: str = SITE_AVATAR_REFUSED_SCOPE
               ) -> tuple[Any, str, str, bool]:
    """The site an upload write addresses: ``(site, key, refusal, forbidden)``.

    The key is the same hidden field the save reads, resolved against the catalogue and narrowed by
    the same per-site scope rule, so a crafted post cannot write a site the caller may not see. A
    site outside the caller's scope answers 403 (``forbidden``), never a notice on a redirect.
    *scope_msgid* is the door's own sentence for that refusal, because a mark and a sheet are not
    the same thing to the operator.
    """
    key = readmodels.text(form.get(SITE_KEY_FIELD))
    if not key:
        return None, key, SITE_REFUSED_UNKNOWN, False
    try:
        catalog = site_catalog(request)
    except sites.SiteConfigError as ex:
        return None, key, str(ex), False
    site = next((candidate for candidate in catalog.sites if candidate.key == key), None)
    if site is None:
        return None, key, SITE_REFUSED_UNKNOWN, False
    if not site_in_scope(request, site):
        return None, key, scope_msgid, True
    return site, key, "", False


def _forbidden(request: Request, msgid: str) -> PlainTextResponse:
    """A mark write the caller may not make: the sentence, and a 403 — nothing was touched."""
    return PlainTextResponse(i18n.translate(request, msgid), status_code=403)


def _discard_marks(paths: list[Path]) -> None:
    """Remove every file in *paths*; a file that is not there is not an error.

    Used for the staged files of a failed write AND for the four live files of a removal — callers
    pass whichever set they own, so a failed upload never deletes a live file.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("Site settings: could not remove %s", path, exc_info=True)


def draft_size_guard(limit: int = SITE_DRAFT_BODY_BYTES) -> Callable:
    """Refuse an oversized draft body BEFORE it is parsed.

    The card posts its own form, so the body is bounded by its declared length and nothing else: a
    body that is too large is refused with ``413`` and joins no store, so it cannot cost the single
    worker the memory of reading it.
    """
    async def _dependency(request: Request) -> None:
        declared = request.headers.get("content-length")
        if declared is None:
            return
        try:
            too_big = int(declared) > limit
        except ValueError:
            too_big = True
        if too_big:
            raise HTTPException(status_code=413, detail="The draft is larger than this page accepts.")

    return _dependency


def add_routes(router: APIRouter) -> APIRouter:
    """Add the page's GET and its four POSTs, and return the router."""

    @router.get(SITE_CONFIG_PATH, response_class=HTMLResponse)
    async def site_settings(request: Request):
        """The Site settings page — one card per site in the visitor's scope."""
        return HTMLResponse(i18n.render(request, SITE_CONFIG_TEMPLATE, **page_context(request)),
                            headers={"Cache-Control": "no-store"})

    @router.get(SITE_MOCK_PATH, response_class=HTMLResponse)
    async def site_settings_mock(request: Request):
        """The card's box for a site that has no address yet: the site's look, drawn from the FILE.

        A site's own preview answers under its prefix, and prefixes are registered at startup — so a
        site this session just added has nowhere to mount a frame, and the box used to show the 404
        page at that address. This one does answer for it: the SAME document the site will serve
        (:data:`services.webservice.site_pages.PREVIEW_TEMPLATE`, the shell's own chrome), rendered
        from the file's site and this session's draft in DEMO mode — inert links, with the palette and
        the marks carried inside the document — because nothing here may point at an address that
        does not answer yet.

        The key travels in the query and is resolved against the FILE's catalogue, never a path: a key
        the file does not carry, or one outside the visitor's scope, answers 404. The answer is
        ``no-store`` because it reflects a draft that changes between keystrokes.
        """
        key = readmodels.text(request.query_params.get(SITE_OPEN_PARAM, ""))
        try:
            catalog = site_catalog(request)
        except sites.SiteConfigError as ex:
            raise HTTPException(status_code=404) from ex
        site = next((candidate for candidate in catalog.sites if candidate.key == key), None)
        if site is None or not site_in_scope(request, site):
            raise HTTPException(status_code=404)
        block = site_pages.draft_block(request, key)
        resolved = site_pages.demo_context(request, site, block)
        markup = site_pages.render_site_page(request, site, site_pages.PREVIEW_TEMPLATE,
                                             resolved=resolved, demo=True)
        markup.headers["Cache-Control"] = "no-store"
        return markup

    @router.get(SITE_MARK_PATH)
    async def site_settings_mark(request: Request, token: str, size: str):
        """Serve ONE of a site's four uploaded mark files at its own address — the card's previews.

        The previews are the ACTUAL files: the 32, the 64, the 128 and the 256 the upload wrote, each
        at its own pixels, so the operator meets the file a chip will draw. The path is built only
        through :func:`services.webservice.sites.avatar_path`, from a size this build names and a
        site in the caller's scope; anything else answers 404 rather than a file.
        """
        if size not in {str(value) for value in sites.AVATAR_SIZES}:
            return PlainTextResponse("", status_code=404)
        try:
            catalog = site_catalog(request)
        except sites.SiteConfigError:
            return PlainTextResponse("", status_code=404)
        site = next((candidate for candidate in catalog.sites
                     if configgen.encode_token(candidate.key) == token), None)
        if site is None or not site_in_scope(request, site):
            return PlainTextResponse("", status_code=404)
        config_dir = settings_page.config_dir(request)
        # THE SAME PREDICATE THE SITE DRAWS WITH: a preview may only serve a file the site itself
        # would draw, so a cleared flag or a vanished file answers 404 instead of a mark no public
        # page shows.
        if site_pages.uploaded_mark(site, config_dir) is None:
            return PlainTextResponse("", status_code=404)
        path = sites.avatar_path(site.slug, int(size), config_dir)
        if not path.is_file():
            return PlainTextResponse("", status_code=404)
        # no-store: a replaced mark must reach the card on the next render, not from a cache
        return Response(path.read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @router.post(SITE_CONFIG_SAVE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_save(request: Request):
        """Write ONE site card: candidate -> the loader's parser -> the one write primitive.

        The posted site is resolved first and the request's scope is compared against THAT site, so
        a group admin posting another site's form is refused rather than silently ignored. The
        candidate document is then parsed by :func:`services.webservice.sites.parse_sites` before the
        file is touched: a document the console would refuse to install is never written. A save that
        moved a structural key then re-reads the file on this request's own application, before the
        answer, and the notice states whether the running console moved.
        """
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        access = site_access(request)
        if not access.write:
            return _refuse(request, SITE_REFUSED_SCOPE)
        key = readmodels.text(form.get(SITE_KEY_FIELD))
        if not key:
            return _refuse(request, SITE_REFUSED_UNKNOWN)
        document, path, error = _document(request)
        if error:
            return _refuse_detail(request, error)
        if document is None:
            return _refuse(request, SITE_REFUSED_UNKNOWN)
        config_dir = settings_page.config_dir(request)
        try:
            catalog = site_catalog(request)
        except sites.SiteConfigError as ex:
            return _refuse_detail(request, str(ex))
        site = next((candidate for candidate in catalog.sites if candidate.key == key), None)
        if site is None:
            return _refuse(request, SITE_REFUSED_UNKNOWN)
        if not site_in_scope(request, site):
            return _refuse(request, SITE_REFUSED_SCOPE)
        foreign = _foreign_site_field(form, key)
        if foreign is not None:
            log.warning("Site settings: refused a post naming another site's field (%s)", foreign)
            return _refuse(request, SITE_REFUSED_FOREIGN)
        if not access.structure and _structural_field(form, key) is not None:
            return _refuse(request, SITE_REFUSED_STRUCTURE)
        if not access.expert and _expert_field(form, key) is not None:
            return _refuse(request, SITE_REFUSED_EXPERT)

        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        tree = form_tree()
        legacy = _legacy_block(request)

        def mutate(target):
            _seed(target, legacy)
            configgen.apply_form(target, form, tree)
            _apply_absent_false(target, form, key)
            _apply_empty_lists(target, form, key)

        # THE EXPERT SHEET IS CHECKED BY ITS OWN VALIDATOR FIRST: the parser bounds it but does not
        # read its grammar, and a sheet the site would refuse to SERVE must be refused where it is
        # written. The refusal names the line, exactly as the editor's live check does.
        expert_text = readmodels.text(form.get(f"f.sites.{configgen.encode_token(key)}.theme.expert"))
        if expert_text:
            refusal = theme_css.validate(expert_text)
            if refusal is not None:
                return _refuse(request, refusal.message)

        # THE CANDIDATE CHECK: the same mutation, in memory, parsed by the loader's own parser. A
        # refusal here is the parser's sentence and nothing is written.
        candidate = copy.deepcopy(document)
        try:
            mutate(candidate)
            after = sites.parse_sites(
                candidate.get("sites"),
                known_roles=sites.known_managed_by(sites.load_servers_config(config_dir)))
        except sites.SiteConfigError as ex:
            return _refuse_detail(request, str(ex))
        except (configgen.UnknownFieldError, configgen.DuplicateEntryError) as ex:
            return _refuse(request, settings_page.SAVE_REFUSED_UNPLACEABLE if isinstance(
                ex, configgen.UnknownFieldError) else settings_page.SAVE_REFUSED_DUPLICATE_ENTRY)

        structural = sites.structure_of(catalog) != sites.structure_of(after)
        try:
            result = configwrite.patch_config_file(path, mutate, schemas=[],
                                                   expect_revision=revision,
                                                   applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigValidationError as ex:
            return _refuse_detail(request, str(ex))
        except configgen.UnknownFieldError:
            return _refuse(request, settings_page.SAVE_REFUSED_UNPLACEABLE)
        except configgen.DuplicateEntryError:
            return _refuse(request, settings_page.SAVE_REFUSED_DUPLICATE_ENTRY)
        except configwrite.ConfigWriteError as ex:
            return _refuse_detail(request, str(ex))

        if not result.changed:
            # THE DRAFT IS SPENT EITHER WAY: a save that landed the form's own values leaves nothing
            # for the demo to remember, and a page left open must go on to the next edit.
            site_pages.forget_draft(request, key)
            settings_page._notice(request, settings_page.SAVED_NO_CHANGE, ok=True, refusal=True,
                                  applies=result.applies)
            return _back(request, key)
        if structural:
            # THE SAVE APPLIES ITS OWN FILE: the console reads the site file again, on THIS request's
            # application, before the answer. No offer, no confirmation, no second press — a
            # structural key is no more than a re-read and there is nothing to warn about.
            site_pages.forget_draft(request, key)
            refusal = await config_apply.apply_console(request, app=request.app)
            if refusal:
                # THE FILE STAYS WRITTEN: the write landed, the running console did not move.
                settings_page._notice(request, refusal, ok=False, refusal=False,
                                      applies=result.applies)
            else:
                settings_page._notice(request, SITE_SAVED_APPLIED, ok=True, refusal=True,
                                      applies=result.applies)
            return _back(request, key)
        settings_page._notice(request, SITE_SAVED_LIVE, ok=True, refusal=True,
                              applies=result.applies)
        return _back(request, key)

    @router.post(SITE_CONFIG_DRAFT_PATH,
                 dependencies=[Depends(draft_size_guard(SITE_DRAFT_BODY_BYTES)),
                               Depends(session.csrf_protect_with(
                                   settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_draft(request: Request):
        """Hold ONE card's current fields in memory so the demo can render them before Save.

        The body is the card's own form, so the fields are read by the SAVE's reader — the same field
        tree, the same ``_seed``/``_apply_absent_false``/``_apply_empty_lists`` passes — against a
        copy of the document, and only that site's block is kept. A name the tree does not place is
        refused (``configgen.UnknownFieldError``), a block for another site or for a site outside the
        caller's scope is refused, and the block is parsed by the loader's own parser before it is
        stored: a draft is held to exactly what a save is.

        NOTHING IS WRITTEN. The store is :mod:`services.webservice.site_pages`'s in-memory one, keyed
        by this session and this site; the answer is a no-op success. A save drops the draft
        (:func:`site_settings_save`) and so does the card's own discard post
        (:data:`SITE_DRAFT_CLEAR_FIELD`), which is what the operator's leaving the card sends.
        """
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        access = site_access(request)
        if not access.write:
            raise HTTPException(status_code=403, detail=SITE_REFUSED_SCOPE)
        key = readmodels.text(form.get(SITE_KEY_FIELD))
        if not key:
            raise HTTPException(status_code=400, detail=SITE_REFUSED_UNKNOWN)
        document, _path, error = _document(request)
        if error or document is None:
            raise HTTPException(status_code=400,
                                detail=error or SITE_REFUSED_UNKNOWN)
        try:
            catalog = site_catalog(request)
        except sites.SiteConfigError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        site = next((candidate for candidate in catalog.sites if candidate.key == key), None)
        if site is None or not site_in_scope(request, site):
            raise HTTPException(status_code=404, detail=SITE_REFUSED_UNKNOWN)
        if _foreign_site_field(form, key) is not None:
            raise HTTPException(status_code=400, detail=SITE_REFUSED_FOREIGN)
        if readmodels.text(form.get(SITE_DRAFT_CLEAR_FIELD)):
            site_pages.forget_draft(request, key)
            return JSONResponse({"ok": True, "stored": False})

        tree = form_tree()
        legacy = _legacy_block(request)
        candidate = copy.deepcopy(document)

        def mutate(target):
            _seed(target, legacy)
            configgen.apply_form(target, form, tree)
            _apply_absent_false(target, form, key)
            _apply_empty_lists(target, form, key)

        try:
            mutate(candidate)
        except (configgen.UnknownFieldError, configgen.DuplicateEntryError) as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        blocks = candidate.get("sites")
        block = blocks.get(key) if isinstance(blocks, Mapping) else None
        if not isinstance(block, Mapping):
            raise HTTPException(status_code=400, detail=SITE_REFUSED_UNKNOWN)
        # AN INVALID EXPERT SHEET IS NOT PREVIEWED. The save refuses the sheet where it is written;
        # here a half-typed sheet would refuse EVERY keystroke's draft and freeze the demo, so the
        # draft keeps the sheet the site serves today and every other edit still lands.
        expert_text = readmodels.text(form.get(f"f.sites.{configgen.encode_token(key)}.theme.expert"))
        if expert_text and theme_css.validate(expert_text) is not None:
            _restore_expert(block, document, key)
        overlay = site_pages.draft_overlay(block)
        if site_pages.draft_bytes(overlay) > site_pages.DRAFT_MAX_BYTES:
            raise HTTPException(status_code=413,
                                detail="The draft is larger than this page accepts.")
        if site_pages.draft_site(request, site, overlay) is None:
            raise HTTPException(status_code=400, detail=SITE_REFUSED_UNKNOWN)
        if not site_pages.remember_draft(request, key, overlay):
            raise HTTPException(status_code=429, detail="No room for another draft right now.")
        return JSONResponse({"ok": True, "stored": True})

    @router.post(SITE_CSS_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_css_validate(request: Request):
        """The expert editor's live check: the refusal, or the composed sheet's size. Writes nothing."""
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        text = readmodels.text(form.get(SITE_SHEET_FIELD))
        refusal = theme_css.validate(text)
        if refusal is not None:
            return JSONResponse(refusal.as_dict())
        composed = theme_css.expert_rule(text)
        return JSONResponse({"ok": True, "bytes": len(composed.encode("utf-8"))})

    @router.post(SITE_AVATAR_UPLOAD_PATH,
                 dependencies=[Depends(mark_form_dependency(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_avatar_upload(request: Request):
        """Publish ONE site's mark: four PNGs Pillow produced, then the flag, through the one writer.

        The body is bounded BEFORE Pillow sees it, and the format is the decoder's answer — never the
        request's content type or the file's suffix. Each of the four variants is STAGED beside its
        target, the ``avatar`` flag then goes through the same document the save writes, and only a
        write that landed is followed by the four renames that publish the files — so a refusal
        leaves the mark in use untouched and never a set split between old and new.
        A refused body writes nothing at all: no file is opened for any refusal.
        """
        refused = _mark_refusal(request)
        if refused:
            return _refuse(request, refused, limit=_avatar_byte_limit())
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS,
                                  max_part_size=SITE_AVATAR_MAX_BYTES)
        if not site_access(request).write:
            return _forbidden(request, SITE_AVATAR_REFUSED_ROLE)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > SITE_AVATAR_MAX_BYTES + SITE_AVATAR_FORM_ALLOWANCE
            except ValueError:
                too_big = True
            if too_big:
                return _refuse(request, SITE_AVATAR_REFUSED_BYTES, limit=_avatar_byte_limit())
        site, key, refusal, forbidden = _mark_site(request, form)
        if site is None:
            return _forbidden(request, refusal) if forbidden else _refuse(request, refusal)
        config_dir = settings_page.config_dir(request)
        # THE MARK IS WRITTEN BESIDE THE SITE IT BELONGS TO: a mark write never CREATES the site
        # file, so a console with no site.yaml yet answers a refusal instead of guessing a folder.
        if not sites.site_file(config_dir).is_file():
            return _refuse(request, SITE_AVATAR_REFUSED_FILE)
        document, path, error = _document(request)
        if error:
            return _refuse_detail(request, error)
        if document is None:
            return _refuse(request, SITE_AVATAR_REFUSED_FILE)
        legacy = _legacy_block(request)
        probe = copy.deepcopy(document)
        _write_avatar_flag(probe, key, legacy, True)
        if _avatar_entry(probe, key) is None:
            return _refuse(request, SITE_AVATAR_REFUSED_FILE)
        try:
            targets = avatar_paths(site.slug, config_dir)
        except AvatarRefusal as ex:
            return _refuse(request, ex.msgid, **ex.params)
        upload = form.get(SITE_AVATAR_FIELD)
        if not isinstance(upload, UploadFile):
            return _refuse(request, SITE_AVATAR_REFUSED_NOT_IMAGE)
        payload = await upload.read(SITE_AVATAR_MAX_BYTES + 1)
        await upload.close()
        if len(payload) > SITE_AVATAR_MAX_BYTES:
            return _refuse(request, SITE_AVATAR_REFUSED_BYTES, limit=_avatar_byte_limit())
        try:
            variants = avatar_variants(payload)
        except AvatarRefusal as ex:
            return _refuse(request, ex.msgid, **ex.params)

        by_size = dict(zip(sites.AVATAR_SIZES, targets))
        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        # FOUR STAGED FILES, THEN THE CONFIG WRITE, THEN FOUR RENAMES. Every refusal below — a stale
        # revision, a writer that says no, a disk that fails — happens while the live files are still
        # the mark in use, and each handler deletes only what it staged. The four renames are last,
        # so a failure on the last variant can no longer leave one old file beside new ones.
        staged: list[Path] = []
        try:
            for size, target in by_size.items():
                staged.append(stage_mark_file(variants[size], target))
            configwrite.patch_config_file(path,
                                          lambda target: _write_avatar_flag(target, key, legacy,
                                                                            True),
                                          schemas=[], expect_revision=revision,
                                          applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            _discard_marks(staged)
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            _discard_marks(staged)
            return _refuse_detail(request, str(ex))
        except OSError as ex:
            _discard_marks(staged)
            return _refuse_detail(request, str(ex))
        try:
            for source, target in zip(staged, by_size.values()):
                os.replace(source, target)
        except OSError as ex:
            # a rename that fails leaves its target as it was; the staged files that never landed go
            _discard_marks(staged)
            return _refuse_detail(request, str(ex))
        settings_page._notice(request, i18n.translate(request, SITE_AVATAR_UPLOADED), ok=True,
                              refusal=True, applies=configwrite.APPLIES_NOW)
        return _back(request, key)

    @router.post(SITE_AVATAR_REMOVE_PATH,
                 dependencies=[Depends(mark_form_dependency(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_avatar_remove(request: Request):
        """Withdraw ONE site's uploaded mark: the flag clear first, then the four files.

        The flag is cleared through the same writer the save uses, so the site stops resolving the
        files before any of them disappears. A file that is already gone is not an error: a second
        removal is a success and answers the same sentence.
        """
        refused = _mark_refusal(request)
        if refused:
            return _refuse(request, refused, limit=_avatar_byte_limit())
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS,
                                  max_part_size=SITE_AVATAR_MAX_BYTES)
        if not site_access(request).write:
            return _forbidden(request, SITE_AVATAR_REFUSED_ROLE)
        site, key, refusal, forbidden = _mark_site(request, form)
        if site is None:
            return _forbidden(request, refusal) if forbidden else _refuse(request, refusal)
        config_dir = settings_page.config_dir(request)
        # THE MARK IS WRITTEN BESIDE THE SITE IT BELONGS TO: a mark write never CREATES the site
        # file, so a console with no site.yaml yet answers a refusal instead of guessing a folder.
        if not sites.site_file(config_dir).is_file():
            return _refuse(request, SITE_AVATAR_REFUSED_FILE)
        document, path, error = _document(request)
        if error:
            return _refuse_detail(request, error)
        if document is None:
            return _refuse(request, SITE_AVATAR_REFUSED_FILE)
        try:
            targets = avatar_paths(site.slug, config_dir)
        except AvatarRefusal as ex:
            return _refuse(request, ex.msgid, **ex.params)
        legacy = _legacy_block(request)
        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        try:
            configwrite.patch_config_file(path,
                                          lambda target: _write_avatar_flag(target, key, legacy,
                                                                            False),
                                          schemas=[], expect_revision=revision,
                                          applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            return _refuse_detail(request, str(ex))
        _discard_marks(targets)
        settings_page._notice(request, i18n.translate(request, SITE_AVATAR_REMOVED), ok=True,
                              refusal=True, applies=configwrite.APPLIES_NOW)
        return _back(request, key)

    @router.post(SITE_SHEET_UPLOAD_PATH,
                 dependencies=[Depends(sheet_form_dependency(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_sheet_upload(request: Request):
        """Publish ONE site's own palette sheet: a UTF-8 ``.css`` file under the FIXED leaf.

        The body is bounded before it is read; the uploaded NAME is read only for its suffix, because
        the stored leaf is :data:`services.webservice.sites.SHEET_LEAF` — no operator-supplied name
        ever reaches a path. The bytes must decode as UTF-8: a sheet is text by contract, and a file
        that is not is refused rather than served with replacement characters. The CONTENT is not
        checked at all: whatever CSS the operator uploaded is what the site serves.

        The file is staged beside its target and renamed into place, then ``theme.sheet`` goes
        through the same document the save writes; a config write that does not land puts the
        previous bytes back, so a refusal leaves the sheet in force exactly as it was. A re-upload
        overwrites the same file, which is why the site names a fixed leaf.
        """
        refused = _sheet_refusal(request)
        if refused:
            return _refuse(request, refused, limit=_sheet_byte_limit())
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS,
                                  max_part_size=sites.MAX_SHEET_LENGTH)
        if not site_access(request).write:
            return _forbidden(request, SITE_SHEET_REFUSED_ROLE)
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                too_big = int(declared) > sites.MAX_SHEET_LENGTH + SITE_SHEET_FORM_ALLOWANCE
            except ValueError:
                too_big = True
            if too_big:
                return _refuse(request, SITE_SHEET_REFUSED_BYTES, limit=_sheet_byte_limit())
        site, key, refusal, forbidden = _mark_site(request, form, SITE_SHEET_REFUSED_SCOPE)
        if site is None:
            return _forbidden(request, refusal) if forbidden else _refuse(request, refusal)
        config_dir = settings_page.config_dir(request)
        # A SHEET IS WRITTEN BESIDE THE SITE IT BELONGS TO: the write never CREATES the site file, so
        # a console with no site.yaml yet answers a refusal instead of guessing a folder.
        if not sites.site_file(config_dir).is_file():
            return _refuse(request, SITE_SHEET_REFUSED_FILE)
        document, path, error = _document(request)
        if error:
            return _refuse_detail(request, error)
        if document is None:
            return _refuse(request, SITE_SHEET_REFUSED_FILE)
        legacy = _legacy_block(request)
        probe = copy.deepcopy(document)
        _write_sheet_flag(probe, key, legacy, sites.SHEET_LEAF)
        if _avatar_entry(probe, key) is None:
            return _refuse(request, SITE_SHEET_REFUSED_FILE)
        try:
            target = sites.sheet_path(sites.SHEET_LEAF, config_dir)
        except ValueError as ex:
            return _refuse_detail(request, str(ex))
        upload = form.get(SITE_SHEET_FILE_FIELD)
        if not isinstance(upload, UploadFile):
            return _refuse(request, SITE_SHEET_REFUSED_NOT_CSS)
        if not str(upload.filename or "").lower().endswith(SITE_SHEET_SUFFIX):
            return _refuse(request, SITE_SHEET_REFUSED_NOT_CSS)
        payload = await upload.read(sites.MAX_SHEET_LENGTH + 1)
        await upload.close()
        if len(payload) > sites.MAX_SHEET_LENGTH:
            return _refuse(request, SITE_SHEET_REFUSED_BYTES, limit=_sheet_byte_limit())
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return _refuse(request, SITE_SHEET_REFUSED_ENCODING)

        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        # THE PREVIOUS BYTES ARE WHAT A FAILED CONFIG WRITE PUTS BACK: a sheet that exists but cannot
        # be read is refused here, before anything is staged, rather than raising out of the route.
        try:
            previous = target.read_bytes() if target.is_file() else None
        except OSError as ex:
            return _refuse_detail(request, str(ex))
        staged = stage_sheet_file(payload, target)
        try:
            os.replace(staged, target)
        except OSError as ex:
            _discard_marks([staged])
            return _refuse_detail(request, str(ex))
        try:
            configwrite.patch_config_file(
                path,
                lambda mutated: _write_sheet_flag(mutated, key, legacy, sites.SHEET_LEAF),
                schemas=[], expect_revision=revision, applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            _restore_sheet(target, previous)
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            _restore_sheet(target, previous)
            return _refuse_detail(request, str(ex))
        settings_page._notice(request, i18n.translate(request, SITE_SHEET_UPLOADED), ok=True,
                              refusal=True, applies=configwrite.APPLIES_NOW)
        return _back(request, key)

    @router.post(SITE_SHEET_REMOVE_PATH,
                 dependencies=[Depends(sheet_form_dependency(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_sheet_remove(request: Request):
        """Withdraw ONE site's uploaded sheet: the key clear first, then the file.

        The key is cleared through the same writer the save uses, so the site stops naming the file
        before it disappears. A file that is already gone is not an error: a second removal is a
        success and answers the same sentence.
        """
        refused = _sheet_refusal(request)
        if refused:
            return _refuse(request, refused, limit=_sheet_byte_limit())
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS,
                                  max_part_size=sites.MAX_SHEET_LENGTH)
        if not site_access(request).write:
            return _forbidden(request, SITE_SHEET_REFUSED_ROLE)
        site, key, refusal, forbidden = _mark_site(request, form, SITE_SHEET_REFUSED_SCOPE)
        if site is None:
            return _forbidden(request, refusal) if forbidden else _refuse(request, refusal)
        config_dir = settings_page.config_dir(request)
        if not sites.site_file(config_dir).is_file():
            return _refuse(request, SITE_SHEET_REFUSED_FILE)
        document, path, error = _document(request)
        if error:
            return _refuse_detail(request, error)
        if document is None:
            return _refuse(request, SITE_SHEET_REFUSED_FILE)
        try:
            target = sites.sheet_path((getattr(site.theme, "sheet", None) or sites.SHEET_LEAF),
                                      config_dir)
        except ValueError as ex:
            return _refuse_detail(request, str(ex))
        legacy = _legacy_block(request)
        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        try:
            configwrite.patch_config_file(path,
                                          lambda mutated: _write_sheet_flag(mutated, key, legacy,
                                                                            None),
                                          schemas=[], expect_revision=revision,
                                          applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            return _refuse_detail(request, str(ex))
        _discard_marks([target])
        settings_page._notice(request, i18n.translate(request, SITE_SHEET_REMOVED), ok=True,
                              refusal=True, applies=configwrite.APPLIES_NOW)
        return _back(request, key)

    @router.post(SITE_ADD_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_add(request: Request):
        """Add ONE site to ``site.yaml``: a key, the role it serves and its slug.

        The entry is written by the SAME primitive a card save uses, and the MUTATED document is
        parsed by the loader's own parser before the write commits: a ``SiteConfigError`` raised
        inside ``mutate`` aborts the write, so an invalid site is refused with the parser's own
        sentence and the file stays byte-identical. Only the keys the site needs are written —
        ``group_role`` and ``slug`` are omitted when empty, so the root site states nothing but its
        name. The role list is read from ``servers.yaml``, which is the only authority for the
        tokens a site may name.
        """
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        if not site_access(request).structure:
            return _forbidden(request, SITE_ADD_REFUSED_ROLE)
        key = readmodels.text(form.get(SITE_ADD_KEY_FIELD))
        role = readmodels.text(form.get(SITE_ADD_ROLE_FIELD))
        slug = readmodels.text(form.get(SITE_ADD_SLUG_FIELD))
        if not key or sites.SLUG_PATTERN.match(key) is None:
            return _refuse(request, SITE_ADD_REFUSED_KEY, key=key)
        if role and not slug:
            return _refuse(request, SITE_ADD_REFUSED_SLUG)
        config_dir = settings_page.config_dir(request)
        path = sites.site_file(config_dir)
        roles = known_roles(config_dir)
        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        legacy = _legacy_block(request)

        def mutate(target) -> None:
            from ruamel.yaml.comments import CommentedMap

            _seed(target, legacy)
            block = target.get("sites")
            if not isinstance(block, dict):
                block = CommentedMap()
                target["sites"] = block
            if key in block:
                raise SiteAddRefusal(SITE_ADD_REFUSED_EXISTS)
            entry = CommentedMap()
            if role:
                entry["group_role"] = role
            if slug:
                entry["slug"] = slug
            block[key] = entry
            sites.parse_sites(block, known_roles=roles, config_dir=config_dir)

        try:
            result = configwrite.patch_config_file(path, mutate, schemas=[],
                                                   expect_revision=revision,
                                                   applies=configwrite.APPLIES_ON_RESTART)
        except SiteAddRefusal as ex:
            return _refuse(request, ex.msgid, key=key)
        except sites.SiteConfigError as ex:
            return _refuse_detail(request, str(ex))
        except configwrite.StaleConfigRevision:
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            return _refuse_detail(request, str(ex))
        if not result.changed:
            settings_page._notice(request, settings_page.SAVED_NO_CHANGE, ok=True, refusal=True,
                                  applies=result.applies)
            return _back(request)
        # A NEW SITE IS ALWAYS STRUCTURAL — the running console has no route for it, and a site that
        # owns `/` must move the console with it. So the file is applied BEFORE the answer, exactly as
        # a structural card save is: otherwise the operator meets a site that exists in the file and
        # answers nothing (and a console still sitting at `/`) until the next restart.
        refusal = await config_apply.apply_console(request, app=request.app)
        if refusal:
            # THE FILE STAYS WRITTEN: the site exists, the running console did not move.
            settings_page._notice(request, refusal, ok=False, refusal=False,
                                  applies=configwrite.APPLIES_NOW)
        else:
            settings_page._notice(request, SITE_SAVED_APPLIED, ok=True, refusal=True,
                                  applies=configwrite.APPLIES_NOW)
        return _back(request, key)

    @router.post(SITE_YAML_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_yaml(request: Request):
        """Write ``site.yaml`` ITSELF from the Expert Mode panel's posted document.

        The document is parsed by the round-trip loader and its ``sites:`` block validated by the
        loader's own parser BEFORE the write, so a document that does not parse, an empty one
        (which would be written as ``null``) and a block the console would refuse to install are all
        refused with the reason and NOTHING is written — the file stays sha-identical.
        """
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        if not site_access(request).expert:
            return _forbidden(request, SITE_YAML_REFUSED_ROLE)
        config_dir = settings_page.config_dir(request)
        path = sites.site_file(config_dir)
        roles = known_roles(config_dir)
        revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
        text = readmodels.text(form.get(settings_page.SETTINGS_YAML_FIELD))
        try:
            document = site_document(text, path, config_dir, roles)
            result = configwrite.save_config_file(path, document, schemas=[],
                                                  expect_revision=revision,
                                                  applies=configwrite.APPLIES_ON_RESTART)
        except (SiteYamlEmpty, configwrite.ConfigParseError, sites.SiteConfigError) as ex:
            return _refuse_detail(request, _site_refusal(request, ex))
        except configwrite.StaleConfigRevision:
            return _refuse(request, SITE_REFUSED_STALE)
        except configwrite.ConfigWriteError as ex:
            return _refuse_detail(request, str(ex))
        if not result.changed:
            settings_page._notice(request, settings_page.SAVED_NO_CHANGE, ok=True, refusal=True,
                                  applies=result.applies)
            return _back(request)
        settings_page._notice(request, settings_page.SAVED_RESTART, ok=True, refusal=True,
                              applies=result.applies)
        return _back(request)

    @router.post(SITE_YAML_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(
                     settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def site_settings_yaml_validate(request: Request):
        """The Expert Mode panel's live check: a JSON verdict over the posted document, nothing written.

        The same reader the save uses (:func:`site_document`), so the editor and the write cannot
        disagree about what a document is worth. A parse refusal carries its ``line``, which the
        panel's script marks in the editor.
        """
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        if not site_access(request).expert:
            return JSONResponse({"ok": False,
                                 "message": i18n.translate(request, SITE_YAML_REFUSED_ROLE)},
                                status_code=403)
        config_dir = settings_page.config_dir(request)
        path = sites.site_file(config_dir)
        text = readmodels.text(form.get(settings_page.SETTINGS_YAML_FIELD))
        try:
            site_document(text, path, config_dir, known_roles(config_dir))
        except configwrite.ConfigParseError as ex:
            return JSONResponse({"ok": False, "message": str(ex), "line": ex.line})
        except (SiteYamlEmpty, sites.SiteConfigError) as ex:
            return JSONResponse({"ok": False, "message": _site_refusal(request, ex)})
        return JSONResponse({"ok": True})

    return router


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    declare()
    return {SITE_CONFIG_PATH: SITE_VIEW_CAPABILITY,
            SITE_MOCK_PATH: SITE_VIEW_CAPABILITY,
            SITE_MARK_PATH: SITE_VIEW_CAPABILITY,
            SITE_CONFIG_SAVE_PATH: SITE_WRITE_CAPABILITY,
            SITE_CONFIG_DRAFT_PATH: SITE_WRITE_CAPABILITY,
            SITE_CSS_VALIDATE_PATH: settings_page.SETTINGS_WRITE_CAPABILITY,
            SITE_ADD_PATH: SITE_STRUCTURE_CAPABILITY,
            SITE_YAML_PATH: SITE_EXPERT_CAPABILITY,
            SITE_YAML_VALIDATE_PATH: SITE_EXPERT_CAPABILITY,
            SITE_AVATAR_UPLOAD_PATH: SITE_WRITE_CAPABILITY,
            SITE_AVATAR_REMOVE_PATH: SITE_WRITE_CAPABILITY,
            SITE_SHEET_UPLOAD_PATH: SITE_WRITE_CAPABILITY,
            SITE_SHEET_REMOVE_PATH: SITE_WRITE_CAPABILITY}


def nav_items() -> tuple[NavItem, ...]:
    """The sidebar entry: under Administration, beside Settings, gated by ``site.view``."""
    return (NavItem(label=SITE_TITLE, url=SITE_CONFIG_PATH, capability=SITE_VIEW_CAPABILITY,
                    group="Administration", glyph="layers"),)
