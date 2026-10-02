"""The MAINTENANCE FLAG's action pair — set / clear it for ONE server — and the DCS-FACE CONFIG
actions — read / write a server's ``serverSettings.lua``.

THE CONFIG PAIR (``get_server_config`` / ``set_server_config``) is the DCS face: the flat
``serverSettings.lua`` read and written through the EXISTING ``Server.settings`` ``SettingsDict`` — the
same path ``/server config`` (``plugins/scheduler/commands.py``) uses. The read returns ``values``
(secrets redacted), ``editable``, ``overridden`` and ``status`` in ``result.data``; the write returns
the old -> new of every applied key in ``ServerConfigResult.applied``. Roles: Admin only, no scope
grant — an authenticated REST/plugin transport is allowed and the MCP/service one is refused until it
authenticates; secrets are write-only. A key pinned in the entry's ``locals['serverSettings']`` block
is APPLIED like any other, and the block is then updated in place and the server reloaded, so the value
never reverts.

The MAINTENANCE pair is the ONE implementation ``/scheduler maintenance`` / ``/scheduler clear`` and
the console's server-row pair all reach, so the console and Discord cannot drift into two meanings for
one flag.

The trail is written HERE, once, whatever the transport, including for a refusal.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from core import utils
from core.action_results import ServerConfigResult, ServerControlResult
from core.actions import (TRANSPORT_DISCORD, TRANSPORT_PLUGIN, TRANSPORT_SERVICE, TRANSPORT_WEB,
                          action, audit_action, server_write_lock)
from core.data.const import Coalition, Status
from core.server_config import CHANNELS_ITEM, MANAGER_DENIED, MANAGER_DENIED_KEYS, manager_denial

log = logging.getLogger(__name__)

# ruamel round-trip loader — preserves the operator's comments and key order in ``servers.yaml``.
yaml = YAML()

__all__ = [
    # the maintenance flag pair
    "ABORTED_BY_MAINTENANCE", "set_maintenance", "clear_maintenance",
    # the DCS-face config pair and its curated table
    "ADMIN_ROLE", "ADMIN_ONLY_SENTENCE", "SERVICE_REFUSAL", "STOP_FIRST_SENTENCE", "WRITABLE_STATES",
    "NO_NAME_SENTINEL", "NAME_RESERVED_SENTENCE",
    # the MANAGER deny-list — re-exported so a reader finds it beside the actions that enforce it
    "MANAGER_DENIED", "MANAGER_DENIED_KEYS", "CHANNELS_ITEM",
    "TRANSPORT_PLUGIN", "TRANSPORT_SERVICE", "TRANSPORT_WEB", "TRANSPORT_DISCORD",
    "DCSField", "DCS_FIELDS", "DCS_READONLY_FIELDS", "validate_value",
    "get_server_config", "set_server_config",
    # the bot-face channels write
    "CHANNEL_KEYS", "CHANNEL_ADMIN_KEY", "CHANNEL_UNSET", "set_server_channels",
    # the coalition face: the plaintext read and the write that goes through the bot's own method
    "get_server_coalitions", "set_coalition_password", "WRITABLE_COALITION_STATES",
    "COALITION_KEYS", "COALITION_STATE_SENTENCE", "COALITION_REST_REFUSAL",
    "COALITION_TRANSPORT_REFUSAL",
    "NEXT_RESTART_SENTENCE", "PASSWORD_CHANGED_SENTENCE", "PASSWORD_CLEARED_SENTENCE",
]

#: The attributes a set flag ABORTS, because setting the flag is what aborts them. Exactly the four
#: ``/scheduler maintenance`` clears today (``plugins/scheduler/commands.py``): a pending restart,
#: the server-empty trigger and the two on-mission-end triggers. Named here once so the action and
#: the command cannot disagree about what "setting the flag" means.
ABORTED_BY_MAINTENANCE: tuple[str, ...] = ("restart_pending", "on_empty", "on_mission_end",
                                           "on_coalition_win")


def _label(server: Any, fallback: str) -> str:
    """The name a message uses: the object's own, never anything the caller sent."""
    return str(getattr(server, "display_name", None) or getattr(server, "name", "") or fallback)


def _abort_pending_restart(server: Any) -> None:
    """Abort everything waiting to restart *server* — the four triggers the flag overrides.

    Read tolerantly, because the four are not one type: ``restart_pending`` is a bool while the other
    three are the event sets (``on_empty`` / ``on_mission_end`` / ``on_coalition_win``). A container
    is ``clear()``-ed; anything else is set to ``False``. An object that carries only some of them —
    or none at all — must not cost the flag: this is housekeeping around the ONE write that matters.
    """
    for attribute in ABORTED_BY_MAINTENANCE:
        value = getattr(server, attribute, None)
        try:
            emptier = getattr(value, "clear", None)
            if callable(emptier):
                emptier()
            elif value is not None:
                setattr(server, attribute, False)
        except Exception:                     # pragma: no cover - a double that refuses the write
            log.exception("Could not clear '%s' on server '%s' while setting the maintenance flag.",
                          attribute, getattr(server, "name", "?"))


