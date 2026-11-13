"""The Access page at ``/access`` — which role reaches which capability on the WEBSITE.

WHAT IT EDITS. A capability's requirement is declared in code (:mod:`services.webservice.permissions`)
and this page turns that ONE axis into a stored block of ``config/services/webservice.yaml``, so an
Admin can extend and narrow a requirement without a code change::

    DEFAULT:
      access:
        music.write:
          add: [Media Manager]
          remove: [DCS Admin]
        music.control:
          manager: true

The block EXTENDS and NARROWS the declaration and never replaces it; the code keeps declaring the
shipped behaviour (:func:`permissions.base_declaration`) and the block layers on top of that base
copy. Where the block lives — ``DEFAULT`` — is the service's own model: ``Service.get_config``
returns the ``DEFAULT`` block, so a key beside it would be read by nothing.

WEBSITE ONLY. This matrix decides which role reaches which control in the CONSOLE. The bot's Discord
commands keep their own role decorators, so a role narrowed here can still act in Discord — the page
says so in words, and no decorator path reads this block (a test pins that).

WHAT THE MATRIX CANNOT SAY. Three cells are structural rather than choices, each refused by the
engine AND drawn non-editable here:

* ``Admin`` is on every capability of the shipped console and is never removed (its column is a
  lock, not a checkbox). Where a declaration does NOT carry Admin, the row has no lock to draw: its
  Admin cell is a dash, and the posted marker grants nothing — a save can never add Admin to a
  capability that was not declared with it (``tests/test_webui_access_matrix.py`` pins that every
  declared capability carries Admin, which is the invariant behind the column);
* a ``managed_by`` scope grant exists only where a server can be named
  (:data:`permissions.MANAGER_SCOPE_NEVER` is the DATA: a machine, an installation-wide setting or
  report, a server's creation or removal, and this page itself have no server for it to narrow);
* this page's own pair (``access.view`` / ``access.write``) is locked outright — it is what the page
  protects.

APPLIED ON THE NEXT REQUEST. The gate, the sidebar and the tab strips read ``permissions.allows``
per call, so a save is live as soon as it lands: the page applies the candidate to the registry and
writes the file, and nothing is cached that would need clearing. A save that would leave no role able
to open this page is refused before anything is written.

A STALE FORM IS REFUSED. The form carries the config file's revision (``FIELD_REVISION``), and the
write presents it as ``expect_revision``: a page rendered before another save landed is refused with
its own sentence and the file keeps the other save, instead of silently overwriting it.

A REFUSED SAVE IS AUDITED AND LOGGED. One audit entry per attempt — naming the refusal kind and the
capability/role it carried — and the first refusal is a WARNING line, so a refused escalation leaves
a trace an operator can find after the fact.

A PART OF THE BLOCK IT CANNOT READ IS REPORTED, NEVER SWALLOWED. The block applies as far as it can
be read; a skipped shape (``access: "junk"``, an entry that is not a mapping, an unknown key inside
one, an ``add`` that names nothing) is logged at startup AND listed on this page, because an operator
whose hand-edit was ignored would otherwise see the shipped matrix and no hint at all.

ONLY ONE PROCESS. The block is applied to the capability table of the running process, at install and
on every save. A capability a plugin declares LATER in the same process (after ``install_shell``) is
not overridden until the next install (see ``DEVELOPER.md``).

WORKS WITH JAVASCRIPT OFF. The form posts the WHOLE matrix (every granted cell, the locked Admin
marker of every row and every ``managed_by`` grant); the server computes the delta against the base
and writes only what changed. The filter and "show changes only" are a GET form and a link, so they
work without a script too; ``static/access.js`` only adds a live change bar and a live filter.
"""
from __future__ import annotations

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.utils import configwrite

from .. import i18n, mount, permissions, session
from ..auth import local as local_auth
from ..registry import NavItem
from . import actions as actions_page
from . import dashboard as dashboard_page

__all__ = [
    "OWNER", "ACCESS_PATH", "SAVE_PATH", "CAPABILITY_VIEW", "CAPABILITY_WRITE", "PAGE_ROLES",
    "STRUCTURAL", "PAGE_CAPABILITY", "TEMPLATE", "STYLE", "SCRIPT", "NAV_LABEL", "PAGE_TITLE",
    "PAGE_LEAD", "NAV_GROUP", "GROUPS", "DESCRIPTIONS", "OVERRIDE_KEY", "CONFIG_BLOCK",
    "CONFIG_RELATIVE", "FIELD_CELL", "FIELD_ADMIN", "FIELD_MANAGER", "FIELD_REVISION",
    "PAIR_SEPARATOR", "ENTRY_KEYS",
    "SHAPE_BLOCK", "SHAPE_ENTRY", "SHAPE_KEY", "SHAPE_VALUE", "SHAPE_SENTENCES",
    "SHAPE_IGNORED_LEAD", "ShapeRefusal", "shape_sentence",
    "QUERY_FILTER", "QUERY_CHANGES", "REFUSAL_SENTENCES", "SAVE_REFUSED_STALE",
    "AUDIT_UPDATED", "AUDIT_REFUSED",
    "role_model", "override_entries", "config_path_for", "schema_files", "change_lines",
    "entries_from_candidate", "candidate_from_form", "matrix_rows", "access_context",
    "apply_from_config", "save_entries", "refusal_sentence", "capabilities", "nav_items",
    "add_routes",
]

log = logging.getLogger(__name__)

#: the registrar owner of these paths
OWNER = "access"

ACCESS_PATH = "/access"
SAVE_PATH = "/access/save"

CAPABILITY_VIEW = "access.view"
CAPABILITY_WRITE = "access.write"
#: Admin only, and no scope grant: this page decides who reaches everything else, so it is not
#: something a server's manager may open.
PAGE_ROLES: tuple[str, ...] = ("Admin",)
#: the pair the page protects: neither row can be edited, and the save refuses a matrix that touches
#: either one (they are the two capabilities that make the page reachable and writable at all).
STRUCTURAL: tuple[str, ...] = (CAPABILITY_VIEW, CAPABILITY_WRITE)
#: the capability a save must leave reachable by at least one role — the lockout rule
PAGE_CAPABILITY = CAPABILITY_VIEW

