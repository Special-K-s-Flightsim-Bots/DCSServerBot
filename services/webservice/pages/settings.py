"""The Settings page — the console's first SCHEMA-DRIVEN config page.

A page is not hand-written per file: it is GENERATED from the pykwalify schema the bot already
validates that file against (see :mod:`services.webservice.configgen`). This page is the generator's
first real consumer — the install's ``main.yaml``, its DEFAULT configuration — and it is here to
prove the machinery end to end: a schema becomes a form, the form's save goes through the ONE write
primitive (``core.utils.configwrite``'s ``patch_config_file``), and the honest banner says a
restart applies it.

WHAT IS WHERE
-------------
* the GET renders the generated form (left) and the file RE-SERIALISED as YAML (right, READ-ONLY in
  this version — it is a dump of the parse, not the raw bytes, and for a no-op save nothing is
  written); expert mode is deliberately NOT built here;
* the POST reads the submitted fields back through the SAME generator tree, mutates the round-trip
  document in place (:func:`configgen.apply_form`), and hands it to ``patch_config_file`` — it never
  opens, parses or writes a file itself, so there is no second write path;
* the capability is declared through the console's own pipeline (``permissions.declare_capability``),
  Admin-only and with NO scope grant — the install's own configuration is not a server a manager's
  scope could name — and the route is gated by the registrar like every other.

EVERY PAGE-WORD IS WRAPPED, EVERY KEY IS NOT
--------------------------------------------
The chrome this page composes (its title, its lead, the "Add", "Remove", "validate on save" markers,
the restart banner) is ``i18n._``-marked and filled in every catalog. A field's LABEL is its schema
key, printed verbatim: the schemas carry identifiers, not prose, so a key stays English in every
language (see :mod:`configgen`). Do not translate a key.
"""
from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.utils import configwrite

from .. import (catalog, config_apply, configgen, i18n, permissions, plugin_registry, readmodels,
                scheduler_plan, scope as identity_scope, scopes, session, validation)
from ..registry import NavItem
from . import actions as actions_page
from . import dashboard as dashboard_page

__all__ = [
    "SETTINGS_PATH", "SETTINGS_VIEW_CAPABILITY", "SETTINGS_WRITE_CAPABILITY", "SETTINGS_ROLES",
    "SETTINGS_SAVE_PATH", "SETTINGS_VALIDATE_PATH", "SETTINGS_REVISION_FIELD",
    "SETTINGS_EXPERT_FIELD", "SETTINGS_YAML_FIELD",
    "SETTINGS_FILE_PATH", "SETTINGS_FILE_SAVE_PATH", "SETTINGS_VALIDATE_FILE_PATH",
    "SETTINGS_MAIN_PATH", "SETTINGS_SERVICE_TOGGLE_PATH", "SETTINGS_FORM_MAX_FIELDS",
    "SETTINGS_ADD_BLOCK_PATH", "SETTINGS_APPLY_PATH", "SETTINGS_APPLY_ASK_PATH", "KIND_NODES",
    "KIND_MAIN", "KIND_SERVICE", "KIND_PLUGIN", "KIND_LABELS",
    "SETTINGS_FILE_VIEW_CAPABILITY", "SETTINGS_FILE_WRITE_CAPABILITY",
    "declare_file_capabilities", "file_capability", "require_file_capability",
    "GlobalFile",
    "discover_files", "file_entries", "file_url",
    "load_file_page", "resolve_file",
    "load_landing",
    "SETTINGS_TEMPLATE", "SETTINGS_TILES_TEMPLATE", "SETTINGS_TITLE", "SETTINGS_LEAD",
    "SETTINGS_TILES_LEAD", "CONFIG_FILE_NAME", "MAIN_SCHEMA_PATH",
    "SCOPE_NODE_LABEL", "SCOPE_SLICE_NOTE", "SCOPE_NODE_LEAD",
    "SCOPE_NOTE_DEFAULT", "SCOPE_NOTE_OVERRIDE", "SCOPE_NOTE_PLAIN",
    "SAVED_RESTART", "SAVED_NOW", "SAVED_NO_CHANGE", "SAVE_REFUSED_UNPLACEABLE",
    "SAVE_REFUSED_DUPLICATE_ENTRY", "SAVE_REFUSED_EMPTY_DOCUMENT",
    "SAVE_REFUSED_FOREIGN_NODE", "SAVE_REFUSED_UNKNOWN_NODE", "VALIDATE_UNKNOWN_NODE",
    "SCHEMA_INVALID_BANNER", "NO_SCHEMA_BANNER", "FILES_LABEL",
    "YAML_NOT_READABLE", "YAML_NOT_AGAINST_SCHEMA", "YAML_NOT_AGAINST_SCHEMA_BLOCK",
    "capabilities", "nav_items", "add_routes", "config_path", "validate_text",
    "validate_slice", "node_slice_keys", "scoped_write_guard", "scoped_context", "is_scoped",
    "REMOVE_ACK_KEY", "REMOVE_ACK_FIELD", "SAVE_REFUSED_REMOVE_ACK", "mint_remove_ack",
    "validate_remove_ack", "spend_remove_ack", "visibility_context", "removal_paths",
    # the Scheduler wizard's ONE write door
    "SCHEDULER_SAVE_PATH", "SCHEDULER_VALIDATE_PATH", "SCHEDULER_SCOPE_FIELD", "SCHEDULER_KIND",
    "SCHEDULER_FILE_NAME", "SCHEDULER_FALLBACK_URL", "SCHEDULER_RESERVED_SECTIONS",
    "SCHEDULER_VIEW_CAPABILITY", "SCHEDULER_WRITE_CAPABILITY", "SCHEDULER_EXPERT_CAPABILITY",
    "SCHEDULER_ROLES", "SCHEDULER_EXPERT_REFUSED", "SchedulerAccess", "scheduler_access",
    "SchedulerRefusal", "SchedulerScope", "scheduler_write_guard", "scheduler_redirect",
    "scheduler_override", "scheduler_candidate", "scheduler_verdict",
    "scheduler_expert_answer", "scheduler_validate_block", "scheduler_posted_scope",
    "scheduler_block_key",
]

log = logging.getLogger(__name__)

#: the page path and the save route, both literal and constant.
SETTINGS_PATH = "/settings"
SETTINGS_SAVE_PATH = "/actions/settings/config"
#: THE EXPERT MODE VALIDATOR ROUTE. The editor checks EVERY change through it; it runs the bot's own
#: schema validation over the posted text and answers with a structured, position-aware verdict in
#: JSON (never a page). A POST with the session's CSRF token — it carries the file's text, so it can
#: not be a query string, and it is a read of the admin's own configuration, gated like the write.
SETTINGS_VALIDATE_PATH = "/actions/settings/validate"

#: THE PER-FILE SETTINGS ROUTES. ``main.yaml`` is served by the two paths above; every OTHER global
#: config file (a service's own file) gets its own generated page and its own save/validate, reachable
#: from the entries list on the Settings area. The list itself is read from DISK at render time
#: (:func:`discover_files`) — never a hard-coded table — so a new service file shows up the moment it
#: is dropped in ``config/services/``.
SETTINGS_FILE_PATH = "/settings/{kind}/{name}"
SETTINGS_FILE_SAVE_PATH = "/actions/settings/config/{kind}/{name}"
SETTINGS_VALIDATE_FILE_PATH = "/actions/settings/validate/{kind}/{name}"

#: THE MAIN CONFIG PAGE'S OWN URL. ``/settings`` is now the tile LANDING page, so ``main.yaml``'s
#: generated form — like every other file's — lives behind its cogwheel at the generic file URL.
SETTINGS_MAIN_PATH = SETTINGS_FILE_PATH.format(kind="main", name="main")

#: THE SERVICE ON/OFF ROUTE. A service is enabled by its config file's PRESENCE — there is no
#: boolean to flip — so turning one off MOVES its file aside (``<name>.yaml`` -> ``<name>.yaml.disabled``)
#: and turning it back on moves it home. No new ``enabled:`` key is invented: the bot reads
#: ``config/services/<name>.yaml`` and nothing else (``core/services/base.py::read_locals``), and a
#: missing file is ITS OWN de-facto disabled state. A service held off by its PLUGIN dependency is
#: NOT this route's business: nothing is moved, the tile points at the plugin list instead.
SETTINGS_SERVICE_TOGGLE_PATH = "/actions/settings/service/{name}"

#: THE "+ ADD BLOCK" ROUTE. It creates a NEW node/instance block in the file the operator opened
#: (``<node>: {<instance>: {}}`` in a multi-node install, ``<instance>: {}`` in a single-node one) and
#: writes it through the ONE write primitive — the same schema-validated, atomic, ``.bak``-keeping
#: path a form save uses. It is what makes "adding a block in the UI" create that block in the YAML.
SETTINGS_ADD_BLOCK_PATH = "/actions/settings/addblock/{kind}/{name}"

#: THE APPLY DOOR's two routes (POST-only, and the ONE control a save leaves). ``ASK`` renders the
#: confirmation, ``APPLY`` runs it; both are registered by their own module
#: (:mod:`services.webservice.config_apply`) under its own capability declaration, and re-exported
#: here so a page reads ONE spelling of the path.
SETTINGS_APPLY_PATH = config_apply.APPLY_PATH
SETTINGS_APPLY_ASK_PATH = config_apply.APPLY_ASK_PATH


#: The field-count ceiling the Settings forms are parsed with. Starlette's default (1000) is a DoS
#: guard, but a schema-generated page legitimately exceeds it — a service schema's DSL can walk to
#: hundreds of fields per entry, so one file posts thousands. The Settings save/validate routes
#: therefore parse their own form (and the CSRF dependency reads it) up to this bound; no other route
#: is affected. ``session.csrf_protect_with`` is the SAME predicate as ``csrf_protect``, only bound.
SETTINGS_FORM_MAX_FIELDS = 20000

#: The KINDS of global config file the Settings area lists beside ``main.yaml``. Each kind names a
#: DISCOVERY rule (where its files live) and a SCHEMA rule (which schema files validate them) — see
#: :func:`discover_files` and the per-kind helpers below. A sibling card adds a kind here, not a
#: second mechanism.
#:
#: EXTENSIONS ARE NOT A KIND HERE ANY MORE. An extension's configuration
#: lives INSIDE ``nodes.yaml``, and the NODE PAGE (``/nodes/<node>``) now edits it — its DEFAULT tab
#: carries the node's own extensions and each instance tab that instance's own extensions. That made
#: the old EXTENSIONS rail + per-extension cogwheel (``/settings/extension/<name>``) a genuine
#: duplicate, so both are gone; ``catalog.discover_extensions`` stays a library the node page consumes.
KIND_MAIN = "main"
KIND_SERVICE = "service"
KIND_PLUGIN = "plugin"
#: ``nodes.yaml`` is NOT a listed kind (the node page owns it), but the apply door resolves it by this
#: name, so the mapping and the route share one spelling.
KIND_NODES = "nodes"

#: The chrome word the entries list shows for each kind. i18n-wrapped like every other composed word.
KIND_LABELS: dict[str, str] = {
    KIND_MAIN: i18n._("Main"),
    KIND_SERVICE: i18n._("Service"),
    KIND_PLUGIN: i18n._("Plugin"),
}
#: The heading over the entries list — the FILES, one entry each.
FILES_LABEL = i18n._("Configuration files")

#: the capabilities the page and its write declare — one string each, so the route, the nav item and
#: the capability map can never drift.
SETTINGS_VIEW_CAPABILITY = "settings.view"
SETTINGS_WRITE_CAPABILITY = "settings.write"

#: THE FILE ROUTES' GATE — the capabilities the PARAMETERISED file paths are declared with.
#:
#: ONE route serves every kind of global file this area lists, and every one of them is installation
#: configuration edited by the same caller, so the two capabilities are the Settings area's own and
#: each request is required to hold the matching one (:func:`file_capability`).
SETTINGS_FILE_VIEW_CAPABILITY = "settings.file.view"
SETTINGS_FILE_WRITE_CAPABILITY = "settings.file.write"

#: the install's own configuration is ADMIN work and carries NO manager scope grant: a manager's
#: scope is a set of SERVERS, and "one of mine runs there" must never become "let me edit main.yaml".
SETTINGS_ROLES: tuple[str, ...] = ("Admin",)

#: THE SCHEDULER'S OWN TWO CAPABILITIES, plus the expert door. The scheduler file is one block per
#: server, so its audience is per server: a DCS Admin reads and writes the servers its scope holds,
#: a manager reads only (the scope grant admits it to the READ, which the per-server scope then
#: narrows), and Expert Mode — free-hand YAML for the block — stays ``Admin`` only.
SCHEDULER_VIEW_CAPABILITY = "scheduler.view"
SCHEDULER_WRITE_CAPABILITY = "scheduler.write"
SCHEDULER_EXPERT_CAPABILITY = "scheduler.expert"
SCHEDULER_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")

SETTINGS_TEMPLATE = "settings.html"
SETTINGS_TITLE = i18n._("Settings")
SETTINGS_LEAD = i18n._("The bot's own configuration, generated from its schema. The form and the "
                       "YAML are one file: a save writes only what you changed, and a restart "
                       "applies it.")
NAV_LABEL = i18n._("Settings")
CRUMB_GROUP = dashboard_page.CRUMB_GROUP

#: the hidden field the form carries its rendered revision in, so the save can refuse a stale form.
SETTINGS_REVISION_FIELD = "_revision"

#: THE EXPERT MODE FIELDS. ``_expert`` is a hidden companion the toggle script keeps at "0" (off) or
#: "1" (on); ``_yaml`` is the editor's text, carried by a control the script NAMES on entry — with
#: JavaScript off neither is posted, so the plain form is exactly what it always was. Both are
#: ``_``-prefixed, so ``configgen.apply_form`` (which reads only ``f.``-prefixed fields) never sees
#: them; the save route reads them itself.
SETTINGS_EXPERT_FIELD = "_expert"
SETTINGS_YAML_FIELD = "_yaml"

#: THE VALIDATOR'S SENTENCES. The editor's summary line and the save's refusal both name the FIRST
#: error in the reader's language; the technical detail the validator appends (the pykwalify message)
#: stays English, exactly like an action's own message. ``i18n._`` marks them for extraction; they
#: render through :func:`i18n.translate` for the request's language.
YAML_NOT_READABLE = i18n._("Not valid YAML on line %(line)s, column %(column)s.")
YAML_NOT_AGAINST_SCHEMA = i18n._("Not valid against the schema — line %(line)s, in %(block)s.")
YAML_NOT_AGAINST_SCHEMA_BLOCK = i18n._("Not valid against the schema — in %(block)s.")

#: the first real consumer: the install's ``main.yaml`` against its own schema.
CONFIG_FILE_NAME = "main.yaml"
#: the config key the dedicated PLUGIN BLOCK renders (and the schema control it replaces on the
#: form). The build of that block lives in ``services.webservice.plugin_registry``; the page only
#: decides WHICH file gets it (``main.yaml``) and which form control it stands in for.
PLUGINS_FIELD = plugin_registry.OPT_PLUGINS_FIELD

#: THE KEYS THE ``main.yaml`` EDITOR DOES NOT SHOW. Both plugin lists are managed by the plugin TILES
#: on the Settings landing (each tile's switch posts the SAME ``f.plugins`` / ``f.opt_plugins`` list
#: through the SAME save route), so the main.yaml editor omits them. This can NEVER round-trip
#: anything away: the save only writes the fields the form POSTS (:func:`configgen.apply_form`), so a
#: ``plugins``/``opt_plugins`` list already in the file comes back byte-identical (the main editor's
#: own save tree drops these keys too, so even a crafted POST cannot reach them — see
#: :func:`entry_save_tree`).
MAIN_SUPPRESSED_FIELDS: frozenset[str] = frozenset(
    {plugin_registry.DEFAULT_FIELD, PLUGINS_FIELD})
#: the schema the file is validated against — spelled absolutely from THIS file so the console never
#: depends on the process working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_SCHEMA_PATH = str(_REPO_ROOT / "schemas" / "main_schema.yaml")
#: the file a node's configuration lives in (``config/nodes.yaml``) — read here ONLY for the cluster's
#: node NAMES (the write guard's node set). The file itself is edited by the NODE page
#: (``pages/node_settings``); its schema is spelled there, not here.
NODES_YAML_NAME = "nodes.yaml"

#: the honest banner. A plain file write applies on the next bot restart — the console must say so.
SAVED_RESTART = i18n._("Saved — this configuration applies on the next bot restart.")
#: the OTHER honest banner: a write a caller made through a bot method that genuinely refreshed the
#: in-memory copy carries :data:`core.utils.configwrite.APPLIES_NOW`, and the banner says so. The
#: page picks between the two from the primitive's OWN ``applies`` value (never a hard-coded claim),
#: so the banner stays true whichever story the write reports.
SAVED_NOW = i18n._("Saved — this change applies immediately.")
SAVED_NO_CHANGE = i18n._("Nothing changed — the file is untouched.")
REFUSED_BANNER = i18n._("The change was refused, and nothing was written.")
#: The refusal a save shows when the form carried a field the schema cannot place (an
#: :class:`configgen.UnknownFieldError`). Such a field would have been DROPPED while the save still
#: reported success — the one failure this page must never have — so the save is refused instead and
#: the operator is told why. Rendered through ``_(…)`` by the one-shot notice (``refusal=True``),
#: so it follows the page's language.
SAVE_REFUSED_UNPLACEABLE = i18n._(
    "No change was saved: the form carried a field this page cannot place in the config, so saving "
    "it would have silently dropped it. Nothing was written — reload the page and try again.")
