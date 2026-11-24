"""ONE NODE's configuration — a node's own block of ``config/nodes.yaml``, from the schema.

WHY THIS MODULE EXISTS. The console lists every node on the Nodes page; the global Settings area edits
the install's ``main.yaml`` and the per-service/plugin files, but a node's OWN configuration lives in
``config/nodes.yaml``, one block per node. This module is where a node row's cogwheel lands.

TWO PAGES LIVE HERE, AND ONLY ONE RENDERS.

* THE NODE PAGE (``/nodes/<node>``, :data:`NODE_PAGE_PATH`) is the node's settings surface: the node's
  own block shown as TABS — "Node-wide" (the node's own settings AND its node-scoped extensions),
  then one "Instance <name>" tab per INSTANCE the node declares (that instance's settings AND its
  instance-scoped extensions) — with the Expert Mode editor over that node's slice of the file. This
  is what replaced the old per-node settings page.
* THE OLD PER-NODE SETTINGS PAGE (``/nodes/<node>/settings``, :data:`NODE_SETTINGS_PATH`) no longer
  renders: both its GET and its POST answer a ``307`` redirect to the node page, so a bookmark still
  lands somewhere useful and a stale form's POST re-posts to the node page's own save. Nothing links
  to it — the row's cogwheel (:func:`node_settings_control`) points at the node page.

IT IS THE SAME RENDERER AS THE SETTINGS AREA. The form is generated from the very pykwalify schema
the bot validates ``nodes.yaml`` against (``schemas/nodes_schema.yaml`` plus every
``extensions/**/*_schema.yaml`` — the set ``core/data/impl/nodeimpl.py`` hands the validator), through
:mod:`services.webservice.configgen`, and rendered by the ONE control macro the Settings page uses
(``templates/_config_fields.html``). There is no second form engine and no hand-written field list.

THE TREE IS THE NODE SCHEMA'S PER-NODE BLOCK, SCOPED TO THIS NODE. ``describe`` of the node schema
yields one ``regex;(.+)`` container (any node name) whose element is the per-node map; this module
REBASES that element's path from ``*`` to the actual node key, so the control names (``f.<node>.…``)
and the document paths line up for exactly one node — the save mutates that node's block in the whole
document, and every other node's block (and its comments) is left untouched.

THE WRITE GOES THROUGH THE ONE PRIMITIVE. A save is a ``configwrite.patch_config_file`` over the WHOLE
``nodes.yaml`` document, with a mutate that calls ``configgen.apply_form`` against the scoped tree, so
it is round-trip safe (comments/order kept), schema-validated, atomic, keeps a ``.bak`` and obeys the
no-op rule — exactly like a global-file save, because it IS the same primitive.

WHO MAY. Admin only, NO scope grant — the SAME rule the node lifecycle controls read
(``pages/actions``: ``NODES_ROLES``). A node's configuration is the machine's, not a server a
manager's scope could name, so a DCS Admin (who may read the Nodes list) is refused the page and
offered no cogwheel.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.actions import action_available, call_action
from core.utils import configwrite

from .. import (catalog, config_apply, config_hints, configgen, extension_switch, i18n, mount,
                permissions, plugin_registry, readmodels, scopes, session, validation)
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import nodes as nodes_page
from . import presets as presets_page
from . import settings as settings_page
#: the confirmation page every destructive console action uses — the ONE name, owned by the action
#: module; the removal dialog below renders THAT template (there is no second dialog template and no
#: client-side modal).
from .actions import CONFIRM_TEMPLATE

__all__ = [
    "NODE_SETTINGS_PATH", "NODE_SETTINGS_CAPABILITY", "NODE_SETTINGS_ROLES",
    "NODE_SETTINGS_TITLE", "NODE_SETTINGS_LEAD", "NODES_YAML_NAME", "NODES_SCHEMA_PATH",
    "NODE_PAGE_PATH", "NODE_PAGE_VALIDATE_PATH", "NODE_PAGE_HINTS_PATH", "NODE_PAGE_TEMPLATE",
    "NODE_EXTENSION_PATH", "NODE_EXTENSION_VALIDATE_PATH", "NODE_EXTENSION_HINTS_PATH",
    "NODE_PAGE_SECTION_SETTINGS", "NODE_PAGE_SECTION_EXTENSIONS", "NODE_PAGE_EXTENSION_KIND",
    "NODE_PAGE_SCOPE_NODE", "NODE_PAGE_SCOPE_INSTANCE",
    "NODE_PAGE_SCOPE_FIELD", "NODE_PAGE_SCOPE_DEFAULT", "NODE_PAGE_SCOPE_INSTANCE_PREFIX",
    "NODE_PAGE_EXTENSION_UNCONFIGURED",
    "NODE_PAGE_EXTENSION_FIELD", "NODE_PAGE_EXTENSION_ACTION_FIELD",
    "NODE_PAGE_EXTENSION_ADD", "NODE_PAGE_EXTENSION_REMOVE",
    "NODE_PAGE_EXTENSION_OFF", "NODE_PAGE_EXTENSION_TURN_ON", "NODE_PAGE_EXTENSION_TURN_OFF",
    "NODE_PAGE_EXTENSION_DEFAULT_BADGE", "NODE_PAGE_EXTENSION_ENABLED", "NODE_PAGE_EXTENSION_DISABLED",
    "NODE_PAGE_EXTENSION_DISABLE", "NODE_PAGE_EXTENSION_ENABLE",
    "NODE_PAGE_EXTENSION_ENABLE_ACTION", "NODE_PAGE_EXTENSION_DISABLE_ACTION",
    "NODE_PAGE_EXTENSION_NEEDS_SETUP", "NODE_PAGE_EXTENSION_INCOMPLETE",
    "NODE_PAGE_EXTENSION_KEY_ON_NODE_TAB", "NODE_PAGE_EXTENSION_KEY_ON_INSTANCE_TAB",
    "NODE_PAGE_EXTENSION_NOT_READY", "NODE_PAGE_EXTENSION_ADD_NEEDS_SETUP",
    "NODE_PAGE_EXTENSION_ADD_LABEL", "NODE_PAGE_EXTENSION_ADD_LINK", "NODE_EXTENSION_CREATE_NOTE",
    "NODE_EXTENSION_CREATE_REJECTED",
    "NODE_PAGE_DICT_SCOPE_NODE", "NODE_PAGE_DICT_SCOPE_INSTANCE",
    "NODE_PAGE_EXTENSION_TURNED_OFF", "NODE_PAGE_EXTENSION_TURNED_ON",
    "NODE_PAGE_EXTENSION_REMOVE_TITLE", "NODE_PAGE_EXTENSION_REMOVE_PAGE_TITLE",
    "NODE_PAGE_EXTENSION_REMOVE_HEADING", "NODE_PAGE_EXTENSION_REMOVE_DETAIL",
    "NODE_PAGE_EXTENSION_DISCARD_WARNING", "NODE_PAGE_EXTENSION_REMOVE_BLOCK",
    "NODE_PAGE_EXTENSION_ADDED", "NODE_PAGE_EXTENSION_REMOVED",
    "NODE_PAGE_EXTENSION_ADD_EXISTS", "NODE_PAGE_EXTENSION_REMOVE_ABSENT",
    "NODE_PAGE_EXTENSION_ALL_CONFIGURED",
    "NODE_PAGE_EXTENSION_REMOVE_PATH", "NODE_PAGE_EXTENSION_ACK_FIELD",
    "SAVE_REFUSED_FOREIGN_SCOPE", "SAVE_REFUSED_UNKNOWN_SCOPE", "SAVE_REFUSED_NO_NODE_BLOCK",
    "SAVE_REFUSED_UNKNOWN_EXTENSION", "SAVE_REFUSED_EXTENSION_ACK",
    "mint_extension_ack", "validate_extension_ack", "spend_extension_ack", "_extension_ack_binding",
    "node_settings_control", "node_tree", "nodes_yaml_path",
    "node_page_url", "node_extension_remove_url", "node_page_context", "save_node", "scope_save_tree",
    "capabilities", "nav_items", "add_routes",
]

log = logging.getLogger(__name__)

#: THE PAGE PATH — one path segment per node, plus a fixed suffix. It answers GET (render) and POST
#: (save) under the SAME literal template path, exactly like the console's other per-target pages.
NODE_SETTINGS_PATH = "/nodes/{node}/settings"

#: The capability the page declares AND the row's cogwheel is offered on. ONE string, so the link and
#: the route's gate cannot disagree.
NODE_SETTINGS_CAPABILITY = "nodes.settings"

#: A node's own configuration is ADMIN work with NO manager scope grant — the rule the node lifecycle
#: controls already read (``pages/actions.NODE_ROLES``), reused rather than re-spelled.
NODE_SETTINGS_ROLES: tuple[str, ...] = ("Admin",)

NODE_SETTINGS_TITLE = i18n._("Node settings")
NODE_SETTINGS_LEAD = i18n._("This node's block of nodes.yaml — the machine's own settings. A save "
                            "applies on the next bot restart.")

#: the file the page edits, and the schema it is generated from and validated against. The schema is
#: spelled from THIS file so the console never depends on the process working directory.
NODES_YAML_NAME = "nodes.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[3]
NODES_SCHEMA_PATH = str(_REPO_ROOT / "schemas" / "nodes_schema.yaml")


def declare() -> None:
    """Declare the capability. Idempotent for the same roles and NO scope grant."""
    permissions.declare_capability(NODE_SETTINGS_CAPABILITY, NODE_SETTINGS_ROLES)


def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    declare()
    return {NODE_SETTINGS_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_PAGE_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_PAGE_VALIDATE_PATH: NODE_SETTINGS_CAPABILITY,
            # a hint is a READ of the schema, so it carries the PAGE's capability — never a write
            NODE_PAGE_HINTS_PATH: NODE_SETTINGS_CAPABILITY,
            # the switch-state poll is a READ of this node's in-flight switches: the PAGE's capability
            NODE_PAGE_SWITCH_STATE_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_EXTENSION_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_EXTENSION_VALIDATE_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_EXTENSION_HINTS_PATH: NODE_SETTINGS_CAPABILITY,
            # opening the removal dialog is not a lesser right than the write it leads to
            NODE_PAGE_EXTENSION_REMOVE_PATH: NODE_SETTINGS_CAPABILITY}


def nav_items():
    """No sidebar entry: the page is reached from a Node row, not from the nav (like the server page)."""
    return ()


#: THE OLD PER-NODE SETTINGS PAGE IS GONE. ``node_settings_url`` (the ``/nodes/<node>/settings`` builder)
#: was removed with it: the row's cogwheel now links to :func:`node_page_url`, and the old path is kept
#: only as a REDIRECT (see ``add_routes``) so a bookmark still lands on the node page.


def node_settings_control(node_name: str, roles) -> dict | None:
    """The node row's settings cogwheel, as DATA — or ``None`` for a caller who may not use it.

    IT OPENS THE NODE PAGE (``/nodes/<node>``), not the old ``/nodes/<node>/settings``: the node page
    edits the SAME ``nodes.yaml``, scoped to the SAME node, and adds the Node-wide + instance tabs and
    Expert Mode. The old settings URL is kept as a redirect for bookmarks; nothing links to it.

    The gate is ``permissions.allows`` on :data:`NODE_SETTINGS_CAPABILITY` — the SAME predicate the
    route's access gate runs, so ``offered ⊆ authorised`` holds by construction: whoever is not
    offered the cogwheel is refused by the page with the console's own 403, and no second
    authorization rule exists to drift.

    A LINK, not a form: ``path`` is a URL and there is no CSRF token, because opening settings changes
    nothing. ``title``/``aria`` name the node, so the control is never an unlabelled glyph.
    """
    if not permissions.allows(NODE_SETTINGS_CAPABILITY, roles or ()):
        return None
    name = readmodels.text(node_name)
    return {
        "path": node_page_url(name),
        "label": i18n._("Settings"),
        "title": {"msgid": i18n._("Settings — open {node}'s own settings (nodes.yaml)"),
                  "params": {"node": name}},
        "aria": {"msgid": i18n._("Open the settings of node {node}"), "params": {"node": name}},
    }


# ------------------------------------------------------------------------------- the scoped tree

def _rebase(field: configgen.Field, node_name: str, *, top: bool = True) -> configgen.Field:
    """A copy of *field* with the node-schema ``*`` placeholder replaced by the node's own key.

    The node schema's per-node block sits at ``("*", …)`` (the element of the root ``regex;(.+)``); the
    rebased copy carries the concrete node key as its FIRST path token (and, at the top, as its KEY),
    so the control names (``f.<node>.…``) and the document paths point at exactly this node's block.
    Deeper ``*`` tokens (a nested sequence/regex element) are left as they are.
    """
    path = (node_name, *field.path[1:]) if (field.path and field.path[0] == "*") else field.path
    return replace(
        field,
        key=node_name if top else field.key,
        path=path,
        children=tuple(_rebase(child, node_name, top=False) for child in field.children),
        element=_rebase(field.element, node_name, top=False) if field.element is not None else None,
    )


def node_tree(node_name: str) -> configgen.Field:
    """The generator tree for ONE node: the node schema's per-node block, scoped to *node_name*.

    ``describe`` of the node schema is one ``regex;(.+)`` container whose element is the per-node map;
    this takes that element and rebases it. The result is wrapped in a root map whose only child is the
    node's block, so ``configgen.scopes`` yields one DEFAULT scope and the save path resolves
    ``f.<node>.…`` fields against it.
    """
    schema = configgen.load_schema(NODES_SCHEMA_PATH,
                                   schema_files=configgen.schema_files_for(NODES_SCHEMA_PATH))
    root = configgen.describe(schema)
    element = next((child.element for child in root.children
                    if child.kind == configgen.KIND_REGEX and child.element is not None), None)
    if element is None:  # pragma: no cover - the node schema always has its regex container
        raise configwrite.ConfigWriteError("The node schema has no per-node block to render.")
    scoped = _rebase(element, node_name)
    return configgen.Field(key="", kind=configgen.KIND_MAP, path=(), children=(scoped,),
                           kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])


# ------------------------------------------------------------------------------- the page

def nodes_yaml_path(request: Request) -> Path:
    """The ``nodes.yaml`` this console reads and writes, from the install config directory."""
    return settings_page.config_dir(request) / NODES_YAML_NAME


def _document_node_key(document, node_name: str) -> str | None:
    """The key in *document* that names *node_name* — exact, else case-folded — or ``None``."""
    if not isinstance(document, dict):
        return None
    if node_name in document:
        return node_name
    wanted = readmodels.text(node_name).casefold()
    for key in document:
        if readmodels.text(key).casefold() == wanted:
            return str(key)
    return None


def _viewed_node(source, node_name: str) -> str | None:
    """The canonical name of *node_name* in the caller's own cluster view — or ``None``.

    Reads the SAME scoped source the Nodes page renders from, so a node the caller cannot see is not
    resolvable here either. The node's REACHABILITY is deliberately not consulted: the settings live in
    the master's ``nodes.yaml``, so an OFFLINE node still has settings to show.
    """
    entries = getattr(source, "nodes", None)
    if not isinstance(entries, dict):
        return None
    if node_name in entries:
        return readmodels.text(node_name)
    wanted = readmodels.text(node_name).casefold()
    for key in entries:
        if readmodels.text(key).casefold() == wanted:
            return readmodels.text(key)
    return None


def visible_nodes(request: Request) -> list[str]:
    """The node names the caller can see — the NODE SELECTOR's choices on this page.

    A READING of the SAME scoped source the Nodes page renders from (:func:`_viewed_node`), so the
    selector never offers a node the caller could not open, and the current node is always one of them.
    """
    source = dashboard_page.request_source(request)
    entries = getattr(source, "nodes", None)
    if not isinstance(entries, dict):
        return []
    return [readmodels.text(name) for name in entries]


def _notice(request: Request, message: str, *, ok: bool, apply: dict | None = None) -> None:
    """Store the one-shot notice of a save — the console's own wording, translated at render.

    ``apply`` is the "Apply now" control's contract for the file just written (``config_apply``), or
    ``None`` for a save that changed nothing and has nothing to apply.
    """
    request.session[actions_page.NOTICE_KEY] = {
        "message": message, "ok": ok, "audit": "", "audit_warning": False,
        "action": NODE_SETTINGS_TITLE, "revert": "", "revert_omitted": "",
        "refusal": True, "applies": "", "apply": apply, "pending": False,
    }


# ------------------------------------------------------------------ the NODE page (/nodes/<node>)
# THE NODE PAGE — The design: a node's own block of ``nodes.yaml`` shown as TABS. The first tab is
# "Node-wide" (the node's own settings AND its node-scoped extensions); then one "Instance <name>" tab
# per INSTANCE the node's block declares (that instance's settings AND its instance-scoped extensions).
# Every tab is
# rendered from the SAME node-schema tree the settings page uses (:func:`node_tree`) and its form is
# saved by the SAME whole-document patch primitive and the SAME node-scoped guard — this page MOVES an
# entry point, it does not rebuild the writer. The Expert YAML editor is the SAME editor
# (``static/settings-yaml.js``) and its slice is the selected NODE (``[<node>]``), exactly as the
# node-settings panel works today, so no other node is ever handed to an operator editing one node.

#: THE PAGE PATH — one path segment per node. It answers GET (render) and POST (save) under the SAME
#: literal template path, exactly like the console's other per-target pages.
NODE_PAGE_PATH = "/nodes/{node}"
#: The Expert Mode validator route for the node page. Same contract as the Settings area's validator:
#: a POST carrying the posted slice, answered with a structured JSON verdict and writing nothing.
NODE_PAGE_VALIDATE_PATH = "/actions/nodes/{node}/validate"
#: THE COMPLETION HINTS for the node page's Expert Mode editor: a read of ``nodes_schema.yaml`` as
#: JSON (:mod:`services.webservice.config_hints`), rooted at the DOCUMENT root — the editor's slice is
#: ``{<node>: block}``, so its root key is the node name the schema's ``regex;(.+)`` block names.
NODE_PAGE_HINTS_PATH = "/actions/nodes/{node}/hints"
#: THE SWITCH'S OWN POLL — what a card whose extension is switching on asks for, every few seconds,
#: until the server says the job is done. A READ: it reports the in-flight jobs of ONE node
#: (:mod:`services.webservice.extension_switch`) and writes nothing, so it needs no CSRF token.
NODE_PAGE_SWITCH_STATE_PATH = "/nodes/{node}/_switch-state"
NODE_PAGE_TEMPLATE = "node_page.html"

#: the two section headings on a tab (the settings form, then the extensions).
NODE_PAGE_SECTION_SETTINGS = i18n._("Settings")
NODE_PAGE_SECTION_EXTENSIONS = i18n._("Extensions")
#: the marker a scope's extensions carry.
NODE_PAGE_EXTENSION_KIND = i18n._("extension")

#: the sentence over a tab's fields. The SAME shape the scoped cogwheel view uses, worded for the node
#: page: the Node-wide tab is the node's own block, an instance tab INHERITS from it.
NODE_PAGE_SCOPE_NODE = i18n._(
    "<b>Node-wide</b> is this node's own block — its settings and the extensions every instance "
    "starts from. An instance tab beside it changes only that instance.")
NODE_PAGE_SCOPE_INSTANCE = i18n._(
    "This block changes only <b>%(scope)s</b>. It INHERITS everything it does not set from the "
    "node's own block — nothing is copied.")
#: the two scope NAMES the tab strip shows, as msgids — the removal dialog names the scope with the
#: SAME words ("Node-wide" / "Instance <name>"), so the page and its dialog cannot drift.
NODE_PAGE_SCOPE_NODE_LABEL = i18n._("Node-wide")
NODE_PAGE_SCOPE_INSTANCE_LABEL = i18n._("Instance %(name)s")

#: THE ACTIVE-SCOPE CARRIER. The tabs are a SERVER-SIDE selection (``?tab=<n>``), but a SAVE must know
#: which scope a form came from WITHOUT inferring it from the posted fields — inference writes a
#: DEFAULT field from an instance tab (and the reverse). So the form carries
#: the scope in a hidden field, and the route ALSO reads the ``?tab=`` index as a fallback (a bookmark
#: or a stale form). The value is :data:`NODE_PAGE_SCOPE_DEFAULT` for the node's own block or
#: ``instance:<name>`` for an instance's. It is ``_``-prefixed, so ``configgen.apply_form`` (which
#: reads only ``f.``-prefixed fields) never sees it.
NODE_PAGE_SCOPE_FIELD = "_scope"
NODE_PAGE_SCOPE_DEFAULT = "default"
NODE_PAGE_SCOPE_INSTANCE_PREFIX = "instance:"

#: the sentence a save shows when a posted field names a scope OTHER than the selected tab's — a
#: sibling INSTANCE, the node's own DEFAULT from an instance tab (or the reverse), or another node.
#: The save is REFUSED (nothing written, the file byte-identical), never silently dropped.
SAVE_REFUSED_FOREIGN_SCOPE = i18n._(
    "No change was saved: the form named a setting outside the tab you were editing, so saving it "
    "would have written into a scope you did not open. Reload the page, open that tab, and try again "
    "— nothing was written.")
#: the sentence a save shows when the page did not name a tab of this node (a bogus scope carrier, or
#: a ``?tab=`` beyond the node's instances). Refused — nothing is written.
SAVE_REFUSED_UNKNOWN_SCOPE = i18n._(
    "No change was saved: this page did not name a tab of this node, so there is nothing to write. "
    "Reload the page and pick a tab — nothing was written.")
#: the sentence a save shows when the node has NO block in ``nodes.yaml``. Such a node renders
#: read-only (it has nothing to show), so its save door REFUSES rather than silently CREATING the
#: block — a node is added in Expert Mode, never as a side effect of saving a form.
SAVE_REFUSED_NO_NODE_BLOCK = i18n._(
    "No change was saved: this node has no block in nodes.yaml yet, so there is nothing to save. Add "
    "the node in Expert Mode first — nothing was written.")
#: the sentence over an extension section the selected scope does NOT configure. The section is still
#: SHOWN (every schema-known extension stays discoverable from the node page) but its controls are
#: disabled and not posted: the form never CREATES an extension block — that is Expert Mode work
#: ("no implicit block creation").
NODE_PAGE_EXTENSION_UNCONFIGURED = i18n._(
    "This extension is not configured for this scope yet. The form never creates it — add its block "
    "in Expert Mode, then it becomes editable here.")

# --------------------------------------------------------------- the extension card's ON/OFF switch
# THE CARD SET COMES FROM THE SCHEMA, THE CARD STATE FROM THE FILE AND THE RUNTIME. Every extension a
# scope's schema declares is shown as a card (``_extension_sections``); an UNCONFIGURED non-default
# extension is a choice of the ONE "+ Add" tile, which writes its block SEEDED from the ``default``
# values its own dict declares for that scope. A CONFIGURED extension
# is switched with the plain, NON-DESTRUCTIVE ``disable``/``enable`` actions: they WRITE
# ``enabled: false`` / ``enabled: true`` into the selected scope's block and DELETE nothing, which the
# runtime reads for EVERY extension (``core/extension.py``: ``enabled`` = ``config.get('enabled',
# True)``). REMOVING a block is a different, destructive write and lives behind the console's own
# confirmation dialog (:data:`NODE_PAGE_EXTENSION_REMOVE_PATH`), never behind the switch.
#
# THE SWITCH IS INSTANCE-TAB ONLY and it is GATED ON COMPUTED READINESS: the Node-wide tab's cards show
# the shared values and carry no control (ON/OFF writes the Instance scope), and an instance card whose
# required values are still empty draws the switch in the shared component's non-actionable state next
# to a warning (see :func:`_extension_missing_keys`).
#
# Both switch actions go through the SAME scoped save (:func:`save_node`) and the SAME whole-document
# patch, so the scope guard (a foreign node, instance or out-of-scope extension is refused with nothing
# written), the no-op rule (an unchanged value leaves the file byte-identical) and the
# byte-identical-elsewhere promise all hold unchanged.
NODE_PAGE_EXTENSION_FIELD = "_ext"
NODE_PAGE_EXTENSION_ACTION_FIELD = "_ext_action"
NODE_PAGE_EXTENSION_ADD = "add"
NODE_PAGE_EXTENSION_REMOVE = "remove"
#: THE TWO ACTIONS THE SWITCH USES, for EVERY extension (see :func:`_extension_switch`). A DEFAULT
#: extension — one the runtime loads even with no ``nodes.yaml`` block (``DEFAULT_EXTENSIONS``, merged
#: by ``core/data/impl/serverimpl.py``'s ``load_extension``/``init_extensions``) — is NOT turned off by
#: removing its block: an absent block means "default on", so the extension would come straight back.
#: Its OFF therefore WRITES the value the loader reads to turn it off — ``enabled: false`` — into the
#: correct scope's block, and its ON writes ``enabled: true``. Neither action ever DELETES the block,
#: which is why the same pair serves a CONFIGURED non-default extension too: its block keeps every
#: value it holds while it is switched off.
NODE_PAGE_EXTENSION_DISABLE = "disable"
NODE_PAGE_EXTENSION_ENABLE = "enable"
#: THE ACKNOWLEDGEMENT the confirmation dialog MINTS and its form posts back. The dialog's GET shows the
#: block's own contents AND mints a random token bound to exactly the (node, scope, extension) it
#: displayed, stamped with the file revision; that token rides in the hidden field
#: :data:`NODE_PAGE_EXTENSION_ACK_FIELD` of the dialog's form, and the ``remove`` save REFUSES with
#: nothing written unless it presents a token that matches THAT binding. This is what makes the
#: "never a silent write" claim a SERVER rule rather than a UI one: a value-carrying block cannot be
#: removed by a bare POST, only by one that presents an acknowledgement the dialog produced (see
#: :func:`mint_extension_ack` / :func:`validate_extension_ack`).
NODE_PAGE_EXTENSION_ACK_FIELD = "_ext_ack"
#: the card's own STATE word and the two switch controls.
NODE_PAGE_EXTENSION_OFF = i18n._("Not configured")
NODE_PAGE_EXTENSION_TURN_ON = i18n._("Turn on")
NODE_PAGE_EXTENSION_TURN_OFF = i18n._("Turn off")
#: THE DEFAULT MARKER — the SAME one-word badge a default PLUGIN tile carries (reused verbatim, so
#: the two surfaces cannot drift and no second style is invented). A default extension shows it
#: whatever its block says, because it is part of the runtime's own set.
NODE_PAGE_EXTENSION_DEFAULT_BADGE = plugin_registry.DEFAULT_BADGE
#: THE TWO STATE WORDS EVERY CONFIGURED CARD'S SWITCH SHOWS. The state is the RUNTIME's
#: (enabled/disabled), not the file's (configured/not configured): an unconfigured default extension
#: runs, and a configured non-default one can be switched off without losing its block, so
#: "Configured"/"Not configured" would be the very lie this fixes.
NODE_PAGE_EXTENSION_ENABLED = i18n._("Enabled")
NODE_PAGE_EXTENSION_DISABLED = i18n._("Disabled")
#: THE THIRD CARD STATE'S WORD — a block exists but a required value of its scope is still empty, so
#: the switch is drawn in the shared component's NON-ACTIONABLE state (``switch_control``'s
#: ``off-fixed`` span: no form, no POST). See :func:`_extension_missing_keys`.
NODE_PAGE_EXTENSION_NEEDS_SETUP = i18n._("Needs setup")
#: THE ONE-LINE WARNING an incomplete card carries (``tile_card(warn=…)``), naming the EMPTY required
#: keys and the tab each one is filled on. Readiness is COMPUTED (the extension's own
#: ``NODE_CONFIG_DICT`` / ``CONFIG_DICT`` ``required: True`` flags, the schema only as the fallback),
#: never inferred from the presence of a block.
NODE_PAGE_EXTENSION_INCOMPLETE = i18n._("Not ready to switch on — %(keys)s.")
NODE_PAGE_EXTENSION_KEY_ON_NODE_TAB = i18n._("%(keys)s must be set on the Node-wide tab")
NODE_PAGE_EXTENSION_KEY_ON_INSTANCE_TAB = i18n._("%(keys)s must be set on this extension's own tab")
#: the sentence the switch's save shows when an enable/disable is posted for an extension that is not
#: ready: REFUSED, nothing written — the same discipline the scope guard already applies.
NODE_PAGE_EXTENSION_NOT_READY = i18n._(
    "No change was saved: the extension %(name)s is not ready to switch on — %(keys)s. Nothing was "
    "written.")
#: the sentence an ADD shows when the scope's schema cannot create the extension's block from its own
#: defaults — a block it would have to ASK for values it cannot seed. REFUSED, nothing written: the
#: only door that can create such a block is the extension's own page, where the fields have room.
NODE_PAGE_EXTENSION_ADD_NEEDS_SETUP = i18n._(
    "The extension %(name)s was not added: this scope cannot create its block from defaults. Nothing "
    "was written — open the extension's own page and save the values there.")
#: the same refusal when a seeded value did not pass the schema — still refused, still nothing written.
NODE_PAGE_EXTENSION_ADD_REJECTED = i18n._(
    "The extension %(name)s was not added: its values did not pass the check. Nothing was written.")
#: THE SWITCH IN FLIGHT. An enable runs the extension's own ``install()``/``prepare()``, which for one
#: that ships a package is a pip install: minutes. The save hands that to a background job
#: (:mod:`services.webservice.extension_switch`) and these are the three things the operator reads —
#: the label the disabled switch wears while it runs, and the sentence the save leaves behind for it.
NODE_PAGE_EXTENSION_BUSY_ENABLE = i18n._("Enabling…")
NODE_PAGE_EXTENSION_BUSY_DISABLE = i18n._("Disabling…")
NODE_PAGE_EXTENSION_BUSY_TITLE = i18n._("This is running now — the switch unlocks when it is done")
NODE_PAGE_EXTENSION_SWITCH_STARTED = i18n._(
    "Turning %(name)s on — installing can take a few minutes. The switch stays locked until it is "
    "done and the result follows here.")
NODE_PAGE_EXTENSION_SWITCH_STOPPED = i18n._(
    "Turning %(name)s off — the switch stays locked until it is done and the result follows here.")
#: A SECOND attempt while the job runs: REFUSED, nothing started — this is the server's answer, so it
#: holds for a second click, a second tab and a second operator alike.
NODE_PAGE_EXTENSION_SWITCH_BUSY = i18n._(
    "%(name)s is already being switched — wait for it to finish. Nothing was started.")
#: THE ADD ROW'S OWN WORD. Its control writes a block and enables nothing, so it is "Add" — never
#: "Turn on", which reads as an enable and made a working add report itself as a broken switch.
NODE_PAGE_EXTENSION_ADD_LABEL = i18n._("Add")
#: THE ADD ENTRY THAT CANNOT WRITE. When the block a scope would seed cannot pass the schema's own
#: required checks, the row's control is a LINK to that extension's page for the same scope instead of
#: a one-click write — this is the link's tooltip, and the destination is where the values are entered.
NODE_PAGE_EXTENSION_ADD_LINK = i18n._("Set up on the extension's page")
#: THE TWO SCOPES an extension's own dicts are read per (``NODE_CONFIG_DICT`` / ``CONFIG_DICT``). A key
#: of the first is filled on the Node-wide tab, a key of the second on the extension's own tab.
NODE_PAGE_DICT_SCOPE_NODE = "node"
NODE_PAGE_DICT_SCOPE_INSTANCE = "instance"
#: THE TWO ACTIONS the switch calls WHEN the bot is up. They run the extension's OWN
#: ``enable()``/``disable()`` (install, prepare, startup / shutdown) inside the server, which a config
#: write cannot do; with no action available (the bot is down) the switch falls back to the
#: ``nodes.yaml`` patch below.
NODE_PAGE_EXTENSION_ENABLE_ACTION = "enable_extension"
NODE_PAGE_EXTENSION_DISABLE_ACTION = "disable_extension"
#: the notices the switch leaves (translated AND interpolated at the route).
NODE_PAGE_EXTENSION_TURNED_OFF = i18n._(
    "Turned off the extension %(name)s for this scope — it will not load there.")
NODE_PAGE_EXTENSION_TURNED_ON = i18n._(
    "Turned on the extension %(name)s for this scope.")
#: the notices the switch's other writes leave (translated AND interpolated at the route, like the
#: Add-block notices, because they carry the extension's name).
NODE_PAGE_EXTENSION_ADDED = i18n._(
    "Added the extension %(name)s to this scope with its default values.")
NODE_PAGE_EXTENSION_REMOVED = i18n._("Removed the extension %(name)s from this scope.")
NODE_PAGE_EXTENSION_ADD_EXISTS = i18n._(
    "The extension %(name)s is already configured for this scope — nothing was added.")
NODE_PAGE_EXTENSION_REMOVE_ABSENT = i18n._(
    "The extension %(name)s is not configured for this scope — nothing was removed.")
#: THE SENTENCES OF THE REMOVAL DIALOG. They are sentence records: the msgid carries ``{name}``-style
#: holes, filled where the page renders (``templating.SENTENCE_GLOBAL``), so a per-request name never
#: goes through the catalog. The discarded block is printed VERBATIM: operator data is never
#: translated, and it is escaped before it rides the record's markup (see
#: :func:`_extension_remove_dialog`).
NODE_PAGE_EXTENSION_REMOVE_TITLE = i18n._("Remove the block")
NODE_PAGE_EXTENSION_REMOVE_PAGE_TITLE = i18n._("Remove the {name} extension from {node}")
NODE_PAGE_EXTENSION_REMOVE_HEADING = i18n._("Remove the {name} extension from {scope}?")
NODE_PAGE_EXTENSION_REMOVE_DETAIL = i18n._(
    "The block is removed from nodes.yaml for {scope}. The bot log records the removal and your name.")
NODE_PAGE_EXTENSION_DISCARD_WARNING = i18n._(
    "Everything this block holds is discarded — no value in it is kept.")
NODE_PAGE_EXTENSION_REMOVE_BLOCK = i18n._(
    "The block, exactly as it is discarded: <pre class=\"st-warn-yaml\">{yaml}</pre>")
#: the sentence the ONE "+ Add" tile shows when the selected scope already configures EVERY extension
#: its schema offers. The tile is STILL drawn — a control that vanishes silently is worse than one
#: that says why — in an explicit, NON-INTERACTIVE "all configured" state
#: with this reason as VISIBLE text, never only a tooltip.
NODE_PAGE_EXTENSION_ALL_CONFIGURED = i18n._(
    "Every extension of this scope is configured already — there is nothing left to add.")
#: the sentence a switch's save shows when the posted extension name is not one the selected scope's
#: schema declares (a forged or stale name). Refused — nothing is written.
SAVE_REFUSED_UNKNOWN_EXTENSION = i18n._(
    "No change was saved: this page did not name an extension of the tab you were editing, so there "
    "is nothing to write. Reload the page and try again — nothing was written.")
#: the sentence a REMOVAL shows when it does not carry a valid acknowledgement for exactly the block
#: it names. The confirmation dialog mints that acknowledgement; a bare POST (one that never opened
#: the dialog, one that raced it, or one minted for a DIFFERENT block) is refused here with nothing
#: written. This is the server-side half of "never a silent write".
SAVE_REFUSED_EXTENSION_ACK = i18n._(
    "Nothing was removed: this removal did not carry the acknowledgement the confirmation page "
    "produces, so the extension's block was left exactly as it was. Open the extension's trashcan, "
    "read what will be discarded, and confirm from there.")


#: the session key the minted acknowledgements live under. The store is a plain ``{binding: token}``
#: mapping kept in the SESSION (server-side, the same cookie that carries the CSRF token), so a token
#: is never forgeable from the page and never leaves the server except as an opaque value in the
#: confirm form. Keying by the BINDING (not by the token) means a warning for one block can never be
#: used to remove another: the lookup is by (node, scope, extension), and a token minted for a
#: different block simply is not found under this key.
_EXTENSION_ACK_KEY = "_ext_ack"


def _extension_ack_binding(doc_key: str, scope_value: str, ext_name: str) -> str:
    """The binding string an acknowledgement is keyed by: the (node, scope, extension) it warned about.

    ``scope_value`` is the same carrier the form posts (:data:`NODE_PAGE_SCOPE_DEFAULT` or
    ``instance:<name>``), so the binding the warning MINTS and the binding the save LOOKS UP are the
    one representation — they cannot drift.

    LENGTH-PREFIXED, never a bare join. A plain ``"\\x1f".join`` is
    AMBIGUOUS: a part can itself CONTAIN the separator, so ``("A\\x1finstance:B", "default", "E")``
    and ``("A", "instance:B\\x1fdefault", "E")`` would encode the SAME string and an acknowledgement
    minted for one block could be spent on the other's. Each part is therefore written as
    ``<length>:<part>`` — the length tells the reader exactly how many characters belong to the part
    and the separator that follows it is unambiguous — so two different tuples can never encode the
    same binding.
    """
    parts = (readmodels.text(doc_key), readmodels.text(scope_value), readmodels.text(ext_name))
    return "\x1f".join(f"{len(part)}:{part}" for part in parts)


def mint_extension_ack(request: Request, doc_key: str, scope_value: str, ext_name: str,
                       revision: str = "") -> str:
    """Mint (and remember in the session) the acknowledgement for ONE block, and return it.

    Called by the warning RENDER — the GET that shows the block's own contents — so the token exists
    only as a consequence of that warning being displayed. The token is STAMPED with *revision* (the
    file's revision the warning displayed, the same sha256 a save presents as ``expect_revision``), so
    it can only ever authorise a removal of the very contents it was displayed for. A second warning
    for the same block REPLACES the token (the earlier one becomes stale and is refused), which is
    correct: the latest warning is the one whose contents the operator acknowledged.
    """
    token = secrets.token_urlsafe(32)
    store = request.session.get(_EXTENSION_ACK_KEY)
    if not isinstance(store, dict):
        store = {}
    store[_extension_ack_binding(doc_key, scope_value, ext_name)] = {
        "token": token, "revision": readmodels.text(revision)}
    request.session[_EXTENSION_ACK_KEY] = store
    return token


def validate_extension_ack(request: Request, doc_key: str, scope_value: str, ext_name: str,
                           token: str | None, revision: str | None = None) -> bool:
    """Whether *token* is the acknowledgement the session minted for EXACTLY this block.

    False for an absent/blank token, for one that was never minted (or was replaced by a newer
    warning — stale), for one minted for a different (node, scope, extension), and for one minted
    under a DIFFERENT revision than *revision* (the file's revision NOW). The comparison is
    constant-time, like the CSRF check.

    THE REVISION BINDING is what makes the token authorise a removal of EXACTLY the contents the
    warning DISPLAYED: a token minted before the file changed cannot authorise a write after it, even
    when the operator opened the CURRENT page — whose ``_revision`` legitimately matches the new file,
    so the revision guard alone cannot help. An absent token cannot bind to it.
    """
    if not token or not isinstance(token, str):
        return False
    store = request.session.get(_EXTENSION_ACK_KEY)
    if not isinstance(store, dict):
        return False
    entry = store.get(_extension_ack_binding(doc_key, scope_value, ext_name))
    if not isinstance(entry, dict):
        return False
    expected = entry.get("token")
    minted_revision = entry.get("revision")
    if not isinstance(expected, str) or not isinstance(minted_revision, str):
        return False
    if revision is None or not secrets.compare_digest(readmodels.text(revision), minted_revision):
        return False
    return secrets.compare_digest(token, expected)


def spend_extension_ack(request: Request, doc_key: str, scope_value: str, ext_name: str) -> None:
    """Consume the acknowledgement for ONE block — DELETE its store entry (a ONE-TIME acknowledgement).

    Called once a removal has been accepted, so a SPENT token can never authorise a second write: the
    replay the review proved (spend once, then re-post the same token against a fresh page whose
    revision legitimately matches the re-created file) now finds no entry and is refused. The revision
    binding in :func:`validate_extension_ack` closes the same hole for a token that was minted but
    never spent; together they make an acknowledgement authorise exactly ONE removal of exactly the
    contents it was displayed for.
    """
    store = request.session.get(_EXTENSION_ACK_KEY)
    if not isinstance(store, dict):
        return
    binding = _extension_ack_binding(doc_key, scope_value, ext_name)
    if binding in store:
        del store[binding]
        request.session[_EXTENSION_ACK_KEY] = store


NODE_EXTENSION_UNCONFIGURED = i18n._(
    "This extension is not configured for this scope yet, so there is nothing to switch here. "
    "Configure it on the extension's own page first.")
#: the state a scope WITHOUT a block renders: its schema form, with this sentence over it, because the
#: save CREATES the block there (the same scope guard, the same whole-document patch, the same
#: validation and ``.bak`` as any other save).
NODE_EXTENSION_CREATE_NOTE = i18n._(
    "This scope does not configure this extension yet. Fill in what it needs and save — the save "
    "creates the block here.")
#: the refusal a CREATING page save shows: it names the field the operator must fix, instead of the
#: validator's own line. Nothing was written — the whole-document patch aborts before the write.
NODE_EXTENSION_CREATE_REJECTED = i18n._(
    "The block was not created: %(fields)s did not pass the check. Nothing was written.")
#: the sentence over an extension page's tabs. Unlike a plugin/service file there is no shared
#: ``DEFAULT`` block: each tab IS one scope's block of ``nodes.yaml``.
NODE_EXTENSION_SCOPE_NOTE = i18n._(
    "Each tab is this extension's block for one scope — the node's own block, then one per instance "
    "of that node. Nothing is copied between them.")


def node_page_url(node_name: str) -> str:
    """The URL of *node_name*'s node page — the name as ONE percent-encoded path segment."""
    from urllib.parse import quote

    return mount.url(f"/nodes/{quote(readmodels.text(node_name), safe='')}")


# --------------------------------------------------------- the extension CONFIGURATION destination
# THE COGWHEEL'S TARGET. A plugin's cogwheel opens the plugin's OWN configuration in the tabbed,
# schema-driven scoped view (``settings_scope.html``); an extension's cogwheel opens EXACTLY the same
# view, over the extension's blocks of ``nodes.yaml`` for ONE node — its own block plus one tab per
# instance of that node that configures it. There is no second form engine: the SAME
# ``_scope_fields.html`` control macro renders the fields, the SAME one write primitive saves them,
# and the fields are the SAME ``f.<node>.extensions.<Name>…`` names the node page always used.
NODE_EXTENSION_PATH = "/nodes/{node}/extensions/{name}"
NODE_EXTENSION_VALIDATE_PATH = "/actions/nodes/{node}/extensions/{name}/validate"
#: THE COMPLETION HINTS for an extension page's Expert Mode editor: the same ``nodes_schema.yaml``
#: tree, rooted at the DOCUMENT root — that editor's slice is ``{<node>: block}`` as well (the block
#: the page's save merges back is a top-level key of ``nodes.yaml``).
NODE_EXTENSION_HINTS_PATH = "/actions/nodes/{node}/extensions/{name}/hints"
NODE_EXTENSION_TEMPLATE = "settings_scope.html"
#: THE REMOVAL DIALOG's path — the trashcan's target, and the ONLY place a removal's acknowledgement is
#: minted. It renders the console's shared confirmation page (``confirm.html``), which posts the
#: ``remove`` action back to the node page's own save with the token this render bound to that block.
NODE_PAGE_EXTENSION_REMOVE_PATH = "/nodes/{node}/extensions/{name}/remove"


def node_extension_remove_url(node_name: str, ext_name: str, tab: int) -> str:
    """The URL of the confirmation dialog for ONE extension's block on ONE tab — names as segments."""
    from urllib.parse import quote

    return mount.url(f"/nodes/{quote(readmodels.text(node_name), safe='')}"
                    f"/extensions/{quote(readmodels.text(ext_name), safe='')}/remove?tab={tab}")


def node_extension_url(node_name: str, ext_name: str) -> str:
    """The URL of ONE extension's configuration for *node_name* — each name one path segment."""
    from urllib.parse import quote

    return mount.url(f"/nodes/{quote(readmodels.text(node_name), safe='')}"
                    f"/extensions/{quote(readmodels.text(ext_name), safe='')}")


def _extension_scope_url(node_name: str, ext_name: str, instance: str | None) -> str:
    """The URL of ONE extension's page for ONE scope — the "`+ Add`" link's target.

    The scope is named by the console's own carrier vocabulary (``default`` / ``instance:<name>``) rather
    than by a tab index: an index is a POSITION in a list the page builds from the file, so it would
    move the moment a scope is created or removed. The page resolves the name against its own tabs.
    """
    scope_value = (NODE_PAGE_SCOPE_DEFAULT if instance is None
                   else NODE_PAGE_SCOPE_INSTANCE_PREFIX + readmodels.text(instance))
    return f"{node_extension_url(node_name, ext_name)}?scope={quote(scope_value, safe=':')}"


def _node_extension_validate_url(node_name: str, ext_name: str) -> str:
    from urllib.parse import quote

    return mount.url(f"/actions/nodes/{quote(readmodels.text(node_name), safe='')}"
                    f"/extensions/{quote(readmodels.text(ext_name), safe='')}/validate")


def _node_extension_hints_url(node_name: str, ext_name: str) -> str:
    """The URL of ONE extension's completion hints — each name one percent-encoded segment."""
    from urllib.parse import quote

    return mount.url(NODE_EXTENSION_HINTS_PATH.format(
        node=quote(readmodels.text(node_name), safe=""),
        name=quote(readmodels.text(ext_name), safe="")))


def nodes_hints(request: Request) -> JSONResponse:
    """``nodes.yaml``'s schema tree as hints JSON, rooted at the document's own keys.

    The instrument is the ONE payload builder (:mod:`services.webservice.config_hints`); the container
    is the EMPTY one — the file's root — because both node surfaces hand the editor a top-level-key
    slice (``{<node>: block}``), whose root key is the node name the schema's ``regex;(.+)`` names.
    A reader gets no file content here, only the schema: never a write, never a cached body.
    """
    return JSONResponse(config_hints.payload(configgen.schema_files_for(NODES_SCHEMA_PATH),
                                             request=request),
                        headers={"Cache-Control": "no-store"})


def _extension_catalog() -> tuple[dict[str, str], dict[str, str]]:
    """``(descriptions, scopes)`` for every extension that EXISTS, keyed by LOWER-CASED name.

    The SAME source a plugin tile's description comes from — the extension's OWN ``README.md`` first
    prose line (:func:`catalog.discover_extensions`) — so an extension's short description is never
    invented prose; an extension with no README yields ``""`` (the tile then shows no description, the
    honest answer). The scopes map is the two schema blocks' reading (:func:`catalog.extension_scopes`).
    """
    descriptions: dict[str, str] = {}
    scopes_map: dict[str, str] = {}
    for info in catalog.discover_extensions():
        key = info.name.lower()
        descriptions[key] = info.description
        scopes_map[key] = info.scope
    for name, scope in catalog.extension_scopes().items():
        scopes_map.setdefault(name.lower(), scope)
    return descriptions, scopes_map


def _extension_switch(name: str, configured: bool, tab_url: str, csrf: str, revision: str, *,
                      is_default: bool = False, enabled: bool = True,
                      ready: bool = True, add: bool = False,
                      busy=None) -> dict:
    """The ON/OFF switch row model for one extension card — the SAME shape a plugin tile's switch uses.

    A CONFIGURED extension (and a DEFAULT one, which runs with no block at all) is switched by a FORM
    posting the ``disable`` action when it is enabled and the ``enable`` action when it is not. Neither
    write deletes anything — ``enabled: false`` / ``enabled: true`` is written into the selected
    scope's block, the value ``core/extension.py`` reads for EVERY extension — so the switch is
    NON-DESTRUCTIVE and the state word follows *enabled*, the runtime's answer, never whether a block is
    present. REMOVING a block is a different write and has its own door (the trashcan and its
    confirmation dialog); the switch never points at it.

    An UNCONFIGURED non-default extension is OFF: its switch is a FORM posting the ``add`` action
    (writing the extension's block seeded from its own dict's defaults). Every action rides the SAME
    scoped save, so the scope guard, the no-op rule and the byte-identical-elsewhere promise all
    hold — the save route handles the switch and nothing else.

    *add* names that last control by what it DOES: an add WRITES a block and enables nothing, so it
    reads "Add" (label and tooltip) — never "Turn on", which made a working add report itself as a
    broken switch. A configured card's own words are untouched.

    *ready* FALSE is the third state: the scope carries the block but a required value of it is still
    empty, so the switch is drawn in the shared component's NON-ACTIONABLE state (``static`` →
    ``switch_control``'s ``off-fixed`` span: no form, no POST). The locked card's warning names the
    missing keys and the tab holding them, and the cogwheel beside it already opens the door — so the
    lock adds no control and no style.

    *busy* is a RUNNING :class:`~services.webservice.extension_switch.SwitchJob` for this extension: the
    action is installing/preparing it in the background, so the switch is drawn in the SAME component's
    ``busy`` state — no form, no POST, the box pulsing and the elapsed time counting up beside the
    label. It wins over every other state (the job is the newest truth) and it is the only state the
    page polls: ``data-switch-state`` names the endpoint that reports the job, and the page reloads
    itself once the server says nothing is running, which is when the result notice arrives.
    """
    if busy is not None:
        return {"type": "busy",
                "label": (NODE_PAGE_EXTENSION_BUSY_ENABLE
                          if busy.action == NODE_PAGE_EXTENSION_ENABLE
                          else NODE_PAGE_EXTENSION_BUSY_DISABLE),
                "title": NODE_PAGE_EXTENSION_BUSY_TITLE,
                "since": busy.started, "key": busy.state_key,
                "state_url": mount.url(NODE_PAGE_SWITCH_STATE_PATH.replace(
                    "{node}", _url_node(tab_url)))}
    if not ready:
        return {"type": "static", "label": NODE_PAGE_EXTENSION_NEEDS_SETUP}
    common = [(session.CSRF_FIELD, csrf),
              (settings_page.SETTINGS_REVISION_FIELD, revision),
              (NODE_PAGE_SCOPE_FIELD, _switch_scope_from_tab(tab_url)),
              (NODE_PAGE_EXTENSION_FIELD, name)]
    if configured or is_default:
        action = NODE_PAGE_EXTENSION_DISABLE if enabled else NODE_PAGE_EXTENSION_ENABLE
        return {"type": "form", "action": tab_url,
                "fields": common + [(NODE_PAGE_EXTENSION_ACTION_FIELD, action)],
                "label": NODE_PAGE_EXTENSION_ENABLED if enabled else NODE_PAGE_EXTENSION_DISABLED,
                "title": NODE_PAGE_EXTENSION_TURN_OFF if enabled else NODE_PAGE_EXTENSION_TURN_ON,
                "on": bool(enabled)}
    return {"type": "form", "action": tab_url,
            "fields": common + [(NODE_PAGE_EXTENSION_ACTION_FIELD, NODE_PAGE_EXTENSION_ADD)],
            "label": NODE_PAGE_EXTENSION_ADD_LABEL if add else NODE_PAGE_EXTENSION_OFF,
            "title": NODE_PAGE_EXTENSION_ADD_LABEL if add else NODE_PAGE_EXTENSION_TURN_ON,
            "on": False}


def _switch_scope_from_tab(tab_url: str) -> str:
    """The scope carrier a switch built from a ``?tab=<n>`` URL must post (``default`` or the tab's)."""
    # The carrier is the tab's own scope VALUE; the route also reads ``?tab=`` as its fallback, but the
    # form carries it explicitly so a save never has to INFER the scope from the posted fields.
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(tab_url).query)
    raw = (query.get("tab") or [""])[0]
    try:
        index = int(raw)
    except (TypeError, ValueError):
        return NODE_PAGE_SCOPE_DEFAULT
    # ``tab=0`` is the node's own DEFAULT block; a positive index is an instance. The instance NAME is
    # not known from the URL alone, so the route's ``?tab=`` fallback resolves it — the carrier is left
    # empty for a non-default tab (the route reads ``?tab=`` when the carrier is absent).
    return NODE_PAGE_SCOPE_DEFAULT if index <= 0 else ""


def _node_page_back_url(request: Request, node_name: str) -> str:
    """Where a node-page save returns to: the SAME tab the form was posted from.

    A save answers with a redirect, and the form posts to the tab's own URL (``?tab=<n>``), so the tab
    index is on the request. Dropping it sent every save back to the Node-wide tab — visible now that a
    switch's in-flight state lives on the instance tab it was pressed on, and it also threw an operator
    out of the tab they were editing.
    """
    url = node_page_url(node_name)
    raw = readmodels.text(request.query_params.get("tab"))
    if raw.isdigit() and int(raw) > 0:
        return f"{url}?tab={int(raw)}"
    return url


def _url_node(tab_url: str) -> str:
    """``tab_url``'s own node name, quoted as ONE path step (what the poll endpoint's URL needs)."""
    from urllib.parse import quote, urlparse

    parts = [part for part in urlparse(tab_url).path.split("/") if part]
    if "nodes" not in parts or parts.index("nodes") + 1 >= len(parts):
        return ""
    return quote(readmodels.text(parts[parts.index("nodes") + 1]), safe="")


def _node_page_validate_url(node_name: str) -> str:
    """The URL of *node_name*'s Expert Mode validator — one percent-encoded path segment."""
    from urllib.parse import quote

    return mount.url(f"/actions/nodes/{quote(readmodels.text(node_name), safe='')}/validate")


def _node_page_hints_url(node_name: str) -> str:
    """The URL of *node_name*'s completion hints — one percent-encoded path segment."""
    from urllib.parse import quote

    return mount.url(NODE_PAGE_HINTS_PATH.format(
        node=quote(readmodels.text(node_name), safe="")))


def _rebase_instance(field: configgen.Field, node_name: str, instance: str) -> configgen.Field:
    """A copy of *field* with the instance placeholder (``('*')`` after ``instances``) made concrete.

    The per-node tree's instance element sits at ``(<node>, "instances", "*", …)``; this replaces the
    ``"*"`` token with the instance's own key so the control names (``f.<node>.instances.<inst>.…``)
    and the document paths point at exactly that instance's block. A deeper ``"*"`` (a nested
    sequence/regex element) is left alone: only the instance placeholder is concrete.
    """
    if field.path[:3] == (node_name, "instances", "*"):
        path = (node_name, "instances", instance) + field.path[3:]
    else:
        path = field.path
    return replace(
        field,
        path=path,
        children=tuple(_rebase_instance(child, node_name, instance) for child in field.children),
        element=(_rebase_instance(field.element, node_name, instance)
                 if field.element is not None else None),
    )


def _instances_element(element: configgen.Field, node_name: str) -> configgen.Field | None:
    """The instance ELEMENT of a node's tree — the ``instances`` regex's element, or ``None``."""
    container = next((child for child in element.children if child.key == "instances"), None)
    if container is None or not container.children:
        return None
    return container.children[0].element


def _rebase_prefix(field: configgen.Field, old: tuple, new: tuple) -> configgen.Field:
    """A copy of *field* with the path prefix *old* replaced by *new* — deeper paths left alone.

    Used to lift an INSTANCE-scoped extension's fields onto the NODE-wide path: an instance
    extension sits at ``(<node>, "instances", "*", "extensions", <Name>)`` and the node-wide scope
    needs the SAME controls at ``(<node>, "extensions", <Name>)``. Only the exact prefix is rewritten
    (the ``*`` instance placeholder disappears with it), so every descendant path follows its parent.
    """
    if field.path[:len(old)] == old:
        path = new + field.path[len(old):]
    else:
        path = field.path
    return replace(
        field,
        path=path,
        children=tuple(_rebase_prefix(child, old, new) for child in field.children),
        element=(_rebase_prefix(field.element, old, new) if field.element is not None else None),
    )


def _node_wide_extensions(element: configgen.Field, node_name: str) -> configgen.Field | None:
    """The NODE-WIDE ``extensions`` container — the node schema's names PLUS the instance schema's.

    the operator's decision: the node-wide scope must also OFFER the instance-scoped
    extension names (``LogAnalyser`` and friends), so an operator can set them once for every instance
    of the node instead of instance by instance. The runtime honours such a block: ``load_extension``
    (``core/data/impl/serverimpl.py:786-793``) resolves an extension's config as the node-level block
    merged UNDER the instance-level one, for ANY extension name — the node block is a per-node default
    an instance overrides (documented in ``extensions/README.md:105-111``).

    The container is the node schema's ``extensions`` child with the instance schema's EXTRA names
    appended, their fields rebased from the instance path to the node-wide path. A name in BOTH
    schemas keeps the NODE partial (its own node-level shape); an instance-only name gets the instance
    partial's fields, addressed at the node-wide path — exactly what the write puts on disk. The
    returned Field is the node container's own shape, so every consumer (sections, options, the save
    tree and the switch allow-list) reads ONE container and cannot drift.
    """
    node_container = next((child for child in element.children if child.key == "extensions"), None)
    if node_container is None:
        return None
    inst_element = _instances_element(element, node_name)
    inst_container = next((child for child in (inst_element.children if inst_element else [])
                           if child.key == "extensions"), None)
    if inst_container is None:
        return node_container
    have = {str(child.key) for child in node_container.children}
    extra = tuple(
        _rebase_prefix(child, (node_name, "instances", "*", "extensions"), (node_name, "extensions"))
        for child in inst_container.children if str(child.key) not in have)
    if not extra:
        return _with_default_extensions(node_container)
    return _with_default_extensions(
        replace(node_container, children=tuple(node_container.children) + extra))


def _with_default_extensions(container: configgen.Field | None) -> configgen.Field | None:
    """*container* with the bot's DEFAULT extensions appended as empty fields — or *container* unchanged.

    THE RUNTIME SET, not the schema's. ``DEFAULT_EXTENSIONS`` names extensions that RUN even when no
    schema declares them and ``nodes.yaml`` configures nothing (``Cloud`` has no schema at all), so a
    scope's card set must include them or the page would hide a running extension. A name the schema
    already declares keeps the schema's shape; only a MISSING default name gets a bare, childless map
    field, so it renders as a card (ON by default, switchable) rather than as an "add" choice — the
    very mistake this fixes. Idempotent: a name already present is left exactly as it is.
    """
    if container is None:
        return None
    names = catalog.default_extensions()
    if not names:
        return container
    have = {str(child.key).lower() for child in container.children}
    extra = tuple(
        configgen.Field(key=spelling, kind=configgen.KIND_MAP, path=container.path + (spelling,),
                        children=(), kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])
        for key, spelling in sorted(names.items()) if key not in have)
    if not extra:
        return container
    return replace(container, children=tuple(container.children) + extra)