TEMPLATE = "access.html"
#: the page's own sheet — never ``shell.css``
STYLE = "access.css"
#: the progressive enhancement (a live change bar and filter); the page works without it
SCRIPT = "access.js"

NAV_LABEL = i18n._("Access")
PAGE_TITLE = i18n._("Access")
PAGE_LEAD = i18n._("Which role reaches which control on the website. Admin keeps everything and "
                   "cannot be reduced here.")
#: the sidebar group this page joins — one of the console's own ``NAV_GROUPS`` names
NAV_GROUP = "Administration"
NAV_GLYPH = "key"
#: the same group as a CRUMB word (a marked msgid, translated at render)
NAV_GROUP_LABEL = i18n._("Administration")

#: WHERE A CAPABILITY SITS IN THE MATRIX: one row per capability, grouped by the console's OWN nav
#: groups (:data:`services.webservice.registry.NAV_GROUPS`), in the sidebar's order. A page capability
#: lands in the group its nav item declares — pinned by
#: ``tests/test_webui_access_matrix.py`` — and a write or tab capability lands with the page it is
#: reached from. A capability the table does not name still renders, under :data:`OTHER_GROUP`, so a
#: page added later is visible instead of silently missing; the same test pins that every declared
#: capability has a group AND a one-line description here.
OTHER_GROUP = i18n._("Other")

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (i18n._("Operations"), (
        "dashboard.view", "servers.view",
        "servers.startup", "servers.start", "servers.stop", "servers.restart", "servers.shutdown",
        "servers.maintenance", "servers.clear_maintenance", "servers.message", "servers.add",
        "servers.delete", "servers.config", "servers.config.dcs", "servers.config.coalitions",
        "servers.config.channels",
        "scheduler.view", "scheduler.write", "scheduler.expert",
        "missions.view", "missions.load", "missions.activate", "missions.pause", "missions.unpause",
        "missions.add", "missions.upload", "missions.delete", "missions.reorder",
        "players.view", "players.kick", "players.mute", "players.ban", "players.edit_ban",
        "players.unban", "players.watch", "players.punish", "players.message", "players.screenshot",
        "bans.view",
    )),
    (i18n._("Media"), (
        "music.view", "music.write", "music.upload", "music.control",
    )),
    (i18n._("Cluster"), (
        "nodes.view", "nodes.online", "nodes.offline", "nodes.restart", "nodes.shutdown",
        "nodes.upgrade", "nodes.dcs_update", "nodes.settings", "instances.view",
        "mods.view", "mods.manage", "audit.view",
    )),
    (i18n._("Data"), (
        "statistics.view", "logs.view", "presets.view", "presets.write",
    )),
    (i18n._("Administration"), (
        "settings.view", "settings.write", "settings.file.view", "settings.file.write",
        "site.view", "site.write", "site.structure", "site.expert",
        CAPABILITY_VIEW, CAPABILITY_WRITE,
    )),
)

#: ONE LINE per capability: what it lets the holder do, in the operator's words. The mock's second
#: cell column, and the only place the page explains a capability.
DESCRIPTIONS: dict[str, str] = {
    "dashboard.view": i18n._("the dashboard: live servers, load and the log panel"),
    "servers.view": i18n._("the server list and one server's page"),
    "servers.startup": i18n._("start a server, including its dependent instances"),
    "servers.start": i18n._("start a stopped server"),
    "servers.stop": i18n._("stop a running server"),
    "servers.restart": i18n._("restart a server — players are dropped"),
    "servers.shutdown": i18n._("take a server down and keep it down"),
    "servers.maintenance": i18n._("flag a server for maintenance"),
    "servers.clear_maintenance": i18n._("clear a server's maintenance flag"),
    "servers.message": i18n._("chat and popup to the players of a server"),
    "servers.add": i18n._("create a new server instance"),
    "servers.delete": i18n._("remove a server instance"),
    "servers.config": i18n._("the server's Configuration tab"),
    "servers.config.dcs": i18n._("the server's own DCS settings"),
    "servers.config.coalitions": i18n._("coalition passwords and slots"),
    "servers.config.channels": i18n._("the server's Discord channels"),
    "scheduler.view": i18n._("a server's schedule"),
    "scheduler.write": i18n._("create and edit a server's schedule"),
    "scheduler.expert": i18n._("Expert Mode and the shared scheduler default"),
    "site.view": i18n._("the public site's settings page, for the sites you hold"),
    "site.write": i18n._("edit the content of a site you hold: branding, character, pages, sections, "
                         "metrics, language and the preset"),
    "site.structure": i18n._("a site's address, its role and its on/off switch — the console reloads"),
    "site.expert": i18n._("write a site's expert CSS sheet"),
    "missions.view": i18n._("the mission list of a server"),
    "missions.load": i18n._("load a mission onto a server"),
    "missions.activate": i18n._("activate a mission and restart the server into it"),
    "missions.pause": i18n._("pause the running mission"),
    "missions.unpause": i18n._("unpause the running mission"),
    "missions.add": i18n._("add a mission from the library"),
    "missions.upload": i18n._("upload a mission file"),
    "missions.delete": i18n._("delete a mission"),
    "missions.reorder": i18n._("reorder a server's mission list"),
    "players.view": i18n._("the player directory and one player's page"),
    "players.kick": i18n._("kick a player right now"),
    "players.mute": i18n._("mute a player right now"),
    "players.ban": i18n._("ban a player on the servers you hold"),
    "players.edit_ban": i18n._("edit an existing ban"),
    "players.unban": i18n._("lift a ban"),
    "players.watch": i18n._("keep a player on the watch list"),
    "players.punish": i18n._("give penalty points"),
    "players.message": i18n._("message a player on Discord"),
    "players.screenshot": i18n._("take a screenshot on a server"),
    "bans.view": i18n._("the console's ban list"),
    "music.view": i18n._("playlists, the library and this server's radios"),
    "music.write": i18n._("create, edit, reorder and delete playlists"),
    "music.upload": i18n._("write files into the music directory"),
    "music.control": i18n._("play, stop, skip, mode and frequency of a running radio"),
    "nodes.view": i18n._("the node list and one node's page"),
    "nodes.online": i18n._("bring a node back online"),
    "nodes.offline": i18n._("take a node offline"),
    "nodes.restart": i18n._("restart the bot on a node"),
    "nodes.shutdown": i18n._("shut a node's machine down"),
    "nodes.upgrade": i18n._("upgrade the bot on a node"),
    "nodes.dcs_update": i18n._("update, roll back or switch the DCS branch of a node"),
    "nodes.settings": i18n._("the per-node settings page"),
    "instances.view": i18n._("the instance list"),
    "mods.view": i18n._("the mods of a server or a node"),
    "mods.manage": i18n._("install and remove the root mods of a node"),
    "audit.view": i18n._("the cluster's audit trail"),
    "statistics.view": i18n._("the installation-wide statistics"),
    "logs.view": i18n._("the bot log"),
    "settings.view": i18n._("the bot's own configuration"),
    "settings.write": i18n._("edit the bot's own configuration"),
    "settings.file.view": i18n._("the global configuration files"),
    "settings.file.write": i18n._("edit the global configuration files"),
    "presets.view": i18n._("the MizEdit presets"),
    "presets.write": i18n._("edit the MizEdit presets"),
    CAPABILITY_VIEW: i18n._("this page"),
    CAPABILITY_WRITE: i18n._("changes on this page"),
}