#: The refusal a save shows when a NEW custom entry's key already names an existing entry: writing it
#: would OVERWRITE that entry (silent data loss), so the save is refused and nothing is written.
SAVE_REFUSED_DUPLICATE_ENTRY = i18n._(
    "No change was saved: a new entry's key already names an existing entry, so saving it would have "
    "overwritten that entry. Give the new entry a different key — nothing was written.")
#: The refusal a save shows when the posted Expert Mode document is EMPTY. An empty document is not a
#: document: the round-trip loader parses ``""`` (and a comments-only text) to ``None``, and writing
#: that would replace the whole file with ``null`` — silent data loss, and on a SCHEMA-LESS file there
#: is no schema to catch it. Refused exactly as an invalid document is refused — nothing written, the
#: file left sha-identical. Rendered through ``_(…)`` by the one-shot notice (``refusal=True``).
SAVE_REFUSED_EMPTY_DOCUMENT = i18n._(
    "No change was saved: the document is empty, and an empty document is not a document — saving it "
    "would replace the file with nothing. Edit the file and try again; nothing was written.")
#: The refusal a scoped save shows when a posted field names a node that is NOT the one the page was
#: scoped to. Writing it would rewrite another node's block (data loss for a node the operator did not
#: open); a silent DROP would be a lie about what was saved. So the save is refused and nothing is
#: written. Applies to BOTH doors into a scoped file — the plain form and the Expert Mode slice.
SAVE_REFUSED_FOREIGN_NODE = i18n._(
    "No change was saved: the form named a node that is not the selected one, so saving it would have "
    "rewritten another node's block. Reload the page, pick that node if you meant to edit it, and try "
    "again — nothing was written.")
#: The refusal an Expert Mode save of a scoped file shows when the page does not name a node the FILE
#: carries (a bogus or absent ``?node=``). The posted slice is then NOT promoted to a whole-file write
#: — that would delete every other node's block — so the save is refused and nothing is written.
SAVE_REFUSED_UNKNOWN_NODE = i18n._(
    "No change was saved: this page did not name a node this file carries, so there is no part of the "
    "file to write. Reload the page and pick a node — nothing was written.")
#: The verdict the Expert Mode validator returns when the page does not name a node the file carries:
#: validating the posted slice as if it were the whole file would be a misleading verdict, so the
#: check says it cannot place the node instead.
VALIDATE_UNKNOWN_NODE = i18n._(
    "This editor did not name a node this file carries, so its part of the file cannot be checked. "
    "Reload the page and pick a node.")

#: Shown on a file page whose content does NOT validate against its schema. The form is still
#: generated from the schema (so the operator can see the intended shape), but the page must NOT
#: pretend the file is fine: the fault is stated, with the validator's own detail appended. The
#: schema is the SAME one a save validates against, so correcting here is what lets a save land.
SCHEMA_INVALID_BANNER = i18n._(
    "This file does not validate against its schema. The form is still generated from the schema, "
    "but the file on disk has an error — correct it before saving.")
#: Shown on a file page whose kind ships NO schema (e.g. a service with no ``schemas/`` directory):
#: the form cannot be generated, so the page is read-only and says why. Never a silent empty form.
NO_SCHEMA_BANNER = i18n._(
    "No schema ships for this file, so its form cannot be generated — it is shown read-only.")

# --------------------------------------------------------------------------------- the landing
# THE LANDING PAGE IS A GRID OF TILES — one per plugin, one per service — not a list of YAML
# filenames. Each tile carries the item's name, its own one-line description, an on/off control that
# is HONEST about how the bot works, and a cogwheel into the item's configuration. (The EXTENSIONS
# rail and the ``nodes.yaml`` tile are GONE: extension and node configuration are reached from the
# NODES page, through the node page at ``/nodes/<node>``.)

SETTINGS_TILES_TEMPLATE = "settings_tiles.html"

#: THE SCOPED (COGWHEEL) VIEW's template — a plugin's / a service's / an extension's own file, shown
#: as TABS (DEFAULT + one per node/instance block). It shares the ONE save path and the ONE Expert
#: Mode editor with the Settings area.
SETTINGS_SCOPE_TEMPLATE = "settings_scope.html"

#: the back link and the "+ Add block" control on the scoped view.
SCOPE_BACK_LABEL = i18n._("Settings")
ADD_BLOCK_INSTANCE_PLACEHOLDER = i18n._("instance name")
#: THE NODE SELECTOR's chrome (the mechanism: pick the NODE first, then see only its 1 + N tabs).
#: ``SCOPE_NODE_LABEL`` reuses the ``Node`` msgid the Nodes page already owns — the selector names a
#: node, exactly as that page does. ``SCOPE_NODE_LEAD`` states the rule once, over the selector.
SCOPE_NODE_LABEL = i18n._("Node")
SCOPE_NODE_LEAD = i18n._(
    "Pick a node to configure. The tabs then show only its own block and its instances — never every "
    "node at once.")
#: The sentence over the Expert Mode panel when a node is selected: the editor holds that node's slice
#: of the file, not the whole file, and a save writes the slice back leaving the other nodes untouched.
SCOPE_SLICE_NOTE = i18n._(
    "Expert Mode edits only this node's part of the file — every other node is left untouched.")
#: the sentence over a scope's fields. ``<b>DEFAULT</b>`` is the shared block; an override INHERITS.
SCOPE_NOTE_DEFAULT = i18n._(
    "<b>DEFAULT</b> is the scope every instance starts from. A block under it changes only that node "
    "or instance — and because the scope is the TAB, no field carries a <code>default.</code> prefix.")
SCOPE_NOTE_OVERRIDE = i18n._(
    "This block changes only <b>%(scope)s</b>. It INHERITS everything it does not set from DEFAULT — "
    "nothing is copied, so a field left unset keeps following the default block.")
#: the sentence over a scope that has NO node/instance blocks to switch between: a service whose schema
#: declares no override container (a flat ``bot``/``backup`` config, or a bare ``DEFAULT`` block). The
#: whole file is the one scope, so the DEFAULT/override story above would be a lie — this says what the
#: operator is actually looking at.
SCOPE_NOTE_PLAIN = i18n._(
    "This configuration has no node or instance blocks — the whole file is one scope, so no field "
    "carries a <code>DEFAULT.</code> prefix.")
#: the notices the "+ Add block" route leaves.
ADD_BLOCK_DONE = i18n._("Added the block %(scope)s.")
ADD_BLOCK_EXISTS = i18n._("The block %(scope)s already exists — nothing was added.")
ADD_BLOCK_REFUSED = i18n._("No block was added: %(detail)s")
ADD_BLOCK_NAME_REQUIRED = i18n._("no name was given.")

SETTINGS_TILES_LEAD = i18n._(
    "Every plugin, service and extension of this install, each with its own configuration behind "
    "one cogwheel. A service change applies on the next bot restart.")

#: the three rail headings.
PLUGINS_HEADING = i18n._("Plugins")
SERVICES_HEADING = i18n._("Services")

#: the rail legends — where each kind is written and how it is enabled (a fact stated ONCE per rail).
#: The plugin legend names BOTH lists: a default plugin loads when it is in ``plugins`` (or the key
#: is absent — the bot falls back to ``DEFAULT_PLUGINS``), and ``opt_plugins`` adds optional ones.
PLUGINS_LEGEND = i18n._(
    "Load on the bot master node · the lists live in main.yaml › plugins and opt_plugins · "
    "%(on)s of %(total)s load today")
SERVICES_LEGEND = i18n._(
    "Node-wide background workers · one file each in config/services/ · "
    "changes apply on the next bot restart")

#: the sentence the plugin "+ Add" tile shows when EVERY optional plugin is already enabled. The tile
#: is STILL drawn (a control that vanishes silently is worse than one that says why, the operator, card
#: the change) in an explicit, NON-INTERACTIVE state with this reason as VISIBLE text.
PLUGIN_ADD_ALL_CONFIGURED = i18n._(
    "Every optional plugin is enabled already — there is nothing left to add.")

#: the scope lines a tile carries. A plugin is bot-master work and states so once in the rail legend.
SERVICE_RUNS_ON = i18n._("runs on")

#: the on/off control's NOUN, per kind — a bare tick is the "plain string list" problem one pixel to
#: the right, so each switch names what off means for ITS kind.
PLUGIN_SWITCH_ON = i18n._("Load on")
PLUGIN_SWITCH_OFF = i18n._("Load off")
SERVICE_SWITCH_ON = i18n._("Configured")
SERVICE_SWITCH_OFF = i18n._("Not configured")
COG_LABEL = i18n._("Config")

#: the service tile's state sentences.
SERVICE_HELD_BY_PLUGIN = i18n._(
    "Held off by the %(plugin)s plugin — it is not in opt_plugins. Add it there to enable this "
    "service; nothing is moved.")
SERVICE_NOT_CONFIGURED = i18n._(
    "No config/services/%(name)s.yaml — the service has nothing to read and does not run.")
SERVICE_TURN_OFF = i18n._("Turn off")
SERVICE_TURN_ON = i18n._("Turn on")
SERVICE_OFF_APPLIES = i18n._("Saved — the service change applies on the next bot restart.")

#: a plugin entry already in opt_plugins that maps to no shipped plugin — shown, never dropped.
PLUGIN_NOT_INSTALLED = plugin_registry.MISSING_BADGE

#: THE INLINE WARNING a DEFAULT plugin's tile shows BEFORE it is switched off, and the two controls
#: beside it. In other words (or equally clear): a default plugin loads regardless of what
#: ``opt_plugins`` says, so turning one off genuinely can change how the bot works — the warning is
#: rendered ON the page (the switch LINKS here, the write happens only on the confirm button), never
#: a modal dismissed blind. The warning is shown for DEFAULT plugins only.
PLUGIN_DEFAULT_WARNING = i18n._(
    "You are turning off a default plugin. This can affect the functionality of DCSServerBot.")
PLUGIN_DEFAULT_TURN_OFF = i18n._("Switch it off anyway")
PLUGIN_DEFAULT_KEEP = i18n._("Keep it on")

#: the two notices the service toggle route leaves.
SERVICE_MOVED_ASIDE = i18n._(
    "Service turned off — its config was moved to %(path)s. It applies on the next bot restart.")
SERVICE_MOVED_BACK = i18n._(
    "Service turned on — its config was moved back to %(path)s. It applies on the next bot restart.")
SERVICE_TOGGLE_REFUSED = i18n._("No change was made: this service has no config file to move.")
SERVICE_TOGGLE_HELD = i18n._(
    "Nothing was moved: this service is held off by its plugin dependency, not by a missing file. "
    "Add the plugin to opt_plugins instead.")
SERVICE_TOGGLE_UNKNOWN = i18n._("No such service.")


def declare() -> None:
    """Declare the Settings capabilities and the Scheduler's own three.

    Idempotent for the same roles. The Settings pair stays ``Admin`` only with no scope grant; the
    Scheduler pair is per server (the server is checked separately) and only its READ carries the
    manager grant, so a manager can look at their own schedule and never write it.
    """
    permissions.declare_capability(SETTINGS_VIEW_CAPABILITY, SETTINGS_ROLES)
    permissions.declare_capability(SETTINGS_WRITE_CAPABILITY, SETTINGS_ROLES)
    permissions.declare_capability(SCHEDULER_VIEW_CAPABILITY, SCHEDULER_ROLES, scope_grants=True)
    permissions.declare_capability(SCHEDULER_WRITE_CAPABILITY, SCHEDULER_ROLES)
    permissions.declare_capability(SCHEDULER_EXPERT_CAPABILITY, ("Admin",))


declare()


@dataclass(frozen=True)
class SchedulerAccess:
    """What ONE identity may do with ONE server's schedule: read, write, and the expert door.

    ``expert`` is separate from ``write`` because Expert Mode writes free-hand YAML for the block,
    which is narrower than the plan-shaped write.
    """

    view: bool
    write: bool
    expert: bool


def scheduler_access(request: Request, server=None) -> SchedulerAccess:
    """THE Scheduler capability decision for this request and *server*.

    Every reader calls this one function: the tab strip, the page route's refusal for
    ``?tab=scheduler``, the landing read, and both POST doors. The capability check is
    ``permissions.allows`` (the gate's own predicate); the per-server half is
    ``scope.server_in_scope`` — the SAME rule the scoped source and the ban/action scope apply,
    never a second spelling.

    ``server=None`` answers the capability question alone (no server to scope against) and is only
    meant for tests and for callers that have already resolved one; a real door passes its server,
    so an out-of-scope server can never earn a grant here.
    """
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    in_scope = server is None or identity_scope.server_in_scope(
        permissions.scope_for(request), server)
    return SchedulerAccess(
        view=in_scope and permissions.allows(SCHEDULER_VIEW_CAPABILITY, roles, manager=manager),
        write=in_scope and permissions.allows(SCHEDULER_WRITE_CAPABILITY, roles, manager=manager),
        expert=in_scope and permissions.allows(SCHEDULER_EXPERT_CAPABILITY, roles, manager=manager),
    )


def _audience(*capabilities: str) -> tuple[str, ...]:
    """The roles that satisfy ANY of *capabilities*, in first-seen order."""
    roles: list[str] = []
    for capability in capabilities:
        for role in (permissions.roles_for_capability(capability) or ()):
            if role not in roles:
                roles.append(role)
    return tuple(roles)


def declare_file_capabilities() -> None:
    """Declare the two capabilities the PARAMETERISED file paths are gated with (see the constants).

    Their audience is the Settings area's own: one route serves every kind of global file this area
    lists, and all of them are installation configuration edited by the same caller. A set that is
    already declared is left alone — registration happens on every application rebuild, and the roles
    a caller must hold are the table's, not this call's.
    """
    for capability, audience in ((SETTINGS_FILE_VIEW_CAPABILITY, (SETTINGS_VIEW_CAPABILITY,)),
                                 (SETTINGS_FILE_WRITE_CAPABILITY, (SETTINGS_WRITE_CAPABILITY,))):
        if permissions.roles_for_capability(capability) is not None:
            continue
        permissions.declare_capability(capability, _audience(*audience))


def file_capability(kind: str, *, write: bool) -> str:
    """The capability ONE KIND of global file requires — the Settings area's own, read or write.

    Every kind this area serves is installation configuration, so the decision does not vary by kind;
    the function stays so the route and the gate keep reading ONE rule.
    """
    return SETTINGS_WRITE_CAPABILITY if write else SETTINGS_VIEW_CAPABILITY


def require_file_capability(request: Request, kind: str, *, write: bool) -> None:
    """Refuse with a 403 unless the caller holds the capability *kind* requires (:func:`file_capability`).

    The decision is ``permissions.allows`` — the very predicate the access gate runs — read the same
    way the gate reads it, so this check and the gate cannot disagree about one capability.
    """
    capability = file_capability(kind, write=write)
    roles = permissions.role_names_for(request)
    granted = permissions.allows(capability, roles)
    if not granted and permissions.scope_grants(capability):
        granted = permissions.allows(capability, roles, manager=permissions.manages_console(request))
    if not granted:
        raise HTTPException(status_code=403, detail=permissions.requirement_text(capability))


# ------------------------------------------------------- the Scheduler wizard's one write door
# ONE file (``config/plugins/scheduler.yaml``) whose top-level blocks are SERVER names plus three
# reserved sections. Its scope NEVER comes from the file, from ``?node=`` or from a posted ``f.``
# field: the tab's own ``_scope`` field names a server and :func:`dashboard_page.server_named` — the
# console's ONE place a name becomes a server, under the caller's scope — resolves it.
#
# The shared :func:`scoped_write_guard` is deliberately NOT used here. For this file it answers
# ``(False, [], frozenset(node_names))``: no allow-list at all and a deny-list of node names only, so
# a crafted field name could reach ``DEFAULT`` or another server's block. The scheduler guard reads
# its own reserved set and returns its own allow-list instead.
#
# The write is a PER-KEY override through ``configwrite.patch_config_file`` — never
# ``save_config_file``, which replaces the document and would lose every other server's block.

SCHEDULER_SAVE_PATH = "/actions/settings/scheduler"
SCHEDULER_VALIDATE_PATH = SCHEDULER_SAVE_PATH + "/validate"
#: the field the tab's scope travels in — its OWN name, so nothing infers it from a plan value.
SCHEDULER_SCOPE_FIELD = "_scope"
SCHEDULER_KIND = KIND_PLUGIN
SCHEDULER_FILE_NAME = "scheduler"
#: where a refusal lands when the request did not resolve to a server.
SCHEDULER_FALLBACK_URL = readmodels.SERVERS_HREF_PREFIX

