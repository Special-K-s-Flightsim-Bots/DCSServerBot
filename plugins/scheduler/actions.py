"""The MAINTENANCE FLAG's action pair — set / clear it for ONE server — and
the DCS-FACE CONFIG actions — read / write a server's ``serverSettings.lua``
the design of record ``CONFIGURATION.md``).

THE CONFIG PAIR (``get_server_config`` / ``set_server_config``) is the DCS face (F1): the flat
``serverSettings.lua`` read and written through the EXISTING ``Server.settings`` ``SettingsDict`` —
the same path ``/server config`` (``plugins/scheduler/commands.py``) uses. ``CONFIGURATION.md`` §4.2
is the shape; §5 the validation, §6 the roles (Admin only, no scope grant — plus §2's rule:
the REST/plugin transport is allowed and the MCP/service one is refused until it authenticates),
§7 the secrets (write-only)
and §8.2-8.4 the undo, the per-server write lock and the F3-override refusal. The read returns
``values`` (secrets redacted), ``editable``, ``overridden`` and ``status`` in ``result.data``; the
write returns the old -> new of every applied key in ``ServerConfigResult.applied``.

The MAINTENANCE pair below is unchanged and unrelated: ``/scheduler maintenance`` / ``/scheduler
clear`` and the console's server-row pair all reach:func:`set_maintenance` /:func:`clear_maintenance`,
so the console and Discord cannot drift into two meanings for one flag (``MAINTENANCE.md`` §4.3, §6,
§7). The Discord commands keep the ONE thing an action cannot carry — the ``yn_question`` that warns
about an aborted pending restart — and delegate the change itself to here.

The trail is written HERE, once, whatever the transport (design §5.3 D1), including for a refusal:
one entry per attempt is what an operator wants after an incident.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.action_results import ServerConfigResult, ServerControlResult
from core.actions import (TRANSPORT_PLUGIN, TRANSPORT_SERVICE, action, audit_action,
                          server_write_lock)
from core.data.const import Status

log = logging.getLogger(__name__)

__all__ = [
    # the maintenance flag pair (W5b)
    "ABORTED_BY_MAINTENANCE", "set_maintenance", "clear_maintenance",
    # the DCS-face config pair (B1) and its curated table
    "ADMIN_ROLE", "ADMIN_ONLY_SENTENCE", "SERVICE_REFUSAL", "STOP_FIRST_SENTENCE", "WRITABLE_STATES",
    "TRANSPORT_PLUGIN", "TRANSPORT_SERVICE",
    "DCSField", "DCS_FIELDS", "DCS_READONLY_FIELDS", "validate_value",
    "get_server_config", "set_server_config",
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

    A server that is ALREADY in the requested state is a TYPED REFUSAL, never a silent re-write: the
    console's control is only offered in the state that applies (``WriteAction.when_maintenance``),
    so a second attempt means a stale page or another transport, and both deserve to be told the
    flag did not move rather than a success they cannot see. This mirrors the two Discord commands'
    own \"is already in maintenance mode\" / \"is not in maintenance mode\" answers, word for word.
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
    """Write the trail for *result* and return it (design §5.3 D1).

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
# The DCS FACE of per-server configuration — ``CONFIGURATION.md`` §4.2 / §5 / §6 / §7 / §8
# ═══════════════════════════════════════════════════════════════════════════════════════════════
#
# F1 is ``Saved Games/<instance>/Config/serverSettings.lua``, read and written through
# ``Server.settings`` (a ``SettingsDict``, ``core/utils/helper.py``). This slice EDITS that file and
# nothing else: no ``servers.yaml`` (F2), no live RPC (D5/D8), no ``Server.set_config``.

#: The role the config pair requires — Admin only, NO scope grant (D6, §6). It is the console's
#: ``NODE_ROLES`` shape ("Admin",) and NOT the scope-granted ``WRITE_ROLES`` ("Admin", "DCS Admin"):
#: a server's configuration carries SECRETS (§7) and its own scope, so a DCS Admin or a scoped manager
#: gets a typed refusal here, not a capability. The Discord ``/server config`` command keeps its OWN
#: ``DCS Admin`` door (§2.1) — the two doors are deliberately different, and that command is NOT
#: re-pointed at these actions in this card.
ADMIN_ROLE = "Admin"

#: The typed refusal a NON-Admin caller gets. The design names no wording (§6 gives the route's 403);
#: this is the action's own sentence, kept short and free of the server's data.
ADMIN_ONLY_SENTENCE = "Changing a server's configuration requires the Admin role."