# ── the stored block ──────────────────────────────────────────────────────
#: the config file, relative to the install's config directory
CONFIG_RELATIVE: tuple[str, ...] = ("services", "webservice.yaml")
#: the top-level block the service reads (``Service.get_config`` returns DEFAULT) …
CONFIG_BLOCK = "DEFAULT"
#: … and the one key inside it this page owns
OVERRIDE_KEY = "access"
#: the keys ONE entry may carry. Anything else inside an entry is a key this page does not read, and
#: it is reported (:class:`ShapeRefusal`) instead of being dropped in silence.
ENTRY_KEYS: tuple[str, ...] = ("add", "remove", "manager")

# ── a part of the stored block this page could not read ───────────────────
# A hand-edited block is the operator's intent, so a part this page cannot read is skipped (the
# fallback is the declared matrix) and REPORTED — the loudness that an unknown capability or role
# already gets. Without it a skipped block looks exactly like a block that was applied and changed
# nothing: the console shows the shipped matrix and says nothing about the file.

#: the block itself is not a mapping (``access: "junk"``)
SHAPE_BLOCK = "block"
#: one capability's entry is not a mapping (``music.view: "notamap"``)
SHAPE_ENTRY = "entry"
#: an entry carries a key this page does not read
SHAPE_KEY = "key"
#: a key this page reads carries a value it cannot read (``add: 5``)
SHAPE_VALUE = "value"


@dataclass(frozen=True, slots=True)
class ShapeRefusal:
    """One part of the stored block that was SKIPPED: what kind of shape, the key, its capability.

    ``key`` is the YAML key that was dropped (``access`` for the block itself, the capability name
    for an entry, the entry's own key for a key or a value). ``capability`` is empty where the
    dropped key IS the capability.
    """

    kind: str
    key: str
    capability: str = ""


# ── the form ──────────────────────────────────────────────────────────────
#: one granted cell: ``value="<capability>|<role>"``. Admin is NOT posted this way — its column is a
#: lock, and it travels in :data:`FIELD_ADMIN` so a page that failed to render it cannot silently
#: drop it.
FIELD_CELL = "cell"
#: the locked Admin grant of one row, one value per row: ``value="<capability>"``
FIELD_ADMIN = "admin"
#: one ``managed_by`` grant: ``value="<capability>"``
FIELD_MANAGER = "manager"
#: the sha256 of the config file this form was rendered from (:func:`configwrite.config_revision`).
#: Posted back with the matrix and presented as ``expect_revision`` on the write, so a form built
#: before another save landed is REFUSED instead of silently overwriting that save.
FIELD_REVISION = "_revision"
PAIR_SEPARATOR = "|"

#: the page's own GET state — the filter and "show changes only", both rendered server-side so they
#: work with JavaScript off
QUERY_FILTER = "q"
QUERY_CHANGES = "changes"

#: the ONE sentence per refusal kind. ``permissions`` names the rule; the wording lives here, beside
#: the page that shows it, so the loader's logged error and the page's notice read the same claim.
REFUSAL_SENTENCES: dict[str, str] = {
    permissions.UNKNOWN_CAPABILITY: i18n._(
        "Nothing was saved: %(capability)s is not a capability this console declares."),
    permissions.UNKNOWN_ROLE: i18n._(
        "Nothing was saved: %(role)s is not a role of this bot's role model."),
    permissions.LOCKOUT: i18n._(
        "Nothing was saved: it would leave no role able to open this page."),
    permissions.STRUCTURAL: i18n._(
        "Nothing was saved: %(capability)s belongs to this page and cannot be edited."),
    permissions.ADMIN_LOCKED: i18n._(
        "Nothing was saved: Admin cannot be removed from %(capability)s."),
    permissions.MANAGER_LOCKED: i18n._(
        "Nothing was saved: %(capability)s is machine- or installation-wide, so it cannot carry a "
        "managed_by grant."),
}

#: the sentence a STALE form is refused with: the file moved since this page was rendered, so the
#: matrix the browser posted is not the matrix on disk and writing it would drop somebody's save.
SAVE_REFUSED_STALE = i18n._(
    "Nothing was saved: the page was loaded before the configuration changed on disk. Reload it "
    "and apply your change again.")