def _instance_extensions(element: configgen.Field, node_name: str,
                         instance: str | None) -> configgen.Field | None:
    """A node's INSTANCE-scoped ``extensions`` container, with the default extensions appended.

    ``instance`` is ``None`` for the schema's placeholder path (only the container's KEYS are read — the
    switch's allow-list); a concrete name rebases the paths so the fields address that instance's block
    (the save tree). Either way the container carries the DEFAULT extension names too, so a default
    extension is switchable on an instance tab exactly as on the node-wide tab.
    """
    inst_element = _instances_element(element, node_name)
    if inst_element is None:
        return None
    if instance is not None:
        inst_element = _rebase_instance(inst_element, node_name, instance)
    container = next((child for child in inst_element.children if child.key == "extensions"), None)
    return _with_default_extensions(container)


def _document_instances(document, key: str | None) -> list[str]:
    """The instance names a node's block declares in *document* (a reading, never a guess)."""
    if document is None or key is None or not isinstance(document, dict):
        return []
    block = document.get(key)
    if not isinstance(block, dict):
        return []
    instances = block.get("instances")
    if not isinstance(instances, dict):
        return []
    return [str(name) for name in instances]


def _extension_sections(container: configgen.Field | None, base: tuple,
                        configured) -> list[dict]:
    """One section per extension a scope OFFERS: its name, its child fields, its path base, whether the
    scope already CONFIGURES it, and whether the section is therefore editable.

    The sections come from the SCHEMA (every extension the scope's schema declares, even one
    ``nodes.yaml`` does not configure yet — so nothing became unreachable when the EXTENSIONS rail left
    Settings). ``configured`` is the set of extension names the scope's block carries in the file: a
    section the scope does not configure is shown LOCKED (its controls disabled and not posted) because
    the form never CREATES an extension block.
    """
    if container is None:
        return []
    return [{"name": str(ext.key), "fields": list(ext.children), "base": base + (str(ext.key),),
             "configured": str(ext.key) in configured,
             "locked_note": NODE_PAGE_EXTENSION_UNCONFIGURED}
            for ext in container.children]