async def _flag(ctx: Any, server_name: str, *, maintenance: bool) -> ServerControlResult:
    """The shared body of the pair — ONE implementation, because they differ by one boolean.

    A server ALREADY in the requested state is a TYPED REFUSAL, never a silent re-write: the flag did
    not move and the caller is told so. The wording matches the two Discord commands' own.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Server '{server_name}' not found.", server_name=server_name))
    name = _label(server, server_name)
    if bool(getattr(server, "maintenance", False)) is maintenance:
        message = (f"Server \"{name}\" is already in maintenance mode." if maintenance
                   else f"Server \"{name}\" is not in maintenance mode.")
        return await _audited(ctx, ServerControlResult(
            success=False, message=message, server_name=server_name), server)
    try:
        server.maintenance = maintenance
    except Exception as ex:
        # Never an AttributeError out of a transport: a typed failure, and the flag did not move.
        log.exception("maintenance flag: could not write it on server '%s'", name)
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Failed to change the maintenance flag on server '{name}': {ex}",
            server_name=server_name), server)
    if maintenance:
        _abort_pending_restart(server)
        message = f"Maintenance mode set for server \"{name}\"."
    else:
        message = f"Maintenance mode cleared for server \"{name}\"."
    return await _audited(ctx, ServerControlResult(
        success=True, message=message, server_name=server_name), server)


async def _audited(ctx: Any, result: ServerControlResult,
                   server: Any = None) -> ServerControlResult:
    """Write the trail for *result* and return it.

    ONE place, so every exit of both halves — the change, the already-in-that-state refusal and the
    not-found refusal — leaves exactly one entry, and none of them can forget the one mechanism.
    """
    await audit_action(ctx, result, server=server)
    return result


@action
async def set_maintenance(ctx: Any, server_name: str) -> ServerControlResult:
    """Set the maintenance flag on ONE server — the same operation ``/scheduler maintenance`` performs.

    The server is taken out of service: the scheduler will not start it, and any restart it was
    already queued for is ABORTED (``restart_pending`` / ``on_empty`` / ``on_mission_end`` /
    ``on_coalition_win`` all cleared, exactly as the command does). The server's process is NOT
    touched — a flagged server keeps running until somebody stops it, which is why this control
    lives on the server row and not with the node's power pair.
    """
    return await _flag(ctx, server_name, maintenance=True)


@action
async def clear_maintenance(ctx: Any, server_name: str) -> ServerControlResult:
    """Clear the maintenance flag on ONE server — the same operation ``/scheduler clear`` performs.

    The server is back in service: it can be started again and the scheduler may start it. Nothing
    is started here — ending maintenance is a state change, not a power operation.
    """
    return await _flag(ctx, server_name, maintenance=False)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# The DCS FACE of per-server configuration
# ═══════════════════════════════════════════════════════════════════════════════════════════════
#
# F1 is ``Saved Games/<instance>/Config/serverSettings.lua``, read and written through
# ``Server.settings`` (a ``SettingsDict``, ``core/utils/helper.py``). This slice EDITS that file and
# nothing else: no ``servers.yaml``, no live RPC, no ``Server.set_config``.

#: The role the config pair requires — Admin only, no scope grant (the console's ``NODE_ROLES`` shape,
#: not the scope-granted ``WRITE_ROLES``): a server's configuration carries secrets and its own scope,
#: so a DCS Admin or a scoped manager is refused here. The Discord ``/server config`` command keeps
#: its own ``DCS Admin`` door and is not re-pointed at these actions.
ADMIN_ROLE = "Admin"

#: The typed refusal a NON-Admin caller gets. The action's own sentence, kept short and free of the
#: server's data.
ADMIN_ONLY_SENTENCE = "Changing a server's configuration requires the Admin role."

#: The typed refusal a SERVICE (MCP) caller gets. The MCP transport has no authentication yet, so this
#: action will not accept a caller it cannot attribute — an unauthenticated write of a server's config
#: (which carries secrets) is exactly what the in-action check exists to prevent.
#:
#: REVISIT WHEN MCP AUTHENTICATION LANDS: map the authenticated identity onto this rule then; until
#: then the refusal stands.
SERVICE_REFUSAL = ("The MCP/service transport has no authentication yet, so a server's configuration "
                   "cannot be changed from it: this action will not accept a caller it cannot "
                   "attribute. Use the admin console, or a REST/plugin caller whose own scheme has "
                   "authenticated it.")

#: The stop-first sentence, worded to match the Discord ``/server config`` flow; the console gives the
#: same answer.
STOP_FIRST_SENTENCE = ("The server must be stopped to change its configuration. "
                       "Stop it on the Servers page, then edit here.")

#: The reserved sentinel that means "unnamed" throughout the core (``ServerImpl.rename``,
#: ``ServiceBus._check_database``): a server must never be RENAMED to it. ``ServerImpl.rename``
#: special-cases ``old_name == 'n/a'`` and takes its no-rollback path, so a rename TO it can leave a
#: half-applied change.
NO_NAME_SENTINEL = "n/a"

#: The typed refusal a submitted ``n/a`` gets — named once so the action and any test agree.
NAME_RESERVED_SENTENCE = (f'The name "{NO_NAME_SENTINEL}" is reserved and cannot be used for a server.')

#: The ONLY statuses a config write is allowed in. ``LOADING`` and ``SHUTTING_DOWN`` are ALSO refused:
#: the file is being read or the process is dying, a race ``SettingsDict``'s mtime check cannot win.
#: ``STOPPED`` is writable — ``/server config`` stops the server, which lands it in ``STOPPED``.
WRITABLE_STATES = (Status.SHUTDOWN, Status.STOPPED, Status.UNREGISTERED)

#: What a redacted secret reads as in the read model and in ``applied`` — the VALUE is
#: never returned, only whether one is set.
SET_SENTINEL = "<set>"
UNSET_SENTINEL = "<unset>"

#: The four kinds the field table uses. ``luadata.serialize`` will faithfully write whatever
#: Python object it is given, so the TYPE check is the guard against a value that stops the server.
KIND_BOOL = "bool"
KIND_INT = "int"
KIND_STR = "str"
KIND_SEQ = "seq"


@dataclass(frozen=True)
class DCSField:
    """One curated field of the DCS face — the schema ``serverSettings.lua`` does NOT have.

    The same information the four scheduler modals already encode implicitly (``plugins/scheduler/
    views.py``): a key, a kind, and (where the modal limits one) a range or a set of choices. ``key``
    is DOTTED for the nested ``advanced`` map — ``advanced.allow_change_skin`` — so the whole editable
    surface is one flat vocabulary the read, the write and the page all share.
    """
    key: str
    kind: str
    help: str = ""
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[Any, ...] | None = None
    secret: bool = False
    unique: bool = False
    readonly: bool = False


#: The CURATED editable set — the DCS fields the four scheduler modals cover, nothing else. Ranges,
#: choices and the string lengths mirror the modals' own limits (``views.py``), so the console can
#: never write a value Discord would have cut.
DCS_FIELDS: tuple[DCSField, ...] = (
    DCSField("name", KIND_STR, "The server's name as DCS shows it.", minimum=1),
    DCSField("description", KIND_STR, "The server's description.", maximum=2000),
    DCSField("password", KIND_STR, "Password required to join. Blank leaves it unchanged; "
                                   "clearing is its own value (None).", secret=True, maximum=80),
    DCSField("port", KIND_INT, "The port DCS listens on.", minimum=1, maximum=65535),
    DCSField("maxPlayers", KIND_INT, "Maximum number of players.", minimum=1, maximum=999),
    DCSField("isPublic", KIND_BOOL, "Whether the server is listed publicly."),
    DCSField("require_pure_clients", KIND_BOOL, "Require pure clients."),
    DCSField("require_pure_scripts", KIND_BOOL, "Require pure scripts."),
    DCSField("require_pure_models", KIND_BOOL, "Require pure models."),
    DCSField("require_pure_textures", KIND_BOOL, "Require pure textures."),
    DCSField("advanced.resume_mode", KIND_INT, "Resume behaviour when empty.", choices=(0, 1, 2)),
    DCSField("advanced.maxPing", KIND_INT, "Maximum allowed ping (0 = unlimited).", minimum=0,
             maximum=999),
    DCSField("advanced.server_can_screenshot", KIND_BOOL, "Allow the server to take screenshots."),
    DCSField("advanced.allow_trial_only_clients", KIND_BOOL, "Allow trial-only clients."),
    DCSField("advanced.allow_change_tailno", KIND_BOOL, "Allow players to change tail numbers."),
    DCSField("advanced.allow_dynamic_radio", KIND_BOOL, "Allow dynamic radio."),
    DCSField("advanced.allow_change_skin", KIND_BOOL, "Allow players to change skins."),
    DCSField("advanced.allow_object_export", KIND_BOOL, "Allow object export."),
    DCSField("advanced.allow_sensor_export", KIND_BOOL, "Allow sensor export."),
    DCSField("advanced.allow_ownship_export", KIND_BOOL, "Allow ownship export."),
    DCSField("advanced.allow_players_pool", KIND_BOOL, "Allow the players pool."),
    DCSField("advanced.disable_events", KIND_BOOL, "Disable all events."),
)

#: Shown READ-ONLY and NEVER written. ``missionList`` is managed by the autoscan and validated at boot.
#: The coalition password HASHES are here so the read can say *set / not set* without echoing them.
DCS_READONLY_FIELDS: tuple[DCSField, ...] = (
    DCSField("missionList", KIND_SEQ, "The mission list (managed by the autoscan).", unique=True,
             readonly=True),
    DCSField("advanced.bluePasswordHash", KIND_STR, "Blue coalition password (hash).", secret=True,
             readonly=True),
    DCSField("advanced.redPasswordHash", KIND_STR, "Red coalition password (hash).", secret=True,
             readonly=True),
)

_FIELDS_BY_KEY: dict[str, DCSField] = {f.key: f for f in DCS_FIELDS}
_READONLY_BY_KEY: dict[str, DCSField] = {f.key: f for f in DCS_READONLY_FIELDS}
_ALL_FIELDS: tuple[DCSField, ...] = DCS_FIELDS + DCS_READONLY_FIELDS
_SECRET_KEYS: frozenset[str] = frozenset(f.key for f in _ALL_FIELDS if f.secret)


def _split(key: str) -> tuple[str, str | None]:
    """``(root, leaf)`` for a dotted key, or ``(key, None)`` for a top-level one."""
    root, sep, leaf = key.partition(".")
    return (root, leaf) if sep else (key, None)


def _read_setting(settings: Any, key: str) -> Any:
    """The current value of *key* in *settings*, reading the nested ``advanced`` map one level deep."""
    root, leaf = _split(key)
    if leaf is None:
        return settings.get(root)
    nested = settings.get(root)
    return nested.get(leaf) if isinstance(nested, dict) else None


def _write_setting(settings: Any, key: str, value: Any) -> None:
    """Write *key* in *settings* through the ``SettingsDict`` — the ONE write.

    A dotted ``advanced.X`` is written by REASSIGNING the whole ``advanced`` map (a copy with the one
    leaf set): a nested in-place mutation would never reach ``SettingsDict.__setitem__`` and so would
    never hit the file.
    """
    root, leaf = _split(key)
    if leaf is None:
        settings[root] = value
        return
    nested = dict(settings.get(root) or {})
    nested[leaf] = value
    settings[root] = nested


async def _apply_setting(server: Any, key: str, value: Any) -> None:
    """Apply ONE setting through the ``Server``'s ``SettingsDict`` — the ONE write, by name.

    Its own coroutine so the write is a nameable seam a test can patch to suspend between the read and
    the write; in production it never suspends (``SettingsDict.__setitem__`` is synchronous).
    """
    _write_setting(server.settings, key, value)


def _redact(value: Any) -> str:
    """A secret's read-model value: *set* or *not set*, never the value.

    A WHITESPACE-only string reads as NOT set — DCS treats it as no password.
    """
    if isinstance(value, str):
        return SET_SENTINEL if value.strip() else UNSET_SENTINEL
    return SET_SENTINEL if value else UNSET_SENTINEL


def _redacted_applied(key: str, old: Any, new: Any) -> dict[str, Any]:
    """``{"from":…, "to":…}`` for *key* — with a secret's two sides REDACTED."""
    if key in _SECRET_KEYS:
        return {"from": _redact(old), "to": _redact(new)}
    return {"from": old, "to": new}