#: The typed refusal a SERVICE (MCP) caller gets (§2, the decision 2026-09-30). The MCP
#: transport has no authentication yet, so this action will not accept a caller it cannot ATTRIBUTE —
#: an unauthenticated write of a server's config (which carries secrets, §7) is exactly what the
#: in-action check exists to prevent.
#:
#: ⚠ REVISIT WHEN MCP AUTHENTICATION LANDS: at that point the authenticated MCP identity can be
#: mapped onto this rule (the maintainer decides how — an MCP role, a trusted-identity claim, or a scoped
#: grant); until then this refusal stands and is not to be relaxed by widening the role check.
SERVICE_REFUSAL = ("The MCP/service transport has no authentication yet, so a server's configuration "
                   "cannot be changed from it: this action will not accept a caller it cannot "
                   "attribute. Use the admin console, or a REST/plugin caller whose own scheme has "
                   "authenticated it.")

#: The stop-first sentence (§5.3, D4), verbatim from the design. The template the Discord
#: ``/server config`` flow already follows ("It has to be stopped to change its configuration.",
#: ``plugins/scheduler/commands.py``); the console must give the same answer.
STOP_FIRST_SENTENCE = ("The server must be stopped to change its configuration. "
                       "Stop it on the Servers page, then edit here.")

#: The ONLY statuses a config write is allowed in (§5.3, D4). ``LOADING`` and ``SHUTTING_DOWN`` are
#: transitional and are ALSO refused — the file is being read or the process is dying, the exact race
#: ``SettingsDict``'s mtime check (``helper.py``) cannot win. ``STOPPED`` is writable: ``/server
#: config`` stops the server (which lands it in ``STOPPED``) and then edits.
WRITABLE_STATES = (Status.SHUTDOWN, Status.STOPPED, Status.UNREGISTERED)

#: What a redacted secret reads as in the read model and in ``applied`` (§7.1/§7.2) — the VALUE is
#: never returned, only whether one is set.
SET_SENTINEL = "<set>"
UNSET_SENTINEL = "<unset>"

#: The four kinds the field table uses (§5.1). ``luadata.serialize`` will faithfully write whatever
#: Python object it is given, so the TYPE check is the guard against a value that stops the server.
KIND_BOOL = "bool"
KIND_INT = "int"
KIND_STR = "str"
KIND_SEQ = "seq"


@dataclass(frozen=True)
class DCSField:
    """One curated field of the DCS face — the schema ``serverSettings.lua`` does NOT have (§5.1).

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


#: The CURATED editable set (§5, D10) — the DCS fields the four scheduler modals cover, nothing else.
#: Ranges mirror the modals' own limits (``views.py``: port ``max_length=5``, ``maxPlayers`` 3,
#: ``maxPing`` 3) and the ``resume_mode`` enum is their three Select options.
DCS_FIELDS: tuple[DCSField, ...] = (
    DCSField("name", KIND_STR, "The server's name as DCS shows it.", minimum=1),
    DCSField("description", KIND_STR, "The server's description."),
    DCSField("password", KIND_STR, "Password required to join. Blank leaves it unchanged; "
                                   "clearing is its own value (None).", secret=True),
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

#: Shown READ-ONLY and NEVER written (§5.2.4, §7.5). ``missionList`` is managed by the autoscan and
#: validated at boot — a bad path is a boot failure, so the page points at the Missions page instead.
#: The coalition password HASHES are here so the read can say *set / not set* without ever echoing
#: them; they are not editable in this cut.
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
    """Write *key* in *settings* through the ``SettingsDict`` — the ONE write (§4.2 step 5).

    A dotted ``advanced.X`` is written by REASSIGNING the whole ``advanced`` map (a copy with the one
    leaf set), because a nested in-place mutation would never reach ``SettingsDict.__setitem__`` and
    so would never hit the file — the exact trap the old ``ServerConfigView`` avoids by reassigning
    ``server.settings['advanced']`` as a whole (``plugins/scheduler/views.py``).
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

    Kept as its own coroutine so the write is a nameable SEAM: the concurrency test patches it to
    yield between the read and the write, which is what makes the per-server lock observable rather
    than merely present. In production it never suspends — ``SettingsDict.__setitem__`` is synchronous.
    """
    _write_setting(server.settings, key, value)


def _redact(value: Any) -> str:
    """A secret's read-model value: *set* or *not set*, never the value (§7.1).

    A string that is only WHITESPACE reads as NOT set (review B1 IMPORTANT-4): DCS treats it as no
    password, so reporting it as "set" would claim a credential that is not there.
    """
    if isinstance(value, str):
        return SET_SENTINEL if value.strip() else UNSET_SENTINEL
    return SET_SENTINEL if value else UNSET_SENTINEL


def _redacted_applied(key: str, old: Any, new: Any) -> dict[str, Any]:
    """``{"from":…, "to":…}`` for *key* — with a secret's two sides REDACTED (§7.1, §8.2)."""
    if key in _SECRET_KEYS:
        return {"from": _redact(old), "to": _redact(new)}
    return {"from": old, "to": new}