def _configured_extensions(document, doc_key: str | None, instance: str | None) -> frozenset:
    """The extension names the selected scope's block CONFIGURES in *document* — a reading, never a guess.

    ``instance`` is ``None`` for the node's own (DEFAULT) block, else the instance's block. An absent
    block, or one without an ``extensions`` mapping, configures nothing.
    """
    if not isinstance(document, dict) or doc_key is None:
        return frozenset()
    block = document.get(doc_key)
    if not isinstance(block, dict):
        return frozenset()
    if instance is None:
        container = block.get("extensions")
    else:
        instances = block.get("instances")
        inst_block = instances.get(instance) if isinstance(instances, dict) else None
        container = inst_block.get("extensions") if isinstance(inst_block, dict) else None
    if not isinstance(container, dict):
        return frozenset()
    return frozenset(str(name) for name in container)


def _block_enabled(document, base: tuple) -> bool | None:
    """The ``enabled`` value of the block at *base*, or ``None`` when it is absent or unstated.

    A block that is not a mapping (a scalar or a list at the slot), or one that carries no ``enabled``
    key, answers ``None`` — the honest "not stated", distinct from an explicit ``False``. The runtime
    reads exactly this key (``core/extension.py``: ``enabled`` = ``config.get('enabled', True)``).
    """
    block = configgen.value_at(document, base)
    if isinstance(block, dict) and "enabled" in block:
        return bool(block["enabled"])
    return None


def _extension_enabled(is_default: bool, configured: bool, block_enabled: bool | None,
                       inherited_enabled: bool | None) -> bool:
    """The RUNTIME state of an extension in the selected scope — what the loader would answer.

    Present in this scope AND not effectively disabled. "Present" is *configured* for a non-default
    extension and always true for a DEFAULT one (it runs with no block at all). The effective flag is
    the block's own ``enabled`` when it states one (``False`` IS a value — that is why
    :func:`_block_enabled` answers ``None`` for "unstated"); otherwise the INHERITED value: on an
    INSTANCE tab the node block's answer (``load_extension`` merges the node block UNDER the instance
    one — ``core/data/impl/serverimpl.py``), and ``None`` on the node-wide tab, which has no parent.
    """
    present = bool(configured or is_default)
    effective = block_enabled if block_enabled is not None else inherited_enabled
    if effective is False:
        return False
    return present


def _restrict_extensions(field: configgen.Field | None, configured) -> configgen.Field | None:
    """A copy of an ``extensions`` map Field carrying only the *configured* names — or ``None`` when
    none is configured (the container is then dropped from the tree, so an extension field is
    unplaceable and the save REFUSES rather than creating the block)."""
    if field is None:
        return None
    kept = tuple(child for child in field.children if str(child.key) in configured)
    if not kept:
        return None
    return replace(field, children=kept)