def validate_value(field: DCSField, value: Any) -> str | None:
    """The TYPE / range / ``unique`` check for one submitted value, or ``None`` if it is good.

    The guard the serializer is not: ``luadata.serialize`` writes whatever it is handed, so a value of
    the wrong kind is caught HERE, before the file. The reasons NEVER echo a value that could be a
    secret field's — a secret's text is only ever checked for being text.
    """
    if field.kind == KIND_BOOL:
        if not isinstance(value, bool):
            return f"'{field.key}' expects a true/false value."
    elif field.kind == KIND_INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{field.key}' expects a whole number."
        if field.choices is not None and value not in field.choices:
            return f"'{field.key}' must be one of {list(field.choices)}."
        if field.minimum is not None and value < field.minimum:
            return f"'{field.key}' must be at least {field.minimum}."
        if field.maximum is not None and value > field.maximum:
            return f"'{field.key}' must be at most {field.maximum}."
    elif field.kind == KIND_STR:
        if not isinstance(value, str):
            return f"'{field.key}' expects text."
        if field.minimum is not None and len(value) < field.minimum:
            return f"'{field.key}' must not be empty."
        if field.maximum is not None and len(value) > field.maximum:
            # The modals' own slice length as a MAXIMUM, so the console cannot write a value Discord
            # would have cut.
            return f"'{field.key}' must be at most {field.maximum} characters."
        if any(not char.isprintable() for char in value):
            # NUL, a newline, a tab …: luadata.serialize would write it and DCS may misbehave at boot.
            return f"'{field.key}' must not contain control characters."
    elif field.kind == KIND_SEQ:
        if not isinstance(value, (list, tuple)):
            return f"'{field.key}' expects a list."
        if field.unique:
            seen: list[Any] = []
            for item in value:
                if item in seen:
                    return f"'{field.key}' contains a duplicate entry: {item!r}."
                seen.append(item)
    return None


def _f3_override_keys(locals_: Any) -> set[str]:
    """The dotted keys the server's ``servers.yaml`` entry PINS — ``locals['serverSettings']``.

    At startup ``ServerImpl._prepare`` copies every key of that block OVER the live ``serverSettings.lua``
    values, so an F1 edit of such a key without updating the block would silently REVERT at the next
    boot. The read returns this set as ``overridden`` — INFORMATION, not a prohibition: the write
    applies those keys like any other and keeps the block in sync (:func:`_sync_override`).
    """
    overrides: set[str] = set()
    block = (locals_ or {}).get("serverSettings")
    if not isinstance(block, dict):
        return overrides
    for key, value in block.items():
        if key == "advanced" and isinstance(value, dict):
            overrides.update(f"advanced.{leaf}" for leaf in value)
        else:
            overrides.add(key)
    return overrides


async def _sync_override(server: Any) -> bool:
    """Keep the server's ``servers.yaml`` override block in step with the settings just written.

    If the entry pins a ``serverSettings`` block — the F3 override ``ServerImpl._prepare`` applies over
    ``serverSettings.lua`` at startup — load ``servers.yaml`` from the NODE's config dir,
    ``utils.update_in_place`` that block from ``server.settings``, dump the file back and
    ``await server.reload()``, so the edit does not revert at the next start.

    ORDER: the rename (in :func:`_rename_server`) comes first, then the settings are written, then the
    override block is synced FROM them, then the reload — so every later reader sees ONE name.

    Returns whether the file was rewritten. A server that pins no block is left alone, and one whose
    name has no entry in ``servers.yaml`` is SKIPPED and logged: the settings write has already landed,
    but there is no entry to update, so the file and the reload are left untouched rather than crashing.
    """
    if not (getattr(server, "locals", None) or {}).get("serverSettings"):
        return False
    node = getattr(server, "node", None)
    config_dir = getattr(node, "config_dir", None)
    if not config_dir:
        # No node config dir to write to. Never an exception out of a transport: the settings write
        # has already landed, so the override is merely left as it was, and the log says so.
        log.warning("F3 override sync: server '%s' pins a serverSettings block but its node has no "
                    "config dir; servers.yaml was left untouched.", getattr(server, "name", "?"))
        return False
    config = os.path.join(config_dir, "servers.yaml")
    data = yaml.load(Path(config).read_text(encoding="utf-8"))
    entry = data.get(server.name) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        # HONEST BEHAVIOUR when the entry is ABSENT: the settings write has already
        # landed in ``serverSettings.lua``, but there is no ``servers.yaml`` entry to update — a
        # renamed server whose ``yaml`` key move never happened, or a hand-edited file. Nothing is
        # pretended and nothing is crashed: ``servers.yaml`` is left untouched, no reload runs, and
        # the log says WHY, so the operator knows the pinned key may still revert at the next start.
        # (``data[server.name]`` used to raise ``KeyError`` straight out of the whole action.)
        log.warning("F3 override sync: server '%s' has no entry in %s, so its override block could "
                    "not be updated; servers.yaml was left untouched and no reload was performed "
                    "(a pinned key may revert at the next start).",
                    getattr(server, "name", "?"), config)
        return False
    utils.update_in_place(entry.get("serverSettings", {}), server.settings)
    with Path(config).open("w", encoding="utf-8") as f:
        yaml.dump(data, f)
    await server.reload()
    return True


def _port_collision(ctx: Any, server: Any, port: Any) -> str | None:
    """The cross-field DCS-level check: *port* already held by another server of the master.

    Read from the CALLER's own view (``ctx.servers``), which for the Admin-only caller is the whole
    fleet. Returns the refusal naming the colliding server, or ``None``.
    """
    for other in (getattr(ctx, "servers", None) or {}).values():
        if other is None or other is server:
            continue
        try:
            other_port = int(_read_setting(other.settings, "port"))
        except (TypeError, ValueError):
            continue
        if other_port == port:
            return f"Port {port} is already used by server \"{getattr(other, 'name', '?')}\"."
    return None


