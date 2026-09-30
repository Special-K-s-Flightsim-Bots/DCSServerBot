"""
Action functions for mission control operations.

Each action is transport-agnostic. It takes typed parameters + an ActionContext,
performs the operation, and returns a result dataclass that Discord/REST/MCP
wrap into their response formats.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from core import Status, utils
from core.actions import audit_action, action
from core.action_results import (
    MissionControlResult,
    MissionLoadResult,
    MissionListResult,
    PlayerActionResult,
    ServerControlResult,
)

log = logging.getLogger(__name__)


# ── Server control ──────────────────────────────────────────────────────────

@action
async def start_server(ctx: Any, server_name: str) -> ServerControlResult:
    """Start a stopped DCS server instance."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return ServerControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status != Status.STOPPED:
        return ServerControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not stopped.",
            server_name=server_name,
        )
    try:
        await server.start()
        return ServerControlResult(
            success=True,
            message=f"Server '{server.display_name}' started.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while starting server '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("start_server failed")
        return ServerControlResult(
            success=False,
            message=f"Failed to start server: {ex}",
            server_name=server_name,
        )


@action
async def stop_server(ctx: Any, server_name: str) -> ServerControlResult:
    """Stop a running or paused DCS server instance."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return ServerControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED):
        return ServerControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not running or paused.",
            server_name=server_name,
        )
    try:
        await server.stop()
        return ServerControlResult(
            success=True,
            message=f"Server '{server.display_name}' stopped.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while stopping server '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("stop_server failed")
        return ServerControlResult(
            success=False,
            message=f"Failed to stop server: {ex}",
            server_name=server_name,
        )


# TEMPORARY-REDUNDANCY(W-R3): the console's Restart is THIS action (a plain stop + start) while
# ``/server restart`` (``plugins/scheduler/commands.py``) sets the maintenance flag, warns the
# players with a popup for ``delay`` seconds and can run extensions — different behaviour for one
# word. Declared here and at that command; the card that fattens this action with the
# maintenance/delay/popup flow and moves the command onto it removes both markers.
@action
async def restart_server(ctx: Any, server_name: str) -> ServerControlResult:
    """Restart a DCS server instance (stop then start)."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return ServerControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED, Status.STOPPED):
        return ServerControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}.",
            server_name=server_name,
        )
    try:
        if server.status in (Status.RUNNING, Status.PAUSED):
            await server.stop()
        await server.start()
        return ServerControlResult(
            success=True,
            message=f"Server '{server.display_name}' restarted.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while restarting server '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("restart_server failed")
        return ServerControlResult(
            success=False,
            message=f"Failed to restart server: {ex}",
            server_name=server_name,
        )


# ── DCS-level server control (startup / shutdown) ───────────────────────────
#
# ``startup_server`` / ``shutdown_server`` are the DCS-LEVEL pair (SHUTDOWN <-> running, the server
# stays registered), while ``start_server`` / ``stop_server`` above are the PROCESS-level pair
# (STOPPED <-> running). The console's row strip offers them on disjoint states, so which one
# applies is decided by the state, never by the viewer.
#


def _set_maintenance(server: Any, maintenance: bool) -> str | None:
    """Write ``server.maintenance`` and return the failure text, or ``None`` on success.

    THE one place these two actions write the flag, so the shutdown half and the startup half cannot
    drift apart. The write goes through ``Server.maintenance``'s own setter
    (``core/data/server.py``) — the SAME mechanism ``/server shutdown|startup`` and the maintenance
    commands use — so the flag reaches the node and DCS exactly as it does from Discord. A write
    that raises is returned, never swallowed: a shutdown that succeeded but could not flag must not
    report a clean success.
    """
    try:
        server.maintenance = maintenance
    except Exception as ex:
        log.exception("the maintenance flag could not be %s on server '%s'",
                      "set" if maintenance else "cleared", server.name)
        return str(ex)
    return None


@action
async def startup_server(ctx: Any, server_name: str, modify_mission: bool = True,
                         use_orig: bool = True, maintenance: bool = True) -> ServerControlResult:
    """Start up a SHUTDOWN DCS server (the DCS-level Startup; process-level Start is another action).

    ``modify_mission`` / ``use_orig`` mirror the arguments ``Server.startup`` takes and the Discord
    command passes (``plugins/scheduler/commands.py``).

    ``maintenance`` mirrors ``/server startup``'s own default: a TRUE value
    CLEARS the maintenance flag on a successful start, so the server rejoins the rotation — a server
    that stays flagged after an explicit startup would be skipped by the scheduler while looking up.
    Pass ``False`` to leave the flag exactly as it is. A flag that could not be cleared is reported
    in the message instead of being claimed. The ``delay`` warning popups and the extension runs of
    that command are still NOT reproduced here — see the W-R3 marker above.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return ServerControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status != Status.SHUTDOWN:
        return ServerControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not shut down.",
            server_name=server_name,
        )
    try:
        await server.startup(modify_mission=modify_mission, use_orig=use_orig)
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while starting up server '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("startup_server failed")
        return ServerControlResult(
            success=False,
            message=f"Failed to start up server: {ex}",
            server_name=server_name,
        )
    # The start is done; the flag is a SECOND write (W5c) and a failed one is reported, not claimed.
    if maintenance:
        failure = _set_maintenance(server, False)
        if failure is not None:
            return ServerControlResult(
                success=False,
                message=(f"Server '{server.display_name}' started up, but the maintenance flag "
                         f"could not be cleared: {failure}"),
                server_name=server_name,
            )
    return ServerControlResult(
        success=True,
        message=f"Server '{server.display_name}' started up.",
        server_name=server_name,
    )


@action
async def shutdown_server(ctx: Any, server_name: str, force: bool = False,
                          maintenance: bool = True) -> ServerControlResult:
    """Shut down a running or paused DCS server gracefully (the DCS-level Shutdown).

    The DCS-level counterpart of ``stop_server``: the server stays registered and reads SHUTDOWN
    until a Startup brings it back, where a Stop ends the process and leaves it STOPPED.

    ``maintenance`` mirrors ``/server shutdown``'s own default: after a
    successful shutdown the flag is set to this value — TRUE (the default) marks the server as in
    maintenance, because a deliberate shutdown that leaves the flag clear is undone by the next
    scheduled start; ``False`` clears it. A write that failed is reported in the message, so a
    shutdown that succeeded but could not flag never reads as a clean success. ``force`` mirrors
    ``Server.shutdown(force=...)``; the ``delay`` warning popups and the extension runs of the
    command are still NOT reproduced here (W-R3 above).
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return ServerControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED):
        return ServerControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not running or paused.",
            server_name=server_name,
        )
    try:
        await server.shutdown(force=force)
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while shutting down server '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("shutdown_server failed")
        return ServerControlResult(
            success=False,
            message=f"Failed to shut down server: {ex}",
            server_name=server_name,
        )
    # The shutdown is done; the flag is a SECOND write (W5c). A write that failed is reported rather
    # than swallowed — a shutdown that succeeded but could not flag is not a clean success.
    failure = _set_maintenance(server, maintenance)
    if failure is not None:
        verb = "set" if maintenance else "cleared"
        return ServerControlResult(
            success=False,
            message=(f"Server '{server.display_name}' shut down, but the maintenance flag could "
                     f"not be {verb}: {failure}"),
            server_name=server_name,
        )
    return ServerControlResult(
        success=True,
        message=f"Server '{server.display_name}' shut down.",
        server_name=server_name,
    )