#: the file's non-server sections, by EXACT name — the way its schema declares them.
SCHEDULER_RESERVED_SECTIONS = frozenset(scheduler_plan.RESERVED_SECTIONS)

SCHEDULER_SCOPE_MISSING = i18n._("No server was named for this save, so nothing was written.")
SCHEDULER_SCOPE_RESERVED = i18n._(
    "DEFAULT, commands and chat_commands are not server blocks — they are edited in Settings, so "
    "nothing was written.")
SCHEDULER_SCOPE_UNKNOWN = i18n._(
    "That name is not a server this console can edit, so nothing was written.")
SCHEDULER_SCOPE_NODE = i18n._(
    "That name is a node, not a server, so nothing was written.")
SCHEDULER_EXPERT_REFUSED = i18n._(
    "Expert Mode is available to an Admin only, so nothing was written. Use the plan's steps "
    "instead.")


class SchedulerRefusal(Exception):
    """A refusal an operator reads: ``message`` is a msgid, translated by whoever renders it."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SchedulerScope:
    """One resolved server's place in the scheduler file: the server, its block PATH and its label.

    ``key`` is ``(instance,)``, or ``(node, instance)`` when the instance name is ambiguous across
    nodes — the shape the bot's validator demands and ``core/plugin.py`` reads. ``label`` is the
    SERVER's name (the page the operator came from, and where a save returns), never a path.
    """

    server: object
    key: tuple[str, ...]
    label: str


def scheduler_write_guard(request: Request, posted_scope) -> tuple[GlobalFile, SchedulerScope]:
    """Resolve ``_scope`` to ONE server block of the scheduler file, or refuse — in this order.

    1. the file itself: a 404 when this installation has no scheduler file;
    2. an empty scope: nothing to resolve, nothing to write;
    3. the file's RESERVED sections by exact name — before any name lookup, because they are not
       servers and must never be writable as one;
    4. the name as a server of the caller's SCOPED view (:func:`dashboard_page.server_named`); a miss
       covers "another server's name" and "no such server" with ONE sentence that discloses neither;
    5. a name that is a NODE (the cluster's own node list) — refused as such;
    6. the per-server WRITE grant (:func:`scheduler_access`); a resolved server outside the caller's
       scope answers the SAME unknown-name sentence, so the refusal never discloses that it exists;
    7. the block path from the RESOLVED server, never from the posting: the key is the server's
       INSTANCE name — ``(instance,)``, or ``(node, instance)`` when that instance name occurs on
       more than one node. ``_scope`` names a SERVER (what the page is addressed by); the file's
       block is keyed by the instance, and the two routinely differ.

    Returns the file entry and the scope. Raises :class:`HTTPException` for the missing file (404),
    :class:`SchedulerRefusal` for the name and write refusals. The capability itself is declared on
    the route (``scheduler.write``), so the deny-by-default gate answers a caller who holds none.
    """
    entry = resolve_file(request, SCHEDULER_KIND, SCHEDULER_FILE_NAME)
    raw = readmodels.text(posted_scope).strip()
    if not raw:
        raise SchedulerRefusal(SCHEDULER_SCOPE_MISSING)
    if raw in SCHEDULER_RESERVED_SECTIONS:
        raise SchedulerRefusal(SCHEDULER_SCOPE_RESERVED)
    server = dashboard_page.server_named(request, raw)
    if server is None:
        raise SchedulerRefusal(SCHEDULER_SCOPE_UNKNOWN)
    declared = readmodels.declared_node_instances()
    if raw in declared:
        raise SchedulerRefusal(SCHEDULER_SCOPE_NODE)
    if not scheduler_access(request, server).write:
        raise SchedulerRefusal(SCHEDULER_SCOPE_UNKNOWN)
    label = readmodels.text(getattr(server, "name", None)) or scheduler_block_key(server)[-1]
    return entry, SchedulerScope(server=server, key=scheduler_block_key(server), label=label)


def scheduler_block_key(server) -> tuple[str, ...]:
    """The block path a write uses for *server*: ``(instance,)``, or ``(node, instance)``.

    The key is the server's INSTANCE name — what the plugin reads — and the node is prepended ONLY
    when that instance name occurs on more than one node, so an unambiguous name stays one level
    deep exactly as a flat file spells it. The page renders its block at this path and the save
    writes at this path, so the YAML the review step shows is the YAML the save produces.
    """
    instance = readmodels.text(getattr(getattr(server, "instance", None), "name", None)) \
        or readmodels.text(getattr(server, "name", None))
    node = readmodels.text(getattr(getattr(server, "node", None), "name", None))
    declared = readmodels.declared_node_instances()
    ambiguous = node and sum(1 for names in declared.values() if instance in names) > 1
    return (node, instance) if ambiguous else (instance,)


def scheduler_override(document, scope: SchedulerScope, plan: dict) -> dict:
    """The override *plan* states for *scope*'s block of *document* — the ONE computation.

    Read from the document that is being written, so the review step of an unmodified file and the
    save of it produce the SAME ``{set, delete}``: the inherited values are the ``DEFAULT`` block as
    the file states it, the current overrides are the server's own block at ``scope.key``.
    """
    return scheduler_plan.minimal_override(scheduler_plan.default_block(document),
                                           scheduler_plan.block_at(document, scope.key),
                                           plan)


def scheduler_redirect(scope: SchedulerScope | None) -> str:
    """The page a save (or its refusal) returns to: the server's Scheduler tab, review step."""
    if scope is None:
        return SCHEDULER_FALLBACK_URL
    return (readmodels.server_url(scope.label, tab="scheduler")
            + "&step=" + scheduler_plan.SCHEDULER_REVIEW_STEP)


def scheduler_expert_answer(form):
    """The Expert Mode block a post states, or ``None`` when the post is the plan's own form.

    Two readings decide it: the posted flag, and then the block text (:func:`scheduler_plan.
    expert_block`). The text alone cannot decide it — the review step's textarea ships the block so
    the page is editable with JavaScript off, so an ordinary save carries it too and the flag is what
    says whether that text is the candidate.
    """
    if readmodels.text(form.get(SETTINGS_EXPERT_FIELD)) != "1":
        return None
    return scheduler_plan.expert_block(form.get(scheduler_plan.BLOCK_YAML_FIELD))


def scheduler_validate_block(form):
    """The Expert Mode block a VALIDATE post states, or ``None`` when it carries none.

    The editor posts its text and the token and nothing else, so here PRESENCE is the signal: a post
    that carries the block is the expert candidate, and the verdict is the one the save would give
    for exactly that text.
    """
    if scheduler_plan.BLOCK_YAML_FIELD not in form:
        return None
    return scheduler_plan.expert_block(form.get(scheduler_plan.BLOCK_YAML_FIELD))


def scheduler_posted_scope(form, request: Request):
    """The server a post names: its own field first, then the panel's validate URL.

    The review step's form posts ``_scope``; the panel's editor posts the carrier and the token only,
    so the render puts ``_scope`` in the validate URL. The field wins when both are there, so a whole
    form's save is unaffected.
    """
    posted = form.get(SCHEDULER_SCOPE_FIELD)
    if posted is None:
        posted = request.query_params.get(SCHEDULER_SCOPE_FIELD)
    return posted


def scheduler_candidate(entry: GlobalFile, scope: SchedulerScope, plan: dict | None = None, *,
                        block=None):
    """The document a save would produce, built IN MEMORY — never re-read after the mutation.

    *block* is the Expert Mode block when the post states one: it REPLACES the server's block, so the
    keys the operator dropped leave with it. Otherwise *plan* is the wizard's plan and the per-key
    override is applied. Both doors end in one document, which is what ``entry.schema_files``
    validates — the very call a save makes, so the check cannot bless what the write would refuse.
    Raises :class:`configwrite.ConfigWriteError` when the file cannot be read or the document cannot
    be serialised.
    """
    document = configwrite.load_config_file(entry.path)
    if document is None:
        document = configgen.read_document(entry.path)
    scheduler_plan.require_mapping(document, entry.display)
    if block is not None:
        scheduler_plan.apply_block(document, scope.key, block)
    else:
        scheduler_plan.apply_override(document, scope.key,
                                      scheduler_override(document, scope, plan or {}))
    return document


def scheduler_verdict(request: Request, document, faults, path: Path) -> dict:
    """The JSON verdict for a refused candidate: the operator's sentence plus the located detail."""
    tokens = [token for token in (faults.error_path or "").split("/") if token]
    found = _locate(document, tokens, path)
    block = found["block"] or ""
    if found["line"]:
        message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA, line=found["line"], block=block)
    elif block:
        message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA_BLOCK, block=block)
    else:
        message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA_BLOCK, block=block or "?")
    detail = _clean_detail(faults.errors)
    return _verdict(ok=False, kind="schema", line=found["line"], column=found["column"],
                    block=block, block_line=found["block_line"],
                    block_end_line=found["block_end_line"],
                    message=(message + " " + detail).strip())


# ------------------------------------------------------------------------------- registration

def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers.

    The four PARAMETERISED file paths declare :data:`SETTINGS_FILE_VIEW_CAPABILITY` because they serve
    every kind this area lists; what the caller may do with the file it names is decided per request by
    :func:`require_file_capability`. Every fixed path keeps ``settings.*``.
    """
    declare()
    declare_file_capabilities()
    return {SETTINGS_PATH: SETTINGS_VIEW_CAPABILITY,
            SETTINGS_SAVE_PATH: SETTINGS_WRITE_CAPABILITY,
            SETTINGS_VALIDATE_PATH: SETTINGS_WRITE_CAPABILITY,
            SETTINGS_FILE_PATH: SETTINGS_FILE_VIEW_CAPABILITY,
            SETTINGS_FILE_SAVE_PATH: SETTINGS_FILE_WRITE_CAPABILITY,
            SETTINGS_VALIDATE_FILE_PATH: SETTINGS_FILE_WRITE_CAPABILITY,
            SETTINGS_SERVICE_TOGGLE_PATH: SETTINGS_WRITE_CAPABILITY,
            SETTINGS_ADD_BLOCK_PATH: SETTINGS_FILE_WRITE_CAPABILITY,
            SCHEDULER_SAVE_PATH: SCHEDULER_WRITE_CAPABILITY,
            SCHEDULER_VALIDATE_PATH: SCHEDULER_WRITE_CAPABILITY}


def nav_items() -> tuple[NavItem, ...]:
    """The sidebar entry this page contributes — data, not markup."""
    return (NavItem(label=NAV_LABEL, url=SETTINGS_PATH, capability=SETTINGS_VIEW_CAPABILITY),)


def config_dir(request: Request) -> Path:
    """The install config directory this console reads and writes, from the app state."""
    return Path(getattr(getattr(request.app, "state", None), "webui_config_dir", None) or "config")


def config_path(request: Request) -> Path:
    """The configuration file this page reads and writes, from the install config directory."""
    return config_dir(request) / CONFIG_FILE_NAME


# ------------------------------------------------- the global files: DISCOVERY + SCHEMA, from disk
#
# THE ONE MECHANISM every "global settings" card builds on. A global config file is found the way
# the BOT itself finds it (``config/services/<service>.yaml``), and it is validated against the SAME
# schema files the bot hands its validator (a service's ``services/<name>/schemas/`` set). Nothing
# here is a hand-kept list: drop a new file in and it appears, drop a new kind in and only
# ``discover_files`` grows.
#
# THE PRESETS PAGE IS NOT PART OF THIS LISTING: it owns presets, so :func:`discover_files` does not
# list a preset file and no route here resolves one. A preset is ONE KEY of one of those files and is
# edited on its own page in the Presets area — this area edits whole global files.

@dataclass(frozen=True)
class GlobalFile:
    """One GLOBAL config file the Settings area offers: its kind, its key, its path and its schema.

    ``name`` is the entry key used in the route (a service's own file stem).
    ``primary_schema`` is the schema file the generator walks; ``schema_files`` is the FULL set a save
    validates against (the primary PLUS any partials) — exactly what ``configwrite.save_config_file``
    is handed, so the page and the write can never disagree. ``primary_schema`` is ``None`` when the
    kind ships no schema, in which case the page is read-only (see :data:`NO_SCHEMA_BANNER`).
    """

    kind: str
    name: str
    path: Path
    primary_schema: str | None
    schema_files: tuple[str, ...]

    @property
    def display(self) -> str:
        """The file's own name, printed verbatim in the entries list (operator data, never a msgid)."""
        return self.path.name


def _service_schema_files(name: str) -> list[str]:
    """The schema files a service's config is validated against — its OWN ``services/<name>/schemas/``.

    This is the SAME source ``core/services/base.py`` reads (every ``*.yaml`` in that directory, in
    name order), so a service schema's ``include:`` partials resolve exactly as the bot resolves them.
    An empty list means the service ships no schema — the bot itself only warns and reads the file
    anyway, and the page shows it read-only.
    """
    directory = _REPO_ROOT / "services" / name / "schemas"
    if not directory.is_dir():
        return []
    return [str(path) for path in sorted(directory.glob("*.yaml"))]


def _plugin_schema_files(name: str) -> list[str]:
    """The schema files a plugin's config is validated against — its OWN ``plugins/<name>/schemas/``.

    The SAME source ``configgen.schema_files_for`` resolves an ``include:`` against for a plugin
    (every ``*.yaml`` beside the primary PLUS ``schemas/commands_schema.yaml``, which
    ``core/plugin.py`` appends for every plugin). An empty list means the plugin ships no schema.
    """
    directory = _REPO_ROOT / "plugins" / name / "schemas"
    if not directory.is_dir():
        return []
    return [str(path) for path in sorted(directory.glob("*.yaml"))]


def discover_files(config: Path) -> list[GlobalFile]:
    """Every GLOBAL config file, read from DISK — the entries the Settings area lists, main first.

    The order is fixed by kind for a stable menu (main, then services, then presets), each kind in
    file-name order. A kind whose directory or files are absent contributes nothing, so the list is a
    truthful reading of the install, not a wish-list. This is the ONE place the file list is decided.
    """
    config = Path(config)
    files: list[GlobalFile] = [GlobalFile(
        kind=KIND_MAIN, name="main", path=config / CONFIG_FILE_NAME,
        primary_schema=MAIN_SCHEMA_PATH,
        schema_files=tuple(configgen.schema_files_for(MAIN_SCHEMA_PATH)))]

    services_dir = config / "services"
    if services_dir.is_dir():
        for path in sorted(services_dir.glob("*.yaml")):
            schemas = _service_schema_files(path.stem)
            files.append(GlobalFile(kind=KIND_SERVICE, name=path.stem, path=path,
                                    primary_schema=schemas[0] if schemas else None,
                                    schema_files=tuple(schemas)))

    # PLUGINS: a plugin's OWN config file (``config/plugins/<name>.yaml``), validated against the
    # plugin's schema (``plugins/<name>/schemas/``). Its form is the same tabbed cogwheel view.
    plugins_dir = config / "plugins"
    if plugins_dir.is_dir():
        for path in sorted(plugins_dir.glob("*.yaml")):
            schemas = _plugin_schema_files(path.stem)
            primary = next((s for s in schemas if Path(s).name == f"{path.stem}_schema.yaml"),
                           schemas[0] if schemas else None)
            files.append(GlobalFile(kind=KIND_PLUGIN, name=path.stem, path=path,
                                    primary_schema=primary,
                                    schema_files=tuple(configgen.schema_files_for(primary)
                                                       if primary else ())))

    # EXTENSIONS ARE NO LONGER A GLOBAL-FILE KIND: an extension's config
    # lives inside ``nodes.yaml`` and is edited from the NODE PAGE (``/nodes/<node>``), whose DEFAULT
    # and instance tabs each carry that scope's own extensions. The entries are gone from this list —
    # so no ``/settings/extension/<name>`` page is offered or resolvable.
    #
    # PRESETS ARE NOT LISTED HERE, ON PURPOSE. The Presets page (``/presets``) is the home of
    # presets: it reads every preset file and draws one tile per preset, and each tile's cog opens
    # that PRESET's own one-block editor. A preset file is therefore neither listed nor resolvable
    # here — this area edits whole global files, and a preset is one key of one of them.
    return files


def file_url(entry: GlobalFile) -> str:
    """The page URL an entry opens: ``/settings/<kind>/<name>`` for EVERY file, ``main.yaml`` too.

    ``main.yaml`` used to keep the bare ``/settings`` path because it WAS the landing page; now the
    landing is the tile grid and ``main.yaml`` is just another config file, so its form lives at
    ``/settings/main/main`` behind its own cogwheel like every service's.
    """
    return SETTINGS_FILE_PATH.format(kind=entry.kind, name=entry.name)


def resolve_file(request: Request, kind: str, name: str) -> GlobalFile:
    """The discovered entry a ``(kind, name)`` route names, or a 404 when nothing matches.

    Resolution is against the DISK listing, so a name that is not an actual file — including one
    trying to escape its directory — matches nothing and is refused with a 404.

    A preset FILE is not in that listing (:func:`discover_files`) and is not resolvable here either:
    the Presets page owns presets and edits each preset's own block on its own page, so a preset file
    has no door in this area at all.
    """
    for entry in discover_files(config_dir(request)):
        if entry.kind == kind and entry.name == name:
            return entry
    raise HTTPException(status_code=404)


def file_entries(request: Request, current: str) -> list[dict]:
    """The entries the Settings area renders — one per global file, from disk, the current one marked."""
    entries = []
    for entry in discover_files(config_dir(request)):
        url = file_url(entry)
        entries.append({"url": url, "name": entry.display, "kind": entry.kind,
                        "kind_label": KIND_LABELS.get(entry.kind, entry.kind),
                        "active": url == current})
    return entries


# ------------------------------------------------------------------- the page model

def _schema_files() -> list[str]:
    """The schema files ``main.yaml`` is validated against (the primary PLUS every partial file)."""
    return configgen.schema_files_for(MAIN_SCHEMA_PATH)


def _tree():
    return configgen.describe(configgen.load_schema(MAIN_SCHEMA_PATH, schema_files=_schema_files()))


def _empty_tree() -> configgen.Field:
    """A schema-less root — no children, so the form is empty and any posted field is refused.

    Used for a global file whose kind ships no schema (and as a safe stand-in when a save arrives for
    one): the page is read-only, and a crafted POST of an ``f.`` field cannot be placed, so it meets
    the SAME refusal a mis-placed field always gets rather than an empty form silently writing nothing.
    """
    return configgen.Field(key="", kind=configgen.KIND_MAP, path=(),
                           kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])