def _live_fleet() -> dict:
    """The WHOLE fleet's ``{name: server}`` as the PROCESS's own registry holds it, or ``{}``.

    Resolved lazily through the service registry (the way ``plugins/mission/actions.py`` reaches the
    ban service), so this module keeps its layering — a console caller's ``ctx.servers`` is a scoped
    view, so a fleet entry outside it is invisible there, which is why :func:`_name_collision` reads
    this too. Never raises: no service bus yields an empty mapping.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
    except Exception:
        return {}
    servers = getattr(bus, "servers", None)
    return servers if isinstance(servers, dict) else {}


def _name_collision(ctx: Any, server: Any, new_name: Any) -> str | None:
    """The cross-field check for a NAME: *new_name* is already another server's. Returns the refusal.

    The twin of :func:`_port_collision`, deliberately wider about WHERE it looks: a name is a key in
    the fleet registry, and ``ctx.servers`` for a console caller is a scoped view. A name that
    collides with a server OUTSIDE that view is STILL a collision — ``ServiceBus.rename_server`` would
    OVERWRITE the invisible server's entry — so this reads the caller's view AND the process's own
    registry (:func:`_live_fleet`).

    Matching folds case: ``SRS-1`` and ``srs-1`` name ONE server. The refusal names the colliding
    server, like :func:`_port_collision`.
    """
    wanted = str(new_name)
    folded = wanted.casefold()
    seen: list[Any] = [server]
    for container in (getattr(ctx, "servers", None), _live_fleet()):
        for other in (container or {}).values():
            if other is None or any(other is candidate for candidate in seen):
                continue
            seen.append(other)
            other_name = str(getattr(other, "name", "") or "")
            if other_name and other_name.casefold() == folded:
                return f'Server name "{wanted}" is already used by server "{other_name}".'
    return None


def _admin(ctx: Any) -> bool:
    """Whether the caller holds the Admin role. No scope grant — roles only, fails closed."""
    return ADMIN_ROLE in (getattr(ctx, "roles", None) or ())


#: The console's CLUSTER roles: an identity holding one is never a "manager ONLY", so the manager
#: deny-list does not apply to it. Mirrors ``services.webservice.scope.CLUSTER_ROLES`` (this plugin
#: may not import the console package).
CLUSTER_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")


def _manages_only(ctx: Any) -> bool:
    """Whether the caller reaches this server as a MANAGER ALONE — the deny-list's own test.

    A manager draws access from the ``managed_by`` scope and NOT from a role; it is read through
    :attr:`core.actions.ActionContext.manages_only` AND excludes a caller that also holds a CLUSTER
    role (:data:`CLUSTER_ROLES`), so a DCS Admin who also declares a scope is not deny-listed.

    Keyed on the identity's KIND, not the transport label, and never raises: a context without the
    property is not a manager, so the deny-list applies only to a positive manager. A non-manager
    (Admin, DCS Admin, break-glass, a scope-less REST/MCP/Discord caller) is never denied a key.
    """
    try:
        if not getattr(ctx, "manages_only", False):
            return False
        return not (set(CLUSTER_ROLES) & set(getattr(ctx, "roles", None) or ()))
    except Exception:                     # pragma: no cover - a hostile context
        return False


def _authorised(ctx: Any) -> str | None:
    """The typed refusal a caller gets, or ``None`` when it may act.

    ONE rule re-checked SERVER-SIDE here, because REST/MCP reach the same action with no console route
    in front:

    * ``service`` (MCP) — REFUSED with :data:`SERVICE_REFUSAL`: the transport authenticates nobody yet,
      so this action will not accept a caller it cannot attribute;
    * ``plugin`` — ALLOWED, for the day an authenticated REST caller reaches this action: such a caller
      (``from_plugin``, actor ``API``) is admitted without the Admin role because its own scheme has
      authenticated it. Forward-looking — ``from_plugin`` has no production caller yet, so nothing live
      takes this branch today;
    * a MANAGER (:func:`_manages_only`) — ALLOWED, but only for the settings the manager deny-list
      permits (``core.server_config``): the config write and the channels write apply the deny-list
      themselves, so a denied key is refused with a typed sentence rather than a hidden form;
    * every other transport — the console with no manager scope, Discord, and a context nothing named —
      requires the Admin role (:data:`ADMIN_ONLY_SENTENCE`). A DCS Admin is refused on the READ and the
      WRITE alike, because a server's config carries its secrets and its own scope.

    Fails CLOSED: an unknown (or empty) transport takes the Admin branch, never the plugin one.
    """
    transport = getattr(ctx, "transport", "")
    if transport == TRANSPORT_SERVICE:
        return SERVICE_REFUSAL
    if transport == TRANSPORT_PLUGIN:
        return None
    if _admin(ctx) or _manages_only(ctx):
        return None
    return ADMIN_ONLY_SENTENCE


async def _audited_config(ctx: Any, result: ServerConfigResult,
                          server: Any = None) -> ServerConfigResult:
    """Write the trail for a CONFIG *result* and return it — one entry per attempt.

    The config twin of:func:`_audited` above (which words the maintenance flag's entries): ONE place
    per family, so every exit of both config halves leaves exactly one entry. The event text names the
    KEYS, never a value: it is built from ``result.message``, which lists applied/skipped keys.
    """
    await audit_action(ctx, result, server=server)
    return result


def _keys(d: Any) -> str:
    """The comma-separated, sorted key names of a mapping — for a message that names NO values."""
    return ", ".join(sorted(d))


def _set_message(name: str, applied: dict, skipped: dict) -> str:
    """The human report: what was saved and what was skipped, by KEY."""
    if applied and skipped:
        return f'Server "{name}": saved {_keys(applied)}; skipped {_keys(skipped)}.'
    if applied:
        return f'Server "{name}": saved {_keys(applied)}.'
    if skipped:
        return f'Server "{name}": nothing changed; skipped {_keys(skipped)}.'
    return f'Server "{name}": no configuration values were supplied.'


async def _rename_server(ctx: Any, server: Any, new_name: str) -> str | None:
    """Rename *server* THROUGH the bot's own ``Server.rename`` — never by writing ``settings`` alone.

    A name lives in ``serverSettings.lua`` AND in the server's key in ``servers.yaml``, the Discord
    channels, the database and the cluster's ``servers`` mapping; writing only ``settings['name']``
    would leave the object un-renamed and every other reader on the old name.

    Mirrors the Discord Save (``plugins/scheduler/views.py``): ``rename(update_settings=True)`` then
    re-key the master's ``servers`` mapping. That re-key is idempotent with the rename's own cluster
    re-key for the plugin/Discord transports; for the web console the bus is a per-request view, so the
    assignment is a no-op and the real re-key is the one ``rename`` performs.

    Returns ``None`` when the rename went through, or a typed reason when the bot REFUSED it — the
    caller must then write NOTHING. Both refusal shapes are handled: ``Server.rename`` SWALLOWS its own
    failures and returns, while a remote proxy re-raises, so an exception is caught here AND a rename
    that neither raised nor moved ``server.name`` is treated as refused too.
    """
    old_name = str(getattr(server, "name", ""))
    try:
        await server.rename(new_name=new_name, update_settings=True)
    except Exception as ex:
        log.exception("Config rename: server '%s' refused the rename to '%s'.", old_name, new_name)
        return f'Could not rename server "{old_name}" to "{new_name}": {ex}'
    if str(getattr(server, "name", old_name)) != str(new_name):
        # the implementation swallowed its own error: the name did NOT move, so the rename was refused
        # and the rest of the change must not be written either.
        return f'Server "{old_name}" refused the rename to "{new_name}".'
    servers = getattr(ctx, "servers", None)
    if isinstance(servers, dict):
        servers[new_name] = server
        servers.pop(old_name, None)
    return None


@action
async def get_server_config(ctx: Any, server_name: str) -> ServerConfigResult:
    """Read ONE server's DCS configuration — the flat ``serverSettings.lua`` shape.

    Authorised by :func:`_authorised`, enforced server-side because REST/MCP reach the same function
    with no console route in front. Returns, in ``result.data``:

    * ``values`` — the curated fields' current values, every SECRET key redacted to ``<set>``/``<unset>``;
      ``missionList`` and the coalition hash keys ride along read-only;
    * ``editable`` — the curated writable field keys;
    * ``overridden`` — the keys ALSO pinned by an F3 override, so a caller can say so without a second
      read — INFORMATION, not a prohibition: the write applies them and syncs the block;
    * ``status`` — the server's status, so the caller can state the stop/run rule.

    The read is deliberately NOT audited: it changes nothing. A name that does not resolve is the
    seam's own typed refusal.
    """
    refusal = _authorised(ctx)
    if refusal:
        return ServerConfigResult(success=False, server_name=server_name,
                                  refused=refusal, message=refusal)
    server = ctx.resolve_server(server_name)
    if server is None:
        message = f"Server '{server_name}' not found."
        return ServerConfigResult(success=False, server_name=server_name, refused=message,
                                  message=message)
    name = _label(server, server_name)
    values: dict[str, Any] = {}
    for field in _ALL_FIELDS:
        raw = _read_setting(server.settings, field.key)
        values[field.key] = _redact(raw) if field.secret else raw
    return ServerConfigResult(
        success=True, server_name=name,
        message=f'Configuration for server "{name}".',
        data={
            "values": values,
            "editable": [field.key for field in DCS_FIELDS],
            "overridden": sorted(_f3_override_keys(getattr(server, "locals", None))),
            "status": server.status.value,
        })


@action
async def set_server_config(ctx: Any, server_name: str,
                            values: dict) -> ServerConfigResult:
    """Write ONE server's DCS configuration — the flat ``serverSettings.lua`` shape.

    The order, and why it matters:

    1. AUTHORISE here (:func:`_authorised`) — a service/MCP caller is refused, a REST/plugin caller is
       allowed, and a DCS Admin or a scoped manager is refused;
    2. REFUSE while the server is up (``RUNNING``/``PAUSED``/``LOADING``/``SHUTTING_DOWN``) with the
       stop-first sentence, server-side, so a direct call gets it too;
    3. VALIDATE every key against the curated table + types + ranges and the cross-field checks (the
       ``port`` collision) — invalid keys are SKIPPED with a reason, never written. A ``values`` that is
       not a MAPPING is a typed skip too, so a caller's mistake is never an ``AttributeError`` a
       transport renders as a 500;
    4. hold the per-server WRITE lock across the read-modify-write;
    5. RENAME FIRST, through the bot's own ``Server.rename`` (:func:`_rename_server`), so
       ``serverSettings.lua``, the ``servers.yaml`` key and the cluster move together. Two guards run
       BEFORE that rename, inside the same lock: the reserved sentinel ``n/a``
       (:data:`NO_NAME_SENTINEL`) and a name another server already carries (:func:`_name_collision`,
       checked against the caller's view AND the whole fleet) are each a WHOLE-ACTION typed refusal —
       neither may reach the bot;
    6. apply each remaining accepted key through ``server.settings`` (the existing ``SettingsDict`` path);
    7. audit once with the actor, the server and the KEYS, and return the old -> new of every applied
       key so a caller can offer a revert through this same action.

    A REFUSED rename is a WHOLE-ACTION typed refusal with NOTHING written (settings, ``servers.yaml``
    key and override block left as they were) — no half-applied state. A key pinned by an F3 override is
    APPLIED like any other, then the block is updated in place and the server reloaded
    (:func:`_sync_override`), so the value does not revert at the next start. The whole read-modify-write
    is ONE critical section under the per-server write lock.

    A submitted BLANK secret means UNCHANGED (whitespace-only included); ``None`` is the explicit CLEAR.
    There is no ``confirmed`` parameter: an accepted-but-ignored one would invite a caller to believe a
    confirmation happened.
    """
    refusal = _authorised(ctx)
    if refusal:
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=server_name, refused=refusal, message=refusal))
    server = ctx.resolve_server(server_name)
    if server is None:
        message = f"Server '{server_name}' not found."
        return await _audited_config(ctx, ServerConfigResult(success=False, server_name=server_name,
                                                             refused=message, message=message))
    name = _label(server, server_name)
    # TOCTOU: this status is read BEFORE the write lock below, so a concurrent START can move the
    # server to RUNNING between the two and this action would still write. The window is narrow and
    # SettingsDict's mtime re-read narrows it further; the lock does NOT close it, because the
    # start/power path takes no such lock. The exposure is the same one /server config already had.
    if server.status not in WRITABLE_STATES:
        reason = f'Server "{name}" is {server.status.value}. {STOP_FIRST_SENTENCE}'
        return await _audited_config(ctx, ServerConfigResult(success=False, server_name=name,
                                                             refused=reason, message=reason), server)
    if not isinstance(values, dict):
        reason = "'values' must be a mapping of setting names to values."
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=name, skipped={"values": reason},
            message=f'Server "{name}": {reason}'), server)
    # THE MANAGER DENY-LIST, enforced HERE and not only at the console route (hiding a form is not the
    # control): a submitted denied key is a WHOLE-ACTION typed refusal — nothing is written, and the
    # sentence reaches the operator. Checked before the write lock, so a refused write locks nothing.
    if _manages_only(ctx):
        denial = manager_denial(values)
        if denial:
            return await _audited_config(ctx, ServerConfigResult(
                success=False, server_name=name, refused=denial, message=denial), server)

    applied: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    async with server_write_lock(str(server.name)):
        # A submitted NAME goes through the bot's own rename FIRST, still inside this critical section
        # (:func:`_rename_server`), before any other key is written. A REFUSED rename returns here with
        # NOTHING written; an INVALID name is skipped with its reason right here, so the two paths
        # cannot word the same refusal differently.
        old_name = str(server.name)
        name_handled = False
        if "name" in values and isinstance(values["name"], str) and values["name"] != old_name:
            name_handled = True
            name_reason = validate_value(_FIELDS_BY_KEY["name"], values["name"])
            if name_reason:
                skipped["name"] = name_reason
            else:
                # Two guards run BEFORE the bot's rename, both WHOLE-ACTION typed refusals (nothing
                # written): a colliding name would OVERWRITE another server's registry entry and the
                # ``n/a`` sentinel takes ``ServerImpl.rename``'s no-rollback path. Neither can be a
                # per-key SKIP — the settings, ``servers.yaml`` and the registry must all stay
                # untouched.
                guard_reason = (NAME_RESERVED_SENTENCE if values["name"] == NO_NAME_SENTINEL
                                else _name_collision(ctx, server, values["name"]))
                if guard_reason:
                    return await _audited_config(ctx, ServerConfigResult(
                        success=False, server_name=old_name, refused=guard_reason,
                        applied={}, skipped=skipped, message=guard_reason), server)
                refusal_reason = await _rename_server(ctx, server, values["name"])
                if refusal_reason:
                    return await _audited_config(ctx, ServerConfigResult(
                        success=False, server_name=old_name, refused=refusal_reason,
                        applied={}, skipped=skipped, message=refusal_reason), server)
                applied["name"] = _redacted_applied("name", old_name, values["name"])
        for key, value in values.items():
            if key == "name" and name_handled:
                continue                    # applied (or skipped) through the bot's rename above
            field = _FIELDS_BY_KEY.get(key)
            if field is None:
                skipped[key] = (f"'{key}' is read-only and is never written here." if key in
                                _READONLY_BY_KEY else f"'{key}' is not an editable setting.")
                continue
            if field.secret:
                if value is None:                      # the explicit CLEAR
                    new_value: Any = ""
                elif isinstance(value, str):
                    if not value.strip():
                        # blank OR whitespace-only: an unchanged submission, not a one-space password.
                        skipped[key] = f"'{key}' was left blank and is unchanged."
                        continue
                    new_value = value
                    reason = validate_value(field, new_value)
                    if reason:                         # the modal's own length, and no control chars
                        skipped[key] = reason
                        continue
                else:
                    skipped[key] = f"'{key}' expects text."
                    continue
            else:
                reason = validate_value(field, value)
                if reason:
                    skipped[key] = reason
                    continue
                new_value = value
            if key == "port":
                collision = _port_collision(ctx, server, new_value)
                if collision:
                    skipped[key] = collision
                    continue
            old = _read_setting(server.settings, key)
            await _apply_setting(server, key, new_value)
            applied[key] = _redacted_applied(key, old, new_value)
        # Sync the F3 override from what was just written and reload, so a pinned key never reverts at
        # the next start — still inside the one critical section.
        await _sync_override(server)

    result = ServerConfigResult(
        success=bool(applied), server_name=name, applied=applied, skipped=skipped,
        message=_set_message(name, applied, skipped))
    return await _audited_config(ctx, result, server)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# The BOT FACE of per-server configuration — the ``servers.yaml`` channels
# ═══════════════════════════════════════════════════════════════════════════════════════════════
#
# F2 is ``config/servers.yaml``, the server's entry — here its ``channels`` map. This action writes
# that map through the bot's own helper ``Server.update_channels`` (``core/data/impl/serverimpl.py``
# / ``core/data/proxy/serverproxy.py``), which rewrites the entry, updates the in-memory ``locals`` and
# clears the channel cache — so a change is effective AT ONCE, with no restart, and an agent-hosted
# server's master-side view follows too. It does not hand-roll YAML: the bot owns the file, its schema
# and its comment-preserving round-trip.

#: The per-server channel keys this action writes — the trio the console edits and the Discord editor
#: offers (``plugins/scheduler/views.py``): ``status``, ``chat`` and ``admin``.
CHANNEL_KEYS: tuple[str, ...] = ("status", "chat", "admin")

#: The key that is NOT per-server when ``bot.yaml`` declares a central admin channel — the action
#: SKIPS it there, as the Discord command does.
CHANNEL_ADMIN_KEY = "admin"

#: The bot's own "channel disabled" marker (``schemas/servers_schema.yaml``: ``range: {min: -1}``;
#: ``plugins/scheduler/commands.py`` passes ``-1`` for an unset channel). "Not set" writes THIS.
CHANNEL_UNSET = -1


def _live_bot() -> Any:
    """The running bot, through the service registry, or ``None`` — the guild's channel list's source.

    Resolved lazily so this module keeps its layering (no module-level ``services`` import). Never
    raises: no bot yields ``None``, and the action then cannot validate a channel id.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        return getattr(ServiceRegistry.get(BotService), "bot", None)
    except Exception:  # noqa: BLE001 - a missing/early registry must not raise out of a transport
        return None


def _central_admin_channel(bot: Any) -> str:
    """The central admin channel id from ``bot.yaml`` (``bot.locals['channels']['admin']``) or ``""``.

    The same fact the read model reads (``readmodels.serverconfig.central_admin_channel``), so the row
    not being RENDERED and the key not being WRITTEN have one source.
    """
    locals_ = getattr(bot, "locals", None)
    if not isinstance(locals_, dict):
        return ""
    channels = locals_.get("channels")
    if not isinstance(channels, dict):
        return ""
    admin = channels.get("admin")
    return "" if admin in (None, "") else str(admin)


def _guild_channel_ids(bot: Any) -> set[int] | None:
    """Every channel id of EVERY guild the bot is in, or ``None`` when no guild could be read.

    ``None`` is the fail-closed answer: with no guild to compare against, a submitted id cannot be
    shown to belong to it, so the write is refused rather than trusted. A guild with no channels yields
    an EMPTY set (a real guild with none), which differs from ``None`` (no guild at all).
    """
    if bot is None:
        return None
    ids: set[int] = set()
    found = False
    for guild in (getattr(bot, "guilds", None) or ()):
        found = True
        for channel in (getattr(guild, "channels", None) or ()):
            channel_id = getattr(channel, "id", None)
            if channel_id is None:
                continue
            try:
                ids.add(int(channel_id))
            except (TypeError, ValueError):
                continue
    return ids if found else None


def _channel_int(value: Any) -> int:
    """A stored/submitted channel value as an int: the id, or :data:`CHANNEL_UNSET` when unreadable."""
    if value is None or isinstance(value, bool):
        return CHANNEL_UNSET
    try:
        return int(value)
    except (TypeError, ValueError):
        return CHANNEL_UNSET


@action
async def set_server_channels(ctx: Any, server_name: str, channels: dict) -> ServerConfigResult:
    """Write ONE server's ``servers.yaml`` CHANNELS — the BOT face of the same tab.

    The order mirrors :func:`set_server_config`:

    1. AUTHORISE here (:func:`_authorised`) — the same rule as the DCS write, so one capability governs
       both faces;
    2. resolve the server — an unknown name is the seam's own typed refusal;
    3. VALIDATE every submitted key: it must be one of :data:`CHANNEL_KEYS`, and its value must be the
       unset marker or a channel id the bot's guild holds. A foreign or unknown id, a non-numeric value,
       and a per-server ``admin`` submitted while a central one is defined are each SKIPPED with a
       reason, never written (and never a 500);
    4. hold the per-server WRITE lock — the same lock the DCS face takes, so the two faces cannot
       interleave;
    5. write through the bot's own ``Server.update_channels``, only the rows this console CHANGES: the
       helper merges them onto the FILE's current map on the owning node, so a key this action does not
       edit is PRESERVED without shipping a stale copy;
    6. audit once with the actor, the server and the KEYS.

    Only CHANGED rows are applied. There is NO state gate: a channel change is applied immediately and
    needs no restart, so a RUNNING server is written like any other.
    """
    refusal = _authorised(ctx)
    if refusal:
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=server_name, refused=refusal, message=refusal))
    server = ctx.resolve_server(server_name)
    if server is None:
        message = f"Server '{server_name}' not found."
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=server_name, refused=message, message=message))
    name = _label(server, server_name)
    # THE MANAGER DENY-LIST: the channels write is denied to a manager as a whole (the declared
    # ``CHANNELS_ITEM``), enforced server-side so a direct call is refused too — the form is not the
    # control, this is.
    if _manages_only(ctx):
        denial = MANAGER_DENIED[CHANNELS_ITEM]
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=name, refused=denial, message=denial), server)
    if not isinstance(channels, dict):
        reason = "'channels' must be a mapping of channel names to ids."
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=name, skipped={"channels": reason},
            message=f'Server "{name}": {reason}'), server)

    bot = _live_bot()
    central = _central_admin_channel(bot)
    guild_ids = _guild_channel_ids(bot)
    current_map = (getattr(server, "locals", None) or {}).get("channels")
    current = current_map if isinstance(current_map, dict) else {}

    applied: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    async with server_write_lock(str(server.name)):
        for key, value in channels.items():
            if key not in CHANNEL_KEYS:
                skipped[key] = f"'{key}' is not an editable channel."
                continue
            if key == CHANNEL_ADMIN_KEY and central:
                skipped[key] = ("'admin' is defined centrally in bot.yaml and is not a per-server "
                                "setting here.")
                continue
            if value is None:
                new_value = CHANNEL_UNSET
            else:
                try:
                    new_value = int(value)
                except (TypeError, ValueError):
                    skipped[key] = f"'{key}' expects a channel id."
                    continue
                if new_value != CHANNEL_UNSET:
                    if guild_ids is None:
                        skipped[key] = ("the bot is not connected to a Discord guild, so a channel "
                                        "id cannot be validated.")
                        continue
                    if new_value not in guild_ids:
                        skipped[key] = f"channel {new_value} is not a channel of this guild."
                        continue
            old_value = _channel_int(current.get(key))
            if old_value == new_value:
                skipped[key] = f"'{key}' is unchanged."
                continue
            applied[key] = {"from": old_value, "to": new_value}
        if applied:
            # ONLY the rows this console CHANGES travel; ``update_channels`` merges them onto the
            # FILE's current map on the owning node, so a key this console does not edit is preserved
            # without shipping a stale copy of it.
            try:
                await server.update_channels({key: change["to"] for key, change in applied.items()})
            except Exception as ex:                     # a transport never gets a stack trace
                log.exception("Config: could not write the channels of server '%s'.", name)
                reason = f'Failed to write the channels of server "{name}": {ex}'
                return await _audited_config(ctx, ServerConfigResult(
                    success=False, server_name=name, refused=reason,
                    applied=applied, skipped=skipped, message=reason), server)

    result = ServerConfigResult(
        success=bool(applied), server_name=name, applied=applied, skipped=skipped,
        message=_set_message(name, applied, skipped))
    return await _audited_config(ctx, result, server)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# The COALITION PASSWORDS — the blue/red join passwords, read from the DATABASE and written through
