"""The website's penalty-point writes — the console face of ``/punish`` and ``/forgive``.

Both actions are ``TRANSPORT_WEB`` only: a Discord caller has the commands, and a REST/MCP caller
carries no roles, so admitting it here would be a door with no identity behind it.

The write itself is not here: a penalty change is the INSERT plus the PubSub publish that makes the
punishment ladder run, and that pair lives in :mod:`.writes` — the same code ``/punish`` calls. A
forgive is the row delete plus the bot's own unban, bot-wide.

THE CHANGE IS FLEET-WIDE, so no server is asked for and neither action takes one. ``pu_events``
still needs a server name (``server_name`` is NOT NULL with a foreign key to ``servers``) and the
ladder is read from that server's config, so ONE reference server is resolved internally — see
:func:`_reference_server` — and it is never the caller's choice.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core import get_translation
from core.actions import TRANSPORT_WEB, ActionResult, action, audit_action

from .writes import (
    PunishmentPublishError,
    delete_points,
    linked_ucids,
    record_event,
)

log = logging.getLogger(__name__)
_ = get_translation(__name__.split('.')[1])

#: The capability the console declares for this surface (``pages/actions.PENALTY_CAPABILITY``).
#: A plugin may not import the console package, so the role names are stated here once and the
#: route's own gate re-checks the same declaration.
PUNISH_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")

#: A UCID is 32 hexadecimal characters — the same shape ``get_player_infractions`` checks.
UCID_PATTERN = re.compile(r"[0-9a-f]{32}")

#: A single penalty change is a human-scale figure; anything larger is a typo, not a punishment.
POINTS_MIN = 1
POINTS_MAX = 1000

REASON_DEFAULT = "admin"
REASON_MAX = 80

CONSOLE_ONLY = _("Penalty points can only be changed from the website.")
CAPABILITY_REFUSAL = _("You need the DCS Admin role to change penalty points.")
NO_FLEET_REFUSAL = _("This installation runs no server yet.")
POINTS_REFUSAL = _("Penalty points must be a whole number between {minimum} and {maximum}.")
POINTS_ZERO = _("A penalty change of 0 does nothing.")
UCID_REFUSAL = _("A UCID is 32 hexadecimal characters.")
DB_UNAVAILABLE = _("The database is not available.")
PLUGIN_OFFLINE = _("The punishment plugin is not running, so the points cannot be applied.")
RECORD_FAILED = _("The penalty points could not be recorded: {error}")
PUBLISH_FAILED = _("The points were recorded, but the punishment could not be applied: {error}")
FORGIVE_FAILED = _("The penalty points could not be deleted: {error}")
UNBAN_FAILED = _("The penalty points were deleted, but the ban could not be lifted: {error}")
FORGIVEN = _("All punishment points deleted and player unbanned (if they were banned by the bot before).")


def _capability_refusal(ctx: Any) -> str | None:
    """The typed refusal for a caller that does not hold the console's punish capability.

    Roles first (the capability's declared roles), then a MANAGER through its resolved scope — the
    same two ways in :func:`services.webservice.permissions.allows` grants a ``scope_grants``
    capability. A context carrying no roles fails closed.
    """
    roles = set(getattr(ctx, "roles", None) or ())
    if roles & set(PUNISH_ROLES):
        return None
    if getattr(ctx, "manages_only", False):
        return None
    return CAPABILITY_REFUSAL


def _points(value: Any) -> int | None:
    """*value* as a whole number, or ``None`` when it is not one — a magnitude, never a sign.

    The sign is the CALLER's meaning: a negative figure takes points back. Zero is refused and the
    magnitude is bounded on BOTH sides, so a crafted body cannot write an absurd ladder trigger.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value == 0 or abs(value) > POINTS_MAX or abs(value) < POINTS_MIN:
        return None
    return value


def _reason(value: Any) -> str:
    """The event text: the typed reason, bounded, with "admin" standing in for an empty one."""
    text = " ".join(str(value or "").split())
    return text[:REASON_MAX] or REASON_DEFAULT


def _pool(ctx: Any) -> Any:
    """The database pool this process's node carries, or ``None``."""
    return getattr(getattr(ctx, "node", None), "apool", None)


def _publish() -> Any:
    """The running punishment cog's PubSub publish, or ``None``.

    The publish channel belongs to the cog (``Punishment.trigger``): a second PubSub instance would
    be a second publisher for one event name, so an absent cog is a refusal — the row must not be
    written when nothing can apply it.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.bot.service import BotService

        bot = getattr(ServiceRegistry.get(BotService), "bot", None)
    except Exception:
        log.exception("the running bot could not be resolved")
        return None
    if bot is None:
        return None
    getter = getattr(bot, "get_cog", None)
    cog = getter("Punishment") if callable(getter) else None
    if cog is None:
        cog = (getattr(bot, "cogs", None) or {}).get("Punishment")
    return getattr(getattr(cog, "trigger", None), "publish", None)


def _ban_service(ctx: Any) -> Any:
    """The service that owns the ban list: the caller's bus when it carries one, else the running one.

    A web caller's ``ctx.bus`` is the console's SCOPED cluster — a keyed view of servers, not a ban
    service — so the real ``ServiceBus`` is resolved for the unban. ``None`` when this process has
    none: a typed refusal, never an AttributeError.
    """
    bus = getattr(ctx, "bus", None)
    if hasattr(bus, "unban"):
        return bus
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        return ServiceRegistry.get(ServiceBus)
    except Exception:
        log.exception("the ban service could not be resolved in this process")
        return None


def _fleet_servers() -> dict[str, Any]:
    """Every server on the bus, keyed by name — this process's own node included.

    Read from the process's ``ServiceBus`` and never from ``ctx.bus``: the caller's bus is the
    console's SCOPED view, and which server anchors a fleet-wide write is an installation fact,
    not a permission.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        bus = ServiceRegistry.get(ServiceBus)
    except Exception:
        log.exception("the service bus could not be resolved in this process")
        return {}
    return dict(getattr(bus, "servers", None) or {})


def _reference_server() -> Any | None:
    """The ONE server a fleet-wide penalty write is anchored on, or ``None`` when the fleet is empty.

    ``pu_events.server_name`` is NOT NULL with a foreign key to ``servers``, and the punishment
    ladder is read from that server's config, so a write still names one server — but the operator
    never picks it. The order is fixed and stops at the first entry that exists:

    1. a server hosted by THIS process's node (``is_remote`` is false), which is the master that
       serves the website;
    2. otherwise the first server by name across the bus;
    3. none at all, which the actions refuse.

    "Hosted" is not "running": a stopped server still carries its ladder, and refusing a write
    because nothing happens to be up would make the penalty log depend on uptime.
    """
    servers = _fleet_servers()
    local = [server for server in servers.values() if not getattr(server, "is_remote", True)]
    candidates = local or list(servers.values())
    if not candidates:
        return None
    return min(candidates, key=lambda server: str(getattr(server, "name", "")))


def _guild_id(ctx: Any) -> int | None:
    """The guild the punishment publish names — the process node's own, exactly as the command."""
    value = getattr(getattr(ctx, "node", None), "guild_id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@action
async def punish_player(ctx: Any, ucid: str, points: int, reason: str = REASON_DEFAULT) -> ActionResult:
    """Change ONE player's penalty points across the whole fleet — positive adds, negative removes.

    The change is one ``pu_events`` row plus its publish (:func:`record_event`), so the punishment
    ladder runs exactly as ``/punish`` makes it run. A publish that fails after the row is written
    reports a PARTIAL outcome rather than a clean success or a clean failure.

    THE ROW IS NAMED BY THE FLEET REFERENCE (:func:`_reference_server`), never by a caller's choice,
    and that is what makes the change fleet-wide: the reference server donates the ladder, and on a
    normal install it declares no ``managed_by``, so the ban that ladder raises lands as the
    bot-wide row (``None``) and covers the whole fleet rather than one cluster.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=CONSOLE_ONLY)
    wanted = str(ucid or "").strip().lower()
    if not UCID_PATTERN.fullmatch(wanted):
        return ActionResult(success=False, message=UCID_REFUSAL, data={"ucid": wanted})
    refusal = _capability_refusal(ctx)
    if refusal:
        return await audit_action(ctx, ActionResult(success=False, message=refusal),
                                  message=f"refused ({refusal}) ucid={wanted}")
    target = _reference_server()
    if target is None:
        return await audit_action(ctx, ActionResult(success=False, message=NO_FLEET_REFUSAL),
                                  message=f"refused ({NO_FLEET_REFUSAL}) ucid={wanted}")
    value = _points(points)
    if value is None:
        message = POINTS_ZERO if points == 0 else POINTS_REFUSAL.format(minimum=POINTS_MIN,
                                                                        maximum=POINTS_MAX)
        return await audit_action(ctx, ActionResult(success=False, message=message), server=target,
                                  message=f"refused ({message}) ucid={wanted}")
    pool = _pool(ctx)
    if pool is None:
        return await audit_action(ctx, ActionResult(success=False, message=DB_UNAVAILABLE),
                                  server=target, message=DB_UNAVAILABLE)
    publish = _publish()
    if publish is None:
        return await audit_action(ctx, ActionResult(success=False, message=PLUGIN_OFFLINE),
                                  server=target, message=PLUGIN_OFFLINE)

    event = _reason(reason)
    server_name = str(getattr(target, "name", "") or "")
    audit = _("changed punishment points of user {ucid} by {points} points.").format(
        ucid=wanted, points=value)
    try:
        await record_event(pool, publish, ucid=wanted, server_name=server_name, event=event,
                           points=value, guild_id=_guild_id(ctx))
    except PunishmentPublishError as ex:
        return await audit_action(ctx, ActionResult(
            success=False, message=PUBLISH_FAILED.format(error=ex),
            data={"ucid": wanted, "points_written": True, "applied": False}),
            server=target, message=audit)
    except Exception as ex:
        log.exception("punish_player failed")
        return await audit_action(ctx, ActionResult(
            success=False, message=RECORD_FAILED.format(error=ex), data={"ucid": wanted}),
            server=target, message=audit)

    if value > 0:
        message = _('User punished with {} points.').format(value)
    else:
        message = _('The users punishment points have been reduced by {} points.').format(abs(value))
    return await audit_action(ctx, ActionResult(success=True, message=message,
                                                data={"ucid": wanted, "points": value}),
                              server=target, message=audit)


@action
async def forgive_player(ctx: Any, ucid: str) -> ActionResult:
    """Delete every penalty entry of the player's UCID group and lift the bot's ban, bot-wide.

    The group is the Discord link's UCID set — the same grouping the player page shows and the
    member path of ``/forgive`` deletes for. The ban is lifted ONCE per UCID with no server
    (``unban(ucid, None)``), which is exactly what ``/forgive`` does when its server declares no
    ``managed_by``: the result is a total forgive, not a per-cluster one. A ban a human added
    elsewhere is not the bot's to lift, so only the bot's own row is lifted.

    The fleet reference (:func:`_reference_server`) is resolved here too, so an installation that
    runs no server refuses this write with the same sentence as the point change.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=CONSOLE_ONLY)
    wanted = str(ucid or "").strip().lower()
    if not UCID_PATTERN.fullmatch(wanted):
        return ActionResult(success=False, message=UCID_REFUSAL, data={"ucid": wanted})
    refusal = _capability_refusal(ctx)
    if refusal:
        return await audit_action(ctx, ActionResult(success=False, message=refusal),
                                  message=f"refused ({refusal}) ucid={wanted}")
    target = _reference_server()
    if target is None:
        return await audit_action(ctx, ActionResult(success=False, message=NO_FLEET_REFUSAL),
                                  message=f"refused ({NO_FLEET_REFUSAL}) ucid={wanted}")
    pool = _pool(ctx)
    if pool is None:
        return await audit_action(ctx, ActionResult(success=False, message=DB_UNAVAILABLE),
                                  server=target, message=DB_UNAVAILABLE)
    service = _ban_service(ctx)
    if service is None:
        return await audit_action(ctx, ActionResult(success=False, message=UNBAN_FAILED.format(
            error=DB_UNAVAILABLE)), server=target, message=UNBAN_FAILED.format(error=DB_UNAVAILABLE))

    audit = _("forgave player {}").format(wanted)
    try:
        async with pool.connection() as conn:
            ucids = await linked_ucids(conn, wanted)
            await delete_points(conn, ucids)
    except Exception as ex:
        log.exception("forgive_player could not delete the penalty rows")
        return await audit_action(ctx, ActionResult(
            success=False, message=FORGIVE_FAILED.format(error=ex), data={"ucid": wanted}),
            server=target, message=audit)

    try:
        for name in ucids:
            await service.unban(name, None)
    except Exception as ex:
        log.exception("forgive_player could not lift the ban")
        return await audit_action(ctx, ActionResult(
            success=False, message=UNBAN_FAILED.format(error=ex), data={"ucid": wanted}),
            server=target, message=audit)
    return await audit_action(ctx, ActionResult(success=True, message=FORGIVEN,
                                                data={"ucid": wanted, "ucids": ucids}),
                              server=target, message=audit)