def _page_context(request: Request, entry: GlobalFile, current_url: str) -> dict:
    """Everything the template needs for ONE global file: the tree, the values, the rows, the YAML.

    The work is identical for ``main.yaml`` and for every other global file — the only inputs are the
    :class:`GlobalFile` (its path and its schema files) and the URL that marks its entry in the list.
    A file whose kind ships no schema renders read-only (no form); a file that parses but does NOT
    validate against its schema is marked so the page can say so (:data:`SCHEMA_INVALID_BANNER`).
    """
    path = entry.path
    primary = entry.primary_schema
    no_schema = primary is None
    if no_schema:
        tree = _empty_tree()
    else:
        tree = configgen.describe(configgen.load_schema(primary,
                                                        schema_files=list(entry.schema_files)))
    readonly = no_schema
    parse_error = ""
    schema_error = ""
    document = None
    try:
        document = configgen.read_document(path)
    except configwrite.ConfigWriteError as ex:
        # A file that does not parse (or cannot be read) is READ-ONLY in form mode: the form never
        # invents a structure it cannot see. The YAML panel still shows whatever is on disk, so the
        # person can fix it by hand.
        document = None
        readonly = True
        parse_error = str(ex)
    values = configgen.collect_current(tree, document)
    # A ``func`` leaf the schema cannot shape (``forcedOptions``, ``difficulty``, ``failures`` …) whose
    # value is a CONTAINER is shown as its YAML, never Python's ``repr``: the repr is not YAML, and a
    # form that posts it back would write the STRING. The page renders the YAML; ``apply_form`` parses
    # it again, so an untouched field round-trips to the SAME structure and the file stays byte-identical.
    for func_field in configgen.leaves(tree):
        if func_field.kind == configgen.KIND_FUNC:
            func_value = values.get(func_field.name)
            if isinstance(func_value, (dict, list)):
                values[func_field.name] = configwrite.dump_config(func_value)
    fields = configgen.flatten(tree)
    # A container's CURRENT value is READ FROM THE DOCUMENT at the container's own path: ``values``
    # carries leaves only (``collect_current`` walks ``leaves``), so a seq/regex looked up there is
    # always None — the rows never rendered, and the seq's hidden length counter rendered 0, so an
    # unchanged save truncated the sequence to []. A regex's entries come from ``regex_value``, which
    # reads its PARENT map and drops the keys the mapping already covers by name.
    # A SEQ/CHOICE field lives at its OWN path, so its ``name`` is unique and its map stays keyed by
    # name. A REGEX container's path is its PARENT's, so two regex containers in one mapping share a
    # ``name``; their maps (customs/elements/sections) are keyed by :func:`configgen.view_id`.
    rows = {field.name: configgen.rows_for(field, configgen.value_at(document, field.path))
            for field in fields if field.kind == configgen.KIND_SEQ}
    customs = {configgen.view_id(field): configgen.custom_entries(
        field, configgen.regex_value(tree, field, document))
        for field in fields if field.kind == configgen.KIND_REGEX}
    # the element leaves (with their name RELATIVE to the element), so the template can render a row
    # without a Jinja-side helper: one list per seq/regex field, one per choice option.
    elements = {}
    for field in fields:
        if field.kind == configgen.KIND_SEQ:
            elements[field.name] = configgen.form_leaves(field)
        elif field.kind == configgen.KIND_REGEX:
            elements[configgen.view_id(field)] = configgen.form_leaves(field)
    choices = {}
    for field in fields:
        if field.kind == configgen.KIND_CHOICE:
            current = configgen.value_at(document, field.path)
            options = []
            for option in field.children:
                leaves = configgen.form_leaves(option)
                # which shape the FILE actually carries: a list value matches the seq/list option, a
                # mapping value matches the map option. The template marks the matching editor so the
                # operator is not left choosing blind between two prefilled shapes.
                matches = ((isinstance(current, list)
                            and option.kind in (configgen.KIND_LIST, configgen.KIND_SEQ))
                           or (isinstance(current, dict)
                               and option.kind == configgen.KIND_MAP))
                options.append({
                    "key": option.key,
                    "leaves": leaves,
                    "matches": matches,
                    "values": {rel: configgen.value_at(document, leaf.path)
                               for leaf, rel in leaves},
                })
            choices[field.name] = options
    # for each FIXED-ALTERNATIVE regex container, the schema's own section names split by whether the
    # document already carries them — the "+ Add section" control's choices.
    sections = {configgen.view_id(field):
                configgen.section_state(field, customs.get(configgen.view_id(field), []))
                for field in fields if field.kind == configgen.KIND_REGEX and field.alternatives}
    yaml_text = ""
    if document is not None:
        yaml_text = configwrite.dump_config(document)
    else:
        try:
            yaml_text = configwrite.read_config_text(path)
        except configwrite.ConfigWriteError:
            yaml_text = ""
    # A file that parses but does NOT validate against its schema is shown AS SUCH, never silently
    # rendered as if it were fine. The check is the SAME one a save makes, over the file's OWN schema
    # files, so a file reported clean here is one a save will accept.
    if document is not None and entry.schema_files:
        try:
            # the notice's check, MEMOISED: an unchanged file is not re-validated per render (see
            # services/webservice/validation.py — a save still validates freshly)
            validation.validate_cached(path, list(entry.schema_files))
        except configwrite.ConfigValidationError as ex:
            schema_error = _clean_detail(ex.errors) or str(ex)
        except configwrite.ConfigWriteError:
            pass
    # THE PLUGIN BLOCK IS GONE FROM THIS PAGE. ``main.yaml`` — the ONLY file that ever carried the
    # dedicated ``opt_plugins`` block — now renders through the SCOPED editor (``settings_scope.html``)
    # and OMITS ``plugins``/``opt_plugins`` entirely (``MAIN_SUPPRESSED_FIELDS``): those lists are
    # managed by the plugin TILES on the Settings landing (each tile's switch posts the same list).
    # So the ordinary form (``_page_context``) is reached only by a file whose kind ships NO schema,
    # which has no ``opt_plugins`` field at all — the block is unreachable here and was removed with it.
    # THE SHOW/HIDE MODEL: what the form shows, what the "+ Add" picker offers and
    # what can be removed. The model is built PER SCOPE, over the scope's own field list (the body's
    # picker key is ``''`` within a scope), because ``settings.html`` renders each scope's fields — the
    # tree's ROOT is not the body when the schema carries a ``DEFAULT`` block. The removal
    # acknowledgement is minted HERE, by the render; a save that removes a value must present it.
    scope_list = configgen.scopes(tree)
    hidden: set = set()
    removable: set = set()
    picks: dict = {}
    scope_picks: list = []
    for scope in scope_list:
        root = configgen.Field(key="", kind=configgen.KIND_MAP, path=(),
                               children=tuple(scope["fields"]),
                               kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])
        model = visibility_context(root, root, document)
        scope_picks.append(model["picks"].get("", []))
        hidden |= set(model["hidden"])
        removable |= set(model["removable"])
        for name, options in model["picks"].items():
            if name:
                picks.setdefault(name, options)
    remove_ack = ""
    if removable and document is not None:
        remove_ack = mint_remove_ack(request, str(path), configwrite.config_revision(path))
    return {
        "tree": tree,
        "scopes": scope_list,
        "values": values,
        "rows": rows,
        "customs": customs,
        "elements": elements,
        "choices": choices,
        "sections": sections,
        "hidden": frozenset(hidden),
        "picks": picks,
        "scope_picks": scope_picks,
        "removable": frozenset(removable),
        "remove_ack": remove_ack,
        "remove_ack_field": REMOVE_ACK_FIELD,
        "remove_companion": configgen.REMOVED_COMPANION,
        "yaml": yaml_text,
        "readonly": readonly or document is None,
        "parse_error": parse_error,
        "schema_error": schema_error,
        "no_schema": no_schema,
        "file_name": entry.display,
        "schema_name": Path(entry.primary_schema).name if entry.primary_schema else "",
        "revision": configwrite.config_revision(path),
        "files": file_entries(request, current_url),
        "current_url": current_url,
    }


# THE MAIN.yaml PAGE IS THE SCOPED VIEW NOW. ``load_page`` (the ordinary-form context builder for
# ``main.yaml``) is GONE: ``is_scoped`` returns True for the main kind, so its cogwheel opens
# :func:`scoped_context` through the ONE per-file route, exactly like every service/plugin file. The
# ordinary form survives only for a file whose kind ships NO schema (see ``_page_context``).


# ------------------------------------------------------------ the SCOPED view (the cogwheel)
# A plugin's / a service's / an extension's file is shown as TABS — DEFAULT plus one block per
# node/instance — by :mod:`services.webservice.scopes`. The tab is a server-side SELECTION
# (``?tab=<n>``), so only the active tab's fields are rendered and posted: a save from a tab applies
# exactly that block, and the SAVE path is the same one write primitive a form save always used.

#: the two little words a tab carries under its label (chrome).
TAB_NOTE_DEFAULT = i18n._("defaults")
TAB_NOTE_OVERRIDE = i18n._("override")


def _node_names(config: Path) -> list[str]:
    """The cluster's node names — the top-level keys of ``nodes.yaml`` (a reading, never a guess)."""
    document = _document_or_none(Path(config) / NODES_YAML_NAME)
    return [str(key) for key in document] if isinstance(document, dict) else []


def _apply_node(request: Request, entry: GlobalFile, node: str) -> str:
    """The node a SERVICE file's change belongs to — or ``""`` for the master.

    A service configuration restart targets the node whose block was edited. The page's own node
    selector names it, so the value is ACCEPTED ONLY IF the install's ``nodes.yaml`` carries that node:
    the mapping then still reads its target from files (the saved service file for the KIND, this
    install's node list for the NAME) and a crafted ``?node=`` can name no other machine than one this
    cluster already has. Every other kind names its node itself (``nodes.yaml``) or names none at all
    (``main.yaml``, a plugin), so it answers the master.
    """
    if entry.kind != KIND_SERVICE:
        return ""
    wanted = readmodels.text(node).strip()
    return wanted if wanted and wanted in _node_names(config_dir(request)) else ""


def is_scoped(entry: GlobalFile) -> bool:
    """Whether a global file's view is the TABBED cogwheel view (``main.yaml``, a plugin, a service).

    ONE view (:data:`SETTINGS_SCOPE_TEMPLATE`) and never a per-KIND one: ``DEFAULT`` first, then one
    tab per node/instance block the file carries. So EVERY ``main.yaml``/plugin/service file whose
    kind ships a schema gets the tabbed renderer — the difference the operator reported (a service showing
    the older, single-form view) came from selecting the renderer by the SCHEMA's SHAPE instead of by
    the file's KIND, so a service whose schema declares no override container (a flat
    ``backup``/``bot`` schema, or ``webservice``/``dashboard`` with a bare ``DEFAULT`` block) fell
    through to the ordinary form.

    A schema that declares no override container is NOT a different view: :func:`scopes.build_scopes`
    renders it as the single ``DEFAULT`` tab over the whole root, so the cogwheel looks the same for
    a flat schema too. ``main.yaml`` is exactly such a flat schema, so its cogwheel opens the SAME
    editor every other config file uses (its ``plugins``/``opt_plugins`` keys are omitted from that
    editor — see :data:`MAIN_SUPPRESSED_FIELDS`). The ONLY file that keeps the ordinary form is one
    whose kind ships no schema at all (:attr:`GlobalFile.primary_schema` is ``None``) — there is
    nothing to generate, and every kind agrees there too.
    """
    if entry.kind not in (KIND_MAIN, KIND_SERVICE, KIND_PLUGIN):
        return False
    return entry.primary_schema is not None


def _tab_url(current_url: str, index: int, node: str = "") -> str:
    """The URL of tab *index* of the file at *current_url* — a server-side selection link.

    When a NODE is selected the link carries it too (``?node=<n>&tab=<i>``), so following a tab keeps
    the operator in the SAME node's view; with no node the URL is exactly ``?tab=<i>`` as before.
    """
    if node:
        from urllib.parse import quote

        return f"{current_url}?node={quote(node)}&tab={index}"
    return f"{current_url}?tab={index}"


def _node_url(current_url: str, node: str) -> str:
    """The URL that selects *node* on the file at *current_url* — the NODE SELECTOR's link (tab 0)."""
    from urllib.parse import quote

    return f"{current_url}?node={quote(node)}&tab=0"


def _scope_note(request: Request, scope: dict) -> str:
    """The sentence over a scope's fields, composed in the request's language (HTML allowed).

    A ``plain`` scope (a file whose schema declares no override container) gets its own sentence: the
    DEFAULT/override story the other two tell would be a lie about a file that has no such blocks.
    """
    if scope.get("plain"):
        return i18n.translate(request, SCOPE_NOTE_PLAIN)
    template = (SCOPE_NOTE_DEFAULT if scope.get("kind") == "default"
                else SCOPE_NOTE_OVERRIDE)
    if "%(scope)s" in template:
        return i18n.translate(request, template, scope=scope.get("label", ""))
    return i18n.translate(request, template)


def _scoped_scope_list(request: Request, entry: GlobalFile, document, node_names) -> list[dict]:
    """The FULL scope list for a scoped entry — every block the file carries, DEFAULT first.

    This is the raw material both the tab strip and the NODE SELECTOR read; it is factored out so the
    render path (``scoped_context``) and the write path (:func:`node_slice_keys`) build the tabs from
    the SAME reading and cannot drift.
    """
    if entry.primary_schema is None:
        scope_list = []
    else:
        tree = configgen.describe(configgen.load_schema(entry.primary_schema,
                                                        schema_files=list(entry.schema_files)))
        scope_list = scopes.build_scopes(tree, document, node_names)
    if not scope_list:
        scope_list = [{"label": scopes.DEFAULT_TAG, "kind": "default", "node": None, "instance": None,
                       "base": (), "present": False, "fields": []}]
    return scope_list


def _scope_target(scope_list, node) -> tuple[str, list[str]]:
    """``(chosen_node, options)`` — the node a scoped view edits, and the selector's choices.

    THE RULE (exhaustive):

    * a plugin/service file's blocks PARTITION the file by node (its ``DEFAULT`` plus one block per
      node it overrides), so a node-less tab list is already "1 + N" when the file carries ONE node:
      the selector and the slice appear only when the file carries TWO OR MORE node blocks;
    * ``options`` is a READING of the file (:func:`scopes.node_options`), and ``chosen`` is the
      requested node when it is one of them, else the first.
    """
    options = scopes.node_options(scope_list)
    if not options or len(options) < 2:
        return "", options
    wanted = readmodels.text(node) if node else ""
    return (wanted if wanted in options else options[0]), options


def node_slice_keys(request: Request, entry: GlobalFile, node: str) -> list[str]:
    """The top-level keys a NODE's slice of *entry*'s file owns — the WRITE path's allow-list.

    Kept as the read half of :func:`scoped_write_guard` (they share ONE reading, so the render path
    and the write path cannot drift). Empty when *node* is falsy, the entry is not scoped, or *node* is
    not a node the file names.
    """
    _require_node, keys, _forbidden = scoped_write_guard(request, entry, node)
    return keys