#: the ONE sentence per SHAPE a stored block got wrong. Read by the startup log (through
#: :func:`shape_sentence`) and rendered on the page, so the two describe one skipped key one way.
SHAPE_SENTENCES: dict[str, str] = {
    SHAPE_BLOCK: i18n._(
        "Ignored: '%(key)s' is not a mapping, so no override was applied."),
    SHAPE_ENTRY: i18n._(
        "Ignored: the entry for '%(key)s' is not a mapping, so that capability keeps its declared "
        "roles."),
    SHAPE_KEY: i18n._(
        "Ignored: '%(key)s' is not a key this page reads, in the entry for '%(capability)s'."),
    SHAPE_VALUE: i18n._(
        "Ignored: '%(key)s' does not carry a readable value in the entry for '%(capability)s'."),
}
#: the page banner's lead-in for two or more skipped keys (the sentences above follow it)
SHAPE_IGNORED_LEAD = i18n._(
    "Some parts of the stored access block could not be read and were skipped — the capabilities "
    "they name keep their declared values:")


#: the sentences the change bar names each edit with. Read by the template AND served to
#: ``access.js`` via the form's data attributes, so the bar reads the same with and without a script.
CHANGE_ADDED = i18n._("%(role)s added")
CHANGE_REMOVED = i18n._("%(role)s removed")
CHANGE_MANAGER_ON = i18n._("managed_by granted")
CHANGE_MANAGER_OFF = i18n._("managed_by removed")

#: the ONE audit event a save writes — one per accepted save, one per refused attempt (:func:`_audit`).
AUDIT_UPDATED = i18n._("Access matrix updated: %(detail)s")
AUDIT_REFUSED = i18n._("Access matrix save refused: %(detail)s")
#: the detail of a STALE refusal: the form never reached the matrix, so it names no cell
AUDIT_STALE = i18n._("the page was stale — the file changed on disk")

#: the notices a save leaves. ``NOTICE_SAVED`` is shown only when the write AND the install landed,
#: so it says the matrix is live — the registry is updated inside this request, not on the next one.
NOTICE_SAVED = i18n._("Access updated — %(count)s change(s), in effect now.")
#: the same save, written to the file but NOT installed in this process (the next start applies it)
NOTICE_SAVED_ON_RESTART = i18n._(
    "Access saved — %(count)s change(s), written to the configuration. This process could not "
    "install them; the next start applies the block.")
NOTICE_NO_CHANGE = i18n._("No change: the matrix is exactly what is stored.")


# ── the role model and the stored entries ─────────────────────────────────

def role_model(config_dir: str | Path) -> tuple[str, ...]:
    """Every role name of this installation, in column order.

    ``bot.yaml``'s ``roles:`` keys in their own order first (the operator's own model — a custom role
    such as ``Media Manager`` appears here the moment it is declared), then the bot's built-in names
    that the file does not override, sorted. ``Admin`` is first when it is in the model at all: its
    cell is the locked column and the header must read that way.
    """
    declared = local_auth.read_bot_roles(config_dir)
    names = list(declared)
    for name in sorted(local_auth.BUILTIN_ROLE_NAMES):
        if name not in names:
            names.append(name)
    if "Admin" in names:
        names.remove("Admin")
        names.insert(0, "Admin")
    return tuple(names)


def config_path_for(config_dir: str | Path) -> Path:
    """The service config file this page reads and writes."""
    return Path(config_dir).joinpath(*CONFIG_RELATIVE)


def schema_files() -> list[str]:
    """The schema files the bot validates this service's config with — the same ones the service's
    own ``read_locals`` uses, so a save and a startup agree about what a valid file is."""
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    return [str(path) for path in sorted(schema_dir.glob("*.yaml"))]


def override_entries(document: Any) -> tuple[dict[str, dict], list[ShapeRefusal]]:
    """The stored block as ``{capability: {add/remove/manager}}``, plus every part that was SKIPPED.

    Shapes are normalised here (`add`/`remove` readable as a sequence or a single value) so the rest
    of the module never re-reads YAML's many spellings of one list.

    A part this page cannot read is skipped — the fallback is the declared matrix — and returned as a
    :class:`ShapeRefusal` naming the key that was dropped, because a hand-edited block that was
    ignored must not look like one that was applied. The block applies as far as it can be read: one
    malformed entry does not discard the entries beside it.
    """
    if not isinstance(document, Mapping):
        return {}, [ShapeRefusal(SHAPE_BLOCK, OVERRIDE_KEY)]
    block = document.get(CONFIG_BLOCK)
    if not isinstance(block, Mapping):
        return {}, []                          # no service block at all: nothing was stored
    stored = block.get(OVERRIDE_KEY)
    if stored is None:
        return {}, []                          # the key is absent (or empty): nothing was stored
    if not isinstance(stored, Mapping):
        return {}, [ShapeRefusal(SHAPE_BLOCK, OVERRIDE_KEY)]
    entries: dict[str, dict] = {}
    refusals: list[ShapeRefusal] = []
    for capability, entry in stored.items():
        name = str(capability)
        if not isinstance(entry, Mapping):
            refusals.append(ShapeRefusal(SHAPE_ENTRY, name))
            continue
        parsed: dict[str, Any] = {}
        for key in ("add", "remove"):
            if key not in entry or entry[key] is None:
                continue
            value = entry[key]
            if isinstance(value, (list, tuple)):
                parsed[key] = [str(item) for item in value]
            elif isinstance(value, str):
                parsed[key] = [value]
            else:
                refusals.append(ShapeRefusal(SHAPE_VALUE, key, name))
        manager = entry.get("manager")
        if manager is not None:
            if isinstance(manager, (Mapping, list, tuple)):
                refusals.append(ShapeRefusal(SHAPE_VALUE, "manager", name))
            else:
                parsed["manager"] = bool(manager)
        for key in entry:
            if str(key) not in ENTRY_KEYS:
                refusals.append(ShapeRefusal(SHAPE_KEY, str(key), name))
        if parsed:
            entries[name] = parsed
    return entries, refusals


def shape_sentence(refusal: ShapeRefusal) -> str:
    """The ONE sentence a skipped part of the stored block is reported with (translated at render)."""
    template = SHAPE_SENTENCES.get(refusal.kind, SHAPE_SENTENCES[SHAPE_KEY])
    return template % {"key": refusal.key, "capability": refusal.capability}