# the bot's OWN method
# ═══════════════════════════════════════════════════════════════════════════════════════════════
#
# ``serverSettings.lua`` carries only a HASH for each coalition; the CLEARTEXT lives in the ``servers``
# table (``blue_password`` / ``red_password``) and the ONE writer is ``Server.setCoalitionPassword``
# (``core/data/impl/serverimpl.py`` / ``core/data/proxy/serverproxy.py``): it tells DCS live while the
# server is up, writes the hash into ``serverSettings.lua`` while it is not, and ALWAYS updates the
# database row. These two actions add the console's read and write of that face WITHOUT re-implementing
# any of it: the read is a SELECT on the master's own pool, and the write calls the bot's own method.
#
# TRANSPORT: unlike the DCS/channels faces, these two REFUSE ``TRANSPORT_PLUGIN`` (and MCP the MCP way
# the other config actions already do). The REST surface up to now has no consumer for a coalition
# password, and admitting one would turn any API key into a fleet-wide plaintext reader — so the door
# stays shut until a caller is wanted explicitly (a one-line change here). ONLY the console and Discord
# reach the role check: they are the two callers that exist, they carry a ROLE or a SCOPE, and they
# reach it through :func:`_coalition_authorised` — every other transport, an unknown or unnamed one
# included, is refused OUTRIGHT there, never fallen through to the role check.