def _scope_extension_names(doc_key: str, instance: str | None) -> frozenset:
    """The extension names the SELECTED scope's schema declares — the card SET, from the schema.

    ``instance`` is ``None`` for the node's own (DEFAULT) block, else the instance's. Read from the
    SAME node-schema tree the page renders from (:func:`node_tree` / :func:`_instances_element`), so the
    set a switch may name and the set of cards the page shows can never drift apart. This is the
    allow-list a switch's posted ``_ext`` is checked against: a name outside it is refused.
    """
    element = node_tree(doc_key).children[0]
    if instance is None:
        # THE NODE-WIDE ALLOW-LIST IS THE UNION: a switch may name an
        # instance-scoped extension on the node-wide tab, exactly the set the tab OFFERS.
        container = _node_wide_extensions(element, doc_key)
    else:
        container = _instance_extensions(element, doc_key, None)
    if container is None:
        return frozenset()
    return frozenset(str(child.key) for child in container.children)


def _extension_base(doc_key: str, instance: str | None, ext_name: str) -> tuple:
    """The document path of ONE extension's block in the selected scope (node or instance)."""
    if instance is None:
        return (doc_key, "extensions", ext_name)
    return (doc_key, "instances", instance, "extensions", ext_name)


#: A sentinel that distinguishes "this key is absent" from "this key holds ``None``". A block slot
#: holding an explicit ``None`` (or any non-mapping) is NOT absent, so the add below must refuse it
#: rather than quietly replace it.
_ABSENT = object()


# ------------------------------------------------------------------ READINESS: a block is not readiness
# THE RUNTIME IS TOLD WHAT AN EXTENSION NEEDS IN TWO PLACES THAT DISAGREE. Its own ``NODE_CONFIG_DICT``
# / ``CONFIG_DICT`` marks each field ``required: True`` — exactly what the Discord command's modal asks
# for before it enables — while its schema marks some of the same keys ``required: true`` and MANY ARE
# MISSING IT (an SRS node block with no ``installation`` validates CLEAN, while a GitHub block without
# ``repo``/``target`` is refused). So the extension's OWN dict is the authority, and the schema is read
# only for a scope the extension declares no dict for. ``enabled`` is not a required key anywhere.
#: ``(lower-cased name, scope) → that scope's declared dict`` — ``None`` when the extension's class
#: could not be resolved (an import failure, or a module that is not installed here). MEMOISED per
#: process: a page must not import every extension module per request, and the answer cannot change
#: while the process runs.
_EXTENSION_DICTS: dict[tuple[str, str], dict | None] = {}
#: the DICT attribute each scope reads. A key of the first is filled on the Node-wide tab, a key of the
#: second on the extension's own instance tab.
_EXTENSION_SCOPE_DICTS = {NODE_PAGE_DICT_SCOPE_NODE: "NODE_CONFIG_DICT",
                          NODE_PAGE_DICT_SCOPE_INSTANCE: "CONFIG_DICT"}


def _extension_config_dict(name: str, scope: str) -> dict | None:
    """The extension's OWN config dict for *scope* — ``NODE_CONFIG_DICT`` or ``CONFIG_DICT``.

    ``None`` means the class could not be resolved, or it declares no dict for that scope; an EMPTY
    dict is a declaration too (``Extension.CONFIG_DICT = {}`` — the scope needs nothing). The lookup is
    wrapped so a failing import can never raise into a render, and MEMOISED, so a page resolves each
    extension once per process rather than once per card.
    """
    cache_key = (readmodels.text(name).lower(), scope)
    if cache_key in _EXTENSION_DICTS:
        return _EXTENSION_DICTS[cache_key]
    found: dict | None = None
    try:
        from core.utils import str_to_class

        cls = str_to_class(f"extensions.{cache_key[0]}.extension.{name}")
        declared = getattr(cls, _EXTENSION_SCOPE_DICTS.get(scope, ""), None) if cls else None
        if isinstance(declared, dict):
            found = dict(declared)
    except Exception:  # noqa: BLE001 - an unresolvable extension is judged by its schema instead
        found = None
    _EXTENSION_DICTS[cache_key] = found
    return found


def _extension_required_keys(name: str, scope: str, fields=()) -> tuple[tuple[str, str], ...]:
    """The ``(key, tab)`` pairs *scope* requires a VALUE for — the extension's own dict first.

    ``tab`` is the scope token the value is filled in (:data:`NODE_PAGE_DICT_SCOPE_NODE` for a key of
    ``NODE_CONFIG_DICT``, :data:`NODE_PAGE_DICT_SCOPE_INSTANCE` for one of ``CONFIG_DICT``), which the
    card's warning turns into the tab's own label. The schema's ``required: true`` is consulted ONLY
    when the extension declares no dict for that scope, and *fields* is then that scope's schema
    controls (already resolved by the caller) — an extension the schema does not name needs nothing.
    """
    declared = _extension_config_dict(name, scope)
    if declared is not None:
        return tuple((str(key), scope) for key, spec in declared.items()
                     if isinstance(spec, dict) and spec.get("required")
                     and str(key) != "enabled")
    return tuple((str(field.key), scope) for field in fields or ()
                 if getattr(field, "required", False) and str(field.key) != "enabled")


def _extension_defaults(name: str, scope: str) -> dict:
    """The DECLARED ``default`` values of the extension's dict for *scope* — nothing else.

    These are what an ADD seeds a new block with, so one click yields a card whose values are editable
    in place: never an invented value, and never a schema ``example`` (an illustration, not a default).
    A key whose spec declares no ``default`` is absent from the answer, which is what makes an
    extension with a defaultless required key (GitHub's ``repo``) unaddable from the card.
    """
    declared = _extension_config_dict(name, scope) or {}
    return {str(key): deepcopy(spec["default"]) for key, spec in declared.items()
            if isinstance(spec, dict) and "default" in spec}


# ------------------------------------------------ THE ADD'S OWN REQUIREMENT PLAN
# THE PLAN DECIDES WHAT THE "+ Add" ROW IS. A new block is seeded from the extension's own dict defaults;
# whatever that seed cannot satisfy decides between the two controls:
#
# * nothing unsatisfied → the row is a one-click WRITE of the seed;
# * anything unsatisfied → the row is a LINK to the extension's own page, where the fields have room and
#   the save creates the block. A requirement is a required key nothing declares a default for (GitHub's
#   ``repo``), a required ``regex;(a|b)`` SECTION block (Olympus's instance port blocks, which pykwalify
#   answers with ``Cannot find required key 'regex;(backend|server)'`` and which a flat required-key walk
#   cannot see), and a declared default the schema's OWN ``func`` rejects on this machine (Olympus's
#   ``nodejs`` where NodeJS is not installed).
#: the leaf kinds a requirement can be — the kinds the ONE ``control`` macro renders as an input. A map /
#: seq / regex / chooser is either a fixed-alternative SECTION (always a requirement when required) or
#: not something a single control could ask for: the plan collects keys, never a new control type.
_ADD_ASKABLE_KINDS = frozenset({configgen.KIND_BOOL, configgen.KIND_NUMBER, configgen.KIND_TEXT,
                                configgen.KIND_MULTILINE, configgen.KIND_SELECT, configgen.KIND_LIST,
                                configgen.KIND_FUNC})


def _validator_rule(name: str):
    """The bot's OWN validator named *name* (``core/utils/validators.py``), or ``None``.

    The schema's ``func:`` names a function THAT module exports, so a default is judged by calling
    the real rule — never by a copy of it. A name this page cannot drive (a layout ``func``, an
    unknown string) answers ``None``, and the default is then taken at its word.
    """
    from core.utils import validators

    found = getattr(validators, name, None)
    return found if callable(found) else None


def _default_accepted(field: configgen.Field, value) -> bool:
    """Whether the schema's OWN ``func`` ACCEPTS a declared ``default`` — the seedability test.

    A default is only seedable when the extension will not start on a value the schema refuses, so a
    ``func: dir_exists`` default on a machine without that directory is ASKED FOR instead of written.
    The rule is the bot's own (its ``{...}`` replacement exemption included), called with the path this
    block would occupy — which is what tells the validator whether the path is checkable at all. The
    ``ports`` memo a port rule books is restored afterwards: judging a default is not a save.
    """
    rule = _validator_rule(str(field.func or ""))
    if rule is None:
        return True
    from core.utils import validators

    path = "/" + "/".join(str(token) for token in field.path)
    booked = dict(validators.ports)
    try:
        answer = rule(value, None, path)
    except Exception:  # noqa: BLE001 - a rule this page cannot drive never condemns a default
        return True
    finally:
        validators.ports.clear()
        validators.ports.update(booked)
    return not isinstance(answer, str)


def _extension_add_plan(ext_name: str, fields, scope: str) -> tuple[configgen.Field, ...]:
    """What an ADD of *ext_name* in *scope* would have to ASK FOR — empty when one click can write it.

    *fields* is the scope's schema controls for the extension (:func:`_tab_extension_fields`), i.e. the
    SAME tree the extension's own configuration page renders. A requirement is anything the block an
    add SEEDS cannot satisfy:

    * the extension's own dict WINS for a key it declares — a required key WITH a declared ``default``
      is seeded and is no requirement, one without a default is;
    * a required LEAF no dict declares a default for is a requirement;
    * a declared ``default`` the schema's own ``func`` REJECTS is not seedable either, so it is one;
    * a required FIXED-ALTERNATIVE container is one, which is the requirement pykwalify reports as a
      missing ``regex;`` key (``Cannot find required key 'regex;(backend|server)'``).

    ``enabled`` is never a requirement: it is not a value the schema requires of a new block.

    THIS IS ALSO THE ADD/LINK SPLIT. An EMPTY plan means the seeded block is what the schema's own
    required checks demand, so the ``+ Add`` row is a one-click write
    (:func:`_add_extension_block`); a non-empty one means no click can produce a valid block, so the
    row is a LINK to the extension's own page, where the fields have room and the save creates the
    block. The split is decided by the SCHEMA's requirements, never by an extension's name.
    """
    declared = _extension_config_dict(ext_name, scope) or {}
    plan: list[configgen.Field] = []
    for field in fields:
        key = str(field.key)
        raw = declared.get(key)
        spec: dict = raw if isinstance(raw, dict) else {}
        if field.kind == configgen.KIND_REGEX:
            if field.section_required and "default" not in spec:
                plan.append(field)
            continue
        if key == "enabled" or field.kind not in _ADD_ASKABLE_KINDS:
            continue
        if not (spec.get("required") if spec else field.required):
            continue
        if "default" not in spec or not _default_accepted(field, spec["default"]):
            plan.append(field)
    return tuple(plan)


def _is_empty(value) -> bool:
    """Whether a required key's value is EMPTY: absent, ``None``, or a blank (whitespace-only) string.

    ``False``, ``0`` and an empty list are VALUES — a declared bool default is a decision the operator
    made, not a hole, and treating it as missing would lock every extension whose required key is a
    switch.
    """
    if value is _ABSENT or value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _extension_missing_keys(name: str, fields=(), *, node_block=None, instance_block=None,
                            node_scope: bool = True) -> list[tuple[str, str]]:
    """The required keys still EMPTY for one extension in the selected scope, each with its tab.

    The value a key carries is the EFFECTIVE one: the instance block merged OVER the node block, the
    order ``ServerImpl.load_extension`` resolves an extension's config in — so a node-level default
    satisfies a required key and an instance-level value wins over it.

    *node_scope* FALSE skips the node-scope lookup: the node-scope fallback judges by the node
    schema's controls, and an extension the two schema blocks declare INSTANCE-only has no node
    controls of its own (its keys are filled on the extension's own tab, which is where they must be
    reported).
    """
    effective: dict = {}
    for block in (node_block, instance_block):
        if isinstance(block, dict):
            effective.update(block)
    missing: list[tuple[str, str]] = []
    for scope, applies in ((NODE_PAGE_DICT_SCOPE_NODE, node_scope),
                           (NODE_PAGE_DICT_SCOPE_INSTANCE, True)):
        if not applies:
            continue
        for key, tab in _extension_required_keys(name, scope, fields):
            if _is_empty(effective.get(key, _ABSENT)):
                missing.append((key, tab))
    return missing


def _missing_keys_sentence(request: Request, missing) -> str:
    """The card's ONE warning line: the empty required keys, grouped by the tab that holds them.

    Translated AND interpolated here, like the switch's notices: the row model carries the finished
    sentence, so the template's ``_(…)`` on it is a lookup miss that leaves it exactly as written.
    """
    grouped = {NODE_PAGE_DICT_SCOPE_NODE: [], NODE_PAGE_DICT_SCOPE_INSTANCE: []}
    for key, tab in missing:
        grouped[tab].append(key)
    parts = []
    if grouped[NODE_PAGE_DICT_SCOPE_NODE]:
        parts.append(i18n.translate(request, NODE_PAGE_EXTENSION_KEY_ON_NODE_TAB,
                                    keys=", ".join(grouped[NODE_PAGE_DICT_SCOPE_NODE])))
    if grouped[NODE_PAGE_DICT_SCOPE_INSTANCE]:
        parts.append(i18n.translate(request, NODE_PAGE_EXTENSION_KEY_ON_INSTANCE_TAB,
                                    keys=", ".join(grouped[NODE_PAGE_DICT_SCOPE_INSTANCE])))
    return i18n.translate(request, NODE_PAGE_EXTENSION_INCOMPLETE, keys="; ".join(parts))


def _declares_node_scope(ext_name: str) -> bool:
    """Whether the two schema blocks place the extension on the NODE (or on both scopes).

    The node-scope readiness FALLBACK judges by the node schema's controls, and an extension the
    schemas declare INSTANCE-only has none of its own there — its keys are filled on the extension's
    own tab, which is where a missing one must be reported.
    """
    _, scopes = _extension_catalog()
    return scopes.get(readmodels.text(ext_name).lower()) in (catalog.SCOPE_NODE, catalog.SCOPE_BOTH)


def _tab_extension_fields(doc_key: str, instance: str | None, ext_name: str) -> tuple:
    """One extension's schema controls in the SELECTED scope — the fallback readiness judges by them.

    Only reached when the extension's class could not be resolved (see
    :func:`_extension_required_keys`); a name the scope's schema does not place yields ``()``, and the
    write's own schema validation still decides in that case.
    """
    element = node_tree(doc_key).children[0]
    container = (_node_wide_extensions(element, doc_key) if instance is None
                 else _instance_extensions(element, doc_key, instance))
    if container is None:
        return ()
    child = next((candidate for candidate in container.children
                  if str(candidate.key) == ext_name), None)
    return tuple(child.children) if child is not None else ()


def _extension_config_pair(document, doc_key: str, instance: str, ext_name: str) -> dict:
    """The ``{"Node": …, "Instance": …}`` pair for ONE extension, read from the FILE.

    The pair is what ``Server.config_extension`` reads and writes, and the enable action needs it
    because the extension's config is merged OVER the process's ``locals`` — which stay as they were
    when this process read them. A block that is absent or not a mapping reads as ``{}``.
    """
    key = _document_node_key(document, doc_key) or doc_key
    node_block = configgen.value_at(document, (key, "extensions", ext_name))
    inst_block = configgen.value_at(document, (key, "instances", instance, "extensions", ext_name))
    return {"Node": dict(node_block) if isinstance(node_block, dict) else {},
            "Instance": dict(inst_block) if isinstance(inst_block, dict) else {}}


def _instance_server_name(request: Request, node_name: str, instance: str) -> str:
    """The NAME of the server running *instance* on *node_name* in this console's own view, or ``""``.

    ``""`` is the honest "no server object here" — a node whose bot is down, or a headless run — which
    is exactly when the live extension action cannot run and the config-write fallback must.
    """
    source = dashboard_page.request_source(request)
    wanted_instance = readmodels.text(instance)
    wanted_node = readmodels.text(node_name)
    for server in getattr(source, "servers", ()) or ():
        name = readmodels.text(readmodels.safe(lambda: server.name))
        if not name:
            continue
        if (readmodels.text(readmodels.safe(lambda: server.instance.name)) == wanted_instance
                and readmodels.text(readmodels.safe(lambda: server.node.name)) == wanted_node):
            return name
    return ""


def _add_extension_block(document, base: tuple, defaults=None, *, enabled: bool | None = False) -> None:
    """Create the block at *base* in *document*, seeded from *defaults*, making missing intermediates.

    This is the write an ADD and a card's turn-ON perform: the extension's block is created holding the
    ``default`` values its own dict declares for that scope (*defaults*, resolved by the caller), so one
    click yields a card whose values are editable in place. Only DECLARED defaults are written — never an
    invented value and never a schema ``example``. A block that already exists is left EXACTLY as it is —
    an already-on card's switch is a no-op, never a replacement (which would discard its values).

    THE CREATED BLOCK IS SWITCHED OFF. ``enabled`` is written as the block's own flag, FALSE by default,
    because ``core/extension.py`` reads an ABSENT key as ON: a block created without it would start the
    extension on the node's next start, which is the opposite of what an add means. A switch then writes
    the value it wants. ``enabled=None`` writes NO flag at all — for a scope whose schema does not
    declare the key, where writing it would make the block unvalidatable and the save refuse it.

    SHAPE-CONSERVATIVE. This helper only ever writes into a mapping slot that is ABSENT; a slot that
    already holds a non-mapping (a scalar, a list, an explicit ``None``) is REFUSED here, with
    :class:`core.utils.configwrite.ConfigWriteError`, instead of being overwritten. Relying on the
    schema validator downstream to catch it would make this the one write that is not shape-conservative
    — a value at the block slot would be silently REPLACED first and only then (maybe) rejected. Now the
    refusal is the helper's own, before anything is written, so the whole-document patch sees no change.
    """
    from ruamel.yaml.comments import CommentedMap
    node = document
    for token in base[:-1]:
        child = node.get(token, _ABSENT) if isinstance(node, dict) else _ABSENT
        if child is _ABSENT:
            child = CommentedMap()
            node[token] = child
        elif not isinstance(child, dict):
            raise configwrite.ConfigWriteError(
                "This extension's block was not added: the path to it already holds a value that "
                "is not a mapping, so writing the block there would replace that value.")
        node = child
    last = base[-1]
    existing = node.get(last, _ABSENT) if isinstance(node, dict) else _ABSENT
    if existing is _ABSENT:
        block = CommentedMap(defaults or {})
        if enabled is not None:
            block["enabled"] = bool(enabled)
        node[last] = block
    elif not isinstance(existing, dict):
        raise configwrite.ConfigWriteError(
            "This extension's block was not added: this scope already holds a value there that is "
            "not a mapping, so adding the block would replace it.")


def _remove_extension_block(document, base: tuple) -> None:
    """Delete exactly the block at *base* in *document* — the whole-block removal.

    This is the write the REMOVAL performs, and a removal is reached ONLY through the extension
    card's trashcan and the confirmation dialog behind it (the ``remove`` action plus the
    acknowledgement that dialog minted); no switch posts it. Only the named key is removed; every
    sibling (every other extension, every other scope, every other node) is left untouched, so the
    whole-document patch sees a change to exactly that one key. An absent block is a no-op (an
    already-absent block's removal changes nothing).
    """
    node = document
    for token in base[:-1]:
        node = node.get(token) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return
    last = base[-1]
    if last in node:
        del node[last]


def _set_extension_enabled(document, base: tuple, enabled: bool) -> None:
    """Write ``enabled: *enabled*`` into the block at *base*, creating the block (and intermediates)
    when absent.

    This is the write EVERY switch performs — a configured extension's and a DEFAULT extension's
    alike: ``enabled: false`` turns the extension off for that scope and ``enabled: true`` turns it
    back on, the value the loader reads (``core/extension.py``: ``enabled`` =
    ``config.get('enabled', True)``). An absent block is CREATED (it means "default on", so a
    DEFAULT extension's switch-off has to write the value somewhere); it NEVER removes a block,
    because a removal would silently RE-ENABLE the extension. The block's other keys are left exactly
    as they were (only ``enabled`` is written), and the creation is shape-conservative —
    :func:`_add_extension_block` refuses a non-mapping slot before anything is written.
    """
    _add_extension_block(document, base)
    node = document
    for token in base:
        node = node[token]
    node["enabled"] = bool(enabled)