def stored_entries(config_dir: str | Path) -> tuple[dict[str, dict], bool, list[ShapeRefusal]]:
    """``(entries, readable, refusals)``: the block on disk, whether the file could be read, and the
    parts of it that were skipped for their SHAPE.

    A missing file is not an error — it means no override, which is the shipped behaviour. An
    unreadable one is reported so the page can say the file is broken instead of pretending the
    matrix it shows is what is stored.
    """
    path = config_path_for(config_dir)
    if not path.exists():
        return {}, True, []
    try:
        entries, refusals = override_entries(configwrite.load_config_file(path))
        return entries, True, refusals
    except configwrite.ConfigWriteError as ex:
        log.error("Access page: could not read '%s' (%s) — rendering the declared values only.",
                  path, ex)
        return {}, False, []


# ── the matrix ────────────────────────────────────────────────────────────

def _grouped(capabilities: Iterable[str]) -> list[tuple[str, list[str]]]:
    """The declared capabilities in group order, plus one final group for the ungrouped ones."""
    remaining = set(capabilities)
    groups: list[tuple[str, list[str]]] = []
    for label, members in GROUPS:
        present = [name for name in members if name in remaining]
        if present:
            groups.append((label, present))
            remaining.difference_update(present)
    if remaining:
        groups.append((OTHER_GROUP, sorted(remaining)))
    return groups


def matrix_rows(config_dir: str | Path, *, filter_text: str = "",
                changes_only: bool = False) -> list[dict]:
    """Every row of the matrix, grouped, filtered — the page's whole table as data.

    A row's cells are the EFFECTIVE value of every declared capability (``permissions``, i.e. what the
    gate reads right now) against the BASE it was declared with (``permissions.base_declaration``):
    that comparison is what makes a cell "changed", and it is the same comparison the save makes, so
    the page cannot mark a cell it would not write.

    ``Admin`` is rendered as a locked cell and never as an input; a role cell is editable unless this
    page's own pair is the row; the ``managed_by`` cell is editable only where a scope grant can
    exist at all (:func:`permissions.manager_scope_allowed`).
    """
    names = role_model(config_dir)
    roles = [name for name in names if name != "Admin"]
    needle = (filter_text or "").strip().casefold()
    rows: list[dict] = []
    for label, members in _grouped(permissions.declared_capabilities()):
        group_rows: list[dict] = []
        for capability in members:
            effective = permissions.roles_for_capability(capability) or ()
            base = permissions.base_declaration(capability) or ((), False)
            manager = permissions.scope_grants(capability)
            why = DESCRIPTIONS.get(capability, "")
            changed = False
            cells = []
            for role in roles:
                granted = role in effective
                was = role in base[0]
                changed = changed or granted != was
                cells.append({
                    "role": role,
                    "granted": granted,
                    "was": was,
                    "editable": capability not in STRUCTURAL,
                })
            manager_editable = capability not in STRUCTURAL and permissions.manager_scope_allowed(
                capability)
            changed = changed or manager != base[1]
            haystack = f"{capability} {why} {label}".casefold()
            if needle and needle not in haystack:
                continue
            if changes_only and not changed:
                continue
            group_rows.append({
                "capability": capability,
                "why": why,
                "cells": cells,
                # the Admin cell is a lock ONLY where the declaration carries Admin: a capability
                # declared without it has nothing to lock, and drawing a tick there would claim a
                # grant the save does not write
                "admin_locked": "Admin" in base[0],
                "manager": manager,
                "manager_was": base[1],
                "manager_editable": manager_editable,
                "changed": changed,
            })
        if group_rows:
            rows.append({"group": label, "rows": group_rows})
    return rows