#: The coalition tokens the write accepts, mapped to the enum the bot's method takes.
COALITION_KEYS: dict[str, Coalition] = {"bluePassword": Coalition.BLUE,
                                        "redPassword": Coalition.RED}

#: The states a coalition change may be made in. Unlike the DCS config write (which refuses a running
#: server), the bot's own method works WHILE the server is up — it tells DCS live — so ``RUNNING`` and
#: ``PAUSED`` are allowed. ``LOADING`` / ``SHUTTING_DOWN`` / ``UNREGISTERED`` are refused: the
#: file-writing branch would race the process's own file handling.
WRITABLE_COALITION_STATES: tuple[Status, ...] = (Status.SHUTDOWN, Status.STOPPED, Status.RUNNING,
                                                 Status.PAUSED)

#: The typed refusal a state that cannot take the change gets — its OWN wording, never the Save's.
COALITION_STATE_SENTENCE = ("The coalition passwords can only be changed while the server is shutdown, "
                            "stopped, running or paused. Wait for it to settle, then edit here.")

#: The typed refusal a REST/plugin caller gets (see the section note above).
COALITION_REST_REFUSAL = ("The coalition passwords are not reachable from the REST/plugin transport: "
                          "this action will not hand a server's coalition password to a caller it "
                          "cannot attribute to the admin console or Discord.")

