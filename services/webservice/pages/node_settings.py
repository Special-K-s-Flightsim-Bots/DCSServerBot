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

import logging
import secrets
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.utils import configwrite

from .. import catalog, configgen, i18n, permissions, plugin_registry, readmodels, scopes, session
from . import actions as actions_page
from . import dashboard as dashboard_page
from . import nodes as nodes_page
from . import settings as settings_page

__all__ = [
    "NODE_SETTINGS_PATH", "NODE_SETTINGS_CAPABILITY", "NODE_SETTINGS_ROLES",
    "NODE_SETTINGS_TITLE", "NODE_SETTINGS_LEAD", "NODES_YAML_NAME", "NODES_SCHEMA_PATH",
    "NODE_PAGE_PATH", "NODE_PAGE_VALIDATE_PATH", "NODE_PAGE_TEMPLATE",
    "NODE_PAGE_SECTION_SETTINGS", "NODE_PAGE_SECTION_EXTENSIONS", "NODE_PAGE_EXTENSION_KIND",
    "NODE_PAGE_SCOPE_NODE", "NODE_PAGE_SCOPE_INSTANCE",
    "NODE_PAGE_SCOPE_FIELD", "NODE_PAGE_SCOPE_DEFAULT", "NODE_PAGE_SCOPE_INSTANCE_PREFIX",
    "NODE_PAGE_EXTENSION_UNCONFIGURED",
    "NODE_PAGE_EXTENSION_FIELD", "NODE_PAGE_EXTENSION_ACTION_FIELD",
    "NODE_PAGE_EXTENSION_ADD", "NODE_PAGE_EXTENSION_REMOVE",
    "NODE_PAGE_EXTENSION_ON", "NODE_PAGE_EXTENSION_OFF",
    "NODE_PAGE_EXTENSION_TURN_ON", "NODE_PAGE_EXTENSION_TURN_OFF", "NODE_PAGE_EXTENSION_KEEP",
    "NODE_PAGE_EXTENSION_DEFAULT_BADGE", "NODE_PAGE_EXTENSION_ENABLED", "NODE_PAGE_EXTENSION_DISABLED",
    "NODE_PAGE_EXTENSION_DISABLE", "NODE_PAGE_EXTENSION_ENABLE",
    "NODE_PAGE_EXTENSION_TURNED_OFF", "NODE_PAGE_EXTENSION_TURNED_ON",
    "NODE_PAGE_EXTENSION_REMOVE_WARNING", "NODE_PAGE_EXTENSION_ADDED", "NODE_PAGE_EXTENSION_REMOVED",
    "NODE_PAGE_EXTENSION_ADD_EXISTS", "NODE_PAGE_EXTENSION_REMOVE_ABSENT",
    "NODE_PAGE_EXTENSION_ALL_CONFIGURED",
    "NODE_PAGE_EXTENSION_WARN_FIELD", "NODE_PAGE_EXTENSION_ACK_FIELD",
    "SAVE_REFUSED_FOREIGN_SCOPE", "SAVE_REFUSED_UNKNOWN_SCOPE", "SAVE_REFUSED_NO_NODE_BLOCK",
    "SAVE_REFUSED_UNKNOWN_EXTENSION", "SAVE_REFUSED_EXTENSION_ACK",
    "mint_extension_ack", "validate_extension_ack", "spend_extension_ack", "_extension_ack_binding",
    "node_settings_control", "node_tree", "nodes_yaml_path",
    "node_page_url", "node_page_context", "save_node", "scope_save_tree",
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
            NODE_EXTENSION_PATH: NODE_SETTINGS_CAPABILITY,
            NODE_EXTENSION_VALIDATE_PATH: NODE_SETTINGS_CAPABILITY}


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