def scope_save_tree(node_name: str, instance: str | None,
                    node_exts=(), instance_exts=()) -> configgen.Field:
    """The tree a save of ONE scope resolves against — the node's element RESTRICTED to that scope.

    ``instance`` is ``None`` for the node's own DEFAULT scope (its settings and node extensions, with
    NO instance subtree) or an instance name (THAT instance's subtree ONLY). The scope's ``extensions``
    map is restricted to the extensions the scope's block already CONFIGURES, so the form can EDIT an
    extension a scope has but never CREATE one. A posted field naming anything outside the scope — a
    sibling instance, another node, the DEFAULT block from an instance tab (or the reverse), or an
    extension the scope does not configure — therefore does not resolve, and ``configgen.apply_form``
    REFUSES the save (:class:`configgen.UnknownFieldError`) instead of writing it.
    """
    element = node_tree(node_name).children[0]
    if instance is None:
        children = []
        wide = _node_wide_extensions(element, node_name)
        for child in element.children:
            if child.key == "instances":
                continue
            if child.key == "extensions":
                child = _restrict_extensions(wide, node_exts)
                if child is None:
                    continue
            children.append(child)
        node_field = replace(element, children=tuple(children))
    else:
        inst_element = _instances_element(element, node_name)
        if inst_element is None:  # pragma: no cover - the node schema always declares instances
            inst_element = configgen.Field(key="", kind=configgen.KIND_MAP, path=(),
                                           kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])
        inst_element = _rebase_instance(inst_element, node_name, instance)
        children = []
        for child in inst_element.children:
            if child.key == "extensions":
                child = _restrict_extensions(child, instance_exts)
                if child is None:
                    continue
            children.append(child)
        inst_element = replace(inst_element, key=instance, children=tuple(children))
        instances_container = configgen.Field(
            key="instances", kind=configgen.KIND_MAP, path=(node_name, "instances"),
            children=(inst_element,), kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])
        node_field = replace(element, children=(instances_container,))
    return configgen.Field(key="", kind=configgen.KIND_MAP, path=(), children=(node_field,),
                           kind_label=configgen.KIND_LABELS[configgen.KIND_MAP])


def node_page_context(request: Request, node_name: str, tab_index: int = 0) -> dict:
    """Everything ``node_page.html`` reads for ONE node: its tabs, the active tab's settings and
    extensions, and the Expert Mode slice (the node's own part of ``nodes.yaml``).

    The tabs are Node-wide first, then one per instance the node's block DECLARES. The active tab
    renders its own settings (the schema-driven form) and then each of its own extensions, each
    configurable; a ``nodes.yaml`` that does not parse, or a node with no block, renders READ-ONLY
    with the honest reason rather than a broken form.
    """
    path = nodes_yaml_path(request)
    schema_files = configgen.schema_files_for(NODES_SCHEMA_PATH)
    readonly = False
    parse_error = ""
    schema_error = ""
    document = None
    key: str | None = node_name
    try:
        document = configgen.read_document(path)
    except configwrite.ConfigWriteError as ex:
        document = None
        readonly = True
        parse_error = str(ex)
    if document is None and not parse_error:
        readonly = True
        parse_error = i18n._("No nodes.yaml on this install, so this node has no settings to show.")
    elif document is not None:
        found = _document_node_key(document, node_name)
        if found is None:
            readonly = True
            parse_error = i18n._("This node has no block in nodes.yaml yet.")
        else:
            key = found

    tree = node_tree(key or node_name)
    element = tree.children[0]
    instances = _document_instances(document, key)

    # The tab LABELS are composed in the TEMPLATE, not here: both are user-facing msgids ("Node-wide",
    # "Instance %(name)s" — the instance NAME is operator data, printed verbatim). This data carries
    # only the tab's KIND and, for an instance tab, the instance's NAME. The tab ORDER, the ?tab=
    # links and the scope values are unchanged by the rename.
    tab_index = max(0, min(tab_index, len(instances)))
    tabs = [{"name": (None if index == 0 else instances[index - 1]),
             "kind": ("default" if index == 0 else "instance"),
             "active": index == tab_index,
             "url": f"{node_page_url(node_name)}?tab={index}"}
            for index in range(len(instances) + 1)]

    if tab_index == 0:
        instance = None
        scope_base = (key or node_name,)
        settings_fields = [child for child in element.children
                           if child.key not in ("extensions", "instances")]
        # THE NODE-WIDE CARD SET IS THE UNION: the node schema's names PLUS the
        # instance schema's, so an instance-scoped extension (LogAnalyser) can be set once for every
        # instance of the node. An INSTANCE tab keeps its own instance-only set (below).
        ext_container = _node_wide_extensions(element, key or node_name)
        ext_base = (key or node_name, "extensions")
        scope_note = i18n.translate(request, NODE_PAGE_SCOPE_NODE)
        configured = _configured_extensions(document, key, None)
        scope_value = NODE_PAGE_SCOPE_DEFAULT
    else:
        instance = instances[tab_index - 1]
        scope_base = (key or node_name, "instances", instance)
        instance_element = _instances_element(element, key or node_name)
        if instance_element is not None:
            instance_element = _rebase_instance(instance_element, key or node_name, instance)
        settings_fields = [child for child in (instance_element.children if instance_element else [])
                           if child.key != "extensions"]
        ext_container = _instance_extensions(element, key or node_name, instance)
        ext_base = (key or node_name, "instances", instance, "extensions")
        scope_note = i18n.translate(request, NODE_PAGE_SCOPE_INSTANCE, scope=instance)
        configured = _configured_extensions(document, key, instance)
        scope_value = NODE_PAGE_SCOPE_INSTANCE_PREFIX + instance

    settings_view = scopes.scope_view(settings_fields, document)
    settings_displays = {field.name: scopes.display_name(field, scope_base)
                         for field in configgen.flatten(settings_view.root)}
    # THE SHOW/HIDE MODEL for this tab's settings body, plus the removal acknowledgement the render
    # mints (a value-carrying removal must present it — see ``save_node``).
    vis = settings_page.visibility_context(settings_view.root, settings_view.root, document)
    remove_ack = ""
    if vis["removable"] and document is not None:
        remove_ack = settings_page.mint_remove_ack(request, str(path),
                                                   configwrite.config_revision(path))
    csrf = session.get_csrf_token(request)
    revision = configwrite.config_revision(path)
    node_url = node_page_url(node_name)
    tab_url = f"{node_url}?tab={tab_index}"
    descriptions, ext_scopes = _extension_catalog()
    # THE CARD SET IS SPLIT, AND DEFAULT EXTENSIONS ARE NEVER "NOT CONFIGURED". A CONFIGURED extension
    # is a TILE of its own (with its cogwheel and a TRASHCAN into its own confirmation dialog). An
    # extension the scope does NOT configure is a CHOICE inside the ONE "+ Add" tile — UNLESS it is a
    # DEFAULT extension (a name in ``DEFAULT_EXTENSIONS``): that one RUNS regardless of the file, so it
    # is shown as a TILE too, wearing the SAME "default" badge a default plugin tile wears, and
    # switchable through the ``disable``/``enable`` actions (which WRITE ``enabled: false``/``true``,
    # never removing the block — an absent block means "default on"). Listing it as "not configured"
    # would tell the operator the opposite of the truth.
    defaults = catalog.default_extensions()
    extensions = []
    extension_options = []
    for section in _extension_sections(ext_container, ext_base, configured):
        name = section["name"]
        is_default = name.lower() in defaults
        description = descriptions.get(name.lower(), "")
        scope_label = catalog.SCOPE_LABELS.get(
            ext_scopes.get(name.lower(), catalog.SCOPE_NONE), "")
        if not is_default and not section["configured"]:
            # an ADDABLE extension: a row in the "+ Add" tile, with ONE control and NO fields. What the
            # block an add would seed cannot satisfy is decided by the schema's own requirement
            # computation (:func:`_extension_add_plan`), and it decides the CONTROL:
            #
            # * nothing missing → a one-click write of the dict's declared defaults, switched off;
            # * anything missing → a LINK to this extension's page for THIS scope, where the fields have
            #   room and the save creates the block. A one-click write there could only be refused.
            plan = _extension_add_plan(name, section["fields"],
                                       NODE_PAGE_DICT_SCOPE_NODE if instance is None
                                       else NODE_PAGE_DICT_SCOPE_INSTANCE)
            if plan:
                switch = {"type": "link",
                          "href": _extension_scope_url(key or node_name, name, instance),
                          "label": NODE_PAGE_EXTENSION_ADD_LABEL,
                          "title": NODE_PAGE_EXTENSION_ADD_LINK, "on": False}
            else:
                switch = _extension_switch(name, False, tab_url, csrf, revision, add=True)
            extension_options.append({
                "name": name,
                "description": description,
                "scope_label": scope_label,
                "switch": switch,
                # the add control's own word: it writes a block and enables nothing
                "labeled": True,
            })
            continue
        block_enabled = _block_enabled(document, section["base"])
        inherited_enabled = (_block_enabled(document, (key or node_name, "extensions", name))
                             if instance is not None else None)
        enabled = _extension_enabled(is_default, section["configured"], block_enabled,
                                     inherited_enabled)
        # READINESS IS COMPUTED, and only where the switch LIVES. The Node-wide tab carries no switch
        # (see below), so there is nothing to gate there; an instance card is gated on the required keys
        # of BOTH scopes — a node-level required value is what the extension starts with, because
        # ``load_extension`` merges the node block UNDER the instance block.
        missing: list[tuple[str, str]] = []
        if instance is not None:
            missing = _extension_missing_keys(
                name, section["fields"],
                node_block=configgen.value_at(document, (key or node_name, "extensions", name)),
                instance_block=configgen.value_at(document, section["base"]),
                node_scope=ext_scopes.get(name.lower()) in (catalog.SCOPE_NODE, catalog.SCOPE_BOTH))
        view = scopes.scope_view(section["fields"], document)
        extensions.append({
            "name": name,
            "kind_label": NODE_PAGE_EXTENSION_KIND,
            "description": description,
            "scope_label": scope_label,
            "default": is_default,
            "enabled": enabled,
            "fields": section["fields"],
            "configured": section["configured"],
            "locked_note": section["locked_note"],
            # THE COGWHEEL — the same destination a plugin's cogwheel opens: the extension's OWN
            # configuration page for this node. A DEFAULT extension with no schema fields (``Cloud``)
            # has no configuration page, so it carries no cogwheel.
            "cog_url": node_extension_url(node_name, name) if section["fields"] else "",
            # ON/OFF BELONGS ON THE INSTANCE TAB: ``enable()`` runs on a Server, ``init_extensions()``
            # needs an instance block to trigger a load at all, and the action writes the INSTANCE
            # scope — so a Node-wide card carries NO switch at all and shows the shared values only
            # (they are editable in place on that tab).
            "switch": (_extension_switch(
                name, section["configured"], tab_url, csrf, revision,
                is_default=is_default, enabled=enabled, ready=not missing,
                # THE JOB WINS over every other state: while it runs, the card shows the disabled
                # "Enabling…" switch and the page polls until the server says it is done.
                busy=extension_switch.job_for(request.app.state, key or node_name, instance, name))
                       if instance is not None else None),
            # THE LOCK'S ONE LINE: the empty required keys and the tab each is filled on. The cogwheel
            # beside the switch is the door those values are edited through, so the locked state adds no
            # control of its own.
            "warn": _missing_keys_sentence(request, missing) if missing else "",
            # THE TRASHCAN — a NON-default configured extension's removal, on its OWN URL: the
            # confirmation dialog (the only place a removal token is minted), never the switch's form.
            # A default extension has NO trashcan: removing its block would RE-ENABLE it (an absent
            # block means "default on"), so offering a removal would imply the opposite of what it does.
            "trash_url": "" if is_default else node_extension_remove_url(node_name, name, tab_index),
            "trash_title": NODE_PAGE_EXTENSION_REMOVE_TITLE,
            "vals": view.values, "rows": view.rows, "customs": view.customs,
            "elements": view.elements, "choices": view.choices, "sections": view.sections,
            "displays": {field.name: scopes.display_name(field, section["base"])
                         for field in configgen.flatten(view.root)},
        })



    yaml_text = ""
    if key is not None and document is not None:
        # THE SLICE: only this node's own top-level block of nodes.yaml — never the whole file.
        yaml_text = configwrite.dump_config(scopes.document_slice(document, [key]))
    elif document is None:
        try:
            yaml_text = configwrite.read_config_text(path)
        except configwrite.ConfigWriteError:
            yaml_text = ""
    if document is not None and schema_files:
        try:
            # the notice's check, MEMOISED: an unchanged file is not re-validated per render (see
            # services/webservice/validation.py — a save still validates freshly)
            validation.validate_cached(path, list(schema_files))
        except configwrite.ConfigValidationError as ex:
            schema_error = settings_page._clean_detail(ex.errors) or str(ex)
        except configwrite.ConfigWriteError:
            pass

    node_choices = [{"name": readmodels.text(name), "url": node_page_url(name),
                     "active": readmodels.text(name) == node_name}
                    for name in visible_nodes(request)]
    return {
        "page_title": NODE_SETTINGS_TITLE,
        "lead": NODE_SETTINGS_LEAD,
        "node": node_name,
        "file_name": NODES_YAML_NAME,
        "schema_name": Path(NODES_SCHEMA_PATH).name,
        "tabs": tabs,
        "node_options": node_choices,
        "node_label": settings_page.SCOPE_NODE_LABEL,
        "node_lead": settings_page.SCOPE_NODE_LEAD,
        "scope_note": scope_note,
        "settings_heading": NODE_PAGE_SECTION_SETTINGS,
        "extensions_heading": NODE_PAGE_SECTION_EXTENSIONS,
        "settings_fields": settings_fields,
        "settings_values": settings_view.values,
        "settings_displays": settings_displays,
        "settings_rows": settings_view.rows,
        "settings_customs": settings_view.customs,
        "settings_elements": settings_view.elements,
        "settings_choices": settings_view.choices,
        "settings_sections": settings_view.sections,
        "settings_hidden": vis["hidden"],
        "settings_picks": vis["picks"],
        "settings_removable": vis["removable"],
        "remove_ack": remove_ack,
        "remove_ack_field": vis["remove_ack_field"],
        "remove_companion": vis["remove_companion"],
        "extensions": extensions,
        # THE "+ ADD" TILE'S OPTIONS — the scope's schema extensions it does NOT configure yet, each
        # with the SAME add control its former OFF card carried. The tile is ALWAYS drawn (the operator,
        #): when the list is empty the template renders it in an explicit "all
        # configured" state, with ``all_configured`` as VISIBLE text (never only a tooltip).
        "extension_add": {"options": extension_options,
                          "all_configured": NODE_PAGE_EXTENSION_ALL_CONFIGURED},
        "extension_kind_label": catalog.kind_labels()[catalog.KIND_EXTENSION],
        "ext_field": NODE_PAGE_EXTENSION_FIELD,
        "ext_action_field": NODE_PAGE_EXTENSION_ACTION_FIELD,
        "ext_add_action": NODE_PAGE_EXTENSION_ADD,
        "ext_remove_action": NODE_PAGE_EXTENSION_REMOVE,
        "extension_default_badge": NODE_PAGE_EXTENSION_DEFAULT_BADGE,
        "yaml": yaml_text,
        "readonly": readonly or document is None,
        "parse_error": parse_error,
        "schema_error": schema_error,
        "schema_invalid_banner": settings_page.SCHEMA_INVALID_BANNER,
        "slice_note": settings_page.SCOPE_SLICE_NOTE,
        "revision": configwrite.config_revision(path),
        "save_path": f"{node_page_url(node_name)}?tab={tab_index}",
        "validate_path": _node_page_validate_url(node_name),
        "hints_path": _node_page_hints_url(node_name),
        "scope_carrier": NODE_PAGE_SCOPE_FIELD,
        "scope_value": scope_value,
    }


def _resolve_doc_key(request: Request, requested: str) -> str | None:
    """The canonical key *requested* names in the file on disk, or ``None`` when it is unreadable."""
    try:
        existing = configgen.read_document(nodes_yaml_path(request))
    except configwrite.ConfigWriteError:
        return None
    if isinstance(existing, dict):
        return _document_node_key(existing, requested)
    return None


#: A sentinel meaning "the page named a tab this node does not have" — distinct from ``None`` (the
#: node's DEFAULT scope).
_INVALID_SCOPE = object()


def _resolve_scope(request: Request, form, document, doc_key: str):
    """The scope a form save came from: ``None`` (DEFAULT) or an instance name — or ``_INVALID_SCOPE``.

    THE SCOPE IS CARRIED, NEVER INFERRED. The hidden :data:`NODE_PAGE_SCOPE_FIELD` names the active tab
    (``default`` / ``instance:<name>``); when it is absent (a bookmark or a stale form) the ``?tab=``
    index on the POST URL is read instead — 0 is DEFAULT, ``n`` the node's n-th instance. A carrier
    naming a tab this node does not have is :data:`_INVALID_SCOPE`, so the save refuses rather than
    guessing a scope.
    """
    block = document.get(doc_key) if isinstance(document, dict) else None
    instances = ([str(name) for name in block.get("instances")]
                 if isinstance(block, dict) and isinstance(block.get("instances"), dict) else [])
    raw = readmodels.text(form.get(NODE_PAGE_SCOPE_FIELD)) or ""
    if raw == NODE_PAGE_SCOPE_DEFAULT:
        return None
    if raw.startswith(NODE_PAGE_SCOPE_INSTANCE_PREFIX):
        name = raw[len(NODE_PAGE_SCOPE_INSTANCE_PREFIX):]
        return name if name in instances else _INVALID_SCOPE
    if raw:
        return _INVALID_SCOPE
    tab_raw = request.query_params.get("tab")
    if tab_raw is None or tab_raw == "":
        return None
    try:
        index = int(tab_raw)
    except (TypeError, ValueError):
        return _INVALID_SCOPE
    if index <= 0:
        return None
    return instances[index - 1] if index - 1 < len(instances) else _INVALID_SCOPE