def scoped_write_guard(request: Request, entry: GlobalFile,
                       node: str) -> tuple[bool, list[str], frozenset[str]]:
    """The NODE GUARD every write into *entry*'s file must pass: ``(require_node, slice_keys, forbidden)``.

    A scoped file is edited ONE NODE at a time, and this is the single reading behind BOTH doors into
    it — the plain form and the Expert Mode slice — so neither can write another node's block:

    * ``require_node`` — True when the file's view offers the NODE SELECTOR (its block count is two or
      more, or it is an extension, whose ``nodes.yaml`` always carries the whole cluster): a WRITE into
      such a file MUST name a node the file carries. A bogus or absent ``?node=`` then yields an EMPTY
      ``slice_keys`` and the write is REFUSED, never promoted to a whole-file write (which would delete
      every other node's block);
    * ``slice_keys`` — the top-level keys the selected node's own view owns (its own block, plus the
      shared ``DEFAULT`` for a plugin/service file): the ONLY keys an Expert Mode slice may replace;
    * ``forbidden`` — every NODE token a posted field must NEVER name (every node but the selected one,
      from the file's own blocks AND the cluster's node list): a form field whose FIRST path token is
      one of these is REFUSED, so a crafted key cannot reach another node's block.

    A non-scoped file (``main.yaml``) yields ``(False, [], frozenset())`` — the ordinary whole-file
    write, unchanged.
    """
    if not is_scoped(entry):
        return False, [], frozenset()
    document = _document_or_none(entry.path)
    if not isinstance(document, dict):
        return False, [], frozenset()
    cluster = {str(name) for name in _node_names(config_dir(request))}
    scope_list = _scoped_scope_list(request, entry, document, list(cluster))
    options = scopes.node_options(scope_list)
    if options and len(options) >= 2:
        # edited per node: a node the FILE carries is mandatory.
        wanted = readmodels.text(node) if node else ""
        if wanted not in options:
            return True, [], frozenset(cluster | set(options))
        visible = scopes.scopes_for_node(scope_list, wanted)
        keys = scopes.scope_keys(visible) if visible else []
        return True, keys, frozenset((cluster | set(options)) - {wanted})
    # a node-less (single-node) reading: the WHOLE file is the unit; only foreign nodes are forbidden.
    return False, [], frozenset(cluster - set(options))



def scoped_context(request: Request, entry: GlobalFile, current_url: str,
                   tab_index: int = 0, node: str = "") -> dict:
    """Everything ``settings_scope.html`` reads for one global file: the node, the tabs, the slice.

    The active scope's fields are rendered by the ONE control macro (``_scope_fields.html``); its
    value/rows/customs/elements/choices maps come from :func:`scopes.scope_view`, keyed by the field's
    FULL name, and every key is PRINTED relative to the tab (``displays``).

    THE NODE STEP (§ the mechanism). Before the tabs, the view offers the NODE SELECTOR when the file
    carries several node blocks; the chosen node filters the tabs to its own blocks (the "1 + N" rule)
    and — when it is set — the Expert Mode panel shows only that node's SLICE of the file
    (:func:`scopes.document_slice` over :func:`scopes.scope_keys`), so the whole file is never shown.
    """
    path = entry.path
    no_schema = entry.primary_schema is None
    readonly = no_schema
    parse_error = ""
    schema_error = ""
    document = None
    try:
        document = configgen.read_document(path)
    except configwrite.ConfigWriteError as ex:
        document = None
        readonly = True
        parse_error = str(ex)
    node_names = _node_names(config_dir(request))
    scope_list = _scoped_scope_list(request, entry, document, node_names)

    chosen, options = _scope_target(scope_list, node)
    visible = scopes.scopes_for_node(scope_list, chosen) if chosen else scope_list
    if not visible:
        visible, chosen = scope_list, ""
    slice_keys = scopes.scope_keys(visible) if chosen else []
    tab_index = max(0, min(tab_index, len(visible) - 1))
    tabs = [{"label": scope["label"], "kind": scope["kind"], "active": index == tab_index,
             "note": i18n.translate(request, TAB_NOTE_DEFAULT if scope["kind"] == "default"
                                    else TAB_NOTE_OVERRIDE),
             "url": _tab_url(current_url, index, chosen)} for index, scope in enumerate(visible)]
    node_choices = [{"name": name, "url": _node_url(current_url, name), "active": name == chosen}
                    for name in options]
    active = visible[tab_index]
    # THE main.yaml EDITOR OMITS THE PLUGIN LISTS (``plugins``/``opt_plugins``): they are managed by
    # the plugin tiles on the landing. Dropping them from the RENDERED fields is enough to keep them
    # intact — the save only writes the fields the form POSTS — and the save tree drops them too, so
    # a crafted POST cannot reach them either (:func:`entry_save_tree`).
    scope_fields = [field for field in active.get("fields", [])
                    if str(field.key) not in
                    (MAIN_SUPPRESSED_FIELDS if entry.kind == KIND_MAIN else frozenset())]
    view = scopes.scope_view(scope_fields, document)
    displays = {field.name: scopes.display_name(field, active.get("base", ()))
                for field in configgen.flatten(view.root)}
    # THE SHOW/HIDE MODEL for this scope's body (``''`` is the body's picker key), plus the removal
    # acknowledgement the render mints so a value-carrying removal can be refused without it.
    vis = visibility_context(view.root, view.root, document)
    remove_ack = ""
    if vis["removable"] and document is not None:
        remove_ack = mint_remove_ack(request, str(path), configwrite.config_revision(path))

    yaml_text = ""
    if document is not None:
        # THE SLICE: when a node is chosen the panel holds only that node's part of the file — the
        # top-level keys its tabs own (its own block, plus the shared DEFAULT when the file carries
        # one). With no node chosen (a single-node file) the whole document is the unit, unchanged.
        yaml_text = configwrite.dump_config(
            scopes.document_slice(document, slice_keys) if slice_keys else document)
    else:
        try:
            yaml_text = configwrite.read_config_text(path)
        except configwrite.ConfigWriteError:
            yaml_text = ""
    if document is not None and entry.schema_files:
        try:
            # the notice's check, MEMOISED: an unchanged file is not re-validated per render (see
            # services/webservice/validation.py — a save still validates freshly)
            validation.validate_cached(path, list(entry.schema_files))
        except configwrite.ConfigValidationError as ex:
            schema_error = _clean_detail(ex.errors) or str(ex)
        except configwrite.ConfigWriteError:
            pass

    # THE "+ ADD BLOCK" DOOR needs a block SHAPE to create. A file whose schema declares no override
    # container has none — adding a block would write a top-level key the schema refuses — so the
    # control is not offered there (the same rule the plugin view already follows for such a file).
    plain = any(scope.get("plain") for scope in scope_list)
    add_path = ""
    if not readonly and document is not None and not plain:
        add_path = SETTINGS_ADD_BLOCK_PATH.format(kind=entry.kind, name=entry.name)
    return {
        "scoped": True,
        "tabs": tabs,
        "fields": scope_fields,
        "displays": displays,
        "values": view.values,
        "rows": view.rows,
        "customs": view.customs,
        "elements": view.elements,
        "choices": view.choices,
        "sections": view.sections,
        "hidden": vis["hidden"],
        "picks": vis["picks"],
        "removable": vis["removable"],
        "remove_ack": remove_ack,
        "remove_ack_field": vis["remove_ack_field"],
        "remove_companion": vis["remove_companion"],
        "scope_note": _scope_note(request, active),
        "add_path": add_path,
        "node_names": node_names,
        "multi_node": len(node_names) > 1,
        "add_placeholder": ADD_BLOCK_INSTANCE_PLACEHOLDER,
        # the NODE SELECTOR (before the tabs) and the slice story for the Expert Mode panel.
        "node_options": node_choices,
        "node_label": SCOPE_NODE_LABEL,
        "node_lead": SCOPE_NODE_LEAD,
        "selected_node": chosen,
        "slice_note": SCOPE_SLICE_NOTE,
        "slice_mode": bool(slice_keys),
        "back_url": SETTINGS_PATH,
        "back_label": SCOPE_BACK_LABEL,
        "crumb_tail": f"{KIND_LABELS.get(entry.kind, entry.kind)} / {entry.name}",
        "kind_label": KIND_LABELS.get(entry.kind, entry.kind),
        "yaml": yaml_text,
        "readonly": readonly or document is None,
        "parse_error": parse_error,
        "schema_error": schema_error,
        "no_schema": no_schema,
        "file_name": entry.display,
        "schema_name": Path(entry.primary_schema).name if entry.primary_schema else "",
        "revision": configwrite.config_revision(path),
        "current_url": current_url,
    }


def load_file_page(request: Request, kind: str, name: str, tab_index: int = 0,
                   node: str = "") -> dict:
    """The page context for a NON-main global file — the scoped view or the ordinary form.

    A plugin/service/extension file yields the TABBED context; a flat file keeps the form context.
    """
    entry = resolve_file(request, kind, name)
    if is_scoped(entry):
        return scoped_context(request, entry, file_url(entry), tab_index, node)
    return _page_context(request, entry, file_url(entry))


def entry_save_tree(request: Request, entry: GlobalFile) -> configgen.Field:
    """The tree a save of *entry* resolves posted fields against — the ONE place the write is shaped.

    A plugin/service file uses its full schema tree (whose ``regex;(.+)`` descent resolves every
    concrete node/instance path). A schema-less file gets the empty tree, so a posted field is refused
    exactly as an unplaceable field always is.

    ``main.yaml``'s tree drops the keys its editor does NOT show (:data:`MAIN_SUPPRESSED_FIELDS`): the
    plugin lists are managed by the landing's plugin tiles, and this is the main editor's OWN save
    route — so a ``f.plugins``/``f.opt_plugins`` field posted here is UNPLACEABLE and the save is
    refused (:class:`configgen.UnknownFieldError`) rather than written. The landing tiles keep their
    OWN route (:data:`SETTINGS_SAVE_PATH`), whose tree is the full ``_tree()`` — so the two writes
    cannot disagree about who may touch the plugin lists.
    """
    if entry.primary_schema is None:
        return _empty_tree()
    tree = configgen.describe(configgen.load_schema(entry.primary_schema,
                                                    schema_files=list(entry.schema_files)))
    if entry.kind == KIND_MAIN:
        tree = replace(tree, children=tuple(child for child in tree.children
                                            if str(child.key) not in MAIN_SUPPRESSED_FIELDS))
    return tree


# ------------------------------------------------------------------ the LANDING page: the tiles
#
# THE LANDING PAGE (``/settings``) IS A GRID OF TILES — one per plugin, one per service, one per
# extension — built here from the SAME discoveries the rest of the area uses: plugins from
# ``plugin_registry``, services and extensions from :mod:`services.webservice.catalog`. Nothing is a
# hand-kept list, and the on/off control of each tile reports the mechanism the bot ACTUALLY uses:
#
# * a PLUGIN is enabled by its name being in opt_plugins — the tile's switch posts the SAME
#   ``f.opt_plugins`` list the existing plugin block writes (no new route);
# * a SERVICE is enabled by its config file EXISTING and, when it declares a plugin dependency, that
#   plugin being loaded. The tile says WHICH of the two holds it off; turning one off moves its file
#   aside (the honest action), and a plugin-held service is never a file action;
# * an EXTENSION is per node/instance — its tile states the scope its schema gives it, from config.

def _document_or_none(path: Path):
    """The parsed config file, or ``None`` when it is missing or does not parse."""
    try:
        return configgen.read_document(path)
    except configwrite.ConfigWriteError:
        return None


def _opt_plugins_value(request: Request) -> tuple[object, bool]:
    """``main.yaml``'s ``opt_plugins`` value and whether the key is present — for the plugin block."""
    mapping = _document_or_none(config_path(request))
    mapping = mapping if isinstance(mapping, dict) else {}
    present = PLUGINS_FIELD in mapping
    return (mapping.get(PLUGINS_FIELD) if present else None), present


def _plugins_value(request: Request) -> tuple[object, bool]:
    """``main.yaml``'s ``plugins`` value and whether the key is present — the DEFAULT plugins' list.

    This is the list the loader reads for a DEFAULT plugin (``config.get('plugins', DEFAULT_PLUGINS)``,
    ``core/data/impl/nodeimpl.py``): present means "use exactly this list"; absent means every name in
    ``DEFAULT_PLUGINS`` loads, which is why a default tile reads ON for an absent key.
    """
    mapping = _document_or_none(config_path(request))
    mapping = mapping if isinstance(mapping, dict) else {}
    present = plugin_registry.DEFAULT_FIELD in mapping
    return (mapping.get(plugin_registry.DEFAULT_FIELD) if present else None), present


def _warned_plugin(request: Request) -> str:
    """The plugin NAME the inline-warning confirm step names (``?warn=<name>``), or ``""``.

    Read defensively: the value is only ever COMPARED to a default row's own name, never echoed back
    into the page, so a forged or malformed value matches nothing and the ordinary page renders. A
    request with no query string is the common case and yields ``""``.
    """
    params = getattr(request, "query_params", None)
    if params is None:
        return ""
    return str(params.get("warn") or "")


def _plugin_switch(row: dict, base_fields: list) -> dict:
    """The ON/OFF switch row model for ONE plugin tile — the SAME shape every kind uses.

    A not-installed entry (an ``opt_plugins``/``plugins`` name that maps to no shipped plugin) cannot
    be moved here, so its switch is the inert ``static`` shape. A default plugin being switched OFF
    shows the inline WARNING first (``warn`` is set): its switch is a ``link`` to ``?warn=<name>``
    while the confirm button (a ``form``) is what actually writes. An ordinary toggle is a ``form``
    posting the WHOLE list (plus the empty sentinel when the key exists) to the existing save route.
    """
    from urllib.parse import quote

    field = "f." + row["field"]
    if not row["installed"]:
        return {"type": "static", "label": PLUGIN_SWITCH_OFF}
    if row["confirm"]:
        return {"type": "form", "action": SETTINGS_SAVE_PATH,
                "fields": list(base_fields) + [(field, "")],
                "list_field": field, "list_values": list(row["toggle_values"]),
                "label": PLUGIN_DEFAULT_TURN_OFF, "title": PLUGIN_DEFAULT_TURN_OFF, "on": False,
                "cancel_url": SETTINGS_PATH, "cancel_label": PLUGIN_DEFAULT_KEEP}
    if row["warn"] and row["enabled"]:
        return {"type": "link", "href": f"{SETTINGS_PATH}?warn={quote(row['name'], safe='')}",
                "label": PLUGIN_SWITCH_ON, "title": PLUGIN_SWITCH_OFF, "on": True}
    fields = list(base_fields)
    if row["send_sentinel"]:
        fields.append((field, ""))
    return {"type": "form", "action": SETTINGS_SAVE_PATH,
            "fields": fields, "list_field": field, "list_values": list(row["toggle_values"]),
            "label": PLUGIN_SWITCH_ON if row["enabled"] else PLUGIN_SWITCH_OFF,
            "title": PLUGIN_SWITCH_OFF if row["enabled"] else PLUGIN_SWITCH_ON,
            "on": bool(row["enabled"])}


def _service_switch(state: str, name: str, csrf: str) -> dict:
    """The ON/OFF switch row model for ONE service tile — the SAME shape every kind uses.

    A service is enabled by its config file EXISTING, so an ``on`` tile's switch posts ``disable``
    (moving the file aside) and an ``off`` tile's posts ``enable``. A service held off by its plugin
    dependency, or one with no file at all, cannot be moved here: its switch is the inert ``static``
    shape (the tile's note says why).
    """
    if state == "on":
        return {"type": "form", "action": SETTINGS_SERVICE_TOGGLE_PATH.format(name=name),
                "fields": [(session.CSRF_FIELD, csrf), ("action", "disable")],
                "label": SERVICE_SWITCH_ON, "title": SERVICE_TURN_OFF, "on": True}
    if state == "off":
        return {"type": "form", "action": SETTINGS_SERVICE_TOGGLE_PATH.format(name=name),
                "fields": [(session.CSRF_FIELD, csrf), ("action", "enable")],
                "label": SERVICE_SWITCH_OFF, "title": SERVICE_TURN_ON, "on": False}
    return {"type": "static", "label": SERVICE_SWITCH_OFF}