# ── Mission control ─────────────────────────────────────────────────────────

@action
async def pause_mission(ctx: Any, server_name: str) -> MissionControlResult:
    """Pause the running mission on a DCS server."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status != Status.RUNNING:
        return MissionControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not running.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{server.display_name}'.",
            server_name=server_name,
        )
    try:
        await server.current_mission.pause()
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{server.display_name}' paused.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("pause_mission failed")
        return MissionControlResult(
            success=False,
            message=f"Failed to pause mission: {ex}",
            server_name=server_name,
        )


@action
async def unpause_mission(ctx: Any, server_name: str) -> MissionControlResult:
    """Unpause the paused mission on a DCS server."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status != Status.PAUSED:
        return MissionControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}, not paused.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{server.display_name}'.",
            server_name=server_name,
        )
    try:
        await server.current_mission.unpause()
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{server.display_name}' unpaused.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("unpause_mission failed")
        return MissionControlResult(
            success=False,
            message=f"Failed to unpause mission: {ex}",
            server_name=server_name,
        )


@action
async def restart_mission(
    ctx: Any,
    server_name: str,
    modify_mission: bool = True,
    use_orig: bool = True,
) -> MissionControlResult:
    """Restart the current mission on a DCS server."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED, Status.STOPPED):
        return MissionControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{server.display_name}'.",
            server_name=server_name,
        )
    try:
        if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
            await server.stop()
        await server.restart(modify_mission=modify_mission)
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{server.display_name}' restarted.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return MissionControlResult(
            success=False,
            message=f"Timeout while restarting mission on '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("restart_mission failed")
        return MissionControlResult(
            success=False,
            message=f"Failed to restart mission: {ex}",
            server_name=server_name,
        )


@action
async def rotate_mission(
    ctx: Any,
    server_name: str,
    modify_mission: bool = True,
    use_orig: bool = True,
) -> MissionControlResult:
    """Rotate to the next mission in the rotation list."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionControlResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED, Status.STOPPED):
        return MissionControlResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}.",
            server_name=server_name,
        )
    try:
        if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
            await server.stop()
        await server.loadNextMission(modify_mission=modify_mission, use_orig=use_orig)
        return MissionControlResult(
            success=True,
            message=f"Mission rotated on server '{server.display_name}'.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return MissionControlResult(
            success=False,
            message=f"Timeout while rotating mission on '{server.display_name}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("rotate_mission failed")
        return MissionControlResult(
            success=False,
            message=f"Failed to rotate mission: {ex}",
            server_name=server_name,
        )


@action
async def load_mission(
    ctx: Any,
    server_name: str,
    mission: str | int,
    modify_mission: bool = True,
    use_orig: bool = True,
) -> MissionLoadResult:
    """Load a specific mission on a DCS server."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionLoadResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.status not in (Status.RUNNING, Status.PAUSED, Status.STOPPED):
        return MissionLoadResult(
            success=False,
            message=f"Server '{server.display_name}' is {server.status.value}.",
            server_name=server_name,
        )

    # Resolve mission identifier to filename
    mission_list = await server.getMissionList()
    if isinstance(mission, int):
        if mission < 0 or mission >= len(mission_list):
            return MissionLoadResult(
                success=False,
                message=f"Mission index {mission} out of range (0-{len(mission_list)-1}).",
                server_name=server_name,
            )
        mission_file = mission_list[mission]
        mission_name = os.path.basename(mission_file[:-4])
    else:
        mission_file = os.path.join(await server.get_missions_dir(), mission)
        if mission_file not in mission_list:
            # Allow missions not in rotation
            mission_name = os.path.basename(mission[:-4]) if mission.endswith('.miz') else mission
        else:
            mission_name = os.path.basename(mission_file[:-4])

    if server.current_mission and mission_file == server.current_mission.filename:
        try:
            await server.restart(modify_mission=modify_mission)
            return MissionLoadResult(
                success=True,
                message=f"Mission '{mission_name}' restarted.",
                server_name=server_name,
                mission_name=mission_name,
            )
        except Exception as ex:
            log.exception("load_mission (restart current) failed")
            return MissionLoadResult(
                success=False,
                message=f"Failed to restart current mission: {ex}",
                server_name=server_name,
                mission_name=mission_name,
            )

    try:
        if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
            await server.stop()
        success = await server.loadMission(
            mission_file, modify_mission=modify_mission, use_orig=use_orig
        )
        if not success:
            return MissionLoadResult(
                success=False,
                message=f"Mission '{mission_name}' NOT loaded. Check installed terrains/mods.",
                server_name=server_name,
                mission_name=mission_name,
            )
        return MissionLoadResult(
            success=True,
            message=f"Mission '{mission_name}' loaded.",
            server_name=server_name,
            mission_name=mission_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return MissionLoadResult(
            success=False,
            message=f"Timeout while loading mission '{mission_name}'.",
            server_name=server_name,
            mission_name=mission_name,
        )
    except Exception as ex:
        log.exception("load_mission failed")
        return MissionLoadResult(
            success=False,
            message=f"Failed to load mission: {ex}",
            server_name=server_name,
            mission_name=mission_name,
        )


# ── Player control ──────────────────────────────────────────────────────────
#
# ``message_player`` is the console's first NEW action (design §8 W3): the two Discord commands that
# do the same thing (``/mission player popup``, ``/mission player chat``, ``plugins/mission/
# commands.py``) keep calling the domain methods directly for now — the declared redundancy W-R2,
# marked at those call sites.

#: Every mode ``message_player`` supports — the SAME two the Discord commands offer, so the console
#: does not invent a third behaviour: an in-game POPUP (private) or an in-game CHAT line.
MESSAGE_MODES: tuple[str, ...] = ("popup", "chat")

#: The longest message that may be sent, in characters. The write design declares the route's field
#: as required and ≤ 1024 (``WRITE-ACTIONS-DESIGN.md`` §3.1) and the limit is enforced HERE, in the
#: action, so every transport is held to the same one — a form field has no cap of its own, and the
#: Discord commands rely on Discord's. Length is measured on the TRIMMED message, which is what
#: would be sent.
MAX_MESSAGE_LENGTH = 1024


@action
async def message_player(ctx: Any, server_name: str, ucid: str, message: str, mode: str = "popup",
                         sender: str | None = None) -> PlayerActionResult:
    """Send a message to ONE player: an in-game popup (default) or a chat line.

    Mirrors the two Discord commands that do the same thing — the same domain calls
    (``Player.sendPopupMessage`` / ``Player.sendChatMessage``), the same mode split, the same
    "Player not found." answer — with the console's TYPED refusals instead of a followup message. A
    message that is empty or only whitespace is refused WITHOUT calling the domain method: a blank
    popup is a mistake, and sending it would put an empty box in front of a player.

    ``sender`` is the label the message appears to come from: the transport's own actor
    (``interaction.user.display_name`` for Discord, the signed-in person's shown name for the
    console). ``None`` sends it anonymous, exactly as ``Player.sendChatMessage``'s own default does.

    The trail is written HERE, once, whatever the transport (design §5.3 D1) — including for a
    REFUSAL, because one entry per attempt is what an operator wants after an incident.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Server '{server_name}' not found.", ucid=ucid))

    body = (message or "").strip()
    if not body:
        return await _audited(ctx, PlayerActionResult(success=False,
                                                      message="Message must not be empty.",
                                                      ucid=ucid), server)
    if len(body) > MAX_MESSAGE_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Message is longer than {MAX_MESSAGE_LENGTH} characters.",
            ucid=ucid), server)

    mode = (mode or "").strip().lower() or MESSAGE_MODES[0]
    if mode not in MESSAGE_MODES:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Unknown message mode '{mode}' (use 'popup' or 'chat').",
            ucid=ucid), server)

    # The player is looked up ON the resolved server (the same ``Server.get_player`` the Discord
    # picker uses), so a player on ANOTHER server is simply not found — there is no lookup that does
    # not begin from the server the caller resolved.
    player = server.get_player(ucid=ucid, active=True)
    if player is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Player not found on server '{server.display_name}'.",
            ucid=ucid), server)

    name = getattr(player, "display_name", None) or getattr(player, "name", "") or ucid
    try:
        if mode == "chat":
            await player.sendChatMessage(body, sender)
            sent = f"Chat message sent to {name} on server '{server.display_name}'."
        else:
            # ``timeout`` stays at the domain method's own default (-1 = the server's configured
            # ``message_timeout``): the Discord commands pass an explicit duration for popups, and
            # the console's control has no field for one, so the default is the honest choice.
            await player.sendPopupMessage(body, sender=sender)
            sent = f"Popup sent to {name} on server '{server.display_name}'."
        result = PlayerActionResult(success=True, message=sent, player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(
            success=False,
            message=f"Timeout while messaging {name} on server '{server.display_name}'.",
            player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("message_player failed")
        result = PlayerActionResult(success=False, message=f"Failed to send the message: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


async def _audited(ctx: Any, result: PlayerActionResult,
                   server: Any = None) -> PlayerActionResult:
    """Write the trail for *result* and return it (design §5.3 D1).

    ONE place, so every exit of :func:`message_player` — the success, the four typed refusals and the
    failed send — leaves exactly one entry, and none of them can forget the one mechanism.
    """
    await audit_action(ctx, result, server=server)
    return result


# ── Player control: kick / ban / mute (W4b) ─────────────────────────────────
#
# The four actions below are the console's PLAYER-row strip (design §1.2's agreed actions plus the
# mute pair the mockup adds). Every one of them MIRRORS an existing Discord surface rather than
# inventing a second behaviour:
#
# * ``kick_player``  -> ``Server.kick(player, reason)``, exactly what ``/mission player kick``
#   (``plugins/mission/commands.py``) and the in-game chat command (``plugins/mission/listener.py``)
#   call. TEMPORARY-REDUNDANCY(W-R1): the two Discord surfaces still call the domain method
#   directly, so one word has two callers; the card that moves them onto this action DELETES their
#   own ``bot.audit(...)`` calls with it (otherwise a kick audits twice). Markers at
#   ``plugins/mission/commands.py`` (the ``kick`` command) and ``plugins/mission/listener.py`` (the
#   chat ``kick`` command).
# * ``mute_player`` / ``unmute_player`` -> ``Player.mute()`` / ``Player.unmute()``, the same methods
#   ``/mission player mute|unmute`` and the chat commands use. TEMPORARY-REDUNDANCY(W4b-mute), the
#   same class as W-R1 and marked at both call sites.
# * ``ban_player`` -> ``ServiceBus.ban(...)``, which is NOT a redundancy at all: the Discord modal
#   (``plugins/mission/views.py``, ``BanModal.on_submit``) already calls that one service method, so
#   the console wraps the SAME operation rather than a second implementation of it. That is why this
#   card adds no W-R marker for the ban: the ban list stays the single source of truth because both
#   surfaces write through the same call.

#: The longest REASON the console's kick accepts, in characters (``WRITE-ACTIONS-DESIGN.md`` §3.1:
#: the route's field is optional and ≤ 200). Enforced HERE, in the action, so every transport is held
#: to the same one; measured on the TRIMMED reason, which is what would be sent.
MAX_REASON_LENGTH = 200

#: The Discord ban modal's own field cap (``plugins/mission/views.py``: ``TextInput(max_length=80)``),
#: kept IDENTICAL so the two surfaces refuse the same reason instead of one silently accepting what
#: the other truncates.
MAX_BAN_REASON_LENGTH = 80

#: The longest ban the console accepts, in days (100 years — far beyond any real suspension, and low
#: enough that the ``banned_until`` timestamp it computes cannot overflow into a nonsense date).
MAX_BAN_DAYS = 36500


def parse_ban_days(value: Any) -> tuple[int | None, str]:
    """The ban duration a request states: ``(days, refusal)`` — exactly one of the two is set.

    The three answers, matching the Discord modal's own field (``BanModal``: ``int(period) if
    period.value else None``):

    * empty / absent / whitespace -> ``(None, "")``: a PERMANENT ban, which is what the modal does
      with an empty field and what the ban dialog's own copy promises;
    * a whole number of days -> ``(days, "")``;
    * anything else — non-numeric, negative, zero, or beyond :data:`MAX_BAN_DAYS` — -> a TYPED
      refusal and no domain call. Zero is refused rather than read as "permanent": the two are
      different intentions and a form that cannot say which one it meant must say so instead of
      guessing. ``str.isdigit`` is the parse, so ``"-3"`` and ``"7.5"`` are both refused here
      rather than reaching ``int()`` (a 500) or the database.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return None, ""
    if not text.isdigit():
        return None, "Days must be a whole number — leave the field empty for a permanent ban."
    days = int(text)
    if days < 1:
        return None, "Days must be at least 1 — leave the field empty for a permanent ban."
    if days > MAX_BAN_DAYS:
        return None, f"Days must not be more than {MAX_BAN_DAYS}."
    return days, ""


def _player_label(player: Any, ucid: str) -> str:
    """The name a player write's copy uses — the object's own, never anything the browser sent."""
    return getattr(player, "display_name", None) or getattr(player, "name", "") or ucid


def _ban_service(ctx: Any) -> Any:
    """The service that owns the ban list: the caller's bus when it carries one, else the running one.

    A web caller is handed the console's SCOPED cluster as ``ctx.bus`` (``pages/actions._Cluster``),
    which is deliberately only a keyed view of servers — it must not become a second way to reach
    the fleet. The ban list is not a server, so this action resolves the real service instead, the
    same one ``BanModal`` resolves (``ServiceRegistry.get(ServiceBus)``). ``None`` when this process
    has no service bus at all: a typed refusal, never an AttributeError.
    """
    bus = getattr(ctx, "bus", None)
    if hasattr(bus, "ban"):
        return bus
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        return ServiceRegistry.get(ServiceBus)
    except Exception:
        log.exception("ban_player: the ban service could not be resolved in this process")
        return None


@action
async def kick_player(ctx: Any, server_name: str, ucid: str, reason: str = "") -> PlayerActionResult:
    """Kick ONE player off a DCS server — the console's Kick, mirroring ``/mission player kick``.

    The validation is the Discord command's plus the console's typed refusals: the player is looked
    up ON the resolved server (so a player on another server is simply not found), and an EMPTY or
    whitespace-only reason is refused WITHOUT calling the domain method — a kick with no reason
    leaves a moderator unable to say why, and the Discord surfaces default to ``'n/a'`` instead of
    refusing, which is the difference this action deliberately keeps (the console's field is
    required).

    The trail is written here, once, whatever the transport (§5.3 D1), including for a refusal.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Server '{server_name}' not found.", ucid=ucid))

    body = (reason or "").strip()
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False, message="A reason is required to kick a player.", ucid=ucid), server)
    if len(body) > MAX_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"The reason is longer than {MAX_REASON_LENGTH} characters.",
            ucid=ucid), server)

    player = server.get_player(ucid=ucid, active=True)
    if player is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Player not found on server '{server.display_name}'.",
            ucid=ucid), server)

    name = _player_label(player, ucid)
    try:
        await server.kick(player, body)
        result = PlayerActionResult(
            success=True,
            message=f"Player {name} (ucid={ucid}) kicked from server '{server.display_name}'.",
            player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(
            success=False,
            message=f"Timeout while kicking {name} from server '{server.display_name}'.",
            player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("kick_player failed")
        result = PlayerActionResult(success=False, message=f"Failed to kick the player: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


@action
async def ban_player(ctx: Any, server_name: str, ucid: str, reason: str = "",
                     days: str | int | None = None, sender: str | None = None) -> PlayerActionResult:
    """Ban ONE player — ``ServiceBus.ban``, the SAME operation the Discord ``BanModal`` performs.

    ``days`` is the form's own value (a string, or ``None``/``''`` for permanent) and is parsed by
    :func:`parse_ban_days`, so an empty duration is a permanent ban, ``N`` is N days, and nonsense
    (negative, zero, non-numeric, absurdly large) is a TYPED refusal with NOTHING written — the ban
    list is the single source of truth, so a malformed duration must not be
    coerced into an approximation of itself.

    ``sender`` is who the ban list names as ``banned_by``, mirroring ``BanModal``'s
    ``interaction.user.display_name``; ``None`` falls back to the transport's own audit actor, so a
    ban is never attributed to nobody.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Server '{server_name}' not found.", ucid=ucid))

    body = (reason or "").strip()
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False, message="A reason is required to ban a player.", ucid=ucid), server)
    if len(body) > MAX_BAN_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"The reason is longer than {MAX_BAN_REASON_LENGTH} characters.",
            ucid=ucid), server)

    period, refusal = parse_ban_days(days)
    if refusal:
        return await _audited(ctx, PlayerActionResult(success=False, message=refusal, ucid=ucid), server)

    player = server.get_player(ucid=ucid, active=True)
    if player is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Player not found on server '{server.display_name}'.",
            ucid=ucid), server)

    service = _ban_service(ctx)
    if service is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message="The ban list is not available in this installation.", ucid=ucid),
            server)

    name = _player_label(player, ucid)
    banned_by = (sender or "").strip() or ctx.audit_actor
    try:
        await service.ban(ucid, banned_by, body, period)
        result = PlayerActionResult(
            success=True,
            message=(f"Player {name} (ucid={ucid}) banned for {period} days."
                     if period else f"Player {name} (ucid={ucid}) banned permanently."),
            player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(success=False, message=f"Timeout while banning {name}.",
                                    player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("ban_player failed")
        result = PlayerActionResult(success=False, message=f"Failed to ban the player: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


async def _set_muted(ctx: Any, server_name: str, ucid: str, mute: bool) -> PlayerActionResult:
    """The shared body of the mute pair — ONE implementation, because they differ by one word.

    TEMPORARY-REDUNDANCY(W4b-mute): ``Player.mute()`` / ``Player.unmute()`` are the same two methods
    ``/mission player mute|unmute`` (``plugins/mission/commands.py``) and the in-game chat commands
    (``plugins/mission/listener.py``) call, so one word has two callers — the same declared
    redundancy W-R1 names for kick, marked at both call sites. Unlike the Discord commands, the
    console's version refuses when the player is already in the requested state instead of quietly
    repeating the DCS call.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Server '{server_name}' not found.", ucid=ucid))
    player = server.get_player(ucid=ucid, active=True)
    if player is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Player not found on server '{server.display_name}'.",
            ucid=ucid), server)

    name = _player_label(player, ucid)
    already = bool(getattr(player, "muted", False)) is mute
    if already:
        verb = "muted" if mute else "unmuted"
        return await _audited(ctx, PlayerActionResult(
            success=False, message=f"Player {name} is already {verb}.", player_name=name, ucid=ucid),
            server)
    try:
        if mute:
            await player.mute()
        else:
            await player.unmute()
        verb = "muted" if mute else "unmuted"
        result = PlayerActionResult(
            success=True,
            message=f"Player {name} on server '{server.display_name}' {verb}.",
            player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(success=False, message=f"Timeout while muting {name}.",
                                    player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("mute/unmute failed")
        result = PlayerActionResult(success=False, message=f"Failed to change the mute state: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


@action
async def mute_player(ctx: Any, server_name: str, ucid: str) -> PlayerActionResult:
    """Mute ONE player in-game — ``Player.mute()``, mirroring ``/mission player mute``."""
    return await _set_muted(ctx, server_name, ucid, True)


@action
async def unmute_player(ctx: Any, server_name: str, ucid: str) -> PlayerActionResult:
    """Unmute ONE player — ``Player.unmute()``, mirroring ``/mission player unmute``."""
    return await _set_muted(ctx, server_name, ucid, False)


# ── Queries ─────────────────────────────────────────────────────────────────

@action
async def list_missions(ctx: Any, server_name: str) -> MissionListResult:
    """List all available missions on a DCS server."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    try:
        base_dir = await server.get_missions_dir()
        ignore = [".dcssb"]
        if server.locals.get("ignore_dirs"):
            ignore.extend(server.locals["ignore_dirs"])
        installed_missions = [os.path.expandvars(x) for x in await utils.get_cached_mission_list(server)]
        exp_base, file_list = await server.node.list_directory(
            base_dir, pattern=["*.miz", "*.sav"], traverse=True, ignore=ignore
        )
        missions = []
        for x in file_list:
            rel = os.path.relpath(x, exp_base)
            missions.append(
                {
                    "name": rel[:-4],
                    "path": rel,
                    "installed": x in installed_missions
                    or os.path.join(os.path.dirname(x), ".dcssb", os.path.basename(x)) in installed_missions,
                }
            )
        return MissionListResult(
            success=True,
            message=f"{len(missions)} missions found.",
            server_name=server_name,
            missions=missions,
        )
    except Exception as ex:
        log.exception("list_missions failed")
        return MissionListResult(
            success=False,
            message=f"Failed to list missions: {ex}",
            server_name=server_name,
        )


@action
async def get_mission_info(ctx: Any, server_name: str) -> MissionListResult:
    """Get info about the currently running mission."""
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(
            success=False,
            message=f"Server '{server_name}' not found.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionListResult(
            success=False,
            message=f"No active mission on server '{server.display_name}'.",
            server_name=server_name,
        )
    m = server.current_mission
    return MissionListResult(
        success=True,
        message=f"Active mission: {m.display_name}",
        server_name=server_name,
        data={
            "name": m.display_name,
            "filename": m.filename,
            "map": m.map,
            "start_time": m.start_time,
            "mission_time": m.mission_time,
            "num_slots_blue": m.num_slots_blue,
            "num_slots_red": m.num_slots_red,
        },
    )