#: The typed refusal a caller from an UNKNOWN (or unnamed) transport gets — the door is CLOSED, never
#: fallen through to the role check. Only the admin console and Discord may reach these two actions,
#: so a context whose transport is neither is refused outright, whatever roles it claims.
COALITION_TRANSPORT_REFUSAL = ("The coalition passwords are not reachable from an unnamed transport: "
                               "this action will not hand a server's coalition password to a caller "
                               "it cannot attribute to the admin console or Discord.")

#: The outcome wording, VERBATIM from the Discord command (``plugins/scheduler/commands.py``), so a
#: change reads the same however it was made.
NEXT_RESTART_SENTENCE = "Password will be changed on next server restart."
PASSWORD_CHANGED_SENTENCE = "Password changed."
PASSWORD_CLEARED_SENTENCE = "Password cleared."


def _coalition_authorised(ctx: Any) -> str | None:
    """The typed refusal a caller of a coalition action gets, or ``None`` when it may act.

    Its own rule, NOT :func:`_authorised`'s: MCP is refused the shared way (:data:`SERVICE_REFUSAL`),
    the REST/plugin transport is refused TOO (:data:`COALITION_REST_REFUSAL`) — the DCS config
    face admits it, this one does not, because a coalition password has no REST consumer and an API key
    must not become a fleet-wide plaintext reader. ONLY the admin console and Discord may reach these
    two actions: any other transport — an unknown label, or ``""`` — is refused OUTRIGHT
    (:data:`COALITION_TRANSPORT_REFUSAL`), never fallen through to the role check, so a future context
    builder that forgets to stamp ``transport`` while carrying Admin roles is admitted by nothing.
    Admin and a manager (for their own server, the scope applied by ``resolve_server``) are admitted
    like every other per-server view.

    The door is CLOSED: no transport reaches the role check except the console and Discord.
    """
    transport = getattr(ctx, "transport", "")
    if transport == TRANSPORT_SERVICE:
        return SERVICE_REFUSAL
    if transport == TRANSPORT_PLUGIN:
        return COALITION_REST_REFUSAL
    if transport not in (TRANSPORT_WEB, TRANSPORT_DISCORD):
        return COALITION_TRANSPORT_REFUSAL
    if _admin(ctx) or _manages_only(ctx):
        return None
    return ADMIN_ONLY_SENTENCE


async def _audited_coalition(ctx: Any, result: ServerConfigResult,
                             server: Any = None) -> ServerConfigResult:
    """Write the trail for a coalition *result* and return it — ONE entry per coalition changed.

    A SUCCESSFUL change uses the Discord command's OWN words
    (``self.bot.audit("changed password for coalition …")``) so a console change is as traceable as a
    Discord one; and, like the Discord command, it writes ONE ENTRY PER COALITION actually changed
    (``… for coalition blue`` and ``… for coalition red``, not one line naming both), so a form that
    changes both leaves exactly the two lines Discord leaves. Every other outcome (a refusal, a
    failure, a no-op) is a SINGLE entry using the result's own message, which names the reason and
    never a value. The audit text names ONLY the coalition — never the password.
    """
    applied = getattr(result, "applied", None) or {}
    if getattr(result, "success", False):
        tokens = [COALITION_KEYS[key].value for key in applied if key in COALITION_KEYS]
        if tokens:
            for token in tokens:
                await audit_action(ctx, result, server=server,
                                   message="changed password for coalition " + token)
            return result
    await audit_action(ctx, result, server=server)
    return result