def load_landing(request: Request) -> dict:
    """Everything the landing template needs: the plugin tiles and the service tiles.

    A plugin tile is HONEST about the loader: a DEFAULT plugin (a ``DEFAULT_PLUGINS`` name) reads its
    state from ``main.yaml``'s ``plugins`` list (absent key = the default set), and an OPTIONAL one
    from ``opt_plugins`` (:func:`plugin_registry.build_tiles`). Each switch posts the WHOLE list of
    its OWN field with its name toggled, computed from the RAW list values so an entry with no tile of
    its own is never dropped. A default plugin being switched OFF is shown the inline warning first
    (``?warn=<name>``) — the switch is a LINK, and only the confirm button posts the write.
    """
    config = config_dir(request)
    main_path = config_path(request)
    csrf = session.get_csrf_token(request)
    core_value, core_present = _plugins_value(request)
    opt_value, opt_present = _opt_plugins_value(request)
    plugin_block = plugin_registry.build_tiles(core_value, core_present, opt_value, opt_present)
    rows = plugin_block["rows"]
    # the list each row toggles — the RAW value, or the loader's own fallback (DEFAULT_PLUGINS) for an
    # absent ``plugins`` key. Computed from the LISTS, never from the rendered rows, so a value with
    # no tile of its own (a default named in opt_plugins, an entry that maps to no shipped plugin)
    # still survives a sibling tile's toggle.
    core_list = plugin_block["core"] if core_present else list(plugin_registry.default_plugins())
    opt_list = plugin_block["opt"]
    warn_name = _warned_plugin(request)
    for row in rows:
        base = core_list if row["field"] == plugin_registry.DEFAULT_FIELD else opt_list
        # a DEFAULT switch always writes a real ``plugins`` list (it may MATERIALISE an absent key —
        # that is how you turn a default off), so it carries the empty-list sentinel; an OPTIONAL
        # switch keeps the old rule (sentinel only when the key exists), so a no-op save creates
        # nothing.
        row["send_sentinel"] = (row["field"] == plugin_registry.DEFAULT_FIELD) or opt_present
        if row["enabled"]:
            row["toggle_values"] = [n for n in base if str(n).lower() != row["name"].lower()]
        else:
            row["toggle_values"] = list(base) + [row["name"]]
        row["confirm"] = bool(row["warn"] and row["enabled"] and warn_name == row["name"])
    # the plugin's OWN membership list, for a service's dependency check — EXACTLY the loader's:
    # the ``plugins`` list (or DEFAULT_PLUGINS when absent) PLUS ``opt_plugins``.
    active = {str(name).lower() for name in core_list} | {str(name).lower() for name in opt_list}
    plugin_block["revision"] = configwrite.config_revision(main_path)
    plugin_block["enabled_count"] = sum(1 for row in rows if row["enabled"] and row["installed"])
    plugin_block["total"] = sum(1 for row in rows if row["installed"])
    # the cogwheel opens the plugin's OWN config file (``config/plugins/<name>.yaml``) — but only when
    # that file EXISTS: a plugin with no config file has no page to open, so no cogwheel is drawn
    # (the plugin itself is still listed and can be toggled).
    plugin_config_stems = {entry.name.lower() for entry in discover_files(config)
                           if entry.kind == KIND_PLUGIN}
    base_fields = [(session.CSRF_FIELD, csrf),
                   (SETTINGS_REVISION_FIELD, plugin_block["revision"]),
                   (SETTINGS_EXPERT_FIELD, "0")]
    for row in plugin_block["rows"]:
        row["cog_url"] = (SETTINGS_FILE_PATH.format(kind=KIND_PLUGIN, name=row["name"])
                          if row["name"].lower() in plugin_config_stems else "")
        row["switch"] = _plugin_switch(row, base_fields)

    # THE TILE LIST IS SPLIT AND SORTED. A tile's presence follows one rule: a DEFAULT plugin is
    # ALWAYS listed (it is part of the bot's own set); an OPTIONAL one ONLY when it is ENABLED. The
    # optional plugins that are NOT enabled are no longer tiles — they are the choices of the ONE
    # "+ Add" tile, each enabling itself exactly as its own tile's switch would (the SAME
    # ``f.opt_plugins`` write through the SAME save route; nothing new). The TILE LIST — never the
    # file — is sorted ALPHABETICALLY by name, CASE-INSENSITIVELY (a stable key on ``name.lower()``),
    # so the grid reads A→Z regardless of where a name sits in ``main.yaml`` (the switch still posts
    # its OWN field's list in the FILE's order, so an untouched toggle stays byte-identical).
    plugin_tiles: list[dict] = []
    plugin_options: list[dict] = []
    for row in plugin_block["rows"]:
        if row["default"] or row["enabled"]:
            plugin_tiles.append(row)
        elif row["installed"]:
            plugin_options.append(row)
    plugin_tiles.sort(key=lambda item: item["name"].lower())
    plugin_options.sort(key=lambda item: item["name"].lower())

    service_tiles = []
    for info in catalog.discover_services(config):
        disabled_path = catalog.disabled_config_path(config, info.name)
        held = bool(info.plugin) and info.plugin not in active
        if info.configured:
            state = "held" if held else "on"
        elif disabled_path.is_file():
            state = "off"
        else:
            state = "unconfigured"
        if state == "held":
            note = i18n.translate(request, SERVICE_HELD_BY_PLUGIN, plugin=info.plugin)
        elif state == "unconfigured":
            note = i18n.translate(request, SERVICE_NOT_CONFIGURED, name=info.name.lower())
        else:
            note = ""
        service_tiles.append({
            "name": info.name,
            "file_key": info.name.lower(),
            "description": info.description,
            "placement": info.placement,
            "placement_label": catalog.PLACEMENT_LABELS.get(info.placement, info.placement),
            "state": state,
            "configured": info.configured,
            "cog_url": (SETTINGS_FILE_PATH.format(kind=KIND_SERVICE, name=info.name.lower())
                        if info.configured else ""),
            "note": note,
            "plugin": info.plugin or "",
            "switch": _service_switch(state, info.name, csrf),
        })

    # THE EXTENSIONS RAIL IS GONE: extensions are configured from the NODE
    # PAGE (``/nodes/<node>``), one scope per tab, so no extension tile is built here any more.
    return {
        "plugin_block": plugin_block,
        "plugin_tiles": plugin_tiles,
        "plugin_options": plugin_options,
        "plugins_legend": i18n.translate(request, PLUGINS_LEGEND,
                                         on=plugin_block["enabled_count"], total=plugin_block["total"]),
        "services": service_tiles,
    }


# ------------------------------------------------------------------ the live validator

#: The two ways pykwalify names the offending location in a message; the value path is the precise one.
_VALUE_PATH = re.compile(r"Value path: '([^']*)'")
_PLAIN_PATH = re.compile(r"Path: '([^']*)'")


def _clean_detail(detail: str) -> str:
    """The pykwalify message as ONE readable sentence: no banner, no line breaks, no path fragments.

    The path is dropped because the editor marks it in the document; what is left reads as the fault
    itself ("Value 'notalist' is not a list."). The detail stays English on purpose — it is technical
    output from the validator, the same class as an action's own message, and is shown verbatim after
    the translated sentence.
    """
    text = " ".join((detail or "").split())
    text = text.replace("Schema validation failed:", "").strip()
    text = _VALUE_PATH.sub("", text)
    text = _PLAIN_PATH.sub("", text)
    return text.strip()


def _locate(document, tokens: list[str], path: Path) -> dict:
    """Where in *document* the pykwalify *tokens* point — line/column, and the block they sit in.

    Walks the ruamel document (which carries line information on its containers) token by token,
    keeping the last key/element position it can read. Returns a dict the validator's JSON carries:

    * ``line``/``column`` — the offending position (1-based), or ``None`` when the fault names only a
      missing key (there is no line to point at);
    * ``block`` — the TOP-LEVEL key the fault lives under (its own name, or the file's name at the
      root) — the "block it belongs to" the editor marks;
    * ``block_line``/``block_end_line`` — the block's own span, so the editor can mark the whole
      block, not just the one line.
    """
    node = document
    line = column = None
    for token in tokens:
        location = getattr(node, "lc", None)
        if isinstance(node, dict) and token in node:
            if location is not None:
                try:
                    ln, col = location.key(token)
                    line, column = ln + 1, col + 1
                except Exception:  # noqa: BLE001 - a container the loader kept no mark for
                    pass
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            if location is not None:
                try:
                    ln, col = location.item(int(token))
                    line, column = ln + 1, col + 1
                except Exception:  # noqa: BLE001
                    pass
            node = node[int(token)]
        else:
            break

    block = tokens[0] if tokens else None
    block_line = block_end_line = None
    if isinstance(document, dict):
        location = getattr(document, "lc", None)
        if block and block in document:
            if location is not None:
                try:
                    block_line = location.key(block)[0] + 1
                except Exception:  # noqa: BLE001
                    block_line = None
            span = _subtree_lines(document[block])
            if block_line is not None:
                span.append(block_line)
            if span:
                block_end_line = max(span)
        elif location is not None:
            try:
                block_line = block_end_line = location.line + 1
            except Exception:  # noqa: BLE001
                block_line = block_end_line = None
    return {"line": line, "column": column, "block": block,
            "block_line": block_line, "block_end_line": block_end_line}


def _subtree_lines(node) -> list[int]:
    """Every line number a container's own marks name, recursively (1-based); scalars contribute none."""
    out: list[int] = []
    location = getattr(node, "lc", None)
    if isinstance(node, dict):
        for key in node:
            if location is not None:
                try:
                    out.append(location.key(key)[0] + 1)
                except Exception:  # noqa: BLE001
                    pass
            out.extend(_subtree_lines(node[key]))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if location is not None:
                try:
                    out.append(location.item(index)[0] + 1)
                except Exception:  # noqa: BLE001
                    pass
            out.extend(_subtree_lines(value))
    return out


def _verdict(*, ok: bool, kind: str = "", line=None, column=None, block=None,
             block_line=None, block_end_line=None, message: str = "") -> dict:
    """The JSON body the editor reads — one shape for a valid and an invalid document alike."""
    return {"ok": ok, "kind": kind, "line": line, "column": column, "block": block,
            "block_line": block_line, "block_end_line": block_end_line, "message": message}


def validate_text(request: Request, text: str, path: Path,
                  schema_files: list[str] | None = None) -> dict:
    """Check *text* through the BOT'S OWN validator and say, in structure, what is wrong.

    Two passes, both reusing the save path's machinery so the editor can never disagree with the bot:

    1. ``configwrite.load_config_text`` — the round-trip loader the save uses. A syntax fault yields a
       ``ConfigParseError`` carrying the line/column the loader's mark named;
    2. ``configwrite.validate_against_schema`` — which hands the candidate to ``core.utils.
       validators.validate`` (pykwalify + this file's schema files), the very call a save makes. A
       schema fault yields a ``ConfigValidationError`` carrying the pykwalify path, which
       :func:`_locate` turns into a line/column and the block.

    ``schema_files`` is the SAME set the file's save validates against (a service's own schema files,
    a preset's pair); when omitted it defaults to ``main.yaml``'s set, so the original caller is
    unchanged. The summary sentence is rendered in the REQUEST's language (the technical detail stays
    English). Nothing is written anywhere — this is a read.
    """
    if schema_files is None:
        schema_files = _schema_files()
    try:
        document = configwrite.load_config_text(text, path=path)
    except configwrite.ConfigParseError as ex:
        if ex.line:
            message = i18n.translate(request, YAML_NOT_READABLE, line=ex.line,
                                     column=ex.column if ex.column is not None else 1)
        else:
            message = i18n.translate(request, YAML_NOT_READABLE, line="?", column="?")
        return _verdict(ok=False, kind="syntax", line=ex.line, column=ex.column, message=message)
    try:
        configwrite.validate_against_schema(text, schema_files, path=path)
    except configwrite.ConfigValidationError as ex:
        tokens = [token for token in (ex.error_path or "").split("/") if token]
        found = _locate(document, tokens, path)
        block = found["block"] or path.name
        if found["line"]:
            message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA, line=found["line"],
                                     block=block)
        else:
            message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA_BLOCK, block=block)
        detail = _clean_detail(ex.errors)
        return _verdict(ok=False, kind="schema", line=found["line"], column=found["column"],
                        block=block, block_line=found["block_line"],
                        block_end_line=found["block_end_line"],
                        message=(message + " " + detail).strip())
    return _verdict(ok=True)


def validate_slice(request: Request, text: str, path: Path,
                   schema_files: list[str], slice_keys: list[str]) -> dict:
    """Check a NODE-scoped Expert Mode slice through the BOT'S OWN validator — as the WHOLE file.

    The page showed only the selected node's slice of the file, so the posted text is that slice. A
    slice alone does NOT satisfy the file's schema (a plugin/service schema needs its ``DEFAULT``; the
    extension view lives inside ``nodes.yaml``), so the slice is MERGED back into the file **on disk**
    — exactly what a save does — and the WHOLE is validated against the SAME schema files. The fault is
    then located in the SLICE's own coordinates (the line numbers the operator sees in the editor), not
    the merged file's. Nothing is written anywhere — this is a read.
    """
    try:
        posted = configwrite.load_config_text(text, path=path)
    except configwrite.ConfigParseError as ex:
        if ex.line:
            message = i18n.translate(request, YAML_NOT_READABLE, line=ex.line,
                                     column=ex.column if ex.column is not None else 1)
        else:
            message = i18n.translate(request, YAML_NOT_READABLE, line="?", column="?")
        return _verdict(ok=False, kind="syntax", line=ex.line, column=ex.column, message=message)
    if posted is None:
        return _verdict(ok=False, kind="schema",
                        message=i18n.translate(request, SAVE_REFUSED_EMPTY_DOCUMENT))
    # THE VALIDATE DOOR MUST AGREE WITH THE SAVE. A save of a slice REPLACES only the keys the selected
    # node's view owns and REFUSES a slice carrying any OTHER top-level key (a hand-written foreign node
    # would otherwise be silently dropped while the editor showed "ok"). The validator applies the SAME
    # rule here, so it can never bless a slice the save would refuse.
    if not isinstance(posted, dict) or any(str(key) not in slice_keys for key in posted):
        return _verdict(ok=False, kind="schema",
                        message=i18n.translate(request, SAVE_REFUSED_FOREIGN_NODE))
    current = configwrite.load_config_file(path)
    if not isinstance(current, dict):
        # nothing to merge into: check the posted text as it stands.
        return validate_text(request, text, path, schema_files=schema_files)
    scopes.merge_slice(current, scopes.document_slice(posted, slice_keys))
    merged_text = configwrite.dump_config(current)
    try:
        configwrite.validate_against_schema(merged_text, list(schema_files), path=path)
    except configwrite.ConfigValidationError as ex:
        tokens = [token for token in (ex.error_path or "").split("/") if token]
        found = _locate(posted, tokens, path)
        block = found["block"] or path.name
        if found["line"]:
            message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA, line=found["line"],
                                     block=block)
        else:
            message = i18n.translate(request, YAML_NOT_AGAINST_SCHEMA_BLOCK, block=block)
        detail = _clean_detail(ex.errors)
        return _verdict(ok=False, kind="schema", line=found["line"], column=found["column"],
                        block=block, block_line=found["block_line"],
                        block_end_line=found["block_end_line"],
                        message=(message + " " + detail).strip())
    except configwrite.ConfigWriteError as ex:
        return _verdict(ok=False, kind="schema", message=str(ex))
    return _verdict(ok=True)


# ---------------------------------------------------------------------------------- the write

def _notice(request: Request, message: str, *, ok: bool, refusal: bool = False,
            applies: str = "", apply: dict | None = None, pending: bool = False,
            reload: str = "") -> None:
    """Store the one-shot notice of this write — the console's OWN wording, translated at render.

    ``apply`` is the "Apply now" control's contract (its file identity, its token and the CSRF field),
    written by :func:`_banner` from the file that was just saved; ``None`` means the file has nothing to
    apply and the notice offers no control at all. ``pending`` marks the notice a started apply leaves,
    which the page renders as a state that reloads itself once.
    """
    request.session[actions_page.NOTICE_KEY] = {
        "message": message, "ok": ok, "audit": "", "audit_warning": False,
        "action": SETTINGS_TITLE, "revert": "", "revert_omitted": "",
        "refusal": refusal, "applies": applies, "apply": apply, "pending": pending,
        "reload": reload,
    }


def _foreign_removal(form, forbidden: frozenset[str],
                     allowed: list[str] | None = None) -> str | None:
    """The first NAMED REMOVAL whose TOP-LEVEL path token the page must NOT write, or ``None``.

    The removal list (``f.<configgen.REMOVED_COMPANION>``) is a second door into the document, so it
    passes the SAME guard a posted field does: with an ALLOW-LIST (a scoped slice) a token outside it
    is refused; without one, a token in the node DENY-list is refused. A removal naming a foreign node
    is therefore refused with nothing written, exactly like a crafted ``f.`` field.
    """
    if not forbidden and not allowed:
        return None
    for text in removal_paths(form):
        token = configgen.decode_token(text.split(".", 1)[0])
        if allowed is not None:
            if token not in allowed:
                return text
        elif token in forbidden:
            return text
    return None


def _foreign_node_field(form, forbidden: frozenset[str],
                        allowed: list[str] | None = None) -> str | None:
    """The first posted ``f.`` field whose TOP-LEVEL path token the page must NOT write, or ``None``.

    A form field name is ``f.`` + a field's dotted path (see :mod:`configgen`), so its first token is
    the top-level block it would write. ONE rule guards it: the write may touch ONLY the top-level keys
    the operator's slice legitimately OWNS. A node is not the only thing that token can name — an
    INSTANCE (a node's ``Nevada``) and the shared ``DEFAULT`` are equally keys of the same file — so a
    node-only DENY-list is not enough. Two readings, one rule:

    * ``allowed`` — the slice's ALLOW-LIST of top-level keys (:func:`node_slice_keys`): when given, a
      token OUTSIDE it is refused. This is what stops a crafted field naming a foreign INSTANCE or the
      shared ``DEFAULT`` from merging a top-level block the scoped view never renders (or overwriting an
      existing one);
    * ``forbidden`` — the node DENY-list, used only when there is NO slice to allow-list against (a
      single-node reading where the whole file is the unit): a token naming a foreign node is refused.

    The token is DECODED first (a key's own ``.`` rides as ``%2E``), matching the resolution
    :func:`configgen.apply_form` would do.
    """
    if not forbidden and not allowed:
        return None
    for name in form:
        if not isinstance(name, str) or not name.startswith("f."):
            continue
        token = configgen.decode_token(name[2:].split(".", 1)[0])
        if token in (configgen.REMOVED_COMPANION, configgen.REMOVE_ACK_COMPANION):
            continue              # a REMOVAL's path / its acknowledgement: guarded by their own rule
        if allowed is not None:
            if token not in allowed:
                return name
        elif token in forbidden:
            return name
    return None