def change_lines(entries: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """The stored override as PLAIN-WORD lines — what the change bar names.

    One line per edit (a capability repeated when it carries several), read from the stored block
    against the base, so the bar describes the file rather than a second opinion about it.
    """
    lines: list[dict] = []
    for capability in sorted(entries):
        entry = entries[capability] or {}
        base = permissions.base_declaration(capability)
        if base is None:
            continue
        for role in entry.get("add") or ():
            if role not in base[0]:
                lines.append({"capability": capability,
                              "msgid": CHANGE_ADDED, "params": {"role": role}})
        for role in entry.get("remove") or ():
            if role in base[0]:
                lines.append({"capability": capability,
                              "msgid": CHANGE_REMOVED, "params": {"role": role}})
        if entry.get("manager") is not None and bool(entry["manager"]) != base[1]:
            lines.append({"capability": capability,
                          "msgid": CHANGE_MANAGER_ON if entry["manager"] else CHANGE_MANAGER_OFF,
                          "params": {}})
    return lines


def entries_from_candidate(candidate: Mapping[str, tuple[Iterable[str], bool]]) -> dict[str, dict]:
    """The stored block a candidate matrix describes — only what differs from the base.

    A capability that matches its declaration is LEFT OUT (and an entry that would say nothing is
    dropped), so applying a matrix that changes nothing writes no block at all and the file keeps no
    residue of a visit.
    """
    entries: dict[str, dict] = {}
    for capability, (roles, manager) in candidate.items():
        base = permissions.base_declaration(capability)
        if base is None:                                  # pragma: no cover - candidate is declared
            continue
        role_set = list(roles)
        entry: dict[str, Any] = {}
        added = [role for role in role_set if role not in base[0]]
        removed = [role for role in base[0] if role not in role_set]
        if added:
            entry["add"] = added
        if removed:
            entry["remove"] = removed
        if bool(manager) != base[1]:
            entry["manager"] = bool(manager)
        if entry:
            entries[capability] = entry
    return entries


def candidate_from_form(form, *, config_dir: str | Path,
                        declared: Iterable[str] | None = None) -> tuple[
                            dict[str, tuple[tuple[str, ...], bool]], list[permissions.OverrideRefusal]]:
    """The matrix a posted form describes, plus what the post named that does not exist.

    The form carries the WHOLE matrix: every granted ``capability|role`` cell, the locked ``Admin``
    marker of every row, and every ``managed_by`` grant. A row whose ``Admin`` marker is missing
    therefore posts "Admin is off here" — which is exactly the shape :func:`permissions.
    validate_candidate` refuses, so a matrix that lost a row cannot silently narrow anything.

    Names are read as DATA and refused when unknown: an unknown capability or role is reported, never
    granted (a typo that silently granted nothing is the failure this rejects).
    """
    names = list(declared if declared is not None else permissions.declared_capabilities())
    known_roles = set(role_model(config_dir))
    grants: dict[str, list[str]] = {}
    admins: set[str] = set()
    managers: set[str] = set()
    refusals: list[permissions.OverrideRefusal] = []
    unknown_capabilities: set[str] = set()

    for value in form.getlist(FIELD_ADMIN):
        capability = _text(value)
        if capability not in names:
            unknown_capabilities.add(capability)
        else:
            admins.add(capability)
    for value in form.getlist(FIELD_CELL):
        capability, _, role = _text(value).partition(PAIR_SEPARATOR)
        if capability not in names:
            unknown_capabilities.add(capability)
            continue
        if role not in known_roles:
            refusals.append(permissions.OverrideRefusal(permissions.UNKNOWN_ROLE, capability, role))
            continue
        grants.setdefault(capability, []).append(role)
    for value in form.getlist(FIELD_MANAGER):
        capability = _text(value)
        if capability not in names:
            unknown_capabilities.add(capability)
        else:
            managers.add(capability)

    for capability in sorted(unknown_capabilities):
        refusals.append(permissions.OverrideRefusal(permissions.UNKNOWN_CAPABILITY, capability))

    candidate: dict[str, tuple[tuple[str, ...], bool]] = {}
    for capability in names:
        roles: list[str] = []
        # the posted Admin marker grants Admin only where the DECLARATION carries it: a row whose
        # base does not hold Admin has no lock to carry, so the marker is not a way in
        base = permissions.base_declaration(capability)
        if capability in admins and base is not None and "Admin" in base[0]:
            roles.append("Admin")
        for role in grants.get(capability, ()):
            if role not in roles:
                roles.append(role)
        candidate[capability] = (tuple(roles), capability in managers)
    return candidate, refusals


def _text(value: Any) -> str:
    """A form value as text (``None`` -> ``""``)."""
    return value if isinstance(value, str) else ("" if value is None else str(value))


def refusal_sentence(refusal: permissions.OverrideRefusal) -> str:
    """The ONE sentence a refusal is shown with (a wrapped msgid, translated at render)."""
    template = REFUSAL_SENTENCES.get(refusal.kind, REFUSAL_SENTENCES[permissions.UNKNOWN_CAPABILITY])
    return template % {"capability": refusal.capability, "role": refusal.role}


# ── applying, writing, loading ────────────────────────────────────────────

def apply_from_config(app) -> list[permissions.OverrideRefusal]:
    """Read the stored block and apply it to the capability registry. Called once at startup.

    A block that names something the installation does not know, that removes ``Admin``, that edits a
    structural cell or that would leave this page unreachable is REFUSED AS A WHOLE — each refusal
    logged at ERROR and the declarations left exactly as shipped. Half an override is a permission
    set nobody wrote down, which is worse than none.

    A part of the block whose SHAPE cannot be read is a different class: it is skipped (that entry or
    key keeps its declared value), the rest of the block still applies, and each skipped key is logged
    at ERROR — the operator has to be able to tell an ignored hand-edit from an empty one.
    """
    config_dir = getattr(getattr(app, "state", None), "webui_config_dir", None) or "config"
    entries, readable, shape_refusals = stored_entries(config_dir)
    if not readable:
        return []
    for refusal in shape_refusals:
        log.error("Access override in '%s' skipped a part of the block (%s): %s",
                  config_path_for(config_dir), refusal.kind, shape_sentence(refusal))
    known_roles = role_model(config_dir)
    candidate, refusals = permissions.candidate_from_entries(entries, roles_known=known_roles)
    refusals.extend(permissions.validate_candidate(candidate, roles_known=known_roles,
                                                   locked=STRUCTURAL, page=PAGE_CAPABILITY))
    if refusals:
        for refusal in refusals:
            log.error("Access override in '%s' refused (%s): %s", config_path_for(config_dir),
                      refusal.kind, refusal_sentence(refusal))
        log.error("Access override in '%s': the whole block was IGNORED — every capability keeps "
                  "its declared roles.", config_path_for(config_dir))
        permissions.reset_role_overrides()
        return refusals
    applied = permissions.apply_role_overrides(candidate)
    if applied:
        log.info("Access override applied to %d capability(ies): %s.", len(applied),
                 ", ".join(sorted(applied)))
    return []


def save_entries(config_dir: str | Path, entries: Mapping[str, Mapping[str, Any]], *,
                 expect_revision: str | None = None) -> configwrite.SaveResult:
    """Write the block and NOTHING else: the rest of the file survives byte-for-byte.

    The write goes through the ONE config primitive (round-trip, schema-validated, atomic, ``.bak``),
    and it mutates the block in place, so comments, key order and every other key — ``auth:``, the
    session policy, the node blocks — come out exactly as they went in. An EMPTY mapping deletes the
    key rather than leaving ``access: {}`` behind: discarding everything has to look like it was never
    there.

    ``expect_revision`` is the sha256 the form was rendered from; a file that moved since raises
    :class:`configwrite.StaleConfigRevision` and is left untouched.
    """
    from ruamel.yaml.comments import CommentedMap

    path = config_path_for(config_dir)

    def mutate(document: Any) -> None:
        if not isinstance(document, MutableMapping):
            raise configwrite.ConfigWriteError(
                "The service configuration is not a mapping; the access block cannot be written.",
                path=path)
        block = document.get(CONFIG_BLOCK)
        if not isinstance(block, CommentedMap):
            block = CommentedMap()
            document[CONFIG_BLOCK] = block
        if not entries:
            block.pop(OVERRIDE_KEY, None)
            return
        stored = block.get(OVERRIDE_KEY)
        if not isinstance(stored, CommentedMap):
            stored = CommentedMap()
            block[OVERRIDE_KEY] = stored
        for key in list(stored.keys()):
            del stored[key]
        for capability in sorted(entries):
            entry = entries[capability] or {}
            written = CommentedMap()
            if entry.get("add"):
                written["add"] = list(entry["add"])
            if entry.get("remove"):
                written["remove"] = list(entry["remove"])
            if entry.get("manager") is not None:
                written["manager"] = bool(entry["manager"])
            if written:
                stored[capability] = written

    return configwrite.patch_config_file(path, mutate, schemas=schema_files(),
                                        expect_revision=expect_revision)


# ── the page ──────────────────────────────────────────────────────────────

def access_context(request: Request) -> dict:
    """Everything the Access template reads, in one value."""
    registrar = getattr(request.app.state, "webui_registrar", None)
    config_dir = getattr(request.app.state, "webui_config_dir", None) or "config"
    filter_text = _text(request.query_params.get(QUERY_FILTER))
    changes_only = _text(request.query_params.get(QUERY_CHANGES)) in ("1", "true", "on")
    entries, readable, shape_refusals = stored_entries(config_dir)
    rows = matrix_rows(config_dir, filter_text=filter_text, changes_only=changes_only)
    lines = change_lines(entries)
    state = readmodels_overview(request)
    return {
        "title": PAGE_TITLE,
        "page_title": PAGE_TITLE,
        "lead": PAGE_LEAD,
        "crumb": dashboard_page.crumb(NAV_GROUP_LABEL, PAGE_TITLE),
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=ACCESS_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "style": STYLE,
        "script": SCRIPT,
        "page_path": mount.url(ACCESS_PATH),
        "save_path": mount.url(SAVE_PATH),
        "roles": [name for name in role_model(config_dir) if name != "Admin"],
        "rows": rows,
        "filter_field": QUERY_FILTER,
        "filter": filter_text,
        "changes_field": QUERY_CHANGES,
        "changes_only": changes_only,
        "changes": lines,
        "change_count": len(lines),
        "readable": readable,
        # the parts of the stored block this page could not read, one sentence each: a skipped key
        # must be visible on the page whose matrix it did not change
        "ignored": [shape_sentence(refusal) for refusal in shape_refusals],
        "ignored_lead": SHAPE_IGNORED_LEAD,
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
        # the form's field names travel from here: the route reads the same constants, so a rename
        # cannot leave the markup posting a name nobody reads
        "cell_field": FIELD_CELL,
        "admin_field": FIELD_ADMIN,
        "manager_field": FIELD_MANAGER,
        "revision_field": FIELD_REVISION,
        # the generation the form is built from — posted back and presented as ``expect_revision``
        "revision": configwrite.config_revision(config_path_for(config_dir)),
        "notice": actions_page.pop_notice(request),
        "added_message": CHANGE_ADDED,
        "removed_message": CHANGE_REMOVED,
        "manager_on_message": CHANGE_MANAGER_ON,
        "manager_off_message": CHANGE_MANAGER_OFF,
        "no_changes": i18n._("No overrides — every capability keeps its declared defaults."),
        "apply_label": i18n._("Apply now"),
        "discard_label": i18n._("Discard"),
        "pending_label": i18n._("%(count)s unsaved change(s)"),
    }


def readmodels_overview(request: Request):
    """The read model the shell chrome renders (the environment marker and the status pills)."""
    from .. import readmodels            # local import: readmodels is large and only needed here

    return readmodels.overview(dashboard_page.request_source(request),
                               level=dashboard_page.log_level(request))


async def _save(request: Request) -> RedirectResponse:
    """Take the posted matrix, refuse it or write it — one body, one notice."""
    config_dir = getattr(request.app.state, "webui_config_dir", None) or "config"
    form = await request.form()
    candidate, refusals = candidate_from_form(form, config_dir=config_dir)
    refusals.extend(permissions.validate_candidate(candidate, roles_known=role_model(config_dir),
                                                   locked=STRUCTURAL, page=PAGE_CAPABILITY))
    if refusals:
        # ONE refusal per save on the notice: the first is the one the operator has to fix, and the
        # rest would only push it off a banner. The FIRST is logged at WARNING because a refused
        # escalation is a security event an operator has to be able to find later; the tail is INFO.
        # The attempt itself is AUDITED, written or not — a refusal must leave a trace.
        log.warning("Access save refused: %s", _refusal_fragment(refusals[0]))
        for refusal in refusals[1:]:
            log.info("Access save refused as well: %s", _refusal_fragment(refusal))
        audit = await _audit(request, refusals=refusals)
        _notice(request, refusal_sentence(refusals[0]), ok=False, refusal=True, audit=audit)
        return RedirectResponse(mount.url(ACCESS_PATH), status_code=303)

    entries = entries_from_candidate(candidate)
    previous = dict(permissions.APPLIED_OVERRIDES)
    # the revision the form was built from; a missing field is a caller that is not this page and is
    # not revision-checked, while an EMPTY value is a real expectation (the file did not exist yet)
    posted_revision = form.get(FIELD_REVISION)
    expect_revision = posted_revision if isinstance(posted_revision, str) else None
    try:
        result = save_entries(config_dir, entries, expect_revision=expect_revision)
    except configwrite.StaleConfigRevision:
        log.warning("Access save refused: '%s' changed on disk after this page was loaded.",
                    config_path_for(config_dir))
        audit = await _audit(request, refused=AUDIT_STALE)
        _notice(request, SAVE_REFUSED_STALE, ok=False, refusal=True, audit=audit)
        return RedirectResponse(mount.url(ACCESS_PATH), status_code=303)
    except configwrite.ConfigWriteError as ex:
        _notice(request, i18n._("Nothing was saved: %(reason)s") % {"reason": str(ex)},
                ok=False, refusal=True)
        return RedirectResponse(mount.url(ACCESS_PATH), status_code=303)

    applied_ok = True
    try:
        applied = permissions.apply_role_overrides(candidate)
    except Exception:                                     # pragma: no cover - a foreign declaration
        # the file is written and VALID; the registry refused to replace a declaration this process
        # did not make. Say so instead of claiming a live change: the next start applies the block.
        log.exception("Access save: '%s' was written but could not be applied in this process.",
                      config_path_for(config_dir))
        applied_ok = False
        applied = dict(previous)
    changes = _changed_cells(previous, applied)
    if not result.changed and not changes:
        _notice(request, NOTICE_NO_CHANGE, ok=True, refusal=True)
        return RedirectResponse(mount.url(ACCESS_PATH), status_code=303)

    audit = await _audit(request, changes)
    message = (NOTICE_SAVED % {"count": len(changes)} if applied_ok
               else NOTICE_SAVED_ON_RESTART % {"count": len(changes)})
    _notice(request, message, ok=True, refusal=True, audit=audit)
    return RedirectResponse(mount.url(ACCESS_PATH), status_code=303)


def _changed_cells(before: Mapping[str, tuple[Iterable[str], bool]],
                   after: Mapping[str, tuple[Iterable[str], bool]]) -> list[str]:
    """The cells that moved between two applied-override maps, one fragment each.

    Read against the BASE, because that is what a person changed: the two maps carry only the
    capabilities an override touches, so a capability dropped from both is back at its declaration
    and is not a change.
    """
    moved: list[str] = []
    for capability in permissions.declared_capabilities():
        base = permissions.base_declaration(capability) or ((), False)
        old = before.get(capability, base)
        new = after.get(capability, base)
        old_roles, old_manager = set(old[0]), bool(old[1])
        new_roles, new_manager = set(new[0]), bool(new[1])
        for role in sorted(new_roles - old_roles):
            moved.append(f"{capability} + {role}")
        for role in sorted(old_roles - new_roles):
            moved.append(f"{capability} - {role}")
        if old_manager != new_manager:
            moved.append(f"{capability} managed_by {'on' if new_manager else 'off'}")
    return moved


async def _audit(request: Request, changes: Iterable[str] = (), *,
                 refusals: Iterable[permissions.OverrideRefusal] = (),
                 refused: str = "") -> str:
    """Record ONE audit entry for ONE save attempt: the cells that moved, or why nothing was written.

    A refused save is audited too, naming the refusal kind and the capability/role it carried — an
    escalation attempt that leaves no trace is the state this exists to prevent. A failed audit is
    reported (:data:`core.actions.AUDIT_NOT_RECORDED`) and the page says so — the save itself still
    stands, and a banner over a lost trail is the one combination that must never be silent.
    """
    from core.action_results import ActionResult
    from core.actions import AUDIT_NOT_RECORDED, audit_action

    reasons = [_refusal_fragment(refusal) for refusal in refusals]
    if refused:
        reasons.append(refused)
    if reasons:
        detail = "; ".join(reasons)
        event = AUDIT_REFUSED % {"detail": detail}
    else:
        detail = "; ".join(changes) if changes else "no cell changed"
        event = AUDIT_UPDATED % {"detail": detail}
    try:
        ctx = actions_page.context_for(request)
        result = await audit_action(ctx, ActionResult(success=not reasons), message=event)
        return str((getattr(result, "data", None) or {}).get("audit") or "")
    except Exception:                                     # pragma: no cover - audit is best effort
        log.exception("Access save: the audit entry could not be recorded (%s).", detail)
        return AUDIT_NOT_RECORDED


def _refusal_fragment(refusal: permissions.OverrideRefusal) -> str:
    """One refusal as a flat phrase: the rule it broke and the names the edit carried."""
    return " ".join(part for part in (refusal.kind, refusal.capability, refusal.role) if part)


def _notice(request: Request, message: str, *, ok: bool, refusal: bool = False,
            audit: str = "") -> None:
    """Store the one-shot notice of this save, in the shape ``pages/actions.pop_notice`` validates."""
    from core.actions import AUDIT_NOT_RECORDED

    text = _text(message)
    if len(text) > actions_page.NOTICE_MAX_CHARS:
        text = text[:actions_page.NOTICE_MAX_CHARS - 1] + "…"
    try:
        request.session[actions_page.NOTICE_KEY] = {
            "message": text, "ok": bool(ok), "audit": audit,
            "audit_warning": audit == AUDIT_NOT_RECORDED, "action": "",
            "revert": "", "revert_omitted": "", "refusal": bool(refusal),
        }
    except (AssertionError, KeyError):                    # pragma: no cover - a request with no session
        log.debug("Access page: no session to carry a notice.")


# ── the routes ────────────────────────────────────────────────────────────

def capabilities() -> dict[str, str]:
    """The capability declaration for every path this module registers."""
    declare()
    return {ACCESS_PATH: CAPABILITY_VIEW, SAVE_PATH: CAPABILITY_WRITE}


def nav_items() -> tuple:
    """The sidebar entry — the read capability's own name."""
    declare()
    return (NavItem(label=NAV_LABEL, url=ACCESS_PATH, capability=CAPABILITY_VIEW,
                    group=NAV_GROUP, glyph=NAV_GLYPH),)


def declare() -> None:
    """Declare the page's pair: ``Admin`` only, no scope grant, so a manager can never reach it."""
    permissions.declare_capability(CAPABILITY_VIEW, PAGE_ROLES)
    permissions.declare_capability(CAPABILITY_WRITE, PAGE_ROLES)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the page and its save to the shell's own router and return it."""
    declare()

    @router.get(ACCESS_PATH, response_class=HTMLResponse, name="page-access")
    async def _access_page(request: Request):
        # no-store: a save returns here and the matrix is the answer to that save
        return HTMLResponse(i18n.render(request, TEMPLATE, **access_context(request)),
                            headers={"Cache-Control": "no-store"})

    @router.post(SAVE_PATH, dependencies=[Depends(session.csrf_protect)], name="access-save")
    async def _access_save(request: Request):
        return await _save(request)

    return router