def _coalition_hash_set(server: Any, coalition: str) -> bool:
    """Whether ``serverSettings.lua`` carries a hash for *coalition*, read IN-PROCESS (no RPC).

    The presence test only — the hash itself is never read out. A settings object that cannot answer
    counts as \"no hash\": a wrong guess costs a note, never the page.
    """
    settings = getattr(server, "settings", None)
    if settings is None:
        return False
    try:
        advanced = _read_setting(settings, "advanced")
    except Exception:                     # pragma: no cover - a hostile settings object
        return False
    if not isinstance(advanced, dict):
        return False
    return bool(str(advanced.get(f"{coalition}PasswordHash") or "").strip())


async def _read_coalition_plaintext(server: Any) -> tuple[Any, Any]:
    """The ``(blue, red)`` cleartext of *server* from the ``servers`` table — or ``(None, None)``.

    Read on the server's OWN pool (the console runs inside the bot's process, so this is a query, not
    a wire — the same SELECT ``plugins/mission/commands.py`` and ``plugins/gamemaster/listener.py``
    already make). Tolerant throughout: a double without a pool, a connection error and a missing row
    all yield ``(None, None)``, so a render can never 500 on the database.
    """
    pool = getattr(server, "apool", None)
    if pool is None:
        return None, None
    try:
        async with pool.connection() as conn:
            cursor = await conn.execute(
                'SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                (server.name,))
            row = await cursor.fetchone()
    except Exception:                     # noqa: BLE001 - a read must never raise out of a transport
        log.exception("Coalition read: could not read the passwords of server '%s'.",
                      getattr(server, "name", "?"))
        return None, None
    if row is None:
        return None, None
    return row[0], row[1]


@action
async def get_server_coalitions(ctx: Any, server_name: str) -> ServerConfigResult:
    """Read ONE server's coalition passwords — the cleartext the bot holds, plus which hashes are set.

    A READ: it is not audited (it changes nothing) and the console reaches it through
    ``core.actions.read_action``, the UNGUARDED twin of ``call_action`` — a render must be able to read
    while a power action is running, which ``call_action``'s in-flight guard would refuse.

    Returns, in ``result.data``:
    ``{"blue": <str|None>, "red": <str|None>, "blue_hash_set": <bool>, "red_hash_set": <bool>}``.
    ``blue``/``red`` is the database cleartext (``None`` when the row holds nothing); ``*_hash_set`` is
    whether ``serverSettings.lua`` carries a hash, read in-process. The HASH is never returned.

    Authorised by :func:`_coalition_authorised`, enforced here because REST/MCP reach the same function
    with no console route in front. A manager resolves only their OWN servers (the scope is applied by
    ``resolve_server``'s bus), so a foreign server is the seam's own \"not found\" refusal.
    """
    refusal = _coalition_authorised(ctx)
    if refusal:
        return ServerConfigResult(success=False, server_name=server_name, refused=refusal,
                                  message=refusal)
    server = ctx.resolve_server(server_name)
    if server is None:
        message = f"Server '{server_name}' not found."
        return ServerConfigResult(success=False, server_name=server_name, refused=message,
                                  message=message)
    name = _label(server, server_name)
    blue, red = await _read_coalition_plaintext(server)
    return ServerConfigResult(
        success=True, server_name=name,
        message=f'Coalition passwords for server "{name}".',
        data={
            "blue": blue,
            "red": red,
            "blue_hash_set": _coalition_hash_set(server, "blue"),
            "red_hash_set": _coalition_hash_set(server, "red"),
        })


@action
async def set_coalition_password(ctx: Any, server_name: str, values: dict) -> ServerConfigResult:
    """Write ONE server's coalition passwords THROUGH the bot's own ``setCoalitionPassword``.

    The order mirrors :func:`set_server_config`:

    1. AUTHORISE here (:func:`_coalition_authorised`) — MCP and the REST/plugin transport are refused,
       Admin and a manager-only caller are admitted;
    2. resolve the server — an unknown name is the seam's own typed refusal;
    3. refuse a state that cannot take the change (:data:`WRITABLE_COALITION_STATES`), server-side, so a
       direct caller gets the SAME sentence the console shows;
    4. hold the per-server WRITE lock — the same lock the DCS and channels faces share;
    5. for each submitted coalition, call ``server.setCoalitionPassword(Coalition, password)``. It is
       NEVER the hash and never a direct ``settings``/``servers`` write: the bot's own method decides
       live-vs-file and owns the database row;
    6. audit once, in the Discord command's own words.

    The submitted-value rules, stated so the page diff and this cannot disagree: ``None`` is the
    explicit CLEAR (the password is removed); a blank/whitespace-only string is UNCHANGED (a no-op,
    never a one-space password); anything else is the new password. There is no length bound — the
    Discord modal imposes none and the bot's method takes what it is given — and, because the console
    sends only CHANGED coalitions, an untouched form submits nothing.

    The result NEVER carries a value: ``applied`` names each written coalition with a redacted
    ``<set>``/``<not set>`` marker, and the audit and message name the coalition only.
    """
    refusal = _coalition_authorised(ctx)
    if refusal:
        return await _audited_coalition(ctx, ServerConfigResult(
            success=False, server_name=server_name, refused=refusal, message=refusal))
    server = ctx.resolve_server(server_name)
    if server is None:
        message = f"Server '{server_name}' not found."
        return await _audited_coalition(ctx, ServerConfigResult(
            success=False, server_name=server_name, refused=message, message=message))
    name = _label(server, server_name)
    if server.status not in WRITABLE_COALITION_STATES:
        reason = f'Server "{name}" is {server.status.value}. {COALITION_STATE_SENTENCE}'
        return await _audited_coalition(ctx, ServerConfigResult(
            success=False, server_name=name, refused=reason, message=reason), server)
    if not isinstance(values, dict):
        reason = "'values' must be a mapping of coalition password names to values."
        return await _audited_coalition(ctx, ServerConfigResult(
            success=False, server_name=name, skipped={"values": reason},
            message=f'Server "{name}": {reason}'), server)

    applied: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    cleared_only = True
    async with server_write_lock(str(server.name)):
        for key, value in values.items():
            coalition = COALITION_KEYS.get(key)
            if coalition is None:
                skipped[key] = f"'{key}' is not a coalition password."
                continue
            if value is None:
                password = ""                   # the explicit CLEAR
            elif isinstance(value, str):
                if not value.strip():
                    skipped[key] = f"'{key}' was left blank and is unchanged."
                    continue
                password = value                # a typed password IS the new password
                cleared_only = False
            else:
                skipped[key] = f"'{key}' expects text."
                continue
            try:
                await server.setCoalitionPassword(coalition, password)
            except Exception as ex:             # a transport never gets a stack trace
                log.exception("Coalition write: could not set the %s password of server '%s'.",
                              coalition.value, name)
                reason = f'Failed to change the coalition password of server "{name}": {ex}'
                return await _audited_coalition(ctx, ServerConfigResult(
                    success=False, server_name=name, refused=reason,
                    applied=applied, skipped=skipped, message=reason), server)
            # REDACTED on purpose: the plaintext is never carried in the result (the audit and the
            # notice name the coalition only). The keys are NOT in ``_SECRET_KEYS``, so this is done
            # explicitly rather than through ``_redacted_applied``.
            applied[key] = {"to": SET_SENTINEL if password else UNSET_SENTINEL}

    if not applied:
        message = (f'Server "{name}": nothing changed; skipped {_keys(skipped)}.' if skipped
                   else f'Server "{name}": no coalition password values were supplied.')
        return await _audited_coalition(ctx, ServerConfigResult(
            success=False, server_name=name, applied={}, skipped=skipped, message=message), server)
    if server.status in (Status.RUNNING, Status.PAUSED):
        message = NEXT_RESTART_SENTENCE
    elif cleared_only:
        message = PASSWORD_CLEARED_SENTENCE
    else:
        message = PASSWORD_CHANGED_SENTENCE
    return await _audited_coalition(ctx, ServerConfigResult(
        success=True, server_name=name, applied=applied, skipped=skipped, message=message), server)