# --------------------------------------------------- the removal acknowledgement (the "+ picker"'s
# trashcan). A field or block that CARRIES A VALUE is removed only through an acknowledgement the
# RENDER minted — the SAME discipline the extension cards use (``node_settings.mint_extension_ack``):
# a bare POST that never rendered the page, or that raced a change, carries no valid token and is
# refused with nothing written. Bound to the FILE and its REVISION, so a token minted before a change
# cannot authorise a removal after it.

#: the session key the minted removal acknowledgements live under (a ``{binding: {token, revision}}``
#: map in the SESSION — never forgeable from the page).
REMOVE_ACK_KEY = "_cfg_remove_ack"

#: the ``f.`` field the removal acknowledgement posts in (``configgen.REMOVE_ACK_COMPANION``).
REMOVE_ACK_FIELD = configgen.REMOVE_ACK_COMPANION

#: the refusal a removal that does not carry a valid acknowledgement shows — nothing written.
SAVE_REFUSED_REMOVE_ACK = i18n._(
    "Nothing was removed: this removal did not carry the acknowledgement the page produces, so the "
    "setting was left exactly as it was. Reload the page and remove it again from there.")


def _remove_ack_binding(path: str, revision: str) -> str:
    """The binding string an acknowledgement is keyed by: the FILE and the REVISION it was minted for.

    Length-prefixed, never a bare join, so two different (path, revision) pairs can never encode the
    same string and a token minted for one file cannot be spent on another.
    """
    parts = (readmodels.text(path), readmodels.text(revision))
    return "\x1f".join(f"{len(part)}:{part}" for part in parts)


def _ack_store(request: Request, *, create: bool = False):
    """The session-backed acknowledgement store, or ``None`` when there is no session.

    A request always has a session in the app, but a page module is also rendered by unit tests that
    hand it a bare namespace. A render with no session CANNOT mint (and nothing is written anyway), so
    the helpers degrade to "no acknowledgement" instead of raising — the save path, which always runs
    with a real session, is unaffected.
    """
    session_obj = getattr(request, "session", None)
    if session_obj is None:
        return None
    store = session_obj.get(REMOVE_ACK_KEY)
    if not isinstance(store, dict):
        if not create:
            return None
        store = {}
    if create:
        session_obj[REMOVE_ACK_KEY] = store
    return store


def mint_remove_ack(request: Request, path: str, revision: str) -> str:
    """Mint (and remember in the session) the removal acknowledgement for ONE file+revision render.

    Called by the RENDER of a page that carries a removable field, so the token exists only as a
    consequence of that page being displayed. A later render of the same file+revision REPLACES the
    token (the earlier one becomes stale), which is correct — the latest page is the one the operator
    acted on.
    """
    store = _ack_store(request, create=True)
    if store is None:
        return ""
    token = secrets.token_urlsafe(32)
    store[_remove_ack_binding(path, revision)] = {"token": token,
                                                  "revision": readmodels.text(revision)}
    request.session[REMOVE_ACK_KEY] = store
    return token


def validate_remove_ack(request: Request, path: str, token: str | None, revision: str | None) -> bool:
    """Whether *token* is the acknowledgement the session minted for EXACTLY this file+revision.

    False for an absent/blank token, one that was never minted (or was replaced — stale), one minted
    for a different file, and one minted under a different revision than *revision* NOW.
    """
    if not token or not isinstance(token, str):
        return False
    store = _ack_store(request)
    if not isinstance(store, dict):
        return False
    entry = store.get(_remove_ack_binding(path, revision))
    if not isinstance(entry, dict):
        return False
    expected = entry.get("token")
    if not isinstance(expected, str):
        return False
    return secrets.compare_digest(token, expected)


def spend_remove_ack(request: Request, path: str, revision: str) -> None:
    """Consume the acknowledgement for ONE file+revision — a ONE-TIME token (delete its store entry).

    Called once a removal has been accepted, so a SPENT token can never authorise a second write.
    """
    store = _ack_store(request)
    if not isinstance(store, dict):
        return
    binding = _remove_ack_binding(path, revision)
    if binding in store:
        del store[binding]
        request.session[REMOVE_ACK_KEY] = store


def removal_paths(form) -> list[str]:
    """The field/block paths a form NAMED as removed — the non-empty lines of its ``f.__remove`` field.

    Read from ``multi_items`` when the form has it (a real Starlette form), so every posted value is
    seen; the ONE form-level holder posts at most one newline-joined value, but reading them all keeps
    the rule a reading of the POST rather than an assumption about the markup.
    """
    out: list[str] = []
    multi = getattr(form, "multi_items", None)
    items = list(multi()) if callable(multi) else []
    for key, value in items:
        if key == "f." + configgen.REMOVED_COMPANION:
            out.extend(line for line in str(value or "").split("\n") if line != "")
    return out