def _notice(request: Request, message: str, *, ok: bool) -> None:
    """Store the one-shot notice of a save — the console's own wording, translated at render."""
    request.session[actions_page.NOTICE_KEY] = {
        "message": message, "ok": ok, "audit": "", "audit_warning": False,
        "action": NODE_SETTINGS_TITLE, "revert": "", "revert_omitted": "",
        "refusal": True, "applies": "",
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

#: THE ACTIVE-SCOPE CARRIER. The tabs are a SERVER-SIDE selection (``?tab=<n>``), but a SAVE must know
#: which scope a form came from WITHOUT inferring it from the posted fields — the review's B3 proved
#: that inference writes a DEFAULT field from an instance tab (and the reverse). So the form carries
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
#: block — a node is added in Expert Mode, never as a side effect of saving a form (the review's B4).
SAVE_REFUSED_NO_NODE_BLOCK = i18n._(
    "No change was saved: this node has no block in nodes.yaml yet, so there is nothing to save. Add "
    "the node in Expert Mode first — nothing was written.")
#: the sentence over an extension section the selected scope does NOT configure. The section is still
#: SHOWN (every schema-known extension stays discoverable from the node page) but its controls are
#: disabled and not posted: the form never CREATES an extension block — that is Expert Mode work
#: (the review's B2, "no implicit block creation").
NODE_PAGE_EXTENSION_UNCONFIGURED = i18n._(
    "This extension is not configured for this scope yet. The form never creates it — add its block "
    "in Expert Mode, then it becomes editable here.")

# --------------------------------------------------------------- the extension card's ON/OFF switch
# THE CARD SET COMES FROM THE SCHEMA, THE CARD STATE FROM THE FILE. Every extension a scope's schema
# declares is shown as a card (``_extension_sections``); whether its block is PRESENT in that scope's
# ``extensions`` mapping of ``nodes.yaml`` is the card's ON/OFF state — exactly how a plugin tile reads
# its state from ``main.yaml``'s ``opt_plugins``. Turning a card ON writes that extension's EMPTY block
# into the scope (the shape the Settings area's "+ Add block" door writes); turning it OFF REMOVES the
# block. A removal is destructive when the block carries values, so the switch first shows an INLINE
# warning (``?warn_ext=<name>``) naming exactly what will be discarded — never a silent write. Both
# actions go through the SAME scoped save (:func:`save_node`) and the SAME whole-document patch, so the
# scope guard (a foreign node, instance or out-of-scope extension is refused with nothing written), the
# no-op rule (an already-on/off card changes nothing) and the byte-identical-elsewhere promise all hold
# unchanged.
NODE_PAGE_EXTENSION_FIELD = "_ext"
NODE_PAGE_EXTENSION_ACTION_FIELD = "_ext_action"
NODE_PAGE_EXTENSION_ADD = "add"
NODE_PAGE_EXTENSION_REMOVE = "remove"
#: THE TWO ACTIONS A DEFAULT EXTENSION'S SWITCH USES (see :func:`_extension_switch`). A DEFAULT
#: extension — one the runtime loads even with no ``nodes.yaml`` block (``DEFAULT_EXTENSIONS``, merged
#: by ``core/data/impl/serverimpl.py``'s ``load_extension``/``init_extensions``) — is NOT turned off by
#: removing its block: an absent block means "default on", so the extension would come straight back.
#: Its OFF therefore WRITES the value the loader reads to turn it off — ``enabled: false``
#: (``core/extension.py``: ``enabled`` = ``config.get('enabled', True)``) — into the correct scope's
#: block, and its ON writes ``enabled: true``. Neither action ever DELETES the block.
NODE_PAGE_EXTENSION_DISABLE = "disable"
NODE_PAGE_EXTENSION_ENABLE = "enable"
#: the query parameter the switch's turn-OFF link carries (``?warn_ext=<name>``): the warning step.
NODE_PAGE_EXTENSION_WARN_FIELD = "warn_ext"
#: THE ACKNOWLEDGEMENT the warning step MINTS and the confirm form posts back. The warning render (a
#: GET carrying ``?warn_ext=<name>``) shows the block's own contents AND mints a random token bound to
#: exactly the (node, scope, extension) it warned about; that token rides in the hidden field
#: :data:`NODE_PAGE_EXTENSION_ACK_FIELD` of the confirm form, and the ``remove`` save REFUSES with
#: nothing written unless it presents a token that matches THAT binding. This is what makes the
#: "never a silent write" claim a SERVER rule rather than a UI one: a value-carrying block cannot be
#: removed by a bare POST, only by one that presents an acknowledgement a warning produced (see
#: :func:`mint_extension_ack` / :func:`validate_extension_ack`).
NODE_PAGE_EXTENSION_ACK_FIELD = "_ext_ack"
#: the switch's two labels (the card's own STATE) and the two controls beside the warning.
NODE_PAGE_EXTENSION_ON = i18n._("Configured")
NODE_PAGE_EXTENSION_OFF = i18n._("Not configured")
NODE_PAGE_EXTENSION_TURN_ON = i18n._("Turn on")
NODE_PAGE_EXTENSION_TURN_OFF = i18n._("Turn off")
NODE_PAGE_EXTENSION_KEEP = i18n._("Keep it")
#: THE DEFAULT MARKER — the SAME one-word badge a default PLUGIN tile carries (reused verbatim, so
#: the two surfaces cannot drift and no second style is invented). A default extension shows it
#: whatever its block says, because it is part of the runtime's own set.
NODE_PAGE_EXTENSION_DEFAULT_BADGE = plugin_registry.DEFAULT_BADGE
#: THE TWO STATE WORDS A DEFAULT EXTENSION'S SWITCH SHOWS. A default extension's state is the
#: RUNTIME's (enabled/disabled), not the file's (configured/not configured): an unconfigured default
#: extension is ON, so "Configured"/"Not configured" would be the very lie this card fixes.
NODE_PAGE_EXTENSION_ENABLED = i18n._("Enabled")
NODE_PAGE_EXTENSION_DISABLED = i18n._("Disabled")
#: the notices a default extension's switch leaves (translated AND interpolated at the route).
NODE_PAGE_EXTENSION_TURNED_OFF = i18n._(
    "Turned off the default extension %(name)s for this scope — it will not load there.")
NODE_PAGE_EXTENSION_TURNED_ON = i18n._(
    "Turned on the default extension %(name)s for this scope.")
#: THE INLINE WARNING a card shows BEFORE its block is removed. The content it will discard (the block's
#: own YAML) is printed VERBATIM beneath this sentence — operator data is never translated.
NODE_PAGE_EXTENSION_REMOVE_WARNING = i18n._(
    "Turning this off REMOVES this extension's block from this scope. Everything it holds right now "
    "is discarded:")
#: the notices the switch's write leaves (translated AND interpolated at the route, like the Add-block
#: notices, because they carry the extension's name).
NODE_PAGE_EXTENSION_ADDED = i18n._(
    "Added the extension %(name)s to this scope as an empty block.")
NODE_PAGE_EXTENSION_REMOVED = i18n._("Removed the extension %(name)s from this scope.")
NODE_PAGE_EXTENSION_ADD_EXISTS = i18n._(
    "The extension %(name)s is already configured for this scope — nothing was added.")
NODE_PAGE_EXTENSION_REMOVE_ABSENT = i18n._(
    "The extension %(name)s is not configured for this scope — nothing was removed.")
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
#: it names. The warning step mints that acknowledgement; a bare POST (one that never rendered the
#: warning, one that raced the warning, or one minted for a DIFFERENT block) is refused here with
#: nothing written. This is the server-side half of "never a silent write".
SAVE_REFUSED_EXTENSION_ACK = i18n._(
    "Nothing was removed: this removal did not carry the acknowledgement the warning produces, so "
    "the extension's block was left exactly as it was. Open the extension's switch, read the warning "
    "that shows what will be discarded, and confirm from there.")


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

    LENGTH-PREFIXED, never a bare join (the review's delimiter nitpick). A plain ``"\\x1f".join`` is
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
    "This extension is not configured for this node yet, so there is nothing to edit here. Turn it "
    "on from the node page first.")
#: the sentence over an extension page's tabs. Unlike a plugin/service file there is no shared
#: ``DEFAULT`` block: each tab IS one scope's block of ``nodes.yaml``.
NODE_EXTENSION_SCOPE_NOTE = i18n._(
    "Each tab is this extension's block for one scope — the node's own block, then one per instance "
    "that configures it. Nothing is copied between them.")


def node_page_url(node_name: str) -> str:
    """The URL of *node_name*'s node page — the name as ONE percent-encoded path segment."""
    from urllib.parse import quote

    return f"/nodes/{quote(readmodels.text(node_name), safe='')}"


# --------------------------------------------------------- the extension CONFIGURATION destination
# THE COGWHEEL'S TARGET. A plugin's cogwheel opens the plugin's OWN configuration in the tabbed,
# schema-driven scoped view (``settings_scope.html``); an extension's cogwheel opens EXACTLY the same
# view, over the extension's blocks of ``nodes.yaml`` for ONE node — its own block plus one tab per
# instance of that node that configures it. There is no second form engine: the SAME
# ``_scope_fields.html`` control macro renders the fields, the SAME one write primitive saves them,
# and the fields are the SAME ``f.<node>.extensions.<Name>…`` names the node page always used.
NODE_EXTENSION_PATH = "/nodes/{node}/extensions/{name}"
NODE_EXTENSION_VALIDATE_PATH = "/actions/nodes/{node}/extensions/{name}/validate"
NODE_EXTENSION_TEMPLATE = "settings_scope.html"


def node_extension_url(node_name: str, ext_name: str) -> str:
    """The URL of ONE extension's configuration for *node_name* — each name one path segment."""
    from urllib.parse import quote

    return (f"/nodes/{quote(readmodels.text(node_name), safe='')}"
            f"/extensions/{quote(readmodels.text(ext_name), safe='')}")


def _node_extension_validate_url(node_name: str, ext_name: str) -> str:
    from urllib.parse import quote

    return (f"/actions/nodes/{quote(readmodels.text(node_name), safe='')}"
            f"/extensions/{quote(readmodels.text(ext_name), safe='')}/validate")


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


def _extension_switch(name: str, configured: bool, confirm: bool, tab_url: str,
                      csrf: str, revision: str, ack: str = "", *,
                      is_default: bool = False, enabled: bool = True) -> dict:
    """The ON/OFF switch row model for one extension card — the SAME shape a plugin tile's switch uses.

    A CONFIGURED extension is ON: its switch is a LINK into the inline warning step (``?warn_ext=``),
    the SAME two-step a DEFAULT plugin's switch-off uses. The confirm step (after opening the warning)
    is a FORM posting the ``remove`` action through this page's own save — and carrying the
    acknowledgement the warning minted (*ack*, :data:`NODE_PAGE_EXTENSION_ACK_FIELD`), so the save can
    verify the operator actually saw the warning before the block is removed. An UNCONFIGURED extension
    is OFF: its switch is a FORM posting the ``add`` action (writing the extension's EMPTY block). Both
    actions go through the SAME scoped save, so the scope guard, the no-op rule and the
    byte-identical-elsewhere promise all hold — the save route handles the switch and nothing else.

    A DEFAULT extension (*is_default*, a name in ``DEFAULT_EXTENSIONS``) is a THIRD case: it is ON
    unless its block says ``enabled: false`` (``core/extension.py``), so removing its block would NOT
    turn it off — an absent block means "default on". Its switch therefore WRITES the value the loader
    reads: OFF posts the ``disable`` action (``enabled: false``), ON posts the ``enable`` action
    (``enabled: true``), both through the SAME scoped save. Neither deletes the block, so neither needs
    the removal warning (nothing is discarded), and the STATE WORD follows *enabled* — the runtime's
    answer — never whether the block is present.
    """
    from urllib.parse import quote

    common = [(session.CSRF_FIELD, csrf),
              (settings_page.SETTINGS_REVISION_FIELD, revision),
              (NODE_PAGE_SCOPE_FIELD, _switch_scope_from_tab(tab_url)),
              (NODE_PAGE_EXTENSION_FIELD, name)]
    if is_default:
        action = NODE_PAGE_EXTENSION_DISABLE if enabled else NODE_PAGE_EXTENSION_ENABLE
        return {"type": "form", "action": tab_url,
                "fields": common + [(NODE_PAGE_EXTENSION_ACTION_FIELD, action)],
                "label": NODE_PAGE_EXTENSION_ENABLED if enabled else NODE_PAGE_EXTENSION_DISABLED,
                "title": NODE_PAGE_EXTENSION_TURN_OFF if enabled else NODE_PAGE_EXTENSION_TURN_ON,
                "on": bool(enabled)}
    if confirm:
        return {"type": "form", "action": tab_url,
                "fields": common + [(NODE_PAGE_EXTENSION_ACTION_FIELD, NODE_PAGE_EXTENSION_REMOVE),
                                    (NODE_PAGE_EXTENSION_ACK_FIELD, ack)],
                "label": NODE_PAGE_EXTENSION_TURN_OFF, "title": NODE_PAGE_EXTENSION_TURN_OFF,
                "on": False, "cancel_url": tab_url, "cancel_label": NODE_PAGE_EXTENSION_KEEP}
    if configured:
        return {"type": "link",
                "href": f"{tab_url}&{NODE_PAGE_EXTENSION_WARN_FIELD}={quote(name, safe='')}",
                "label": NODE_PAGE_EXTENSION_ON, "title": NODE_PAGE_EXTENSION_TURN_OFF, "on": True}
    return {"type": "form", "action": tab_url,
            "fields": common + [(NODE_PAGE_EXTENSION_ACTION_FIELD, NODE_PAGE_EXTENSION_ADD)],
            "label": NODE_PAGE_EXTENSION_OFF, "title": NODE_PAGE_EXTENSION_TURN_ON, "on": False}


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



def _node_page_validate_url(node_name: str) -> str:
    """The URL of *node_name*'s Expert Mode validator — one percent-encoded path segment."""
    from urllib.parse import quote

    return f"/actions/nodes/{quote(readmodels.text(node_name), safe='')}/validate"


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
    the form never CREATES an extension block (the review's B2).
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

    A NON-default extension is loaded only when its block is present in the scope's own ``extensions``
    map, so its state is simply *configured*. A DEFAULT extension is loaded even with no block, so it is
    ON unless a block explicitly says ``enabled: false``; on an INSTANCE tab the instance's own block
    wins and otherwise the NODE block is inherited (``load_extension`` merges the node block UNDER the
    instance one — ``core/data/impl/serverimpl.py``), so *inherited_enabled* is the node block's answer
    (``None`` for the node-wide tab, which has no parent).
    """
    if not is_default:
        return configured
    if block_enabled is not None:
        return block_enabled
    if inherited_enabled is not None:
        return inherited_enabled
    return True


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


def _add_extension_block(document, base: tuple) -> None:
    """Create the EMPTY block at *base* in *document*, making any missing intermediate maps.

    This is the write a card's turn-ON performs: the extension's block is created EMPTY (the same shape
    the Settings area's "+ Add block" door writes) and the schema validation on the write decides whether
    that empty shape is a valid one. A block that already exists is left EXACTLY as it is — an already-on
    card's switch is a no-op, never a replacement (which would discard its values).

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
        node[last] = CommentedMap()
    elif not isinstance(existing, dict):
        raise configwrite.ConfigWriteError(
            "This extension's block was not added: this scope already holds a value there that is "
            "not a mapping, so adding the block would replace it.")


def _remove_extension_block(document, base: tuple) -> None:
    """Delete exactly the block at *base* in *document* — the write a card's turn-OFF performs.

    Only the named key is removed; every sibling (every other extension, every other scope, every other
    node) is left untouched, so the whole-document patch sees a change to exactly that one key. An absent
    block is a no-op (an already-off card's switch changes nothing).
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
    """Set ``enabled`` in the block at *base*, creating the block (and intermediates) when absent.

    This is the write a DEFAULT extension's switch performs: ``enabled: false`` turns the extension off
    for that scope and ``enabled: true`` turns it back on — the value the loader reads
    (``core/extension.py``: ``enabled`` = ``config.get('enabled', True)``). It NEVER removes the block:
    an absent block means "default on", so a removal would silently RE-ENABLE the extension. The block's
    other keys are left exactly as they were (only ``enabled`` is written), and the creation is
    shape-conservative — :func:`_add_extension_block` refuses a non-mapping slot before anything is
    written, exactly as the turn-ON switch does.
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
    # THE SWITCH's warning step: ``?warn_ext=<name>`` names the ONE card whose removal was asked for, so
    # its card renders the inline warning (and the confirm form) instead of the plain turn-off link.
    warn_ext = readmodels.text(request.query_params.get(NODE_PAGE_EXTENSION_WARN_FIELD)) or ""
    csrf = session.get_csrf_token(request)
    revision = configwrite.config_revision(path)
    node_url = node_page_url(node_name)
    tab_url = f"{node_url}?tab={tab_index}"
    descriptions, ext_scopes = _extension_catalog()
    # THE CARD SET IS SPLIT, AND DEFAULT EXTENSIONS ARE NEVER "NOT CONFIGURED". A CONFIGURED extension
    # is a TILE of its own (with its cogwheel and a TRASHCAN into the SAME acknowledged removal path).
    # An extension the scope does NOT configure is a CHOICE inside the ONE "+ Add" tile — UNLESS it is
    # a DEFAULT extension (a name in ``DEFAULT_EXTENSIONS``): that one RUNS regardless of the file, so
    # it is shown as a TILE too — ON, wearing the SAME "default" badge a default plugin tile wears, and
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
        warn_url = f"{tab_url}&{NODE_PAGE_EXTENSION_WARN_FIELD}={quote(name, safe='')}"
        if not is_default and not section["configured"]:
            # an ADDABLE extension: a row in the "+ Add" tile. Its switch is the SAME add form the
            # (formerly separate) OFF card carried — the SAME save, the SAME scope guard, nothing new.
            extension_options.append({
                "name": name,
                "description": description,
                "scope_label": scope_label,
                "switch": _extension_switch(name, False, False, tab_url, csrf, revision, ""),
            })
            continue
        block_enabled = _block_enabled(document, section["base"])
        inherited_enabled = (_block_enabled(document, (key or node_name, "extensions", name))
                             if instance is not None else None)
        enabled = _extension_enabled(is_default, section["configured"], block_enabled,
                                     inherited_enabled)
        view = scopes.scope_view(section["fields"], document)
        # A DEFAULT extension's OFF is the ``disable`` write, not a removal — so no warning step and no
        # acknowledgement (nothing is discarded); a NON-default configured extension keeps the SAME
        # acknowledged removal the trashcan uses.
        confirm = bool(not is_default and warn_ext == name)
        discard = ""
        ack = ""
        if confirm:
            block = configgen.value_at(document, section["base"])
            discard = configwrite.dump_config(block) if block is not None else ""
            # THE ACKNOWLEDGEMENT is minted HERE, by the render that DISPLAYED this block's contents —
            # so it can only exist as a consequence of the warning. It is stamped with the file's
            # revision the warning displayed, so a token minted BEFORE a change cannot authorise a
            # write AFTER it. The confirm form carries it back and the remove save refuses without it
            # (see _save_extension_switch).
            ack = mint_extension_ack(request, key or node_name, scope_value, name, revision)
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
            "confirm": confirm,
            "discard": discard,
            # THE COGWHEEL — the same destination a plugin's cogwheel opens: the extension's OWN
            # configuration page for this node. A DEFAULT extension with no schema fields (``Cloud``)
            # has no configuration page, so it carries no cogwheel.
            "cog_url": node_extension_url(node_name, name) if section["fields"] else "",
            "switch": _extension_switch(name, section["configured"], confirm, tab_url, csrf, revision,
                                        ack, is_default=is_default, enabled=enabled),
            # THE TRASHCAN — a NON-default configured extension's acknowledged removal. A default
            # extension has NO trashcan: removing its block would RE-ENABLE it (an absent block means
            # "default on"), so offering a removal would imply the opposite of what it does.
            "trash_url": "" if is_default else warn_url,
            "trash_title": NODE_PAGE_EXTENSION_TURN_OFF,
            "warn_url": warn_url,
            "keep_url": tab_url,
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
            configwrite.validate_against_schema(configwrite.read_config_text(path),
                                                list(schema_files), path=path)
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
        "extension_remove_warning": NODE_PAGE_EXTENSION_REMOVE_WARNING,
        "extension_default_badge": NODE_PAGE_EXTENSION_DEFAULT_BADGE,
        "extension_on": NODE_PAGE_EXTENSION_ON,
        "extension_off": NODE_PAGE_EXTENSION_OFF,
        "extension_turn_on": NODE_PAGE_EXTENSION_TURN_ON,
        "extension_turn_off": NODE_PAGE_EXTENSION_TURN_OFF,
        "extension_keep": NODE_PAGE_EXTENSION_KEEP,
        "yaml": yaml_text,
        "readonly": readonly or document is None,
        "parse_error": parse_error,
        "schema_error": schema_error,
        "schema_invalid_banner": settings_page.SCHEMA_INVALID_BANNER,
        "slice_note": settings_page.SCOPE_SLICE_NOTE,
        "revision": configwrite.config_revision(path),
        "save_path": f"{node_page_url(node_name)}?tab={tab_index}",
        "validate_path": _node_page_validate_url(node_name),
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
    """The ONE body for a card's turn-ON / turn-OFF — the same scoped save, a block-shaped mutate.

    The scope is CARRIED (:func:`_resolve_scope`), never inferred; a carrier naming no tab of this node
    is refused. The posted extension name must be one the SELECTED scope's schema declares
    (:func:`_scope_extension_names`) — a foreign node or instance (through the scope) or an out-of-scope
    name is refused with NOTHING written. An ``add`` writes the extension's EMPTY block; a ``remove``
    deletes exactly that block. Both go through :func:`configwrite.patch_config_file`, so the schema
    validation, the ``.bak``, the revision guard and the no-op rule are the SAME as any other save.
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
    # THE ``disable``/``enable`` ACTIONS ARE FOR DEFAULT EXTENSIONS ONLY. A default extension runs even
    # with no block, so it is switched off by WRITING ``enabled: false`` — never by removing its block
    # (an absent block means "default on", and the extension would return). A NON-default extension is
    # switched off by removing its block (the ``remove`` action); writing ``enabled: false`` into a
    # block it did not configure would be a different, unintended write, so the two actions are refused
    # for it here rather than guessed at.
    if (action in (NODE_PAGE_EXTENSION_DISABLE, NODE_PAGE_EXTENSION_ENABLE)
            and ext_name.lower() not in catalog.default_extensions()):
        _notice(request, SAVE_REFUSED_UNKNOWN_EXTENSION, ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    # THE REMOVAL'S ACKNOWLEDGEMENT (the review's F1): a ``remove`` must present the token the warning
    # step minted for EXACTLY this (node, scope, extension), AND that token must have been minted under
    # the file's CURRENT revision (the review's replay fix). Absent, stale (a newer warning replaced
    # it), minted for a different block, or minted before the file changed -> REFUSED here, before the
    # patch runs, so nothing is written and the refusal says so. An ``add`` needs no acknowledgement —
    # it writes an empty block and discards nothing.
    scope_value = (NODE_PAGE_SCOPE_DEFAULT if scope is None
                   else NODE_PAGE_SCOPE_INSTANCE_PREFIX + str(scope))
    if action == NODE_PAGE_EXTENSION_REMOVE:
        ack = readmodels.text(form.get(NODE_PAGE_EXTENSION_ACK_FIELD))
        if not validate_extension_ack(request, doc_key, scope_value, ext_name, ack,
                                      configwrite.config_revision(path)):
            _notice(request, SAVE_REFUSED_EXTENSION_ACK, ok=False)
            return RedirectResponse(redirect_to, status_code=303)
    revision = readmodels.text(form.get(settings_page.SETTINGS_REVISION_FIELD)) or None
    schema_files = list(configgen.schema_files_for(NODES_SCHEMA_PATH))
    base = _extension_base(doc_key, scope, ext_name)
    if action == NODE_PAGE_EXTENSION_ADD:
        mutate = lambda document: _add_extension_block(document, base)
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
        detail = settings_page._clean_detail(ex.errors) or str(ex)
        _notice(request, f"{settings_page.REFUSED_BANNER} {detail}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    except configwrite.ConfigWriteError as ex:
        _notice(request, f"{settings_page.REFUSED_BANNER} {ex}", ok=False)
        return RedirectResponse(redirect_to, status_code=303)
    # THE ACKNOWLEDGEMENT IS SPENT — a ONE-TIME token (the review's replay fix): once a removal has
    # been accepted, delete the store entry so the same token can never authorise a SECOND removal.
    if action == NODE_PAGE_EXTENSION_REMOVE:
        spend_extension_ack(request, doc_key, scope_value, ext_name)
    if result.changed:
        msgid = {NODE_PAGE_EXTENSION_ADD: NODE_PAGE_EXTENSION_ADDED,
                 NODE_PAGE_EXTENSION_REMOVE: NODE_PAGE_EXTENSION_REMOVED,
                 NODE_PAGE_EXTENSION_DISABLE: NODE_PAGE_EXTENSION_TURNED_OFF,
                 NODE_PAGE_EXTENSION_ENABLE: NODE_PAGE_EXTENSION_TURNED_ON}[action]
        _notice(request, i18n.translate(request, msgid, name=ext_name), ok=True)
    elif action in (NODE_PAGE_EXTENSION_ADD, NODE_PAGE_EXTENSION_REMOVE):
        msgid = (NODE_PAGE_EXTENSION_ADD_EXISTS if action == NODE_PAGE_EXTENSION_ADD
                 else NODE_PAGE_EXTENSION_REMOVE_ABSENT)
        _notice(request, i18n.translate(request, msgid, name=ext_name), ok=True)
    else:
        # a ``disable``/``enable`` that changed nothing: the scope already held the value, so the file
        # is byte-identical — say so, the SAME sentence every no-op save leaves.
        _notice(request, i18n.translate(request, settings_page.SAVED_NO_CHANGE), ok=True)
    return RedirectResponse(redirect_to, status_code=303)


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

    # B4: the node must HAVE a block. A node with none renders read-only; its save door REFUSES rather
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
    else:
        message = settings_page.SAVED_NO_CHANGE
    _notice(request, message, ok=True)
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


def extension_page_context(request: Request, node_name: str, ext_name: str, tab_index: int = 0) -> dict:
    """Everything ``settings_scope.html`` reads for ONE extension's configuration on ONE node.

    Returns the SAME shape the plugin/service cogwheel view (:func:`settings.scoped_context`) does, so
    the two surfaces render through the ONE template and the ONE control macro. A ``nodes.yaml`` that
    does not parse, a node with no block, or an extension the node does not configure renders READ-ONLY
    with the honest reason rather than a broken form.
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
        document if isinstance(document, dict) else {}, ext_name, node_names)
    visible = scopes.scopes_for_node(scope_list, key) if key is not None else scope_list
    if not visible:
        readonly = True
        parse_error = NODE_EXTENSION_UNCONFIGURED

    base_url = node_extension_url(node_name, ext_name)
    tab_index = max(0, min(tab_index, max(0, len(visible) - 1)))
    tabs = [{"label": scope["label"], "kind": scope["kind"], "active": index == tab_index, "note": "",
             "url": f"{base_url}?tab={index}"} for index, scope in enumerate(visible)]
    node_choices = [{"name": name, "url": f"{node_extension_url(name, ext_name)}?tab=0",
                     "active": name == (key or node_name)} for name in scopes.node_options(scope_list)]
    active = visible[tab_index] if visible else {"fields": [], "base": (), "kind": "node"}
    view = scopes.scope_view(active.get("fields", []), document)
    displays = {field.name: scopes.display_name(field, active.get("base", ()))
                for field in configgen.flatten(view.root)}
    slice_keys = scopes.scope_keys(visible) if visible else []
    # THE SHOW/HIDE MODEL for this tab's body + the removal acknowledgement (see ``save_extension``).
    vis = settings_page.visibility_context(view.root, view.root, document)
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
            configwrite.validate_against_schema(configwrite.read_config_text(path),
                                                list(schema_files), path=path)
        except configwrite.ConfigValidationError as ex:
            schema_error = settings_page._clean_detail(ex.errors) or str(ex)
        except configwrite.ConfigWriteError:
            pass

    descriptions, _scopes = _extension_catalog()
    return {
        "scoped": True,
        "ext_name": ext_name,
        "tabs": tabs,
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
        "removable": vis["removable"],
        "remove_ack": remove_ack,
        "remove_ack_field": vis["remove_ack_field"],
        "remove_companion": vis["remove_companion"],
        "scope_note": i18n.translate(request, NODE_EXTENSION_SCOPE_NOTE),
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


def _foreign_extension_field(form, doc_key: str) -> str | None:
    """The first posted ``f.`` field whose TOP-LEVEL token is not *doc_key* — or ``None``.

    The extension page edits ONE node's blocks; every ``f.`` field it renders begins with that node's
    key (``f.<node>.extensions.<Name>…`` or ``f.<node>.instances.<inst>.extensions.<Name>…``). A field
    naming any other top-level key — a sibling NODE — is refused, never written.
    """
    multi = getattr(form, "multi_items", None)
    items = list(multi()) if callable(multi) else []
    for name, _value in items:
        if not isinstance(name, str) or not name.startswith("f."):
            continue
        token = configgen.decode_token(name[2:].split(".", 1)[0])
        if token in (configgen.REMOVED_COMPANION, configgen.REMOVE_ACK_COMPANION):
            continue              # the removal list / its acknowledgement: guarded by the removal rule
        if token != doc_key:
            return name
    return None


async def save_extension(request: Request, node_name: str, ext_name: str,
                         redirect_to: str) -> RedirectResponse:
    """The ONE save body for an extension's configuration on ONE node — the SAME write primitive.

    A ``nodes.yaml`` is written as a WHOLE-DOCUMENT patch. The plain form's fields are applied against
    the extension's schema tree (:func:`scopes.build_extension_scopes`), guarded so only the selected
    NODE's token may be written. The Expert Mode door merges ONLY that node's slice back. Every other
    node, scope and block is left byte-identical, and the no-op rule holds — because it IS the node
    page's save with a different resolve tree.
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
    _scopes, save_tree = scopes.build_extension_scopes(existing, ext_name, node_names)
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
            if _foreign_extension_field(form, doc_key) is not None:
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
                configgen.apply_form(document, form, save_tree)

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
    else:
        message = settings_page.SAVED_NO_CHANGE
    _notice(request, message, ok=True)
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
        return await save_node(request, doc_key, node_page_url(canonical))

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

    # ------------------------------------------------------ the extension CONFIG page (the cogwheel)
    def _canonical_extension(ext_name: str) -> str | None:
        """The schema key *ext_name* names, case-insensitively — or ``None`` (the console's 404)."""
        wanted = readmodels.text(ext_name).lower()
        for key in catalog.extension_scopes():
            if str(key).lower() == wanted:
                return str(key)
        return None

    def _render_extension(request: Request, node_name: str, ext_name: str, tab: int) -> HTMLResponse:
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        base_url = node_extension_url(node_name, ext_name)
        context = extension_page_context(request, node_name, ext_name, tab)
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
            "save_path": f"{base_url}?tab={tab}",
            "validate_path": _node_extension_validate_url(node_name, ext_name),
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
    async def node_extension(request: Request, node: str, name: str, tab: int = 0):
        """The extension's OWN configuration for ONE node — the cogwheel's destination."""
        requested = _extension_node(request, node)
        canonical = _canonical_extension(name)
        if canonical is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
        return _render_extension(request, requested, canonical, tab)

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
        return await save_extension(request, requested, canonical, target)

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

    return router