async def _save_extension_switch(request: Request, form, existing, doc_key: str, redirect_to: str,
                                 ext_name: str) -> RedirectResponse:
    """The ONE body for a card's switch — the same scoped save, a block-shaped mutate.

    The scope is CARRIED (:func:`_resolve_scope`), never inferred; a carrier naming no tab of this node
    is refused. The posted extension name must be one the SELECTED scope's schema declares
    (:func:`_scope_extension_names`) — a foreign node or instance (through the scope) or an out-of-scope
    name is refused with NOTHING written. A ``disable``/``enable`` additionally requires the name to
    be one the selected scope already CONFIGURES, or a DEFAULT extension (which runs with no block at
    all) — the switch never creates a non-default extension's block. An ``add`` writes the extension's
    block SEEDED FROM ITS OWN DICT'S DEFAULTS; a ``disable``/``enable`` writes ``enabled: false`` /
    ``enabled: true`` beside the block's own values; a
    ``remove`` deletes exactly that block, and only with the acknowledgement its dialog minted. Every
    action goes through :func:`configwrite.patch_config_file`, so the schema validation, the ``.bak``,
    the revision guard and the no-op rule are the SAME as any other save.

    TWO WRITES THE READINESS GATE AND THE LIVE ACTION ADD:

    * a ``disable``/``enable`` posted for an extension whose required values are still EMPTY is
      REFUSED with nothing written (:func:`_extension_missing_keys`) — the same discipline the scope
      guard applies, and the same answer the locked card cannot post;
    * an ``add`` whose required key declares no ``default`` is refused BEFORE the patch, naming the
      key and pointing at Expert Mode, instead of being refused by the schema downstream.

    A switch on an INSTANCE tab whose server this console knows calls the extension's OWN
    ``enable()``/``disable()`` through :func:`core.actions.call_action` — the live half a config write
    cannot do — handing it the ``{"Node": …, "Instance": …}`` pair read from the file at POST time,
    because the process's ``locals`` are stale until its node restarts. With no action available (the
    bot is down, a headless run) the flag is written into ``nodes.yaml`` as before and the operator is
    told it applies on the next start.
    """
    path = nodes_yaml_path(request)
    scope = _resolve_scope(request, form, existing, doc_key)
    if scope is _INVALID_SCOPE:
        _notice(request, SAVE_REFUSED_UNKNOWN_SCOPE, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    action = readmodels.text(form.get(NODE_PAGE_EXTENSION_ACTION_FIELD))
    if (action not in (NODE_PAGE_EXTENSION_ADD, NODE_PAGE_EXTENSION_REMOVE,
                       NODE_PAGE_EXTENSION_DISABLE, NODE_PAGE_EXTENSION_ENABLE)
            or ext_name not in _scope_extension_names(doc_key, scope)):
        _notice(request, SAVE_REFUSED_UNKNOWN_EXTENSION, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    key = _document_node_key(existing, doc_key) or doc_key
    instance = scope if isinstance(scope, str) else None
    # A ``disable``/``enable`` writes INTO a block: the scope must already carry one for that name, or
    # the name must be a DEFAULT extension (which runs with no block at all, so its switch-off has to
    # CREATE one). A non-default extension the scope does not configure is a choice of the "+ Add"
    # tile — its card posts ``add``, never a switch action — so a crafted switch action for it would
    # create a block no control offers. Refused, nothing written.
    if action in (NODE_PAGE_EXTENSION_DISABLE, NODE_PAGE_EXTENSION_ENABLE):
        if (ext_name not in _configured_extensions(existing, key, instance)
                and ext_name.lower() not in catalog.default_extensions()):
            _notice(request, NODE_PAGE_EXTENSION_UNCONFIGURED, ok=False)
            return RedirectResponse(redirect_to, status_code=303)
        # THE LOCK IS A SERVER RULE: an extension whose required values of EITHER scope are still
        # empty is refused here, exactly as its card cannot post — so a crafted enable can never start
        # an extension the page shows as not ready, and nothing is written.
        missing = _extension_missing_keys(
            ext_name, _tab_extension_fields(doc_key, instance, ext_name),
            node_block=configgen.value_at(existing, (key, "extensions", ext_name)),
            instance_block=(configgen.value_at(existing, _extension_base(doc_key, scope, ext_name))
                            if instance is not None else None),
            node_scope=_declares_node_scope(ext_name))
        if missing:
            _notice(request, i18n.translate(request, NODE_PAGE_EXTENSION_NOT_READY,
                                            name=ext_name,
                                            keys=_missing_keys_sentence(request, missing)),
                     ok=False)
            return RedirectResponse(redirect_to, status_code=303)
    # THE ACKNOWLEDGEMENT: the REMOVAL is the ONLY labelled write, and it must present the token the
    # confirmation dialog minted for EXACTLY this (node, scope, extension), minted under the file's
    # CURRENT revision. Absent, stale (a newer dialog replaced it), minted for a different block, or
    # minted before the file changed -> REFUSED here, before the patch runs, so nothing is written and
    # the refusal says so. An ``add`` needs no acknowledgement — it writes a block seeded from
    # defaults and discards nothing; ``disable``/``enable`` discard nothing either, so they need none.
    scope_value = (NODE_PAGE_SCOPE_DEFAULT if scope is None
                   else NODE_PAGE_SCOPE_INSTANCE_PREFIX + str(scope))
    if action == NODE_PAGE_EXTENSION_REMOVE:
        ack = readmodels.text(form.get(NODE_PAGE_EXTENSION_ACK_FIELD))
        if not validate_extension_ack(request, doc_key, scope_value, ext_name, ack,
                                      configwrite.config_revision(path)):
            _notice(request, SAVE_REFUSED_EXTENSION_ACK, ok=False)
            return RedirectResponse(redirect_to, status_code=303)
    dict_scope = (NODE_PAGE_DICT_SCOPE_NODE if instance is None
                  else NODE_PAGE_DICT_SCOPE_INSTANCE)
    seed: dict = {}
    add_enabled: bool | None = False
    if action == NODE_PAGE_EXTENSION_ADD:
        # THE ADD WRITES AND ENABLES NOTHING — the values are filled on the extension's own page. The
        # seed is the dict's declared defaults for THIS scope, plus ``enabled: false``: the block is
        # created switched off, because the loader reads an ABSENT key as ON. A key the scope's schema
        # does not declare cannot be written at all, so the seed carries only the keys it knows — and a
        # scope whose schema does not declare ``enabled`` gets no flag rather than an unvalidatable one.
        fields = _tab_extension_fields(doc_key, instance, ext_name)
        declared_keys = {str(field.key) for field in fields}
        seed = {name: value for name, value in _extension_defaults(ext_name, dict_scope).items()
                if not declared_keys or name in declared_keys}
        if declared_keys and "enabled" not in declared_keys:
            add_enabled = None
        # A BLOCK THAT CANNOT BE SEEDED IS A LINK, NOT A CLICK: the same schema requirement computation
        # the node page uses to draw the row as a link refuses a crafted ``add`` for it, before the
        # patch, naming where the values are actually entered.
        if _extension_add_plan(ext_name, fields, dict_scope):
            _notice(request, i18n.translate(request, NODE_PAGE_EXTENSION_ADD_NEEDS_SETUP,
                                            name=ext_name), ok=False)
            return RedirectResponse(redirect_to, status_code=303)
    if action in (NODE_PAGE_EXTENSION_DISABLE, NODE_PAGE_EXTENSION_ENABLE) and instance is not None:
        # THE LIVE SWITCH: the extension's OWN lifecycle, not a flag write. The pair comes from the
        # file AS IT IS ON DISK at this moment, because the process's ``locals`` stay stale until its
        # node restarts. Nothing is patched here — the action's own ``config_extension`` writes
        # ``enabled``.
        server_name = _instance_server_name(request, key, instance)
        qualname = (NODE_PAGE_EXTENSION_ENABLE_ACTION
                    if action == NODE_PAGE_EXTENSION_ENABLE
                    else NODE_PAGE_EXTENSION_DISABLE_ACTION)
        if server_name and action_available(qualname):
            try:
                current = configgen.read_document(path)
            except configwrite.ConfigWriteError:
                current = existing
            params: dict = {"server_name": server_name, "extension": ext_name}
            if action == NODE_PAGE_EXTENSION_ENABLE:
                params["config"] = _extension_config_pair(current, doc_key, instance, ext_name)
            # THE SWITCH IS HANDED OVER, NOT AWAITED. An enable runs the extension's ``install()`` — a
            # pip install for one that ships a package — so awaiting it here held the request open for
            # minutes: the browser waited with nothing to read, could drop the connection, and a second
            # click or operator started a SECOND install. The job is claimed in the registry (so a
            # concurrent attempt is REFUSED here, before anything starts), the action runs as a
            # background task, and the page reads the job to draw its disabled "Enabling…" switch and to
            # collect the result once it lands.
            from urllib.parse import unquote
            job = extension_switch.begin(request.app.state, key, unquote(_url_node(redirect_to)),
                                         instance, ext_name, action)
            if job is None:
                _notice(request, i18n.translate(request, NODE_PAGE_EXTENSION_SWITCH_BUSY,
                                                name=ext_name), ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            # ``track`` — never a bare ``create_task``: a task nothing references can be collected at
            # an await point, which cancels it silently and leaves the switch blinking forever.
            extension_switch.track(request.app.state, job, _run_extension_switch(
                request.app.state, job, qualname, actions_page.context_for(request), params))
            started = (NODE_PAGE_EXTENSION_SWITCH_STARTED
                       if action == NODE_PAGE_EXTENSION_ENABLE else NODE_PAGE_EXTENSION_SWITCH_STOPPED)
            _notice(request, i18n.translate(request, started, name=ext_name), ok=True)
            return RedirectResponse(redirect_to, status_code=303)
    revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
    schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
    base = _extension_base(doc_key, scope, ext_name)
    if action == NODE_PAGE_EXTENSION_ADD:
        mutate = lambda document: _add_extension_block(document, base, seed, enabled=add_enabled)
    elif action == NODE_PAGE_EXTENSION_REMOVE:
        mutate = lambda document: _remove_extension_block(document, base)
    else:
        want = action == NODE_PAGE_EXTENSION_ENABLE
        mutate = lambda document: _set_extension_enabled(document, base, want)
    try:
        result = configwrite.patch_config_file(
            path, mutate, schemas=schema_files, expect_revision=revision)
    except configwrite.StaleConfigRevision:
        _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                "rendered. Reload and try again."), ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigValidationError as ex:
        if action == NODE_PAGE_EXTENSION_ADD:
            # THE ADD SPEAKS IN OUR WORDS: a seeded value the schema refused is reported as an add that
            # did not pass the check, never as pykwalify's line (which names a document path). Nothing
            # was written either way.
            _notice(request, i18n.translate(request, NODE_PAGE_EXTENSION_ADD_REJECTED,
                                            name=ext_name), ok=False)
            return RedirectResponse(redirect_to, status_code=303)
        detail = settings_page._clean_detail(ex.errors) or str(ex)
        _notice(request, f"{settings_page.REFUSED_BANNER} {detail}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigWriteError as ex:
        _notice(request, f"{settings_page.REFUSED_BANNER} {ex}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    # THE ACKNOWLEDGEMENT IS SPENT — a ONE-TIME token: once a removal has
    # been accepted, delete the store entry so the same token can never authorise a SECOND removal.
    if action == NODE_PAGE_EXTENSION_REMOVE:
        spend_extension_ack(request, doc_key, scope_value, ext_name)
    if result.changed:
        msgid = {NODE_PAGE_EXTENSION_ADD: NODE_PAGE_EXTENSION_ADDED,
                 NODE_PAGE_EXTENSION_REMOVE: NODE_PAGE_EXTENSION_REMOVED,
                 NODE_PAGE_EXTENSION_DISABLE: NODE_PAGE_EXTENSION_TURNED_OFF,
                 NODE_PAGE_EXTENSION_ENABLE: NODE_PAGE_EXTENSION_TURNED_ON}[action]
        # THE FALLBACK'S OWN SENTENCE: with no live action the flag is written into ``nodes.yaml``,
        # which is a BOOT file — so the operator is offered the apply control, exactly like any other
        # save of this file, and told it takes effect on the next start.
        apply = (config_apply.offer(request, path=path,
                                    config_dir=settings_page.config_dir(request),
                                    kind=settings_page.KIND_NODES, name=NODES_YAML_NAME,
                                    node=doc_key or "", back=redirect_to)
                 if action in (NODE_PAGE_EXTENSION_DISABLE, NODE_PAGE_EXTENSION_ENABLE) else None)
        _notice(request, i18n.translate(request, msgid, name=ext_name), ok=True, apply=apply)
    elif action in (NODE_PAGE_EXTENSION_ADD, NODE_PAGE_EXTENSION_REMOVE):
        msgid = (NODE_PAGE_EXTENSION_ADD_EXISTS if action == NODE_PAGE_EXTENSION_ADD
                 else NODE_PAGE_EXTENSION_REMOVE_ABSENT)
        _notice(request, i18n.translate(request, msgid, name=ext_name), ok=True)
    else:
        # a ``disable``/``enable`` that changed nothing: the scope already held the value, so the file
        # is byte-identical — say so, the SAME sentence every no-op save leaves.
        _notice(request, i18n.translate(request, settings_page.SAVED_NO_CHANGE), ok=True)
    return RedirectResponse(redirect_to, status_code=303)


async def _run_extension_switch(state, job, qualname: str, ctx: Any, params: dict) -> None:
    """Run ONE claimed switch job to completion and record its verdict.

    The job was claimed before this task started (:func:`services.webservice.extension_switch.begin`),
    so the switch is locked for its whole duration and a second attempt was refused rather than queued.
    The action's own message is the result — success or the refusal sentence it words (a failed
    ``install()`` names itself) — and :func:`extension_switch.finish` stores it for the next page render
    to show as the console's ordinary notice. An exception is caught HERE and recorded as a refusal:
    nothing awaits this task, so an escaping error would be a silently dead switch, not a traceback an
    operator could see.
    """
    started = time.monotonic()
    # THE LOG IS THE ONLY PLACE THIS IS VISIBLE: nothing awaits the task, so a switch that never
    # finishes leaves no traceback anywhere. These two lines name the action, so a stuck switch can be
    # told apart from one that simply has not finished yet.
    log.info("extension switch started: %s (%s)", qualname, job.state_key)
    try:
        result = await call_action(qualname, ctx, **params)
        ok = bool(getattr(result, "success", False))
        message = readmodels.text(getattr(result, "message", ""))
    except asyncio.CancelledError:
        # CANCELLED IS A VERDICT TOO: a shutdown or a collected task must still close the job, or the
        # page has nothing to stop blinking on. Recorded, then re-raised so the loop sees the cancel.
        log.warning("extension switch cancelled: %s", qualname)
        extension_switch.finish(state, job, False, extension_switch.STOPPED_SHORT)
        raise
    except BaseException as ex:          # noqa: BLE001 - the task has no caller at all; record it
        # BaseException, not Exception: the action runs extension code inside, and something escaping
        # as a SystemExit would otherwise kill the task with the job left "running" for ever.
        log.exception("extension switch failed: %s", qualname)
        ok, message = False, f"{type(ex).__name__}: {ex}"
    log.info("extension switch %s: %s in %.1fs — %s", "succeeded" if ok else "failed", qualname,
             time.monotonic() - started, message[:300])
    extension_switch.finish(state, job, ok, message)


def _surface_switch_results(request: Request, node_name: str) -> None:
    """Turn every finished-but-unshown switch job of this node into the page's OWN notice.

    Called at the top of a render, BEFORE the notice is popped, so the result of a background switch
    (the sentence the blocking save used to return inline) arrives on the reload the poll triggers.
    Each job is taken once — a later reload does not repeat it.

    Reconciled FIRST: a job whose task died closes here rather than staying "running", so a plain
    reload (with no poll in flight) also ends the blink and says what happened.
    """
    extension_switch.reconcile(request.app.state, node_name)
    for job in extension_switch.take_results(request.app.state, node_name):
        if job.message:
            _notice(request, job.message, ok=job.ok)


def _scope_from_tab_index(document, doc_key: str, tab_index: int):
    """The scope a ``?tab=<n>`` index names: ``None`` (DEFAULT), an instance name, or ``_INVALID_SCOPE``."""
    if tab_index <= 0:
        return None
    instances = _document_instances(document, doc_key)
    if tab_index - 1 >= len(instances):
        return _INVALID_SCOPE
    return instances[tab_index - 1]


def _scope_label(request: Request, instance: str | None) -> str:
    """The scope's own NAME, translated — the words the page's tab strip shows."""
    if instance is None:
        return i18n.translate(request, NODE_PAGE_SCOPE_NODE_LABEL)
    return i18n.translate(request, NODE_PAGE_SCOPE_INSTANCE_LABEL, name=readmodels.text(instance))


def _extension_remove_dialog(request: Request, node_name: str, doc_key: str, ext_name: str,
                             instance: str | None, scope_value: str, tab_index: int,
                             block, path: Path) -> dict:
    """The removal dialog's context: the console's confirmation page, worded for ONE extension block.

    THE ACKNOWLEDGEMENT IS MINTED HERE — the render that DISPLAYED this block's contents — and stamped
    with the file revision it displayed, so a token minted before a change cannot authorise a write
    after it. The form posts the ``remove`` action back to the node page's own save with that token;
    the save refuses without it (see :func:`_save_extension_switch`). The block's YAML is operator data:
    it is printed verbatim, escaped before it rides the record's markup.
    """
    from markupsafe import escape

    revision = configwrite.config_revision(path)
    discard = configwrite.dump_config(block) if block is not None else ""
    ack = mint_extension_ack(request, doc_key, scope_value, ext_name, revision)
    tab_url = f"{node_page_url(node_name)}?tab={tab_index}"
    scope_label = _scope_label(request, instance)
    return {
        "page_title": {"msgid": NODE_PAGE_EXTENSION_REMOVE_PAGE_TITLE,
                       "params": {"name": ext_name, "node": node_name}},
        "back": tab_url,
        "dialog": {
            "path": tab_url,
            "heading": {"msgid": NODE_PAGE_EXTENSION_REMOVE_HEADING,
                        "params": {"name": ext_name, "scope": escape(scope_label)}},
            "tag": i18n._("confirm"),
            "detail": {"msgid": NODE_PAGE_EXTENSION_REMOVE_DETAIL,
                       "params": {"scope": escape(scope_label)}},
            "detail_notes": (
                {"msgid": NODE_PAGE_EXTENSION_REMOVE_BLOCK,
                 "params": {"yaml": escape(discard)}},
            ),
            "warning": {"msgid": NODE_PAGE_EXTENSION_DISCARD_WARNING},
            "warning_note": "",
            "target": node_name,
            "irreversible": False,
            "danger": True,
            "go": {"msgid": i18n._("Remove")},
            "go_title": {"msgid": NODE_PAGE_EXTENSION_REMOVE_TITLE},
            "inputs": (),
            # THE WHOLE WRITE travels in the BODY: which scope, which extension, which action — and the
            # revision the operator was looking at, so a file that changed meanwhile is refused rather
            # than written over.
            "hidden": ((NODE_PAGE_SCOPE_FIELD, scope_value),
                       (NODE_PAGE_EXTENSION_FIELD, ext_name),
                       (NODE_PAGE_EXTENSION_ACTION_FIELD, NODE_PAGE_EXTENSION_REMOVE),
                       (settings_page.SETTINGS_REVISION_FIELD, revision)),
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
            "confirm_field": NODE_PAGE_EXTENSION_ACK_FIELD,
            "confirm_token": ack,
        },
    }


def _extension_remove_chrome(request: Request, node_name: str) -> dict:
    """The shell chrome of the removal dialog — the same sidebar, pills and breadcrumb as the node page."""
    registrar = getattr(getattr(request, "app", None), "state", None)
    registrar = getattr(registrar, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                               level=dashboard_page.log_level(request))
    return {
        "title": NODE_SETTINGS_TITLE,
        "lead": NODE_SETTINGS_LEAD,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, nodes_page.NODES_TITLE,
                                      target=node_name),
        "notice": actions_page.pop_notice(request),
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=nodes_page.NODES_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
    }


def _foreign_scope_field(form, tree: configgen.Field) -> str | None:
    """The first posted ``f.`` field the selected scope's tree cannot place, or ``None``.

    The allow-list is the SCOPED TREE itself (:func:`scope_save_tree`): a posted field resolves only if
    its whole path is inside the selected scope — so a sibling instance, another node, the DEFAULT block
    from an instance tab (or the reverse), or an extension the scope does not configure does not
    resolve and the save is REFUSED (never silently dropped). The companions the save handles itself
    (``__key`` / ``__entries`` / ``__n``) are skipped here; ``apply_form`` still refuses an unplaceable
    one.
    """
    multi = getattr(form, "multi_items", None)
    items = list(multi()) if callable(multi) else []
    for key, _value in items:
        if not isinstance(key, str) or not key.startswith("f."):
            continue
        raw = tuple(key[2:].split("."))
        if raw and raw[-1] in (configgen.KEY_COMPANION, configgen.ENTRIES_COMPANION, "__n"):
            continue
        if raw and raw[-1] in (configgen.REMOVED_COMPANION, configgen.REMOVE_ACK_COMPANION):
            continue      # the removal list / its acknowledgement: guarded by the removal rule itself
        tokens = configgen._decode_tokens(raw)
        if configgen.find_field(tree, tokens) is None:
            return key
    return None


async def save_node(request: Request, doc_key: str, redirect_to: str) -> RedirectResponse:
    """The ONE save body for a node's block of ``nodes.yaml`` — shared by both entry points.

    A ``nodes.yaml`` is written as a WHOLE-DOCUMENT patch. The plain form's posted ``f.<node>.…``
    fields are applied against the SELECTED TAB's scope (:func:`scope_save_tree`) — the node's own block
    for DEFAULT, or one instance's block — so a field naming anything outside that scope (a sibling
    instance, another node, DEFAULT from an instance tab or the reverse, or an extension the scope does
    not configure) cannot be placed and the save is REFUSED, byte-identical. The Expert Mode door merges
    ONLY the selected node's slice back — never promoting it to a whole-file write. A node with NO block
    in the file has nothing to save, so its door REFUSES instead of creating the block. Both doors go
    through the ONE write primitive, so an untouched save is byte-identical and every other node's block
    is left exactly as it was. A card's ON/OFF switch (:func:`_save_extension_switch`) rides the SAME
    door: it posts the scope carrier plus the extension's name and action, and adds or removes exactly
    that extension's block.
    """
    path = nodes_yaml_path(request)
    schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
    form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
    revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
    expert = readmodels.text(form.get(settings_page.SETTINGS_EXPERT_FIELD)) == "1"
    slice_keys = [doc_key]

    # The node must HAVE a block. A node with none renders read-only; its save door REFUSES rather
    # than silently CREATING the block (a node is added in Expert Mode, never as a save side effect).
    try:
        existing = configgen.read_document(path)
    except configwrite.ConfigWriteError:
        existing = None
    if not isinstance(existing, dict) or _document_node_key(existing, doc_key) is None:
        _notice(request, SAVE_REFUSED_NO_NODE_BLOCK, ok=False)
        return RedirectResponse(redirect_to, status_code=303)

    # THE EXTENSION CARD's switch posts NO ``f.`` field at all (its form carries only the scope carrier,
    # the revision and the extension's name + action), so it is handled HERE — through the SAME
    # whole-document patch and the SAME node-scoped guard as a plain save (:func:`_save_extension_switch`).
    ext_name = readmodels.text(form.get(NODE_PAGE_EXTENSION_FIELD))
    if ext_name:
        return await _save_extension_switch(request, form, existing, doc_key, redirect_to, ext_name)

    removals: list[str] = []
    try:
        if expert:
            text = form.get(settings_page.SETTINGS_YAML_FIELD)
            document = configwrite.load_config_text("" if text is None else str(text), path=path)
            if document is None:
                _notice(request, settings_page.SAVE_REFUSED_EMPTY_DOCUMENT, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            if not isinstance(document, dict) or any(
                    str(entry) not in slice_keys for entry in document):
                _notice(request, settings_page.SAVE_REFUSED_FOREIGN_NODE, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            posted_slice = document

            def merge_mutate(document):
                scopes.merge_slice(document, scopes.document_slice(posted_slice, slice_keys))

            result = configwrite.patch_config_file(path, merge_mutate, schemas=schema_files,
                                                   expect_revision=revision)
        else:
            # THE SCOPE, CARRIED ON THE FORM (never inferred from the posted fields): the save applies
            # to the selected tab's block and nothing else. A carrier naming no tab of this node is
            # REFUSED.
            scope = _resolve_scope(request, form, existing, doc_key)
            if scope is _INVALID_SCOPE:
                _notice(request, SAVE_REFUSED_UNKNOWN_SCOPE, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            tree = scope_save_tree(doc_key, scope,
                                   _configured_extensions(existing, doc_key, None),
                                   _configured_extensions(existing, doc_key, scope))
            if _foreign_scope_field(form, tree) is not None:
                _notice(request, SAVE_REFUSED_FOREIGN_SCOPE, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            # A value-carrying REMOVAL (the "+ Add" picker's trashcan) is refused unless it carries the
            # acknowledgement the node page's render minted for this file+revision. An unplaceable
            # removal path is already refused by ``apply_form`` (the scoped tree is its allow-list), so
            # this only closes the "a bare POST never rendered the page" hole.
            removals = settings_page.removal_paths(form)
            if removals:
                token = readmodels.text(form.get("f." + settings_page.REMOVE_ACK_FIELD))
                if not settings_page.validate_remove_ack(request, str(path), token, revision):
                    _notice(request, settings_page.SAVE_REFUSED_REMOVE_ACK, ok=False)
                    return RedirectResponse(redirect_to, status_code=303)

            def mutate(document):
                configgen.apply_form(document, form, tree)

            result = configwrite.patch_config_file(path, mutate, schemas=schema_files,
                                                   expect_revision=revision)
            if removals:
                settings_page.spend_remove_ack(request, str(path), revision)
    except configwrite.StaleConfigRevision:
        _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                "rendered. Reload and try again."), ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigValidationError as ex:
        detail = settings_page._clean_detail(ex.errors) or str(ex)
        _notice(request, f"{settings_page.REFUSED_BANNER} {detail}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configgen.UnknownFieldError:
        _notice(request, settings_page.SAVE_REFUSED_UNPLACEABLE, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configgen.DuplicateEntryError:
        _notice(request, settings_page.SAVE_REFUSED_DUPLICATE_ENTRY, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigWriteError as ex:
        _notice(request, f"{settings_page.REFUSED_BANNER} {ex}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    if result.changed:
        message = (settings_page.SAVED_NOW if result.applies == configwrite.APPLIES_NOW
                   else settings_page.SAVED_RESTART)
        # the change belongs to THIS node's block: the offer names it, so the door restarts that node
        # and never a node a request could name.
        apply = config_apply.offer(request, path=path, config_dir=settings_page.config_dir(request),
                                   kind=settings_page.KIND_NODES, name=NODES_YAML_NAME,
                                   node=doc_key or "", back=redirect_to)
    else:
        message = settings_page.SAVED_NO_CHANGE
        apply = None
    _notice(request, message, ok=True, apply=apply)
    return RedirectResponse(redirect_to, status_code=303)


# ------------------------------------------------------------------ the extension CONFIG page
# THE COGWHEEL'S DESTINATION — the SAME tabbed, schema-driven view a plugin's cogwheel opens
# (``settings_scope.html``), over ONE extension's blocks of ``nodes.yaml`` for ONE node. The tabs are
# the node's own block, then one per instance of that node that configures the extension; the fields
# come from the extension's two schema partials (via ``scopes.build_extension_scopes``) and are saved
# by the SAME whole-document patch the node page uses, guarded so a save can only touch that node.

def _extension_context_node_key(document, node_name: str) -> str | None:
    """The key in *document* that names *node_name* — the SAME reading the node page uses."""
    return _document_node_key(document, node_name)


# ------------------------------------------------ the preset-FILE choice: one field, not free text
# THE ONE FIELD THE EDITOR DOES NOT RENDER AS FREE TEXT. ``MizEdit.presets`` names the preset file(s)
# an instance applies at startup (``extensions/mizedit/schemas/mizedit_schema.yaml``, ``str_or_list``);
# a mistyped path is silent, because the extension only logs a missing file. The control offers one
# CHECKBOX per file the console can KNOW — the files ``discover_preset_files`` finds under the console's
# config directory plus every label another node/instance already names — and ONE free-text row for a
# path that list does not carry.
#
# WHY THE FREE ROW IS NOT DECORATION: the console runs inside the master's process, so it can list the
# MASTER's config directory and nothing else. A file that exists only on a remote node has no row here;
# the free row is the only way to name it, and the field's help says so on a node that is not the master.
#
# THE VALUE SHAPES ARE THE SCHEMA'S OWN. One choice is a STRING, several are a LIST, none removes the
# key — which is what makes the extension fall back to ``presets.yaml`` in the instance's own config
# directory. Nothing new is invented, and a value the document already carries is a row of its own, so
# an untouched save cannot drop a hand-written path.

#: the extension the control belongs to, and the one key it replaces.
PRESETS_EXTENSION = "MizEdit"
PRESETS_KEY = "presets"
#: the companions the control posts — never document paths (the save collapses them into the value).
#: ``PRESETS_FORM_FIELD`` is the marker that says the control was ON the page: without it a post carries
#: no opinion about this field (an empty selection is invisible otherwise — a browser posts no checkbox).
PRESETS_CHOICE_FIELD = "__presets"
PRESETS_FREE_FIELD = "__presets_free"
PRESETS_FORM_FIELD = "__presets_present"


def _presets_field(active: dict) -> configgen.Field | None:
    """The ``presets`` field of the ACTIVE tab, or ``None`` when this tab does not carry one."""
    base = tuple(active.get("base") or ())
    wanted = base + (PRESETS_KEY,)
    for field in active.get("fields") or []:
        if tuple(field.path) == wanted:
            return field
    return None


def _values_named(current: Any) -> list[str]:
    """The strings a ``presets`` value names — the SHAPES the schema allows and no others."""
    if isinstance(current, str):
        return [current]
    if isinstance(current, (list, tuple)):
        return [str(item) for item in current if item is not None]
    return []


def _master_node_names(request: Request) -> set[str]:
    """The nodes the cluster view reports as MASTER (empty when no node reports itself)."""
    entries = getattr(dashboard_page.request_source(request), "nodes", None)
    if not isinstance(entries, dict):
        return set()
    return {readmodels.text(name) for name, node in entries.items()
            if readmodels.safe(lambda: bool(getattr(node, "master", False)), False)}


def presets_choice_context(request: Request, nodes_document: Any, current: Any,
                           field: configgen.Field, node_name: str) -> dict:
    """The rows the preset-file control renders, and whether this node's own disk is readable here."""
    config = settings_page.config_dir(request)
    rows: list[dict] = []
    seen: set[str] = set()

    def offer(label: str, who: str = "") -> None:
        if label and label not in seen:
            seen.add(label)
            rows.append({"label": label, "checked": False, "who": who})

    # the extension's own DEFAULT file first, then what the console's config directory holds, then
    # every label another node/instance already names (its files may only exist on that machine)
    offer(f"{presets_page.CONFIG_LABEL}/{presets_page.MIZEDIT_DEFAULT_FILE}")
    for path in presets_page.discover_preset_files(config):
        offer(presets_page.display_path(config, path))
    for label, who in presets_page.mizedit_preset_files(nodes_document, config):
        offer(label, who)
    # a value the list does NOT offer keeps its own row, checked: an untouched save must not drop it
    for value in _values_named(current):
        offer(value)
    named = set(_values_named(current))
    for row in rows:
        row["checked"] = row["label"] in named
    masters = _master_node_names(request)
    return {"path": field.name, "rows": rows,
            "remote": bool(masters) and readmodels.text(node_name) not in masters}


def _derived_tab_index(scope_list) -> int:
    """The tab a page opens on when the URL names none: the FIRST scope that CARRIES a block.

    The cogwheel of a configured card addresses the page without a tab, so it must land on that card's
    own scope even when the page also offers a scope that does not exist yet (which comes first for a
    node-scoped extension). No scope carrying a block means tab 0 — the first one, which then creates
    it on save.
    """
    for index, scope in enumerate(scope_list):
        if scope.get("present"):
            return index
    return 0


def _scope_from_carrier(scope: "str | None"):
    """``(kind, instance)`` named by a ``?scope=`` carrier, or ``None`` when it names neither."""
    raw = readmodels.text(scope or "")
    if raw == NODE_PAGE_SCOPE_DEFAULT:
        return "node", None
    if raw.startswith(NODE_PAGE_SCOPE_INSTANCE_PREFIX):
        name = raw[len(NODE_PAGE_SCOPE_INSTANCE_PREFIX):]
        return ("instance", name) if name else None
    return None


def extension_page_context(request: Request, node_name: str, ext_name: str,
                           tab_index: "int | None" = 0,
                           scope: "str | None" = None) -> dict:
    """Everything ``settings_scope.html`` reads for ONE extension's configuration on ONE node.

    Returns the SAME shape the plugin/service cogwheel view (:func:`settings.scoped_context`) does, so
    the two surfaces render through the ONE template and the ONE control macro. A ``nodes.yaml`` that
    does not parse, a node with no block, or an extension the node cannot be configured for anywhere
    renders READ-ONLY with the honest reason rather than a broken form.

    *scope* is the console's scope carrier (``default`` / ``instance:<name>``) and WINS over *tab_index*:
    it names a scope, where a tab index names a position. *tab_index* ``None`` is the page's own
    default: it opens on the FIRST scope that already CARRIES a block, so the cogwheel of a configured
    card lands on that card's scope even when the same page also offers a scope that does not exist yet.
    An explicit ``?tab=<n>`` is honoured as it is.
    """
    path = nodes_yaml_path(request)
    readonly = False
    parse_error = ""
    schema_error = ""
    document = None
    try:
        document = configgen.read_document(path)
    except configwrite.ConfigWriteError as ex:
        parse_error = str(ex)
        readonly = True
    if document is None and not parse_error:
        readonly = True
        parse_error = i18n._("No nodes.yaml on this install, so this extension has nothing to show.")
    key: str | None = node_name
    if document is not None:
        found = _extension_context_node_key(document, node_name)
        if found is None:
            readonly = True
            parse_error = i18n._("This node has no block in nodes.yaml yet.")
        else:
            key = found

    node_names = settings_page._node_names(settings_page.config_dir(request))
    scope_list, _save_tree = scopes.build_extension_scopes(
        document if isinstance(document, dict) else {}, ext_name, node_names, node=key)
    visible = scopes.scopes_for_node(scope_list, key) if key is not None else scope_list
    if not visible:
        readonly = True
        parse_error = NODE_EXTENSION_UNCONFIGURED

    base_url = node_extension_url(node_name, ext_name)
    named = _scope_from_carrier(scope)
    if named is not None:
        tab_index = scopes.scope_tab_index(visible, named[0], named[1])
    elif tab_index is None:
        tab_index = _derived_tab_index(visible)
    tab_index = max(0, min(tab_index, max(0, len(visible) - 1)))
    tabs = [{"label": scope["label"], "kind": scope["kind"], "active": index == tab_index, "note": "",
             "url": f"{base_url}?tab={index}"} for index, scope in enumerate(visible)]
    node_choices = [{"name": name, "url": f"{node_extension_url(name, ext_name)}?tab=0",
                     "active": name == (key or node_name)} for name in scopes.node_options(scope_list)]
    active = visible[tab_index] if visible else {"fields": [], "base": (), "kind": "node",
                                                "present": True}
    # A SCOPE WITHOUT A BLOCK RENDERS THE SAME FORM, and its save CREATES the block: the tab set now
    # includes the scopes this node could configure (``build_extension_scopes(node=…)``), so the page
    # says which state it is in rather than offering a form that would not save.
    creates_block = document is not None and not active.get("present", True)
    view = scopes.scope_view(active.get("fields", []), document)
    displays = {field.name: scopes.display_name(field, active.get("base", ()))
                for field in configgen.flatten(view.root)}
    slice_keys = scopes.scope_keys(visible) if visible else []
    # THE SHOW/HIDE MODEL for this tab's body + the removal acknowledgement (see ``save_extension``).
    vis = settings_page.visibility_context(view.root, view.root, document)
    # THE PRESET-FILE CHOICE. ``MizEdit.presets`` is rendered as a control instead of free text, and it
    # is shown even when the block does not carry it — an operator who has set nothing yet is exactly
    # who needs to see the files. It stays in the REMOVABLE set: that is what mints the save's removal
    # acknowledgement, which a "no row checked" removal must carry like any other.
    presets_control = None
    presets_here = (_presets_field(active) if ext_name.lower() == PRESETS_EXTENSION.lower() else None)
    if presets_here is not None:
        presets_id = configgen.view_id(presets_here)
        vis["hidden"] = frozenset(item for item in vis["hidden"] if item != presets_id)
        vis["picks"] = {name: [option for option in options if option["id"] != presets_id]
                        for name, options in vis["picks"].items()}
        vis["picks"] = {name: options for name, options in vis["picks"].items() if options}
        presets_control = presets_choice_context(request, document, view.values.get(presets_here.name),
                                                 presets_here, key or node_name)
    remove_ack = ""
    if vis["removable"] and document is not None:
        remove_ack = settings_page.mint_remove_ack(request, str(path),
                                                   configwrite.config_revision(path))

    yaml_text = ""
    if document is not None:
        yaml_text = configwrite.dump_config(
            scopes.document_slice(document, slice_keys) if slice_keys else document)
    else:
        try:
            yaml_text = configwrite.read_config_text(path)
        except configwrite.ConfigWriteError:
            yaml_text = ""
    if document is not None:
        schema_files = configgen.schema_files_for(NODES_SCHEMA_PATH)
        try:
            # the notice's check, MEMOISED: an unchanged file is not re-validated per render (see
            # services/webservice/validation.py — a save still validates freshly)
            validation.validate_cached(path, list(schema_files))
        except configwrite.ConfigValidationError as ex:
            schema_error = settings_page._clean_detail(ex.errors) or str(ex)
        except configwrite.ConfigWriteError:
            pass

    descriptions, _scopes = _extension_catalog()
    return {
        "scoped": True,
        "ext_name": ext_name,
        "tabs": tabs,
        # the RESOLVED tab: a page opened by a ``?scope=`` carrier still posts its save to a ``?tab=``
        # URL, so the save's redirect lands back on the scope the operator was looking at
        "tab_index": tab_index,
        "fields": active.get("fields", []),
        "displays": displays,
        "values": view.values,
        "rows": view.rows,
        "customs": view.customs,
        "elements": view.elements,
        "choices": view.choices,
        "sections": view.sections,
        "hidden": vis["hidden"],
        "picks": vis["picks"],
        "presets_control": presets_control,
        "removable": vis["removable"],
        "remove_ack": remove_ack,
        "remove_ack_field": vis["remove_ack_field"],
        "remove_companion": vis["remove_companion"],
        "scope_note": i18n.translate(request, NODE_EXTENSION_SCOPE_NOTE),
        # the ACTIVE tab's state: a scope the file carries no block for renders its form anyway, and
        # this is the sentence that says the save CREATES the block there
        "creates_block": creates_block,
        "create_note": i18n.translate(request, NODE_EXTENSION_CREATE_NOTE) if creates_block else "",
        "add_path": "",
        "add_placeholder": settings_page.ADD_BLOCK_INSTANCE_PLACEHOLDER,
        "node_names": node_names,
        "multi_node": len(node_names) > 1,
        "node_options": node_choices,
        "node_label": settings_page.SCOPE_NODE_LABEL,
        "node_lead": settings_page.SCOPE_NODE_LEAD,
        "selected_node": key or "",
        "slice_note": settings_page.SCOPE_SLICE_NOTE,
        "slice_mode": bool(slice_keys),
        "back_url": node_page_url(node_name),
        "back_label": NODE_SETTINGS_TITLE,
        "crumb_tail": f"{catalog.kind_labels()[catalog.KIND_EXTENSION]} / {ext_name}",
        "kind_label": catalog.kind_labels()[catalog.KIND_EXTENSION],
        "lead": descriptions.get(ext_name.lower(), ""),
        "yaml": yaml_text,
        "readonly": readonly or document is None,
        "parse_error": parse_error,
        "schema_error": schema_error,
        "no_schema": False,
        "file_name": ext_name,
        "schema_name": "",
        "revision": configwrite.config_revision(path),
        "current_url": base_url,
    }


def _foreign_extension_field(form, doc_key: "str | None", instances=()) -> str | None:
    """The first posted ``f.`` field the SELECTED node's block cannot place — or ``None``.

    The extension page edits ONE node's blocks; every ``f.`` field it renders begins with that node's
    key (``f.<node>.extensions.<Name>…`` or ``f.<node>.instances.<inst>.extensions.<Name>…``). A field
    naming any other top-level key — a sibling NODE — is refused, never written, and so is one naming an
    INSTANCE the node's block does not declare: the save tree addresses instances by a ``regex;(.+)``
    placeholder, which would otherwise CREATE the instance that field names.
    """
    multi = getattr(form, "multi_items", None)
    items = list(multi()) if callable(multi) else []
    for name, _value in items:
        if not isinstance(name, str) or not name.startswith("f."):
            continue
        tokens = [configgen.decode_token(token) for token in name[2:].split(".")]
        if tokens[0] in (configgen.REMOVED_COMPANION, configgen.REMOVE_ACK_COMPANION):
            continue              # the removal list / its acknowledgement: guarded by the removal rule
        if tokens[0] != doc_key:
            return name
        if len(tokens) > 2 and tokens[1] == "instances" and tokens[2] not in instances:
            return name
    return None


def _document_carries(document, tokens: tuple) -> bool:
    """Whether *document* carries the key at *tokens*."""
    current = document
    for token in tokens:
        if not isinstance(current, dict) or token not in current:
            return False
        current = current[token]
    return True


class _CollapsedForm:
    """A posted form with one field's choice rows collapsed into the schema's OWN value shape.

    ``configgen.apply_form`` and the save's removal reading take the form through ``multi_items`` (and
    ``get`` for the companions), so a sequence of ``(name, value)`` pairs is all this has to carry.
    """

    def __init__(self, form, items):
        self._form = form
        self._items = list(items)

    def multi_items(self):
        return list(self._items)

    def get(self, key, default=None):
        values = [value for name, value in self._items if name == key]
        return values[-1] if values else default

    def __iter__(self):
        return iter([name for name, _value in self._items])


def _collapse_presets_choice(form, document):
    """Rebuild *form* with the preset-file control's rows as the ``presets`` value it means.

    The control posts one ``f.<path>.__presets`` per CHECKED file (in page order) and the free-text row
    as ``f.<path>.__presets_free``. Those are not document paths, so they are replaced here by the shapes
    the schema declares: ONE file is a STRING, several are a LIST in that order, NONE removes the key
    (the removal the save then requires its acknowledgement for). A typed path is kept VERBATIM — it is
    operator data, and a normalising step here would silently rename a file.
    """
    multi = getattr(form, "multi_items", None)
    if not callable(multi):
        return form
    choice_suffix = "." + PRESETS_CHOICE_FIELD
    free_suffix = "." + PRESETS_FREE_FIELD
    form_suffix = "." + PRESETS_FORM_FIELD
    chosen: dict[str, list[str]] = {}
    typed: dict[str, str] = {}
    rendered: set[str] = set()
    kept: list[tuple] = []
    for name, value in list(multi()):
        text = name if isinstance(name, str) else ""
        if not text.startswith("f."):
            kept.append((name, value))
        elif text.endswith(choice_suffix):
            chosen.setdefault(text[2:-len(choice_suffix)], []).append(str(value or ""))
        elif text.endswith(free_suffix):
            typed[text[2:-len(free_suffix)]] = str(value or "")
        elif text.endswith(form_suffix):
            rendered.add(text[2:-len(form_suffix)])
        else:
            kept.append((name, value))
    if not rendered:
        return form
    paths = list(chosen) + [path for path in typed if path not in chosen]
    paths += [path for path in rendered if path not in paths]
    stale = {"f." + path for path in paths}
    stale |= {"f." + path + "." + configgen.FORM_COMPANION for path in paths}
    derived: list[tuple] = []
    for path in paths:
        values = [value for value in chosen.get(path, []) if value.strip()]
        extra = typed.get(path, "")
        if extra.strip() and extra not in values:
            values.append(extra)
        name = "f." + path
        if not values:
            if _document_carries(document, tuple(path.split("."))):
                derived.append(("f." + configgen.REMOVED_COMPANION,
                                configgen.encode_path(tuple(path.split(".")))))
        elif len(values) == 1:
            derived.append((name, values[0]))
            derived.append((name + "." + configgen.FORM_COMPANION, "scalar"))
        else:
            derived.extend((name, value) for value in values)
            derived.append((name + "." + configgen.FORM_COMPANION, "list"))
    return _CollapsedForm(form, [pair for pair in kept if pair[0] not in stale] + derived)


def _page_save_target(scope_list, doc_key: str | None, tab: int, scope: str | None):
    """The block a page save addresses, and whether that scope's schema declares ``enabled``.

    Resolved from the SAME scope list the page renders its tabs from: a ``?scope=`` carrier names a
    scope and wins over a ``?tab=`` position, exactly as :func:`extension_page_context` resolves it, so
    the save writes the block the operator was looking at.
    """
    visible = scopes.scopes_for_node(scope_list, doc_key)
    if not visible:
        return None, False
    named = _scope_from_carrier(scope)
    if named is not None:
        index = scopes.scope_tab_index(visible, named[0], named[1])
    else:
        index = max(0, int(tab or 0))
    active = visible[max(0, min(index, len(visible) - 1))]
    declared = any(str(field.key) == "enabled" for field in active.get("fields") or ())
    return tuple(active.get("base") or ()), declared


#: pykwalify's line for a required key the candidate does not carry (``Cannot find required key 'repo'``).
_REQUIRED_KEY = re.compile(r"Cannot find required key '([^']+)'")


def _fixed_alternatives(raw: str) -> list[str]:
    """A ``regex;`` fixed-alternative key as the names it offers; any other key as itself."""
    prefix = "regex;("
    if raw.startswith(prefix) and raw.endswith(")"):
        return raw[len(prefix):-1].split("|")
    return [raw]


def _refused_field_words(detail: str, error_path: str, base: tuple) -> list[str]:
    """The fields a refused CREATING save names: the required keys it is missing, else the offending
    path read RELATIVE to the block (``port`` for ``…/SRS/port``).
    """
    words = [" | ".join(_fixed_alternatives(raw)) for raw in _REQUIRED_KEY.findall(detail or "")]
    words = list(dict.fromkeys(words))
    if words:
        return words
    tokens = [token for token in str(error_path or "").split("/") if token]
    block = [str(token) for token in base]
    if tokens[:len(block)] == block:
        tokens = tokens[len(block):]
    return [".".join(tokens)] if tokens else []


async def save_extension(request: Request, node_name: str, ext_name: str, redirect_to: str,
                         *, tab: int = 0, scope: str | None = None) -> RedirectResponse:
    """The ONE save body for an extension's configuration on ONE node — the SAME write primitive.

    A ``nodes.yaml`` is written as a WHOLE-DOCUMENT patch. The plain form's fields are applied against
    the extension's schema tree (:func:`scopes.build_extension_scopes`), guarded so only the selected
    NODE's token may be written. The Expert Mode door merges ONLY that node's slice back. Every other
    node, scope and block is left byte-identical, and the no-op rule holds — because it IS the node
    page's save with a different resolve tree.

    A SAVE THAT CREATES THE BLOCK SWITCHES IT OFF. ``apply_form`` never creates a ``False`` bool on a
    creating save (it would invent the key on every other file), so the new block's own ``enabled`` is
    written here — ``false``, whatever the form posted: ``core/extension.py`` reads an ABSENT key as ON,
    so a block created without the flag would start the extension on the node's next start. One rule
    for every door that creates a block (:func:`_add_extension_block` writes the same value).

    A SAVE OVER AN EXISTING BLOCK NEVER TOUCHES ``enabled``: editing configuration never switches an
    extension on or off. Only the switch and the enable/disable actions write that value.

    *tab* / *scope* address the scope the page was showing (a ``?scope=`` carrier wins over a ``?tab=``
    position, exactly as the render resolves it), so the created block is the one on screen.
    """
    path = nodes_yaml_path(request)
    schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
    form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
    revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
    expert = readmodels.text(form.get(settings_page.SETTINGS_EXPERT_FIELD)) == "1"
    try:
        existing = configgen.read_document(path)
    except configwrite.ConfigWriteError:
        existing = None
    if not isinstance(existing, dict) or _document_node_key(existing, node_name) is None:
        _notice(request, SAVE_REFUSED_NO_NODE_BLOCK, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    doc_key = _document_node_key(existing, node_name)
    node_names = settings_page._node_names(settings_page.config_dir(request))
    scope_list, save_tree = scopes.build_extension_scopes(existing, ext_name, node_names, node=doc_key)
    base, enabled_declared = _page_save_target(scope_list, doc_key, tab, scope)
    creating = base is not None and configgen.value_at(existing, base) is None
    try:
        if expert:
            text = form.get(settings_page.SETTINGS_YAML_FIELD)
            document = configwrite.load_config_text("" if text is None else str(text), path=path)
            if document is None:
                _notice(request, settings_page.SAVE_REFUSED_EMPTY_DOCUMENT, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            if not isinstance(document, dict) or any(str(entry) != doc_key for entry in document):
                _notice(request, settings_page.SAVE_REFUSED_FOREIGN_NODE, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            posted_slice = document

            def merge_mutate(document):
                scopes.merge_slice(document, scopes.document_slice(posted_slice, [doc_key]))

            result = configwrite.patch_config_file(path, merge_mutate, schemas=schema_files,
                                                   expect_revision=revision)
        else:
            # THE PRESET-FILE CONTROL posts its rows as companions, not document paths: collapse them
            # into the field's own value shape BEFORE anything reads the form — the removal reading
            # included, since "no file selected" IS a removal.
            form = _collapse_presets_choice(form, existing)
            if _foreign_extension_field(form, doc_key,
                                        _document_instances(existing, doc_key)) is not None:
                _notice(request, SAVE_REFUSED_FOREIGN_SCOPE, ok=False)
                return RedirectResponse(redirect_to, status_code=303)
            # A value-carrying removal needs the acknowledgement the render minted (an unplaceable
            # path is already refused by ``apply_form`` against the extension's own tree).
            removals = settings_page.removal_paths(form)
            if removals:
                token = readmodels.text(form.get("f." + settings_page.REMOVE_ACK_FIELD))
                if not settings_page.validate_remove_ack(request, str(path), token, revision):
                    _notice(request, settings_page.SAVE_REFUSED_REMOVE_ACK, ok=False)
                    return RedirectResponse(redirect_to, status_code=303)

            def mutate(document):
                before = configgen.value_at(document, base) if base is not None else None
                # READ THE SWITCH BEFORE ``apply_form``: the value read here is the LIVE mapping it then
                # mutates, so the flag and its presence have to be captured as plain values first.
                before_book = before if isinstance(before, dict) else None
                had_flag = "enabled" in before_book if before_book is not None else False
                flag = before_book.get("enabled") if before_book is not None else None
                configgen.apply_form(document, form, save_tree)
                if base is None:
                    return
                after = configgen.value_at(document, base)
                if not isinstance(after, dict):
                    return
                switch_removed = configgen.encode_path(tuple(base) + ("enabled",)) in removals
                if before is None:
                    # THE CREATING SAVE WRITES THE FLAG ITSELF: a block created without it reads as ON.
                    # Only a scope whose schema DECLARES the key may carry it, exactly as the Add row.
                    if enabled_declared:
                        after["enabled"] = False
                elif had_flag and not switch_removed:
                    # AN EXISTING BLOCK KEEPS ITS SWITCH: the form's toggle is not a switch. An
                    # ACKNOWLEDGED removal of the field is the operator's own edit, so it stands.
                    after["enabled"] = flag
                else:
                    # absence stays absence — an absent key is ON, and a save may not invent it.
                    after.pop("enabled", None)

            result = configwrite.patch_config_file(path, mutate, schemas=schema_files,
                                                   expect_revision=revision)
            if removals:
                settings_page.spend_remove_ack(request, str(path), revision)
    except configwrite.StaleConfigRevision:
        _notice(request, i18n._("This page is stale — the file changed on disk since it was "
                                "rendered. Reload and try again."), ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigValidationError as ex:
        if creating:
            # THE CREATING DOOR SPEAKS IN OUR WORDS: a sentence naming the field the operator must fix,
            # never the validator's own line. Nothing was written — the patch aborts before the write.
            fields = _refused_field_words(ex.errors, ex.error_path, base or ())
            if fields:
                _notice(request, i18n.translate(request, NODE_EXTENSION_CREATE_REJECTED,
                                                fields=", ".join(fields)), ok=False)
                return RedirectResponse(redirect_to, status_code=303)
        detail = settings_page._clean_detail(ex.errors) or str(ex)
        _notice(request, f"{settings_page.REFUSED_BANNER} {detail}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configgen.UnknownFieldError:
        _notice(request, settings_page.SAVE_REFUSED_UNPLACEABLE, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configgen.DuplicateEntryError:
        _notice(request, settings_page.SAVE_REFUSED_DUPLICATE_ENTRY, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigWriteError as ex:
        _notice(request, f"{settings_page.REFUSED_BANNER} {ex}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    if result.changed:
        message = (settings_page.SAVED_NOW if result.applies == configwrite.APPLIES_NOW
                   else settings_page.SAVED_RESTART)
        apply = config_apply.offer(request, path=path, config_dir=settings_page.config_dir(request),
                                   kind=settings_page.KIND_NODES, name=NODES_YAML_NAME,
                                   node=doc_key or "", back=redirect_to)
    else:
        message = settings_page.SAVED_NO_CHANGE
        apply = None
    _notice(request, message, ok=True, apply=apply)
    return RedirectResponse(redirect_to, status_code=303)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the node page (``/nodes/<node>``) and the OLD settings path as a redirect, plus saves.

    The node page owns the node's ``nodes.yaml`` editing (DEFAULT + instance tabs, Expert Mode). The
    old ``/nodes/<node>/settings`` path renders NOTHING any more: both its GET and its POST answer a
    ``307`` redirect to ``/nodes/<node>`` (method preserved, so a stale form's POST re-posts to the
    node page's own save), and it keeps :data:`NODE_SETTINGS_CAPABILITY` as its gate — so a caller who
    was refused the old page is still refused at the redirect, never bounced through it.
    """
    declare()

    def _canonical(request: Request, node: str) -> str:
        """The canonical name *node* names in the caller's own view, or raise the console's 404."""
        canonical = _viewed_node(dashboard_page.request_source(request), readmodels.text(node))
        if canonical is None:
            # the console's own 404: a node the caller cannot see is the same answer as one that does
            # not exist (never a page that discloses one out of view).
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return canonical

    @router.get(NODE_SETTINGS_PATH, response_class=HTMLResponse, name="node-settings")
    async def node_settings_page(request: Request, node: str):
        """THE OLD PER-NODE SETTINGS URL — a 307 redirect to the node page (``/nodes/<node>``)."""
        return RedirectResponse(node_page_url(_canonical(request, node)), status_code=307)

    @router.post(NODE_SETTINGS_PATH,
                 dependencies=[Depends(session.csrf_protect_with(settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def node_settings_save(request: Request, node: str):
        """THE OLD per-node SAVE URL — a 307 redirect, method and body preserved, to the node page."""
        return RedirectResponse(node_page_url(_canonical(request, node)), status_code=307)

    # ------------------------------------------------------------------ the NODE page (/nodes/<node>)
    # The design: the FIRST tab is DEFAULT (the node's own settings and its node-scoped
    # extensions), then one tab per instance the node's block declares (that instance's settings and
    # its instance-scoped extensions). It is the node's ONE settings surface now — the old
    # per-node settings page above was retired to a redirect.

    def _render_page(request: Request, node_name: str, tab: int) -> HTMLResponse:
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        # a background switch that finished since the last render speaks on THIS one, as the notice
        _surface_switch_results(request, node_name)
        context = node_page_context(request, node_name, tab)
        context.update({
            "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, nodes_page.NODES_TITLE,
                                          target=node_name),
            "nav_groups": dashboard_page.nav_groups(registrar, roles,
                                                    current=nodes_page.NODES_PATH,
                                                    manager=permissions.manages_console(request)),
            "user": dashboard_page.identity_summary(request),
            "notice": actions_page.pop_notice(request),
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
            "cog_label": settings_page.COG_LABEL,
            "revision_field": settings_page.SETTINGS_REVISION_FIELD,
            "expert_field": settings_page.SETTINGS_EXPERT_FIELD,
            "yaml_field": settings_page.SETTINGS_YAML_FIELD,
        })
        return HTMLResponse(i18n.render(request, NODE_PAGE_TEMPLATE, **context),
                            headers={"Cache-Control": "no-store"})

    @router.get(NODE_PAGE_PATH, response_class=HTMLResponse, name="node-page")
    async def node_page(request: Request, node: str, tab: int = 0):
        requested = readmodels.text(node)
        if _viewed_node(dashboard_page.request_source(request), requested) is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return _render_page(request, requested, tab)

    @router.post(NODE_PAGE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def node_page_save(request: Request, node: str):
        requested = readmodels.text(node)
        canonical = _viewed_node(dashboard_page.request_source(request), requested)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        doc_key = _resolve_doc_key(request, requested) or requested
        return await save_node(request, doc_key, _node_page_back_url(request, canonical))

    @router.get(NODE_PAGE_SWITCH_STATE_PATH, name="node-switch-state")
    async def node_switch_state(request: Request, node: str):
        """Which of this node's extensions are switching right now — a READ, nothing written.

        The card whose switch is in flight polls this and reloads when its own job is gone, which is
        when the result notice is waiting on the page. It answers ONLY the running jobs (the finished
        ones are the render's to show once), and only for a node the operator may view.
        """
        requested = readmodels.text(node)
        if _viewed_node(dashboard_page.request_source(request), requested) is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        # A JOB ONLY COUNTS AS RUNNING WHILE A LIVE TASK RUNS IT: a task that died (cancelled at
        # shutdown, collected, or hung past the limit) is closed here, so the page's poll always ends
        # and the reload that follows shows what happened instead of blinking forever.
        extension_switch.reconcile(request.app.state, requested)
        return JSONResponse({"running": {job.state_key: job.action
                                         for job in extension_switch.running(request.app.state,
                                                                            requested)}},
                            headers={"Cache-Control": "no-store"})

    @router.post(NODE_PAGE_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def node_page_validate(request: Request, node: str):
        """The Expert Mode editor's live check — a JSON verdict, never a page, and nothing written.

        The posted text is the selected NODE's SLICE of ``nodes.yaml``, so it is checked the way a save
        checks it: merged back into the file on disk and validated as the WHOLE, with the fault located
        in the slice's own coordinates. A slice carrying any OTHER top-level key is refused exactly as
        the save would refuse it, so the check can never bless a slice the save rejects.
        """
        requested = readmodels.text(node)
        canonical = _viewed_node(dashboard_page.request_source(request), requested)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        doc_key = _resolve_doc_key(request, requested) or requested
        path = nodes_yaml_path(request)
        schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        text = form.get(settings_page.SETTINGS_YAML_FIELD)
        payload = "" if text is None else str(text)
        return JSONResponse(settings_page.validate_slice(request, payload, path, schema_files,
                                                         [doc_key]))

    @router.get(NODE_PAGE_HINTS_PATH)
    async def node_page_hints(request: Request, node: str):
        """The node page's completion hints — the schema tree, never the file and never a write.

        The page's OWN read gate answers here: the capability is declared on the route, and a node
        outside the caller's view is the console's 404 — the same pair the page itself applies, since
        the hints describe only the schema and a visible node's editor is what fetches them.
        """
        requested = readmodels.text(node)
        if _viewed_node(dashboard_page.request_source(request), requested) is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return nodes_hints(request)

    # ------------------------------------------------------ the extension CONFIG page (the cogwheel)
    def _canonical_extension(ext_name: str) -> str | None:
        """The schema key *ext_name* names, case-insensitively — or ``None`` (the console's 404)."""
        wanted = readmodels.text(ext_name).lower()
        for key in catalog.extension_scopes():
            if str(key).lower() == wanted:
                return str(key)
        return None

    def _render_extension(request: Request, node_name: str, ext_name: str,
                          tab: "int | None", scope: "str | None" = None) -> HTMLResponse:
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        base_url = node_extension_url(node_name, ext_name)
        # the switch's result (a background job that finished) speaks here as the notice
        _surface_switch_results(request, node_name)
        context = extension_page_context(request, node_name, ext_name, tab, scope)
        context.update({
            "title": NODE_SETTINGS_TITLE,
            "page_title": NODE_SETTINGS_TITLE,
            "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, nodes_page.NODES_TITLE,
                                          target=node_name),
            "nav_groups": dashboard_page.nav_groups(registrar, roles,
                                                    current=nodes_page.NODES_PATH,
                                                    manager=permissions.manages_console(request)),
            "user": dashboard_page.identity_summary(request),
            "notice": actions_page.pop_notice(request),
            "save_path": f"{base_url}?tab={context.get('tab_index', 0)}",
            "validate_path": _node_extension_validate_url(node_name, ext_name),
            "hints_path": _node_extension_hints_url(node_name, ext_name),
            "revision_field": settings_page.SETTINGS_REVISION_FIELD,
            "expert_field": settings_page.SETTINGS_EXPERT_FIELD,
            "yaml_field": settings_page.SETTINGS_YAML_FIELD,
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request),
            "no_schema_banner": settings_page.NO_SCHEMA_BANNER,
            "schema_invalid_banner": settings_page.SCHEMA_INVALID_BANNER,
        })
        return HTMLResponse(i18n.render(request, NODE_EXTENSION_TEMPLATE, **context),
                            headers={"Cache-Control": "no-store"})

    def _extension_node(request: Request, node: str) -> str:
        requested = readmodels.text(node)
        canonical = _viewed_node(dashboard_page.request_source(request), requested)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return requested

    @router.get(NODE_EXTENSION_PATH, response_class=HTMLResponse, name="node-extension")
    async def node_extension(request: Request, node: str, name: str, tab: "int | None" = None,
                             scope: "str | None" = None):
        """The extension's OWN configuration for ONE node — the cogwheel's destination.

        Without ``?tab=`` or ``?scope=`` the page opens on the first scope that already carries a block;
        the ``+ Add`` link names its SCOPE, so it lands on the scope that creates the block even when
        that scope is the only one with no block yet.
        """
        requested = _extension_node(request, node)
        canonical = _canonical_extension(name)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return _render_extension(request, requested, canonical, tab, scope)

    @router.post(NODE_EXTENSION_PATH,
                 dependencies=[Depends(session.csrf_protect_with(settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def node_extension_save(request: Request, node: str, name: str):
        requested = _extension_node(request, node)
        canonical = _canonical_extension(name)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        tab = readmodels.text(request.query_params.get("tab")) or "0"
        target = f"{node_extension_url(requested, canonical)}?tab={tab}"
        try:
            tab_index = int(tab)
        except ValueError:
            tab_index = 0
        return await save_extension(request, requested, canonical, target, tab=tab_index,
                                    scope=request.query_params.get("scope"))

    @router.post(NODE_EXTENSION_VALIDATE_PATH,
                 dependencies=[Depends(session.csrf_protect_with(settings_page.SETTINGS_FORM_MAX_FIELDS))])
    async def node_extension_validate(request: Request, node: str, name: str):
        """The Expert Mode editor's live check for ONE extension — a JSON verdict, nothing written."""
        requested = _extension_node(request, node)
        canonical = _canonical_extension(name)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        doc_key = _resolve_doc_key(request, requested) or requested
        path = nodes_yaml_path(request)
        schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
        form = await request.form(max_fields=settings_page.SETTINGS_FORM_MAX_FIELDS)
        text = form.get(settings_page.SETTINGS_YAML_FIELD)
        payload = "" if text is None else str(text)
        return JSONResponse(settings_page.validate_slice(request, payload, path, schema_files,
                                                         [doc_key]))

    @router.get(NODE_EXTENSION_HINTS_PATH)
    async def node_extension_hints(request: Request, node: str, name: str):
        """ONE extension page's completion hints — the schema tree, resolved exactly as its page is.

        A node outside the caller's view is the console's 404 and an extension the catalog does not
        declare is the same answer, so the URL a page carries cannot be replayed at another target.
        """
        _extension_node(request, node)
        if _canonical_extension(name) is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return nodes_hints(request)

    @router.get(NODE_PAGE_EXTENSION_REMOVE_PATH, response_class=HTMLResponse,
                name="node-extension-remove")
    async def node_extension_remove(request: Request, node: str, name: str, tab: int = 0):
        """The trashcan's target: the console's confirmation page for ONE extension block.

        It writes nothing. It DISPLAYS the block's own YAML and mints the removal acknowledgement for
        exactly that (node, scope, extension) at exactly that revision — the only place such a token
        exists — and its form posts the ``remove`` action back to the node page's own save. A scope
        that does not configure the extension has nothing to discard, so it answers a redirect to the
        tab rather than a dialog for an absent block.
        """
        requested = _extension_node(request, node)
        canonical = _canonical_extension(name)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        path = nodes_yaml_path(request)
        try:
            document = configgen.read_document(path)
        except configwrite.ConfigWriteError:
            document = None
        tab_index = max(0, tab)
        fallback = RedirectResponse(f"{node_page_url(requested)}?tab={tab_index}", status_code=303)
        doc_key = _document_node_key(document, requested) if isinstance(document, dict) else None
        if doc_key is None:
            return fallback
        scope = _scope_from_tab_index(document, doc_key, tab_index)
        if scope is _INVALID_SCOPE:
            return fallback
        block = configgen.value_at(document, _extension_base(doc_key, scope, canonical))
        if not isinstance(block, dict):
            return fallback
        scope_value = (NODE_PAGE_SCOPE_DEFAULT if scope is None
                       else NODE_PAGE_SCOPE_INSTANCE_PREFIX + str(scope))
        context = _extension_remove_dialog(request, requested, doc_key, canonical, scope,
                                           scope_value, tab_index, block, path)
        chrome = _extension_remove_chrome(request, requested)
        chrome["page_title"] = context["page_title"]
        # ``confirm.html`` reads the return path at the TOP level (its Cancel link and the form's
        # ``data-back``) and everything else off ``dialog``.
        return HTMLResponse(i18n.render(request, CONFIRM_TEMPLATE, **chrome,
                                        back=context["back"], dialog=context["dialog"]),
                            headers={"Cache-Control": "no-store"})

    return router