def visibility_context(tree: configgen.Field, container: configgen.Field, document) -> dict:
    """The SHOW/HIDE model for ONE rendered container, as the templates read it.

    Wraps :func:`configgen.visibility` so a page module passes the tree + the container and gets the
    three maps plus the two companion names the form needs. A ``document`` that did not parse (``None``)
    yields an EMPTY model — the page renders every field, read-only, exactly as it always did, and no
    picker is offered for a file that cannot be written.
    """
    if document is None:
        model = {"picks": {}, "hidden": frozenset(), "removable": frozenset()}
    else:
        model = configgen.visibility(tree, container, document)
    model["remove_ack_field"] = REMOVE_ACK_FIELD
    model["remove_companion"] = configgen.REMOVED_COMPANION
    return model


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Settings pages — ``main.yaml`` AND every other global file — and their saves.

    The ``main.yaml`` page keeps its two literal routes; every other global file is served by the
    SAME generated page through one parameterised route pair (a GET page and a POST save), resolved
    against the DISK listing (:func:`resolve_file`) so a new service or preset file needs no code.
    """

    def _render(request: Request, page: dict, *, save_path: str, validate_path: str):
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        template = SETTINGS_SCOPE_TEMPLATE if page.get("scoped") else SETTINGS_TEMPLATE
        html = i18n.render(
            request, template,
            title=SETTINGS_TITLE,
            page_title=SETTINGS_TITLE,
            lead=SETTINGS_LEAD,
            crumb=dashboard_page.crumb(CRUMB_GROUP, SETTINGS_TITLE),
            notice=actions_page.pop_notice(request),
            # the sidebar highlights Settings for every file page, since they are one area
            nav_groups=dashboard_page.nav_groups(registrar, roles, current=SETTINGS_PATH),
            user=dashboard_page.identity_summary(request),
            save_path=save_path,
            validate_path=validate_path,
            revision_field=SETTINGS_REVISION_FIELD,
            expert_field=SETTINGS_EXPERT_FIELD,
            yaml_field=SETTINGS_YAML_FIELD,
            csrf_field=session.CSRF_FIELD,
            csrf_token=session.get_csrf_token(request),
            no_schema_banner=NO_SCHEMA_BANNER,
            schema_invalid_banner=SCHEMA_INVALID_BANNER,
            **page,
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def _render_landing(request: Request) -> HTMLResponse:
        """The landing page: the SAME chrome every page gets, over the tile grid."""
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        html = i18n.render(
            request, SETTINGS_TILES_TEMPLATE,
            title=SETTINGS_TITLE,
            page_title=SETTINGS_TITLE,
            lead=SETTINGS_TILES_LEAD,
            crumb=dashboard_page.crumb(CRUMB_GROUP, SETTINGS_TITLE),
            notice=actions_page.pop_notice(request),
            nav_groups=dashboard_page.nav_groups(registrar, roles, current=SETTINGS_PATH),
            user=dashboard_page.identity_summary(request),
            save_path=SETTINGS_SAVE_PATH,
            service_toggle_path=SETTINGS_SERVICE_TOGGLE_PATH,
            revision_field=SETTINGS_REVISION_FIELD,
            csrf_field=session.CSRF_FIELD,
            csrf_token=session.get_csrf_token(request),
            file_path=SETTINGS_FILE_PATH,
            plugins_heading=PLUGINS_HEADING,
            services_heading=SERVICES_HEADING,
            services_legend=SERVICES_LEGEND,
            service_runs_on=SERVICE_RUNS_ON,
            plugin_switch_on=PLUGIN_SWITCH_ON,
            plugin_switch_off=PLUGIN_SWITCH_OFF,
            service_switch_on=SERVICE_SWITCH_ON,
            service_switch_off=SERVICE_SWITCH_OFF,
            cog_label=COG_LABEL,
            kind_labels=catalog.kind_labels(),
            settings_path=SETTINGS_PATH,
            plugin_default_warning=PLUGIN_DEFAULT_WARNING,
            plugin_default_turn_off=PLUGIN_DEFAULT_TURN_OFF,
            plugin_default_keep=PLUGIN_DEFAULT_KEEP,
            service_held_by_plugin=SERVICE_HELD_BY_PLUGIN,
            service_not_configured=SERVICE_NOT_CONFIGURED,
            service_turn_off=SERVICE_TURN_OFF,
            service_turn_on=SERVICE_TURN_ON,
            not_installed_badge=PLUGIN_NOT_INSTALLED,
            plugin_add_all_configured=PLUGIN_ADD_ALL_CONFIGURED,
            **load_landing(request),
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.get(SETTINGS_PATH, response_class=HTMLResponse)
    async def settings(request: Request):
        """THE LANDING PAGE — a grid of tiles (plugins, services), not a file list."""
        return _render_landing(request)

    @router.get(SETTINGS_FILE_PATH, response_class=HTMLResponse)
    async def settings_file(request: Request, kind: str, name: str, tab: int = 0, node: str = ""):
        """One global config file's generated page (``main.yaml``'s, a service's, a preset's).

        ``?node=<name>`` selects the NODE the scoped view edits (before the tabs); ``?tab=<n>`` selects
        the active scope of that node's view (DEFAULT is 0). The node rides into the save and validate
        URLs too, so a save from the page writes only that node's slice.

        THE KIND'S OWN CAPABILITY is required here (:func:`require_file_capability`): the route's
        declaration admits both audiences because one route serves every kind, and this is where the
        kind decides which one the caller must hold.
        """
        require_file_capability(request, kind, write=False)
        page = load_file_page(request, kind, name, tab, node)
        save_path = SETTINGS_FILE_SAVE_PATH.format(kind=kind, name=name)
        validate_path = SETTINGS_VALIDATE_FILE_PATH.format(kind=kind, name=name)
        chosen = page.get("selected_node") or ""
        if chosen:
            from urllib.parse import quote

            query = f"?node={quote(chosen)}"
            save_path += query
            validate_path += query
        return _render(request, page, save_path=save_path, validate_path=validate_path)

    @router.post(SETTINGS_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_validate(request: Request):
        """The Expert Mode editor's live check — a JSON verdict, never a page, and nothing written."""
        form = await request.form(max_fields=SETTINGS_FORM_MAX_FIELDS)
        text = form.get(SETTINGS_YAML_FIELD)
        return JSONResponse(validate_text(request, "" if text is None else str(text),
                                          config_path(request)))

    @router.post(SETTINGS_VALIDATE_FILE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_validate_file(request: Request, kind: str, name: str, node: str = ""):
        """The Expert Mode check for an OTHER global file — through that file's OWN schema files.

        When a NODE is selected the posted text is that node's SLICE, so it is validated by merging it
        back into the file on disk and checking the WHOLE — the same thing a save does — with the fault
        located in the slice's own coordinates (``?node=`` rides in on the validate URL).
        """
        require_file_capability(request, kind, write=True)
        entry = resolve_file(request, kind, name)
        form = await request.form(max_fields=SETTINGS_FORM_MAX_FIELDS)
        text = form.get(SETTINGS_YAML_FIELD)
        payload = "" if text is None else str(text)
        require_node, slice_keys, _forbidden = scoped_write_guard(request, entry, node)
        if require_node and not slice_keys:
            # the page did not name a node the file carries: validating the posted slice as if it were
            # the whole file would be a misleading verdict, so the check says it cannot place the node.
            return JSONResponse(_verdict(ok=False, kind="schema",
                                         message=i18n.translate(request, VALIDATE_UNKNOWN_NODE)))
        if slice_keys:
            return JSONResponse(validate_slice(request, payload, entry.path,
                                               list(entry.schema_files), slice_keys))
        return JSONResponse(validate_text(request, payload, entry.path,
                                          schema_files=list(entry.schema_files)))

    def _banner(request: Request, result, *, kind: str, name: str, node: str = "",
                back: str = "") -> None:
        """The one-shot notice a successful save leaves, read from the primitive's own apply story.

        The "Apply now" control rides the same notice, and its target is derived HERE from the file
        that was just written (:func:`config_apply.offer`) — never from the form. Only a save that
        CHANGED something offers it: a file that came out identical has nothing to apply.
        """
        apply = None
        if result.changed:
            apply = config_apply.offer(request, path=result.path, config_dir=config_dir(request),
                                       kind=kind, name=name, node=node, back=back)
            message = SAVED_NOW if result.applies == configwrite.APPLIES_NOW else SAVED_RESTART
            _notice(request, message, ok=True, refusal=True, applies=result.applies, apply=apply)
        else:
            _notice(request, SAVED_NO_CHANGE, ok=True, refusal=True, applies=result.applies)

    async def _save(request: Request, *, path, schema_files: list[str], tree,
                    redirect_to: str, slice_keys: list[str] | None = None,
                    require_node: bool = False,
                    forbidden: frozenset[str] = frozenset(),
                    apply_kind: str = "", apply_name: str = "",
                    apply_node: str = "") -> RedirectResponse:
        """The ONE save body, shared by ``main.yaml`` and every other global file.

        Everything that differs between the two is an ARGUMENT — the file, its schema files, its
        generator tree and where to redirect — so there is still exactly one write path and one set of
        refusals, no matter which file the operator opened.

        The NODE GUARD (:func:`scoped_write_guard`) rides in as ``require_node`` and ``forbidden`` and
        applies to BOTH doors: the plain form may not name a foreign node (``forbidden``), and an
        Expert Mode save must name a node the file carries (``require_node``) — a bogus or absent node
        is REFUSED, never promoted to a whole-file write. ``slice_keys`` is the top-level keys the
        selected node's slice owns; the posted slice may carry ONLY those, and only they are merged, so
        every node the operator did not edit comes out byte-for-byte as it was.
        """
        form = await request.form(max_fields=SETTINGS_FORM_MAX_FIELDS)
        revision = readmodels.text(form.get(SETTINGS_REVISION_FIELD)) or None
        # EXPERT MODE takes the OTHER door into the SAME primitive: the posted YAML document is parsed
        # and handed to ``save_config_file`` — the sibling of the form path's ``patch_config_file`` in
        # the one write primitive — so it is validated, made atomic, keeps the ``.bak`` and obeys the
        # no-op rule exactly as a form save does. A document that does not parse, or that fails the
        # same schema, is refused with the reason and NOTHING is written.
        expert = readmodels.text(form.get(SETTINGS_EXPERT_FIELD)) == "1"

        def mutate(document):
            configgen.apply_form(document, form, tree)

        removals: list[str] = []
        try:
            if expert:
                # THE NODE GUARD, FIRST: a scoped file whose page must name a node but does not (a
                # bogus or absent ``?node=``) has no slice to write. The posted document is NOT
                # promoted to a whole-file write — that is the door that would delete every other
                # node's block. Refused, nothing written.
                if require_node and not slice_keys:
                    _notice(request, SAVE_REFUSED_UNKNOWN_NODE, ok=False, refusal=True)
                    return RedirectResponse(redirect_to, status_code=303)
                text = form.get(SETTINGS_YAML_FIELD)
                document = configwrite.load_config_text("" if text is None else str(text), path=path)
                # AN EMPTY DOCUMENT IS NOT A DOCUMENT. The round-trip loader parses "" (and a
                # comments-only text) to None, and writing that would replace the WRONG file with
                # ``null`` — on a schema-less file there is no schema to catch it, so the check lives
                # here, on BOTH the main.yaml and the per-file path. Refused exactly as an invalid
                # document is refused: nothing written, the file left sha-identical.
                if document is None:
                    _notice(request, SAVE_REFUSED_EMPTY_DOCUMENT, ok=False, refusal=True)
                    return RedirectResponse(redirect_to, status_code=303)
                if slice_keys:
                    # THE SLICE SAVE: the posted document is one node's slice. It may REPLACE ONLY the
                    # keys the selected node's view owns — a document carrying any other top-level key
                    # (a hand-written foreign node) is REFUSED, never silently dropped, which would be
                    # a lie about what was saved. Then load the WHOLE file, merge ONLY those keys back
                    # and write, so the other nodes are untouched; ``patch_config_file`` validates the
                    # MERGED document against the same schema a whole-file save would.
                    if not isinstance(document, dict) or any(
                            str(key) not in slice_keys for key in document):
                        _notice(request, SAVE_REFUSED_FOREIGN_NODE, ok=False, refusal=True)
                        return RedirectResponse(redirect_to, status_code=303)
                    posted_slice = document

                    def merge_mutate(document):
                        scopes.merge_slice(document, scopes.document_slice(posted_slice, slice_keys))

                    result = configwrite.patch_config_file(
                        path, merge_mutate, schemas=schema_files, expect_revision=revision)
                else:
                    result = configwrite.save_config_file(
                        path, document, schemas=schema_files, expect_revision=revision)
            else:
                # THE NODE GUARD, on the PLAIN FORM door too. A scoped file whose page must name a
                # node but does not (a bogus or absent ``?node=``) has no slice to write: refused
                # EXACTLY as the Expert door refuses, so the shared ``DEFAULT`` is not writable from a
                # bogus-node page (the two doors agree).
                if require_node and not slice_keys:
                    _notice(request, SAVE_REFUSED_UNKNOWN_NODE, ok=False, refusal=True)
                    return RedirectResponse(redirect_to, status_code=303)
                # A posted ``f.`` field may name ONLY the top-level keys the selected node's slice owns
                # (its own block plus the shared ``DEFAULT``). A token naming a foreign node, a foreign
                # INSTANCE, or ``DEFAULT`` outside the slice is REFUSED — nothing written, the file
                # byte-identical — never merged into a block the scoped view does not render. When the
                # file is NOT edited per node (single-node reading) there is no slice, and the node
                # DENY-list still applies.
                if _foreign_node_field(form, forbidden, slice_keys or None) is not None:
                    _notice(request, SAVE_REFUSED_FOREIGN_NODE, ok=False, refusal=True)
                    return RedirectResponse(redirect_to, status_code=303)
                # A REMOVAL (the "+ Add" picker's trashcan) is refused unless it carries the
                # acknowledgement the render minted for THIS file+revision, and its own top-level token
                # passes the SAME node guard a posted field does — so a bare POST can neither delete a
                # value nor reach another node's block. Both checks refuse with nothing written.
                if _foreign_removal(form, forbidden, slice_keys or None) is not None:
                    _notice(request, SAVE_REFUSED_FOREIGN_NODE, ok=False, refusal=True)
                    return RedirectResponse(redirect_to, status_code=303)
                removals = removal_paths(form)
                if removals:
                    token = readmodels.text(form.get("f." + REMOVE_ACK_FIELD))
                    if not validate_remove_ack(request, str(path), token, revision):
                        _notice(request, SAVE_REFUSED_REMOVE_ACK, ok=False, refusal=True)
                        return RedirectResponse(redirect_to, status_code=303)
                result = configwrite.patch_config_file(
                    path, mutate, schemas=schema_files, expect_revision=revision)
        except configwrite.StaleConfigRevision:
            _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                    "rendered. Reload and try again."), ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigValidationError as ex:
            detail = _clean_detail(ex.errors) or str(ex)
            _notice(request, f"{REFUSED_BANNER} {detail}", ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configgen.UnknownFieldError:
            # a field the schema cannot place: the save is refused (nothing is written) rather than
            # letting apply_form drop it while the banner would still claim success.
            _notice(request, SAVE_REFUSED_UNPLACEABLE, ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configgen.DuplicateEntryError:
            # a NEW entry whose key already names an existing one: writing it would OVERWRITE that
            # entry (silent data loss), so the save is refused and nothing is written.
            _notice(request, SAVE_REFUSED_DUPLICATE_ENTRY, ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigWriteError as ex:
            _notice(request, f"{REFUSED_BANNER} {ex}", ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)

        _banner(request, result, kind=apply_kind, name=apply_name, node=apply_node,
                back=redirect_to)
        if removals:
            spend_remove_ack(request, str(path), revision)
        return RedirectResponse(redirect_to, status_code=303)

    @router.post(SETTINGS_SAVE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_save(request: Request):
        return await _save(request, path=config_path(request), schema_files=_schema_files(),
                           tree=_tree(), redirect_to=SETTINGS_PATH,
                           apply_kind=KIND_MAIN, apply_name="main")

    @router.post(SETTINGS_FILE_SAVE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_file_save(request: Request, kind: str, name: str, node: str = ""):
        require_file_capability(request, kind, write=True)
        entry = resolve_file(request, kind, name)
        require_node, slice_keys, forbidden = scoped_write_guard(request, entry, node)
        return await _save(request, path=entry.path, schema_files=list(entry.schema_files),
                           tree=entry_save_tree(request, entry),
                           redirect_to=file_url(entry),
                           slice_keys=slice_keys, require_node=require_node, forbidden=forbidden,
                           apply_kind=entry.kind, apply_name=entry.name,
                           apply_node=_apply_node(request, entry, node))

    # THE SCHEDULER WIZARD'S TWO POSTS. Both live on ``/actions/`` — NOT under the greedy
    # ``/fleet/{name:path}`` page route — so the "register a suffixed route before the page route"
    # ordering rule (``server_detail.add_routes``) does not apply to either of them.
    @router.post(SCHEDULER_SAVE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_scheduler_save(request: Request):
        """Write the review step's override into the scheduler file, through the ONE primitive.

        The scope, the plan and the refusal rules are the guard's and ``scheduler_plan``'s; this
        handler only decides the answer. Every refusal is a one-shot notice plus a 303, mirroring
        :func:`_save`'s set: a stale revision, an invalid candidate and an unwritable file each leave
        the file byte-identical. The write itself is PER-KEY (:func:`scheduler_override`), so no
        other server's block, no comment and no key this tab does not own can be lost.

        TWO DOORS, ONE PRIMITIVE. With the Expert flag set, the posted block REPLACES the server's
        block (:func:`scheduler_plan.apply_block`) — the operator wrote the block, so a key they
        dropped leaves with it. A block that does not parse, is not a mapping, or names a top-level
        key a server's block cannot carry is refused with one sentence and nothing written. Without
        the flag the plan is read and the per-key override is written, exactly as before.
        """
        form = await request.form(max_fields=SETTINGS_FORM_MAX_FIELDS)
        try:
            entry, scope = scheduler_write_guard(request, form.get(SCHEDULER_SCOPE_FIELD))
        except SchedulerRefusal as refusal:
            _notice(request, refusal.message, ok=False, refusal=True)
            return RedirectResponse(SCHEDULER_FALLBACK_URL, status_code=303)
        redirect_to = scheduler_redirect(scope)
        revision = readmodels.text(form.get(SETTINGS_REVISION_FIELD)) or None
        expert = scheduler_expert_answer(form)
        if expert is not None and not scheduler_access(request, scope.server).expert:
            # THE EXPERT DOOR IS ADMIN-ONLY, checked on the resolved server before anything is
            # built: a crafted flag plus block text from any other writer writes nothing.
            _notice(request, SCHEDULER_EXPERT_REFUSED, ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        plan: dict = {}
        if expert is not None:
            if expert["refusal"]:
                _notice(request, i18n.translate(request, expert["refusal"], **expert["params"]),
                        ok=False, refusal=True)
                return RedirectResponse(redirect_to, status_code=303)
        else:
            refusal = scheduler_plan.posted_key_refusal(form.multi_items())
            if refusal:
                _notice(request, refusal, ok=False, refusal=True)
                return RedirectResponse(redirect_to, status_code=303)
            plan = scheduler_plan.plan_from_form(form.multi_items())

        def mutate(document):
            if expert is not None:
                scheduler_plan.apply_block(document, scope.key, expert["block"],
                                           name=entry.display)
                return
            scheduler_plan.apply_override(document, scope.key,
                                          scheduler_override(document, scope, plan),
                                          name=entry.display)

        try:
            # A file the round trip reads as NO document — empty, comments only, or a bare ``null`` —
            # states no server blocks either: ``patch_config_file`` would substitute an empty mapping
            # for it and write into that. The shape is read here, from the file, so this save answers
            # the refusal the validate route already answers for the same file.
            if entry.path.is_file():
                scheduler_plan.require_mapping(configwrite.load_config_file(entry.path),
                                               entry.display)
            # ``applies`` is the file's own story (``config_apply`` derives it from the path), so the
            # notice says what really happens: a plugin configuration is live after that plugin is
            # reloaded, which is the offer's own target — not "the next bot restart".
            result = configwrite.patch_config_file(
                entry.path, mutate, schemas=list(entry.schema_files), expect_revision=revision,
                applies=configwrite.APPLIES_NOW)
        except configwrite.StaleConfigRevision:
            _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                    "rendered. Reload and try again."), ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigValidationError as ex:
            detail = _clean_detail(ex.errors) or str(ex)
            _notice(request, f"{REFUSED_BANNER} {detail}", ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigWriteError as ex:
            _notice(request, f"{REFUSED_BANNER} {ex}", ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        _banner(request, result, kind=SCHEDULER_KIND, name=SCHEDULER_FILE_NAME,
                back=redirect_to)
        return RedirectResponse(redirect_to, status_code=303)

    @router.post(SCHEDULER_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(SETTINGS_FORM_MAX_FIELDS))])
    async def settings_scheduler_validate(request: Request):
        """Check the review step's candidate WITHOUT writing: a JSON verdict, never a page.

        The candidate is built in memory (:func:`scheduler_candidate`) and handed to the very call a
        save makes, so a verdict that says "valid" is one the save would accept. The fault is located
        in the candidate (:func:`_locate`) so the operator sees a block and a line, not a pykwalify
        path. Nothing on disk is touched.

        AN EXPERT POST wins the candidate, the same way it wins the save: the panel's editor posts its
        text and the token only, so the block's PRESENCE is what makes this an expert check, and the
        server rides in the validate URL (:func:`scheduler_posted_scope`). One verdict therefore
        covers exactly the text a save would write.
        """
        form = await request.form(max_fields=SETTINGS_FORM_MAX_FIELDS)
        try:
            entry, scope = scheduler_write_guard(request, scheduler_posted_scope(form, request))
        except SchedulerRefusal as refusal:
            return JSONResponse(_verdict(ok=False, kind="scope",
                                         message=i18n.translate(request, refusal.message)))
        expert = scheduler_validate_block(form)
        if expert is not None and not scheduler_access(request, scope.server).expert:
            return JSONResponse(_verdict(ok=False, kind="scope",
                                         message=i18n.translate(request,
                                                                SCHEDULER_EXPERT_REFUSED)))
        plan: dict = {}
        if expert is not None:
            if expert["refusal"]:
                return JSONResponse(_verdict(ok=False, kind="scope",
                                             message=i18n.translate(request, expert["refusal"],
                                                                    **expert["params"])))
        else:
            refusal = scheduler_plan.posted_key_refusal(form.multi_items())
            if refusal:
                return JSONResponse(_verdict(ok=False, kind="scope",
                                             message=i18n.translate(request, refusal)))
            plan = scheduler_plan.plan_from_form(form.multi_items())
        try:
            document = scheduler_candidate(entry, scope, plan,
                                           block=expert["block"] if expert is not None else None)
        except configwrite.ConfigWriteError as ex:
            return JSONResponse(_verdict(ok=False, kind="schema", message=str(ex)))
        try:
            configwrite.validate_against_schema(configwrite.dump_config(document),
                                                list(entry.schema_files), path=entry.path)
        except configwrite.ConfigValidationError as ex:
            return JSONResponse(scheduler_verdict(request, document, ex, entry.path))
        except configwrite.ConfigWriteError as ex:
            return JSONResponse(_verdict(ok=False, kind="schema", message=str(ex)))
        return JSONResponse(_verdict(ok=True))

    @router.post(SETTINGS_SERVICE_TOGGLE_PATH, dependencies=[Depends(session.csrf_protect)])
    async def settings_service_toggle(request: Request, name: str):
        """Turn a SERVICE off (move its config aside) or back on (move it home).

        A service is enabled by its config file's PRESENCE — there is no boolean to flip — so "off"
        is a FILE action and this route names it. A service held off by its PLUGIN dependency is NOT
        a file action: nothing is moved and the notice points at the plugin list. Only a name that
        matches a discovered service is accepted, and only the two exact actions are honoured; the
        moved path is always the service's own ``config/services/<name>.yaml`` (never a posted one).
        """
        services = catalog.discover_services(config_dir(request))
        info = next((s for s in services if s.name.lower() == name.lower()), None)
        if info is None:
            _notice(request, SERVICE_TOGGLE_UNKNOWN, ok=False, refusal=True)
            return RedirectResponse(SETTINGS_PATH, status_code=303)
        form = await request.form()
        action = str(form.get("action") or "")
        real = catalog.service_config_path(config_dir(request), info.name)
        aside = catalog.disabled_config_path(config_dir(request), info.name)
        enabled_names = {row["name"].lower() for row in
                         plugin_registry.build_block(*_opt_plugins_value(request))["rows"]
                         if row["enabled"]}
        active = enabled_names | set(plugin_registry.default_plugins())
        if info.plugin and info.plugin not in active and real.is_file():
            # the file is fine; the PLUGIN is what holds it off — move nothing, say so.
            _notice(request, SERVICE_TOGGLE_HELD, ok=False, refusal=True)
            return RedirectResponse(SETTINGS_PATH, status_code=303)
        if action == "disable" and real.is_file():
            real.rename(aside)
            _notice(request, i18n.translate(request, SERVICE_MOVED_ASIDE, path=str(aside)),
                    ok=True, refusal=True)
        elif action == "enable" and aside.is_file():
            aside.rename(real)
            _notice(request, i18n.translate(request, SERVICE_MOVED_BACK, path=str(real)),
                    ok=True, refusal=True)
        else:
            _notice(request, SERVICE_TOGGLE_REFUSED, ok=False, refusal=True)
        return RedirectResponse(SETTINGS_PATH, status_code=303)

    def _ensure_block(document, base: tuple) -> None:
        """Create the (empty) block at *base* in *document*, making any missing intermediate maps.

        This is HOW a "+ Add block" writes the YAML: a multi-node install writes
        ``<node>: {<instance>: {}}`` and a single-node one ``<instance>: {}``; an extension writes
        ``<node>: {instances: {<instance>: {extensions: {<Name>: {}}}}}``. Only the block is created —
        the operator fills it in, and the deep-merge the bot does means an empty block is a no-op
        override until a field is set.
        """
        from ruamel.yaml.comments import CommentedMap
        node = document
        for token in base[:-1]:
            child = node.get(token) if isinstance(node, dict) else None
            if not isinstance(child, dict):
                child = CommentedMap()
                node[token] = child
            node = child
        last = base[-1]
        if not isinstance(node.get(last), dict):
            node[last] = CommentedMap()

    @router.post(SETTINGS_ADD_BLOCK_PATH, dependencies=[Depends(session.csrf_protect)])
    async def settings_add_block(request: Request, kind: str, name: str):
        """Add a NEW node/instance block to the file the operator opened, through the ONE primitive.

        The block is created empty (the documented shape) and schema-validated like any other write; a
        block that already exists adds nothing. The label the operator reads is the same rule the tabs
        use (``<node> + <instance>`` when several nodes, else the plain name).
        """
        require_file_capability(request, kind, write=True)
        entry = resolve_file(request, kind, name)
        redirect_to = file_url(entry)
        form = await request.form()
        node = readmodels.text(form.get("_node")) or None
        instance = readmodels.text(form.get("_instance")).strip()
        if not instance:
            _notice(request, i18n.translate(request, ADD_BLOCK_REFUSED,
                                            detail=i18n.translate(request, ADD_BLOCK_NAME_REQUIRED)),
                    ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        node_names = _node_names(config_dir(request))
        target = scopes.add_target(kind, node, instance, node_names)
        base = target["base"]
        label = target["instance"]
        revision = readmodels.text(form.get(SETTINGS_REVISION_FIELD)) or None
        try:
            result = configwrite.patch_config_file(
                entry.path, lambda document: _ensure_block(document, base),
                schemas=list(entry.schema_files), expect_revision=revision)
        except configwrite.StaleConfigRevision:
            _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                    "rendered. Reload and try again."), ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigValidationError as ex:
            detail = _clean_detail(ex.errors) or str(ex)
            _notice(request, i18n.translate(request, ADD_BLOCK_REFUSED, detail=detail),
                    ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        except configwrite.ConfigWriteError as ex:
            _notice(request, i18n.translate(request, ADD_BLOCK_REFUSED, detail=str(ex)),
                    ok=False, refusal=True)
            return RedirectResponse(redirect_to, status_code=303)
        if result.changed:
            _notice(request, i18n.translate(request, ADD_BLOCK_DONE, scope=label),
                    ok=True, refusal=True, applies=result.applies)
        else:
            _notice(request, i18n.translate(request, ADD_BLOCK_EXISTS, scope=label),
                    ok=True, refusal=True, applies=result.applies)
        return RedirectResponse(redirect_to, status_code=303)

    return router