def validate_value(field: DCSField, value: Any) -> str | None:
    """The TYPE / range / ``unique`` check for one submitted value (§5.2), or ``None`` if it is good.

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
        if any(not char.isprintable() for char in value):
            # NUL, a newline, a tab …: luadata.serialize would faithfully write it and DCS may
            # misbehave at boot (review B1 IMPORTANT-3 — a control character in a server NAME).
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
    """The dotted keys the server's ``servers.yaml`` entry PINS — ``locals['serverSettings']`` (§1).

    At startup ``ServerImpl._prepare`` copies every key of that block OVER the live ``serverSettings.lua``
    values (``core/data/impl/serverimpl.py``), so an F1 edit of such a key would silently REVERT at the
    next boot. The read returns this set as ``overridden`` so a page can mark those fields without a
    second read; the write refuses them (§8.4).
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


def _override_message(key: str) -> str:
    """The design's §8.4 refusal, word for word — the F3 path is the key's own dotted path."""
    return (f"'{key}' is pinned by servers.yaml (serverSettings.{key}) and would revert at the next "
            f"start; edit it there (second slice) or remove the override.")


def _port_collision(ctx: Any, server: Any, port: Any) -> str | None:
    """The cross-field DCS-level check (§5.2.5): *port* already held by another server of the master.

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


def _admin(ctx: Any) -> bool:
    """Whether the caller holds the Admin role (§6). No scope grant — roles only, fails closed."""
    return ADMIN_ROLE in (getattr(ctx, "roles", None) or ())


def _authorised(ctx: Any) -> str | None:
    """The typed refusal a caller gets, or ``None`` when it may act (§2, design §6).

    ONE rule re-checked SERVER-SIDE here, because REST/MCP reach the same action with no console
    route in front (design §6, the maintainer's "never by hiding a form"):

    * ``service`` (MCP) — REFUSED with:data:`SERVICE_REFUSAL`: the MCP transport authenticates nobody
      yet, so this action will not accept a caller it cannot attribute;
    * ``plugin`` (the REST surface + the operator's installed plugins) — ALLOWED: it reaches the seam
      only through its own scheme, which has already authenticated the caller, and
      the actor recorded in the audit (``API``) makes the automated write attributable;
    * every other transport — the console, Discord, and a context nothing named — requires the Admin
      role (:data:`ADMIN_ONLY_SENTENCE`). A DCS Admin or a scoped manager is refused on the READ and
      the WRITE alike, because a server's config carries its secrets (§7) and its own scope (§6).

    Fails CLOSED: an unknown (or empty) transport takes the Admin branch, never the plugin one, so a
    context built by hand cannot accidentally inherit the REST allowance.
    """
    transport = getattr(ctx, "transport", "")
    if transport == TRANSPORT_SERVICE:
        return SERVICE_REFUSAL
    if transport == TRANSPORT_PLUGIN:
        return None
    if _admin(ctx):
        return None
    return ADMIN_ONLY_SENTENCE


async def _audited_config(ctx: Any, result: ServerConfigResult,
                          server: Any = None) -> ServerConfigResult:
    """Write the trail for a CONFIG *result* and return it (design §5.3 D1) — one entry per attempt.

    The config twin of:func:`_audited` above (which words the maintenance flag's entries): ONE place
    per family, so every exit of both config halves leaves exactly one entry. The event text names the
    KEYS, never a value: it is built from ``result.message``, which lists applied/skipped keys (§7.4).
    """
    await audit_action(ctx, result, server=server)
    return result


def _keys(d: Any) -> str:
    """The comma-separated, sorted key names of a mapping — for a message that names NO values."""
    return ", ".join(sorted(d))


def _set_message(name: str, applied: dict, skipped: dict) -> str:
    """The human report: what was saved and what was skipped, by KEY (§7.4 forbids values)."""
    if applied and skipped:
        return f'Server "{name}": saved {_keys(applied)}; skipped {_keys(skipped)}.'
    if applied:
        return f'Server "{name}": saved {_keys(applied)}.'
    if skipped:
        return f'Server "{name}": nothing changed; skipped {_keys(skipped)}.'
    return f'Server "{name}": no configuration values were supplied.'


@action
async def get_server_config(ctx: Any, server_name: str) -> ServerConfigResult:
    """Read ONE server's DCS configuration (F1) — the shape of ``CONFIGURATION.md`` §4.2.

    Authorised by:func:`_authorised` (§6 + §2: the console and Discord require the Admin
    role with no scope grant, a plugin/REST caller is allowed, and a service/MCP caller is refused
    because that transport has no authentication yet). Enforced server-side, because REST/MCP reach
    the same function with no console route in front. Returns, in
    ``result.data``:

    * ``values`` — the curated fields' current values, with every SECRET key redacted to
      ``<set>``/``<unset>`` (§7.1); ``missionList`` and the coalition hash keys ride along read-only;
    * ``editable`` — the curated writable field keys (§5);
    * ``overridden`` — the keys pinned by an F3 override, so a caller can mark them WITHOUT a second
      read (§8.4);
    * ``status`` — the server's status, so the caller can state the stop/run rule (§5.3).

    A name that does not resolve is the seam's own typed refusal (``Server '<name>' not found.``).

    The read is deliberately NOT audited: it changes nothing, and the transport-agnostic read actions
    of ``plugins/mission/actions.py`` (``list_missions``) do not audit either. The console does not
    call it on a render anyway (D3: it reads the master's snapshot).
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
    """Write ONE server's DCS configuration (F1) — the shape of ``CONFIGURATION.md`` §4.2.

    The order is the design's, and it matters:

    1. AUTHORISE here (:func:`_authorised`, §6) — a service/MCP caller is refused with a typed reason
       (no authentication exists yet), a plugin/REST caller is allowed, and a DCS Admin or a scoped
       manager is refused;
    2. REFUSE while the server is up (§5.3, D4) — ``RUNNING``/``PAUSED``/``LOADING``/``SHUTTING_DOWN``
       are refused with the stop-first sentence. Server-side, so a direct call gets it too;
    3. VALIDATE every key against the curated table + types + ranges, and the DCS-level cross-field
       checks (the ``port`` collision) — invalid keys are SKIPPED with a reason, never written. A
       ``values`` that is not a MAPPING is refused here too, as a typed skip, so a caller's mistake
       (a list, a string, a number) is never an ``AttributeError`` that a transport renders as a 500;
    4. hold the per-server WRITE lock §8.3 across the read-modify-write;
    5. apply each accepted key through ``server.settings`` (the existing ``SettingsDict`` path);
    6. audit once with the actor, the server and the KEYS, and return the old -> new of every applied
       key so the console can offer a revert through this same action (§8.2).

    A key pinned by an F3 override is refused KEY BY KEY with the design's §8.4 message; the others
    still apply. A submitted BLANK secret means UNCHANGED (a whitespace-only one included); ``None``
    is the explicit CLEAR (§7.2).

    There is NO ``confirmed`` parameter: the design's signature carries one (§4.2) but no BEHAVIOUR
    was ever designed for it, and an accepted-but-ignored parameter invites a caller to believe a
    confirmation happened. The console's confirm step is its own route's concern (a later card), not
    this action's; when a rule for it is designed, it is added back WITH that rule.
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
    # TOCTOU, stated rather than left to be discovered (review B1 IMPORTANT-2): this status is read
    # BEFORE the write lock below, so between the read and the write a concurrent START can move the
    # server to RUNNING and this action would still write. The window is narrow and the SettingsDict
    # mtime re-read (helper.py) narrows it further, but the lock does NOT close it: the per-server
    # write lock serialises CONFIG writers (§8.3) only — the start/power path takes no such lock, so
    # moving the check inside the lock would not widen the guard's reach either. Closing it properly
    # needs the power path to share this lock, which is a later card's call; until then the exposure
    # is the same one /server config already had (it stops the server itself, then edits).
    if server.status not in WRITABLE_STATES:
        reason = f'Server "{name}" is {server.status.value}. {STOP_FIRST_SENTENCE}'
        return await _audited_config(ctx, ServerConfigResult(success=False, server_name=name,
                                                             refused=reason, message=reason), server)
    if not isinstance(values, dict):
        reason = "'values' must be a mapping of setting names to values."
        return await _audited_config(ctx, ServerConfigResult(
            success=False, server_name=name, skipped={"values": reason},
            message=f'Server "{name}": {reason}'), server)

    applied: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    async with server_write_lock(str(server.name)):
        overrides = _f3_override_keys(getattr(server, "locals", None))
        for key, value in values.items():
            field = _FIELDS_BY_KEY.get(key)
            if field is None:
                skipped[key] = (f"'{key}' is read-only and is never written here." if key in
                                _READONLY_BY_KEY else f"'{key}' is not an editable setting.")
                continue
            if key in overrides:
                skipped[key] = _override_message(key)
                continue
            if field.secret:
                if value is None:                      # the explicit CLEAR (§7.2)
                    new_value: Any = ""
                elif isinstance(value, str):
                    if not value.strip():
                        # blank OR whitespace-only: an unchanged submission, not a one-space password
                        # (review B1 IMPORTANT-4) — and it reads back as <unset> too, via _redact.
                        skipped[key] = f"'{key}' was left blank and is unchanged."
                        continue
                    new_value = value
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

    result = ServerConfigResult(
        success=bool(applied), server_name=name, applied=applied, skipped=skipped,
        message=_set_message(name, applied, skipped))
    return await _audited_config(ctx, result, server)
