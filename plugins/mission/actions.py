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

from core import Status, UploadStatus, utils
from core.actions import (
    TRANSPORT_DISCORD,
    TRANSPORT_SERVICE,
    action,
    audit_action,
)
from core.action_results import (
    MissionControlResult,
    MissionDownloadResult,
    MissionListResult,
    MissionLoadResult,
    MissionUploadResult,
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
    expected_name: str = "",
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
        # STALE-PAGE GUARD: the console posts the row's LOGICAL name beside its index, so a row whose
        # list changed between render and POST is refused — the server never loads the wrong mission.
        if expected_name:
            reason = await _stale_mission_refusal(server, mission_file, expected_name)
            if reason:
                return MissionLoadResult(success=False, message=reason, server_name=server_name)
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


# ── Mission rotation: making a configured mission the START one ──────────────
#
# ``set_active_mission`` changes the bot's START INDEX — the ``listStartIndex`` ``Server.do_startup``
# reads and ``Server.loadNextMission`` rotates from — through the bot's OWN ``Server.setStartIndex``
# (``core/data/impl/serverimpl.py``). ``listStartIndex`` is the REAL one (the field DCS takes to
# start the mission); ``current`` is documentary. The console never writes ``listStartIndex`` or
# ``missionList`` itself; that rule has not changed.
#
# The ONE state it refuses is ``LOADING``. The bot's ``setStartIndex`` routes to DCS in
# ``{STOPPED, PAUSED, RUNNING}`` (the process is up and owns ``net.missionlist`` in memory, so the
# command sticks) and writes the FILE in every other state — but in ``LOADING`` the process is ALSO
# up (DCS owns the list) while the method still takes the file branch, so the change is SILENTLY
# REVERTED by the list DCS re-syncs on load. Every other state sticks: the process-up trio through
# DCS, the file-authoritative trio (``{UNREGISTERED, SHUTDOWN, SHUTTING_DOWN}``) by the file write.

#: The one state ``set_active_mission`` refuses — see the section note above for why.
MISSION_ACTIVE_REFUSED_STATES = (Status.LOADING,)

#: The sentence a ``LOADING`` POST is answered with — and the reason the console omits the control
#: in that state. The console keeps its OWN copy (the generic web surface never imports a plugin
#: module); ``test_webui_missions_active`` pins the two equal.
MISSION_ACTIVE_LOADING_REFUSAL = "The start mission cannot be changed while the server is loading."


@action
async def set_active_mission(ctx: Any, server_name: str, mission: int | str,
                             expected_name: str = "") -> MissionListResult:
    """Make a CONFIGURED mission the START mission — the bot's own ``setStartIndex``.

    ``mission`` is the 1-based ``missionList`` index (what ``setStartIndex`` takes) or the mission's
    logical name (:func:`_resolve_mission_id`, the same resolver its siblings use). The action takes
    ONE snapshot, resolves the mission against it, and hands the 1-based index to the bot's own
    ``Server.setStartIndex`` — never a hand-written ``settings`` write.

    ``LOADING`` is refused (:data:`MISSION_ACTIVE_LOADING_REFUSAL`): the bot's method would write the
    FILE while DCS holds the authoritative list, so the change would not stick. The process-up trio
    (``STOPPED`` / ``PAUSED`` / ``RUNNING``) routes to DCS and the file-write trio
    (``UNREGISTERED`` / ``SHUTDOWN`` / ``SHUTTING_DOWN``) writes the file — both stick, so both are
    accepted.

    An UNKNOWN name, an out-of-range index and a non-positive index all resolve to ``None`` and are
    refused with the one \"not found\" sentence — the range check is done HERE because the bot's own
    method silently CLAMPS an out-of-range index to 1 instead of refusing it, which would otherwise
    make the wrong mission the start one. A WRITE, so it writes its own audit entry.

    ``expected_name`` is the row's LOGICAL name the console posts beside its index: a mismatch
    against the FRESH snapshot refuses the write with :data:`MISSION_LIST_CHANGED`. Empty → no check.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    if server.status in MISSION_ACTIVE_REFUSED_STATES:
        return MissionListResult(success=False, message=MISSION_ACTIVE_LOADING_REFUSAL,
                                 server_name=server_name)
    try:
        missions = await server.getMissionList()
    except Exception as ex:
        log.exception("set_active_mission failed")
        return MissionListResult(success=False, message=f"Failed to list missions: {ex}",
                                 server_name=server_name)
    index = _resolve_mission_id(missions, mission)
    if index is None:
        return MissionListResult(
            success=False, message=f"Mission '{mission}' not found on server '{display}'.",
            server_name=server_name, missions=missions)
    # STALE-PAGE GUARD: the console posts the row's LOGICAL name beside its index, so a row whose
    # list changed between render and POST is refused — the wrong mission never becomes the start one.
    if expected_name:
        reason = await _stale_mission_refusal(server, missions[index - 1], expected_name)
        if reason:
            return MissionListResult(success=False, message=reason, server_name=server_name,
                                     missions=missions)
    name = os.path.basename(_logical_mission(missions[index - 1]))
    try:
        await server.setStartIndex(index)
    except (TimeoutError, asyncio.TimeoutError):
        return MissionListResult(success=False,
                                 message=f"Timeout while making mission '{name}' the start mission.",
                                 server_name=server_name, missions=missions)
    except Exception as ex:
        log.exception("set_active_mission failed")
        return MissionListResult(success=False,
                                 message=f"Failed to make mission '{name}' the start mission: {ex}",
                                 server_name=server_name, missions=missions)
    return MissionListResult(
        success=True, message=f"Mission '{name}' will be loaded when the server next starts.",
        server_name=server_name, missions=missions,
        data={"missions": missions, "index": index, "active": index})


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


# ── Mission FILES: upload / download / list ─────────────────────────────────
#
# The ONE implementation of the three mission-file operations, re-used by the REST surface
# (``plugins/restapi/commands.py``) and, next, by the console's Missions tab. The rules that make it
# "one implementation" and not a second copy:
#
# * the WRITE never touches a mission file directly — every byte goes through
#   ``Server.uploadMission``, the only writer, which routes through ``create_writable_mission``;
# * the newest-copy RESOLVER is the bot's own ``Server.getAllMissionFiles`` (primary vs ``.dcssb``
#   vs ``.dcssb/*.orig``, newest wins) — never a second resolver;
# * ``missionList`` is never written by hand: ``uploadMission`` owns it through ``addMission``.

#: The size cap for an uploaded mission, as ONE named constant so the action, REST and the future
#: tab cannot disagree about it. 100 MiB — comfortably above a real ``.miz`` and far below a mistake.
MAX_MISSION_UPLOAD_BYTES = 100 * 1024 * 1024

_MIZ_SUFFIX = ".miz"

#: The transports that may hand the action a ``str`` source — an http(s) URL or a master-side PATH.
#: A ``str`` NAMES something the CALLER already holds (a Discord attachment URL, a file an in-process
#: caller named), so it is reserved for a caller that did not come through a request: the Discord
#: transport and the in-process SERVICE (MCP). The admin console (``web``) and the REST surface
#: (``plugin``) may hand in BYTES only — a request must never name a path the bot would then read,
#: and a URL from a request would be an outbound fetch it never asked for. An EMPTY transport (a
#: hand-built context) fails CLOSED, like every other check on it (``core/actions.py``).
_STR_SOURCE_TRANSPORTS = frozenset({TRANSPORT_DISCORD, TRANSPORT_SERVICE})

#: Chunk size for the capped URL fetch, so the cap is enforced AS the bytes arrive.
_URL_FETCH_CHUNK = 64 * 1024


def _refuse_upload(name: Any) -> str | None:
    """The typed refusal a posted mission NAME gets, or ``None`` when it is acceptable.

    The rules, all explicit, because this is the traversal gate every transport inherits: the name
    must be a non-empty LEAF (its own ``os.path.basename``), carry no path separator of either kind
    (a ``/`` or a ``\\`` is a directory component on SOME platform this bot runs on), not be ``.``
    or ``..``, and end in ``.miz`` (case-insensitive — the REST surface already accepted that). The
    remaining sanitisation is ``utils.sanitize_filename``'s job (the same helper the admin download
    uses), which is applied to the joined destination at the call site.
    """
    if not isinstance(name, str) or not name:
        return "A mission file name is required."
    if name in (".", "..") or "/" in name or "\\" in name or name != os.path.basename(name):
        return f"'{name}' is not a valid mission file name."
    if not name.casefold().endswith(_MIZ_SUFFIX):
        return f"'{name}' is not a .miz file."
    return None


async def _stage_mission_bytes(node: Any, name: str, data: bytes) -> int:
    """Put *data* in the ``files`` table on *node*'s pool and return its row id.

    THE route bytes take to the OWNING node — the same table ``NodeProxy.write_file`` stages into
    (``core/data/proxy/nodeproxy.py``): the node that owns the mission then reads the row back by id
    through ``Node.write_file(target, <id>)``. ONE route for a master-hosted server and an
    agent-hosted one alike, so no second transport is invented.
    """
    import psycopg

    async with node.apool.connection() as conn:
        cursor = await conn.execute("""
            INSERT INTO files (guild_id, name, data)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (node.guild_id, name, psycopg.Binary(data)))
        return (await cursor.fetchone())[0]


class _SourceTooLarge(Exception):
    """Raised by :func:`_fetch_mission_url` when a source crosses the cap — refused WHOLE."""


def _is_mission_url(source: str) -> bool:
    """Whether *source* is an http(s) URL rather than a filesystem path."""
    return source.lower().startswith(("http://", "https://"))


def _over_cap_message() -> str:
    """The typed refusal for a source over :data:`MAX_MISSION_UPLOAD_BYTES` (read live, so a test
    that patches the constant sees its own value)."""
    return (f"The mission file is larger than the "
            f"{MAX_MISSION_UPLOAD_BYTES // (1024 * 1024)} MiB limit and was not uploaded.")


def _empty_mission_message(leaf: str) -> str:
    """The typed refusal for a ZERO-BYTE source — its OWN sentence.

    A mission with no bytes is not a mission: the DOWNLOAD side already refuses a mission whose read
    answered nothing ("answered with no file content"), and the upload side refuses one too, in its
    own words, BEFORE anything is staged or written. Distinct from ``_refuse_upload``'s name refusals
    (the name may be perfectly valid — the content is not).
    """
    return f"The mission file '{leaf}' is empty and was not uploaded."


async def _fetch_mission_url(node: Any, url: str, limit: int) -> bytes:
    """Fetch *url* and return its bytes, refusing WHOLE once *limit* is exceeded.

    The cap is applied AS the bytes arrive (``iter_chunked`` accumulating a running total), never by
    trusting a ``Content-Length`` a server chose: the chunk that crosses the limit aborts the read
    and nothing is returned, so a caller stages nothing and writes nothing. The connection honours
    the owning node's outbound proxy, the same one ``NodeImpl.write_file`` uses for an http source.
    """
    import aiohttp

    proxy = getattr(node, "proxy", None)
    proxy_auth = getattr(node, "proxy_auth", None)
    chunks: list[bytes] = []
    total = 0
    async with aiohttp.ClientSession() as session:
        async with session.get(url, proxy=proxy, proxy_auth=proxy_auth) as response:
            if response.status != 200:
                raise OSError(f"HTTP {response.status} while fetching the mission source.")
            async for chunk in response.content.iter_chunked(_URL_FETCH_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise _SourceTooLarge()
                chunks.append(chunk)
    return b"".join(chunks)


async def _discard_staged_mission(node: Any, row_id: int) -> None:
    """Delete the staged ``files`` row *row_id* — UNCONDITIONALLY and IDEMPOTENTLY.

    The action OWNS the row it staged (:func:`_stage_mission_bytes`), so it guarantees no row — and
    no mission bytes — survives the call. The writer deletes the row on its ONE success path
    (``NodeImpl.write_file``'s int-source branch); this runs in ``finally`` for EVERY other outcome
    (a typed ``FILE_EXISTS`` / ``FILE_IN_USE`` refusal, a ``WRITE_ERROR``, or a raised exception).
    ``DELETE`` of an already-deleted id is a no-op, so a success deletes exactly once and a failure
    still leaves nothing behind. A cleanup that itself fails is REPORTED, never raised: the
    operation's own outcome must not be masked by its cleanup.
    """
    try:
        async with node.apool.connection() as conn:
            await conn.execute("DELETE FROM files WHERE id = %s", (row_id,))
    except Exception:
        log.exception("upload_mission: could not discard the staged mission row %s.", row_id)


def _copy_of(real: str) -> str:
    """WHICH physical copy *real* is: ``"orig"`` / ``"secondary"`` (``.dcssb``) / ``"primary"``.

    The copy marker is a path COMPONENT — decided by the ONE normaliser (``logical_mission_path``),
    never a bare ``.dcssb`` substring, so a directory whose NAME merely contains the marker
    (``foo.dcssbbar``) is a ``primary``, not a ``secondary``.
    """
    if str(real).endswith(".orig"):
        return "orig"
    if utils.logical_mission_path(str(real)) != os.path.normpath(str(real)):
        return "secondary"
    return "primary"


def _configured_name(path: str, base_dir: str) -> str:
    """The LOGICAL name of a CONFIGURED ``missionList`` entry — ``.dcssb`` / ``.orig`` stripped.

    Uses the ONE shared normaliser (``core.utils.logical_mission_path``) — the SAME one
    ``ServerImpl.getAllMissionFiles`` names its rows with — so a configured entry and the resolver
    name one mission the same way: a path ending in ``.orig`` loses that suffix and a ``.dcssb``
    directory COMPONENT is dropped (never a substring). The result is missions-dir-RELATIVE when
    *path* is under *base_dir*, else the bare basename (a configured entry outside the missions dir
    names itself).
    """
    text = utils.logical_mission_path(str(path))
    try:
        return os.path.relpath(text, base_dir)
    except ValueError:
        return os.path.basename(text)


def _resolved_index(files: list[tuple[str, str]], base_dir: str) -> dict[str, tuple]:
    """The NEWEST-COPY resolver for a CONFIGURED path, keyed by BOTH spellings of one mission.

    ``getAllMissionFiles`` is the bot's own newest-copy resolver: it names the current copy ONCE
    (``logical`` = the ``.dcssb`` / ``.orig`` stripped path, ``real`` = the physical file that wins).
    It is used here as the RESOLVER for a configured entry, never as the list itself. A configured
    entry may be written either as the primary path or as its ``.dcssb`` sibling, so both spellings
    key the SAME entry. The value is ``(name, path, copy, real)`` where ``name`` / ``path`` are
    missions-dir-relative and ``real`` is the absolute physical path a READ would open.
    """
    resolved: dict[str, tuple] = {}
    for logical, real in files:
        logical_norm = os.path.normpath(str(logical))
        real_norm = os.path.normpath(str(real))
        entry = (os.path.relpath(logical_norm, base_dir), os.path.relpath(real_norm, base_dir),
                 _copy_of(real), real_norm)
        resolved[logical_norm] = entry
        secondary = os.path.normpath(os.path.join(os.path.dirname(logical_norm), ".dcssb",
                                                  os.path.basename(logical_norm)))
        resolved[secondary] = entry
    return resolved


def _lookup_configured(configured_path: str, resolved: dict[str, tuple], base_dir: str) -> tuple:
    """The resolver entry a CONFIGURED path names, or its ``missing`` stand-in.

    A configured entry the disk can no longer resolve is NEVER dropped: it is shown as itself
    (:func:`_configured_name`) tagged ``missing``, so the tab always has one row per configured
    mission — exactly the list the bot's own commands index into. The value is
    ``(name, path, copy, real)`` with ``real`` = ``None`` for an unresolvable entry.
    """
    configured_norm = os.path.normpath(str(configured_path))
    entry = resolved.get(configured_norm)
    if entry is None:
        primary = utils.logical_mission_path(configured_norm)
        if primary != configured_norm:
            entry = resolved.get(primary)
    if entry is not None:
        return entry
    name = _configured_name(configured_path, base_dir)
    return name, name, "missing", None


def _resolve_mission_id(missions: list[str], mission: Any) -> int | None:
    """The 1-BASED ``missionList`` position *mission* names, or ``None`` — the identifier the bot's
    own ``deleteMission`` / ``setStartIndex`` take (``missionList`` order).

    An INT is the 1-based position itself. A NAME matches the configured entry's LOGICAL name (its
    ``.dcssb`` / ``.orig`` stripped form), its basename, or either without the ``.miz`` extension,
    all case-insensitively — so a caller may pass what the tab shows. A path can never smuggle a
    directory component in that resolves to something else: an unmatched name is ``None``.
    """
    if isinstance(mission, bool):
        return None
    if isinstance(mission, int):
        return mission if 1 <= mission <= len(missions) else None
    wanted = str(mission or "")
    if not wanted:
        return None
    folded = wanted.casefold()
    for index, configured in enumerate(missions, start=1):
        logical = _configured_name(configured, os.path.dirname(str(configured)) or "")
        base = os.path.basename(str(configured))
        candidates = {str(configured), base, logical, os.path.basename(logical)}
        expanded = set()
        for candidate in candidates:
            expanded.add(candidate)
            if candidate.casefold().endswith(_MIZ_SUFFIX):
                expanded.add(candidate[:-len(_MIZ_SUFFIX)])
        if folded in {candidate.casefold() for candidate in expanded}:
            return index
    return None


def _select_mission(entries: list[dict[str, Any]], mission: Any) -> dict[str, Any] | None:
    """The entry *mission* names, or ``None`` — a 1-based INDEX or a logical NAME (never a path).

    A NAME matches the logical name, its basename, or either without the ``.miz`` extension, all
    case-insensitively — so a caller may pass what the list shows or what a message named, but a
    path can never smuggle a directory component in: it simply matches nothing.
    """
    if isinstance(mission, bool):
        return None
    if isinstance(mission, int):
        if 1 <= mission <= len(entries):
            return entries[mission - 1]
        return None
    wanted = str(mission or "")
    if not wanted:
        return None
    folded = wanted.casefold()
    for entry in entries:
        name = str(entry["name"])
        base = os.path.basename(name)
        candidates = {name, base}
        if name.casefold().endswith(_MIZ_SUFFIX):
            candidates.add(name[:-len(_MIZ_SUFFIX)])
        if base.casefold().endswith(_MIZ_SUFFIX):
            candidates.add(base[:-len(_MIZ_SUFFIX)])
        if folded in {candidate.casefold() for candidate in candidates}:
            return entry
    return None


async def _audited_upload(ctx: Any, result: MissionUploadResult,
                          server: Any) -> MissionUploadResult:
    """Write the trail for an upload *result* and return it — one entry per attempt.

    The typed result is returned rather than the audit helper's value, so the action keeps its
    declared type (``audit_action`` returns the generic base historically).
    """
    await audit_action(ctx, result, server=server)
    return result


@action
async def upload_mission(ctx: Any, server_name: str, name: str, source: bytes | str, *,
                         force: bool = False, autostart: bool = False,
                         load: bool = False) -> MissionUploadResult:
    """Upload ONE ``.miz`` mission to a server — the ONE writer, shared by every transport.

    The order, and why it matters:

    1. the NAME is validated here (:func:`_refuse_upload`), so a traversal or a non-``.miz`` name is
       refused before anything is written, whatever transport called;
    2. the DESTINATION is validated with ``utils.sanitize_filename`` against the server's
       ``missions_dir`` — the same helper the admin download uses, so the two cannot disagree about
       what "inside ``missions_dir``" means;
    3. the SOURCE is normalised to a STAGED row id — the route an agent-hosted server already uses
       — under the trust rule declared at :data:`_STR_SOURCE_TRANSPORTS`:
       * ``bytes`` — refused whole when EMPTY (:func:`_empty_mission_message`), measured against
         :data:`MAX_MISSION_UPLOAD_BYTES` (refuse whole) and staged;
       * an http(s) URL — fetched, size-capped AS the bytes arrive (refuse whole, and refused when
         the source answered zero bytes) and staged, so the cap is the action's and never a
         ``Content-Length`` a server chose. Only a caller that did not come through a request may
         pass one (the Discord upload's attachment URL);
       * a bare filesystem PATH — accepted ONLY from such a privileged caller and passed through
         untouched; the admin console (``web``) and the REST surface (``plugin``) may never name
         one, and an unnamed context fails CLOSED;
    4. ``Server.uploadMission`` writes it — the ONE writer, which routes through
       ``create_writable_mission`` and owns ``missionList`` (``addMission``). Its typed outcome is
       carried through untouched in ``upload_status``;
    5. the STAGED row is cleaned up UNCONDITIONALLY and IDEMPOTENTLY
       (:func:`_discard_staged_mission`) in ``finally``: a typed refusal, a ``WRITE_ERROR`` or a
       raised exception leaves no row — and no mission bytes — behind, and a success deletes it
       exactly once.

    First slice: a LEAF file in the ROOT of ``missions_dir`` only — no subdirectories. A WRITE, so it
    writes its own audit entry.

    ``autostart`` and ``load`` are the two LOAD options the console offers (Frank's card M3) — the
    SAME two the bot's own methods already express, never a new mechanism:

    * ``autostart=True`` arms the rotation so the uploaded mission is the ``listStartIndex`` — the
      position ``Server.do_startup`` reads and ``Server.loadNextMission`` advances from — set through
      the bot's own ``Server.setStartIndex`` (``uploadMission`` has no ``autostart`` parameter itself;
      the ADD path gets the same result through ``Server.addMission(path, autostart=True)``).
    * ``load=True`` loads it NOW by calling the bot's own ``Server.loadMission(index)`` — only when
      the server is RUNNING / PAUSED / STOPPED, the states ``load_mission`` itself accepts.

    Both are best-effort and never mask the upload's own outcome: a mission the writer reports as
    uploaded stays a success, and the message says whether the load option took effect.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionUploadResult(success=False, message=f"Server '{server_name}' not found.",
                                   server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    refusal = _refuse_upload(name)
    if refusal:
        return await _audited_upload(ctx, MissionUploadResult(
            success=False, message=refusal, server_name=server_name), server=server)
    base_dir = os.path.expandvars(await server.get_missions_dir())
    try:
        dest = utils.sanitize_filename(os.path.abspath(os.path.join(base_dir, name)), base_dir)
    except (ValueError, OSError):
        message = f"'{name}' is not a valid mission file name."
        return await _audited_upload(ctx, MissionUploadResult(
            success=False, message=message, server_name=server_name), server=server)
    leaf = os.path.basename(str(dest))

    staged: bytes | str | int = source
    row_id: int | None = None
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if not data:
            # ZERO BYTES is refused with its OWN sentence, before staging: an empty row would be
            # written as an empty mission file (the download side refuses the mirror case too).
            return await _audited_upload(ctx, MissionUploadResult(
                success=False, message=_empty_mission_message(leaf), server_name=server_name,
                filename=leaf), server=server)
        if len(data) > MAX_MISSION_UPLOAD_BYTES:
            return await _audited_upload(ctx, MissionUploadResult(
                success=False, message=_over_cap_message(), server_name=server_name,
                filename=leaf), server=server)
        try:
            row_id = await _stage_mission_bytes(server.node, leaf, data)
        except Exception as ex:                 # a transport never gets a stack trace
            log.exception("upload_mission: could not stage the bytes of '%s'.", leaf)
            return await _audited_upload(ctx, MissionUploadResult(
                success=False, message=f"Failed to upload mission '{leaf}': {ex}",
                server_name=server_name, filename=leaf), server=server)
        staged = row_id
    else:
        # a ``str`` NAMES something the caller already holds; only a caller that did not come
        # through a request may pass one. An unnamed/unknown transport fails CLOSED.
        if ctx.transport not in _STR_SOURCE_TRANSPORTS:
            message = (f"Mission '{leaf}' must be uploaded as file content; a path or URL is not "
                       f"accepted from this transport.")
            return await _audited_upload(ctx, MissionUploadResult(
                success=False, message=message, server_name=server_name, filename=leaf),
                server=server)
        text = str(source or "")
        if _is_mission_url(text):
            try:
                data = await _fetch_mission_url(server.node, text, MAX_MISSION_UPLOAD_BYTES)
            except _SourceTooLarge:
                return await _audited_upload(ctx, MissionUploadResult(
                    success=False, message=_over_cap_message(), server_name=server_name,
                    filename=leaf), server=server)
            except Exception as ex:
                log.exception("upload_mission: could not fetch the source of '%s'.", leaf)
                return await _audited_upload(ctx, MissionUploadResult(
                    success=False, message=f"Failed to upload mission '{leaf}': {ex}",
                    server_name=server_name, filename=leaf), server=server)
            if not data:
                # the fetched source answered zero bytes — the SAME refusal as an empty upload
                return await _audited_upload(ctx, MissionUploadResult(
                    success=False, message=_empty_mission_message(leaf), server_name=server_name,
                    filename=leaf), server=server)
            try:
                row_id = await _stage_mission_bytes(server.node, leaf, data)
            except Exception as ex:
                log.exception("upload_mission: could not stage the bytes of '%s'.", leaf)
                return await _audited_upload(ctx, MissionUploadResult(
                    success=False, message=f"Failed to upload mission '{leaf}': {ex}",
                    server_name=server_name, filename=leaf), server=server)
            staged = row_id
        # else: a bare master-side path from a privileged caller — passed through untouched.
    try:
        rc = await server.uploadMission(leaf, staged, force=force)
    except Exception as ex:
        log.exception("upload_mission failed")
        return await _audited_upload(ctx, MissionUploadResult(
            success=False, message=f"Failed to upload mission '{leaf}': {ex}",
            server_name=server_name, filename=leaf), server=server)
    finally:
        # UNCONDITIONAL and IDEMPOTENT: the action owns every row it staged, so no outcome — a
        # typed refusal from the writer, a write error, a raised exception — may leave one behind.
        if row_id is not None:
            await _discard_staged_mission(server.node, row_id)
    status = rc.name if isinstance(rc, UploadStatus) else str(rc)
    if rc == UploadStatus.OK:
        message = f"Mission '{leaf}' uploaded to server '{display}'."
        success = True
        if autostart or load:
            # THE LOAD OPTIONS, best-effort and AFTER the writer succeeded. The uploaded mission's
            # position is read back from the CONFIGURED list (the writer owns ``missionList``), so the
            # index handed to ``setStartIndex`` / ``loadMission`` is the bot's own 1-based one.
            try:
                missions = await server.getMissionList()
            except Exception:
                missions = []
            index = _resolve_mission_id(missions, leaf)
            if index is None:
                message += " It could not be found in the mission list, so it was not armed to load."
            else:
                if autostart:
                    await server.setStartIndex(index)
                    message += " It will be loaded when the server next starts."
                if load and server.status in (Status.RUNNING, Status.PAUSED, Status.STOPPED):
                    try:
                        await server.loadMission(index)
                        message += " It was loaded now."
                    except Exception as ex:
                        log.exception("upload_mission: could not load '%s'.", leaf)
                        message += f" It could not be loaded now: {ex}"
    elif rc == UploadStatus.FILE_EXISTS:
        message = f"Mission '{leaf}' already exists on server '{display}'."
        success = False
    elif rc == UploadStatus.FILE_IN_USE:
        message = (f"Mission '{leaf}' is currently in use on server '{display}' and was not "
                   f"replaced.")
        success = False
    else:
        message = f"Failed to upload mission '{leaf}' to server '{display}' ({status})."
        success = False
    return await _audited_upload(ctx, MissionUploadResult(
        success=success, message=message, server_name=server_name, mission_name=leaf,
        filename=leaf, upload_status=status), server=server)


@action
async def download_mission(ctx: Any, server_name: str, mission: int | str) -> MissionDownloadResult:
    """Read ONE mission's BYTES — resolved by the newest-copy rule and validated before any read.

    ``mission`` is the LOGICAL mission: a 1-based INDEX into the list (:func:`get_mission_list`'s
    order) or a NAME (the logical name or its basename, with or without the ``.miz`` extension,
    case-insensitive). A PATH is never accepted — the caller NAMES the mission, and the action
    resolves and validates it against ``missions_dir``.

    The resolution is the bot's own ``Server.getAllMissionFiles`` (the newest of primary / ``.dcssb``
    / ``.dcssb/*.orig``), and the resolved path is checked with ``utils.sanitize_filename`` before it
    is read. The BYTES ride this result (``content``) with the LOGICAL filename to stream
    (``filename``, ``.orig`` stripped), so every caller streams the same bytes under the same name
    and none re-resolves the newest copy. A READ: it is NOT audited.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionDownloadResult(success=False, message=f"Server '{server_name}' not found.",
                                     server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    base_dir = os.path.expandvars(await server.get_missions_dir())
    try:
        configured = await server.getMissionList()
        files = await server.getAllMissionFiles()
    except Exception as ex:
        log.exception("download_mission failed")
        return MissionDownloadResult(success=False, message=f"Failed to list missions: {ex}",
                                     server_name=server_name)
    # THE CONFIGURED list is the list — the download names a mission by the SAME 1-based index (or
    # name) the tab shows, resolved through the newest-copy resolver. A mission not on the
    # configured list is not downloadable through this seam.
    resolved = _resolved_index(files, base_dir)
    entries = []
    for index, configured_path in enumerate(configured, start=1):
        name, path, _copy, real = _lookup_configured(configured_path, resolved, base_dir)
        entries.append({"index": index, "name": name, "path": path, "real": real})
    chosen = _select_mission(entries, mission)
    if chosen is None or not chosen.get("real"):
        return MissionDownloadResult(
            success=False, message=f"Mission '{mission}' not found on server '{display}'.",
            server_name=server_name)
    try:
        path = utils.sanitize_filename(os.path.abspath(chosen["real"]), base_dir)
    except (ValueError, OSError):
        return MissionDownloadResult(success=False,
                                     message=f"'{mission}' is not a valid mission.",
                                     server_name=server_name)
    try:
        content = await server.node.read_file(str(path))
    except FileNotFoundError:
        return MissionDownloadResult(success=False,
                                     message=f"Mission '{chosen['name']}' not found.",
                                     server_name=server_name)
    except Exception as ex:
        log.exception("download_mission failed")
        return MissionDownloadResult(success=False, message=f"Failed to read mission: {ex}",
                                     server_name=server_name)
    if isinstance(content, int):
        # An agent node hands back a files-row id rather than the bytes; the proxy that carries a
        # real mission read resolves it. Reaching here means the bytes could not be read.
        return MissionDownloadResult(success=False,
                                     message=f"Could not read mission '{chosen['name']}'.",
                                     server_name=server_name)
    return MissionDownloadResult(
        success=True, message=f"Mission '{chosen['name']}'.",
        server_name=server_name, mission_name=os.path.basename(chosen["name"]),
        filename=os.path.basename(chosen["name"]), content=content)


@action
async def get_mission_list(ctx: Any, server_name: str) -> MissionListResult:
    """The Missions tab's list — 1-based, the newest copy named, and the rotation state.

    Built on the CONFIGURED ``Server.getMissionList()`` — one entry per configured mission, in
    ``missionList`` order — so the 1-based ``index`` every row carries is the identifier the bot's own
    add / delete / set-next / load take. The bot's ``Server.getAllMissionFiles`` (the newest-copy
    resolver) is used only to NAME each configured entry's current copy, never as the list itself: a
    mission file that is not configured does not appear, and a configured entry whose file is gone is
    shown as ``missing`` rather than dropped. Each entry, in ``result.missions`` and in
    ``data["missions"]``, carries:

    * ``index`` — the 1-based position in the configured ``missionList``;
    * ``name`` — the logical name (``.orig`` / ``.dcssb`` stripped), relative to ``missions_dir``;
    * ``path`` — the physical file CURRENTLY used (the newest of primary / ``.dcssb`` / ``.orig``),
      relative to ``missions_dir``; for an unresolvable entry it is the logical name and ``copy`` reads
      ``"missing"``;
    * ``copy`` — WHICH physical copy that is: ``"primary"`` / ``"secondary"`` (``.dcssb``) /
      ``"orig"`` / ``"missing"`` (the file is gone);
    * ``current`` — whether it is the mission the server has loaded;
    * ``active`` — the field's name for the START mission: whether it is the mission the server will
      load — the row the ``listStartIndex`` names (the rotation pointer ``Server.do_startup`` reads).
      It is a DIFFERENT fact from ``current`` (the RUNNING mission), and at most one row carries it;
    * ``resolved`` — whether the resolver found a file for it (``False`` exactly when ``copy`` is
      ``"missing"``).

    ``data`` also carries the rotation state: ``listStartIndex`` (the serverSetting, read through the
    bot's own ``getStartIndex``), ``current`` (the 1-based index of the loaded mission, or 0),
    ``active`` (the 1-based index ``listStartIndex`` names — the START mission, or 0 when it names no
    row) and ``next`` (= ``listStartIndex + 1``, wrapping over the list exactly as ``loadNextMission``
    wraps; 0 when there are no missions). A READ: it is NOT audited.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    try:
        base_dir = os.path.expandvars(await server.get_missions_dir())
        configured = await server.getMissionList()
        files = await server.getAllMissionFiles()
        start = int(await server.getStartIndex() or 1)
        current_file = await server.get_current_mission_file() or ""
    except Exception as ex:
        log.exception("get_mission_list failed")
        return MissionListResult(success=False, message=f"Failed to list missions: {ex}",
                                 server_name=server_name)
    # THE CONFIGURED ``missionList`` IS the list — one row per configured entry, in ``missionList``
    # order, so the 1-based index on every row is the identifier the bot's own add / delete /
    # set-next / load take. ``getAllMissionFiles`` is the RESOLVER for a configured path (the newest
    # of primary / ``.dcssb`` / ``.orig`` wins), never the list itself: a mission file that is not
    # configured does NOT appear, and a configured entry whose file is gone is shown as ``missing``
    # rather than silently dropped.
    resolved = _resolved_index(files, base_dir)
    missions: list[dict[str, Any]] = []
    current_index = 0
    #: ``listStartIndex`` names the START mission's row (1-based); 0 when it names no row. Distinct
    #: from ``current_index`` (the LOADED mission). At most one row is ``active`` (the field's name).
    active_index = start if 1 <= start <= len(configured) else 0
    for index, configured_path in enumerate(configured, start=1):
        name, path, copy, real = _lookup_configured(configured_path, resolved, base_dir)
        is_resolved = real is not None
        is_current = bool(current_file) and (
            (is_resolved and os.path.normpath(str(real)) == os.path.normpath(str(current_file)))
            or os.path.normpath(str(configured_path)) == os.path.normpath(str(current_file)))
        if is_current:
            current_index = index
        missions.append({
            "index": index,
            "name": name,
            "path": path,
            "copy": copy,
            "current": is_current,
            "active": index == active_index,
            "resolved": is_resolved,
        })
    count = len(missions)
    next_index = 0
    if count:
        next_index = start + 1
        if next_index > count:
            next_index = 1
    return MissionListResult(
        success=True, message=f"{count} configured mission(s) on server '{display}'.",
        server_name=server_name, missions=missions,
        data={"missions": missions, "listStartIndex": start, "current": current_index,
              "active": active_index, "next": next_index})


# ── Mission LIST maintenance: the file picker, add, delete ──────────────────
#
# The console's Missions tab mirrors ``plugins/mission/commands.py``'s ``/mission add`` and
# ``/mission delete`` — the same domain methods, the same questions, no invented behaviour. Every
# write goes through the bot's own ``Server.addMission`` / ``Server.deleteMission`` (which own
# ``missionList`` and route through DCS while the server is up), so the console never writes a
# mission file, a ``.dcssb`` copy or ``missionList`` itself.

#: The suffixes the ADD picker offers — the SAME pair ``mizfile_autocomplete`` lists, so the file a
#: person may pick is the file the action will accept.
_ADDABLE_SUFFIXES = (".miz", ".sav")


@action
async def get_addable_missions(ctx: Any, server_name: str) -> MissionListResult:
    """The missions in ``missions_dir`` that are NOT yet configured — the ADD picker's own list.

    The rule is ``plugins/mission/commands.py::mizfile_autocomplete``'s, stated once here so the two
    cannot drift: list ``missions_dir`` with ``pattern=['*.miz','*.sav']``, ``traverse=True``, ignoring
    ``.dcssb`` AND ``server.locals['ignore_dirs']``, then keep only files NOT already installed —
    checked through ``utils.get_cached_mission_list(server)`` (the API Frank means) against BOTH the
    plain path and its ``.dcssb/<basename>`` spelling. Each entry's ``path`` is the
    missions-dir-relative path (exactly the value ``/mission add`` autocompletes), which is what a
    console form posts back. A READ: it is NOT audited.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    try:
        base_dir = await server.get_missions_dir()
        ignore = [".dcssb"]
        if server.locals.get("ignore_dirs"):
            ignore.extend(server.locals["ignore_dirs"])
        installed = [os.path.expandvars(x) for x in await utils.get_cached_mission_list(server)]
        exp_base, file_list = await server.node.list_directory(
            base_dir, pattern=["*.miz", "*.sav"], traverse=True, ignore=ignore)
    except Exception as ex:
        log.exception("get_addable_missions failed")
        return MissionListResult(success=False, message=f"Failed to list mission files: {ex}",
                                 server_name=server_name)
    missions: list[dict[str, Any]] = []
    for path in file_list:
        if path in installed:
            continue
        if os.path.join(os.path.dirname(path), ".dcssb", os.path.basename(path)) in installed:
            continue
        rel = os.path.relpath(path, exp_base)
        missions.append({"name": rel[:-4], "path": rel})
    return MissionListResult(
        success=True, message=f"{len(missions)} mission file(s) available to add.",
        server_name=server_name, missions=missions, data={"missions": missions})


def _refuse_add(path: Any) -> str | None:
    """The typed refusal a posted ADD path gets, or ``None`` when it is acceptable.

    The path is missions-dir-RELATIVE (the picker's own value) and may carry DIRECTORY components —
    a real mission may live in a subdirectory — but must end in a known mission suffix, must not be
    absolute and must not be ``.``/``..``. The remaining traversal gate is ``utils.sanitize_filename``
    at the call site, which is what actually keeps the resolved path inside ``missions_dir``.
    """
    if not isinstance(path, str) or not path:
        return "A mission file is required."
    if path in (".", "..") or os.path.isabs(path) or path.endswith(os.path.sep):
        return f"'{path}' is not a valid mission file path."
    if not path.casefold().endswith(_ADDABLE_SUFFIXES):
        return f"'{path}' is not a mission file."
    return None


@action
async def add_mission(ctx: Any, server_name: str, path: str, *, autostart: bool = False,
                      load: bool = False) -> MissionListResult:
    """Add ONE file from ``missions_dir`` to a server's ``missionList`` — mirroring ``/mission add``.

    ``path`` is the missions-dir-RELATIVE value the ADD picker (``get_addable_missions``) produced;
    the action joins it to ``missions_dir`` and validates the result with ``utils.sanitize_filename``
    before handing it to the bot's own ``Server.addMission`` — the ONE writer of ``missionList``.

    ``autostart=True`` is the flag that delivers "loaded at next start": ``Server.addMission`` sets
    ``listStartIndex`` to the added mission's position (the field ``Server.do_startup`` reads), which
    is exactly what ``Server.setStartIndex`` does for the upload path. ``load=True`` mirrors Discord's
    follow-up question ("Do you want to load this mission?") ONLY when the server is RUNNING / PAUSED
    / STOPPED; the mission is loaded through the bot's own ``Server.loadMission`` and never silently.
    A WRITE, so it writes its own audit entry.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    refusal = _refuse_add(path)
    if refusal:
        return MissionListResult(success=False, message=refusal, server_name=server_name)
    base_dir = os.path.expandvars(await server.get_missions_dir())
    try:
        dest = utils.sanitize_filename(os.path.abspath(os.path.join(base_dir, path)), base_dir)
    except (ValueError, OSError):
        return MissionListResult(success=False,
                                 message=f"'{path}' is not a valid mission file path.",
                                 server_name=server_name)
    name = os.path.basename(dest)
    try:
        new_list = await server.addMission(os.path.normpath(dest), autostart=autostart)
    except (TimeoutError, asyncio.TimeoutError):
        return MissionListResult(success=False,
                                 message=f"Timeout while adding mission '{name}'.",
                                 server_name=server_name)
    except Exception as ex:
        log.exception("add_mission failed")
        return MissionListResult(success=False,
                                 message=f"Failed to add mission '{name}': {ex}",
                                 server_name=server_name)
    index = _resolve_mission_id([os.path.normpath(x) for x in new_list], os.path.normpath(dest))
    message = f"Mission '{name}' added to server '{display}'."
    loaded = False
    if autostart:
        message += " It will be loaded when the server next starts."
    if load and server.status in (Status.RUNNING, Status.PAUSED, Status.STOPPED) and index is not None:
        try:
            if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
                await server.stop()
            loaded = bool(await server.loadMission(index, modify_mission=False, use_orig=True))
            message += " It was loaded now." if loaded else " It could NOT be loaded."
        except (TimeoutError, asyncio.TimeoutError):
            message += " Timeout while loading it."
        except Exception as ex:
            log.exception("add_mission (load) failed")
            message += f" It could not be loaded now: {ex}"
    return MissionListResult(
        success=True, message=message, server_name=server_name, missions=new_list,
        data={"missions": new_list, "index": index, "loaded": loaded})


@action
async def delete_mission(ctx: Any, server_name: str, mission: int | str, *,
                         delete_from_disk: bool = False,
                         expected_name: str = "") -> MissionListResult:
    """Remove ONE mission from the ``missionList`` — mirroring ``/mission delete``.

    ``mission`` is the 1-based ``missionList`` index (the identifier the bot's own ``deleteMission``
    takes) or the mission's logical name. The bot REFUSES the running mission itself
    (``AttributeError: Can't delete the running mission!``) — surfaced AS ITSELF, never reworded —
    and the same condition is pre-checked HERE, in the SENTENCE the Discord command uses
    (``plugins/mission/commands.py::/mission delete``: "You can't delete the running mission."), so
    an operator sees ONE message for the one rule on both surfaces. The pre-check is what normally
    fires (the two conditions are the same), which is why its wording must match the command's.

    ``delete_from_disk=True`` is Discord's SECOND question ("Delete "X" also from disk?"): it
    removes the primary file, the ``.dcssb`` copy and the ``.orig``, in the SAME order the command
    does. A file that is already gone is reported, not raised. A WRITE, so it writes its own audit
    entry.

    ``expected_name`` is the LOGICAL name the console posted beside its 1-based index. When it is
    given, the mission the FRESH snapshot puts at ``mission`` must name the SAME, or the WHOLE action
    is refused with :data:`MISSION_LIST_CHANGED` through the SAME :func:`_stale_mission_refusal` path
    the load and the two list writes use — a stale page must fail honestly rather than delete the
    wrong mission. Absent/empty (a direct caller, or an older page) → no such check.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    try:
        missions = await server.getMissionList()
    except Exception as ex:
        log.exception("delete_mission failed")
        return MissionListResult(success=False, message=f"Failed to list missions: {ex}",
                                 server_name=server_name)
    index = _resolve_mission_id(missions, mission)
    if index is None:
        return MissionListResult(success=False,
                                 message=f"Mission '{mission}' not found on server '{display}'.",
                                 server_name=server_name)
    filename = missions[index - 1]
    # STALE-PAGE GUARD: the console posts the row's LOGICAL name beside its index, so a row whose
    # list changed between render and POST is refused — the server never deletes the wrong mission.
    if expected_name:
        reason = await _stale_mission_refusal(server, filename, expected_name)
        if reason:
            return MissionListResult(success=False, message=reason, server_name=server_name,
                                     missions=missions)
    name = os.path.basename(_configured_name(filename, os.path.dirname(str(filename)) or ""))
    if server.status in (Status.RUNNING, Status.PAUSED, Status.STOPPED) and server.current_mission \
            and os.path.normpath(str(filename)) == os.path.normpath(
                str(getattr(server.current_mission, "filename", "") or "")):
        return MissionListResult(success=False, message="You can't delete the running mission.",
                                 server_name=server_name, missions=missions)
    try:
        new_list = await server.deleteMission(index)
    except AttributeError as ex:
        # the bot's OWN refusal wording — surfaced as itself (the running-mission guard)
        return MissionListResult(success=False, message=str(ex), server_name=server_name,
                                 missions=missions)
    except (TimeoutError, asyncio.TimeoutError):
        return MissionListResult(success=False,
                                 message="Timeout while deleting mission.\n"
                                         "Please reconfirm that the deletion was successful.",
                                 server_name=server_name)
    except Exception as ex:
        log.exception("delete_mission failed")
        return MissionListResult(success=False,
                                 message=f"Failed to delete mission '{name}': {ex}",
                                 server_name=server_name)
    message = f"Mission '{name}' removed from the list."
    removed_from_disk = False
    if delete_from_disk:
        try:
            await _delete_mission_files(server, filename)
            removed_from_disk = True
            message += " It was deleted from disk."
        except PermissionError:
            message += " Permission denied while deleting it from disk."
        except FileNotFoundError:
            message += " It was already deleted from disk."
        except Exception as ex:
            log.exception("delete_mission (disk) failed")
            message += f" It could not be deleted from disk: {ex}"
    return MissionListResult(
        success=True, message=message, server_name=server_name, missions=new_list,
        data={"missions": new_list, "index": index, "deleted_from_disk": removed_from_disk})


async def _delete_mission_files(server: Any, filename: str) -> None:
    """Delete a mission's three physical copies — the primary, the ``.dcssb`` copy and the ``.orig``.

    The EXACT statements, in the EXACT order, of ``plugins/mission/commands.py::/mission delete``
    (``server.node.remove_file(filename)``, then the primary/secondary counterpart, then
    ``secondary + '.orig'``), so the two cannot disagree about WHAT is removed or IN WHICH ORDER. A
    configured entry written as the ``.dcssb`` spelling is removed FIRST and derives the primary; a
    primary entry is removed first and derives the ``.dcssb`` sibling. Each removal is independent
    and a file that is already gone is reported by the caller, never raised.
    """
    filename = os.path.normpath(str(filename))
    primary = utils.logical_mission_path(filename)
    await server.node.remove_file(filename)
    if primary != filename:
        # a ``.dcssb``-spelled configured entry: the given file IS the copy, so derive the primary —
        # component-anchored (the SAME normaliser as everywhere else), never a bare ``.dcssb`` replace
        # that could mangle a directory whose NAME merely contains the marker.
        secondary = filename
        await server.node.remove_file(primary)
    else:
        secondary = os.path.join(os.path.dirname(filename), ".dcssb", os.path.basename(filename))
        await server.node.remove_file(secondary)
    await server.node.remove_file(secondary + ".orig")


# ── Mission-list maintenance: bulk removal (every state but LOADING) and move up/down (offline) ──
#
# BULK REMOVAL is the row's own delete, repeated: ``delete_missions`` calls the bot's own
# ``Server.deleteMission`` (``core/data/impl/serverimpl.py``) once per selected mission, DESCENDING
# (largest index first) so an earlier removal cannot shift a later index. ONE code path for every
# state: ``deleteMission`` routes through DCS in ``{STOPPED, PAUSED, RUNNING}`` and edits the mission
# file in every other state, so the removal STICKS everywhere — except ``LOADING``, where the DCS
# process is up (it owns ``net.missionlist`` in memory) while the method still takes the FILE branch,
# so DCS reverts the removal. Only ``LOADING`` is refused; every other state is accepted.
#
# MOVE UP/DOWN replaces the ORDER of ``missionList`` in ONE go through the bot's own
# ``Server.setMissionList`` — the ONE writer of the order (added for M5), which validates the
# replacement and recomputes the rotation pointer by PATH. While the DCS process is up
# (``{LOADING, STOPPED, PAUSED, RUNNING}``) it holds ``net.missionlist`` in memory and re-syncs the
# file from its own copy, so a file write made then is SILENTLY REVERTED — and Frank's ruling is that
# a reorder is not done live, so the move is offered only in the file-write states
# (``{UNREGISTERED, SHUTDOWN, SHUTTING_DOWN}``) and REFUSES a POST outside them; ``setMissionList``
# refuses too, as the last line. The client posts an INTENT (a selection, or a direction) and the
# action derives the new list from a FRESH snapshot — never from a list the browser rendered.

#: The sentence the OFFLINE-ONLY move refuses with, and the reason the console shows where it omits
#: the control. The console keeps its OWN copy (the generic web surface never imports a plugin
#: module); ``test_webui_missions_reorder`` pins the two together.
MISSION_LIST_OFFLINE_ONLY = "Missions can only be changed while the server is not running."

#: The four process-UP states: DCS holds ``net.missionlist`` in memory in every one of them, so a
#: file write made then is reverted. The OFFLINE-ONLY move is accepted only OUTSIDE this set.
MISSION_LIST_ONLINE_STATES = (Status.LOADING, Status.STOPPED, Status.PAUSED, Status.RUNNING)

#: The ONE state BULK REMOVAL refuses: ``LOADING``. ``Server.deleteMission`` routes to DCS in
#: ``{STOPPED, PAUSED, RUNNING}`` and edits the mission file in every other state — but in
#: ``LOADING`` the DCS process is up (it owns ``net.missionlist`` in memory) while the method still
#: takes the FILE branch, so DCS reverts the removal on load. Every other state sticks.
MISSION_DELETE_REFUSED_STATES = (Status.LOADING,)

#: The sentence a ``LOADING`` bulk-removal POST is answered with — and the console's own copy of it
#: (the generic web surface never imports a plugin module; a test pins the two together).
MISSION_DELETE_LOADING_REFUSAL = "Missions cannot be removed while the server is loading."

#: The sentence a mission write refuses with when the row the console rendered no longer names the
#: mission the FRESH snapshot puts at that position — the list changed between render and POST.
#: A write carries the row's LOGICAL name beside its 1-based index, and THIS is what a mismatch
#: answers with: an index is a POSITION, and a position stops identifying anything the moment the
#: list changes. The console shows the action's own message verbatim, so this IS the console's
#: sentence — but the refusal lives in the action, so the wording is owned here.
MISSION_LIST_CHANGED = ("The mission list has changed since this page was loaded. "
                        "Reload the page and try again.")


async def _stale_mission_refusal(server: Any, configured: str, wanted: Any) -> str:
    """The :data:`MISSION_LIST_CHANGED` sentence when *wanted* no longer names the mission the FRESH
    snapshot puts at *configured*'s row, else ``""``.

    ``wanted`` is the LOGICAL name the console rendered (:func:`_configured_name`'s own spelling,
    relative to the missions dir). ``""`` is returned — i.e. NO refusal — when the request carried no
    name (a direct caller, or an older page) or when the missions dir cannot be read: the guard only
    ever fires on a name the console actually supplied.
    """
    wanted = str(wanted or "")
    if not wanted:
        return ""
    try:
        base_dir = os.path.expandvars(await server.get_missions_dir())
    except Exception as e:
        # A skipped check must be VISIBLE: an unreadable missions dir silently disabling the guard is
        # indistinguishable from a check that passed. Return the same value, but record why.
        log.warning("Mission-list staleness check skipped for '%s': the missions directory could not be "
                    "read (%s)", configured, e)
        return ""
    if not base_dir:
        return ""
    have = _configured_name(configured, base_dir)
    if os.path.normcase(str(have)) == os.path.normcase(wanted):
        return ""
    return MISSION_LIST_CHANGED


def _logical_mission(path: Any) -> str:
    """A mission path with its ``.orig`` suffix and ``.dcssb`` copy marker stripped — the LOGICAL path.

    The SAME normalisation ``ServerImpl.setMissionList`` uses for its pointer recompute (both call
    ``core.utils.logical_mission_path``), so a primary-spelled entry and the ``.dcssb`` file DCS
    reports as current name ONE mission — which is what lets the running-mission pre-check recognise
    the current mission however it is spelled. The ``.dcssb`` marker is a path COMPONENT, never a
    substring: ``foo.dcssbbar`` is NOT the same mission as ``foobar``.
    """
    return utils.logical_mission_path(str(path))


def _mission_list_online(server: Any) -> bool:
    """Whether the DCS process is up for *server* — the states the mission-list writes refuse in."""
    return server.status in MISSION_LIST_ONLINE_STATES


@action
async def delete_missions(ctx: Any, server_name: str, missions: list[int], *,
                          delete_from_disk: bool = False,
                          expected_names: dict[int, str] | None = None) -> MissionListResult:
    """Remove SEVERAL missions — the row's own delete, repeated, in every state but ``LOADING``.

    ``missions`` is the list of 1-based ``missionList`` indexes the operator selected. The action
    takes ONE snapshot of the list, resolves the selection against it, and hands each selected index
    to the bot's OWN per-mission delete (``Server.deleteMission``) — DESCENDING, largest index first,
    so an earlier removal cannot shift a later index. There is ONE code path for every state: the
    bot's method routes through DCS while the process is up (``{STOPPED, PAUSED, RUNNING}``) and edits
    the mission file otherwise, so the removal sticks in all of them.

    The ONE state it refuses is ``LOADING`` (:data:`MISSION_DELETE_LOADING_REFUSAL`): the process is
    up (DCS owns ``net.missionlist``) while ``deleteMission`` takes its FILE branch, so DCS reverts
    the removal. Every other state is accepted.

    The running mission cannot be selected (the console omits it), and if it arrives anyway — a stale
    page — it is REFUSED while the OTHER selected missions are still removed: the action reports per
    mission, honestly (``Removed N of M. Refused: the running mission (name.miz).``), NAMING the
    mission rather than quoting a row number the console no longer shows, and never rounds up
    to a flat ``removed M``. Duplicates collapse to one. An empty selection is a no-op.
    ``delete_from_disk=True`` is the SECOND, optional step — for every mission actually removed, its
    three physical copies (primary, ``.dcssb``, ``.orig``) are deleted in the Discord command's order;
    a file already gone is reported, not raised. A WRITE, so it writes its own audit entry.

    ``expected_names`` is the ``{1-based index: logical name}`` map the console posts beside its
    selection: for a selected index it names, the name the FRESH snapshot puts at that position must
    be the SAME, or the WHOLE action is refused with :data:`MISSION_LIST_CHANGED` (a stale page must
    fail honestly rather than delete the wrong mission). Absent/empty → no such check.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    if server.status in MISSION_DELETE_REFUSED_STATES:
        return MissionListResult(success=False, message=MISSION_DELETE_LOADING_REFUSAL,
                                 server_name=server_name)
    try:
        current = [os.path.normpath(x) for x in await server.getMissionList()]
    except Exception as ex:
        log.exception("delete_missions failed")
        return MissionListResult(success=False, message=f"Failed to list missions: {ex}",
                                 server_name=server_name)
    if not isinstance(missions, (list, tuple)):
        return MissionListResult(success=False, message="A mission selection is required.",
                                 server_name=server_name)
    selected: list[int] = []
    refused: list[str] = []
    for entry in missions:
        if isinstance(entry, bool) or not isinstance(entry, int):
            refused.append(f"{entry!r} is not a mission index")
            continue
        if not 1 <= entry <= len(current):
            refused.append(f"{entry} is not a mission index")
            continue
        if entry not in selected:
            selected.append(entry)
    if not selected and not refused:
        return MissionListResult(success=True, message="No missions were selected.",
                                 server_name=server_name, missions=current,
                                 data={"missions": current, "removed": 0, "total": 0,
                                       "refused": []})
    if not selected:
        return MissionListResult(
            success=False, message="No mission could be resolved: " + "; ".join(refused) + ".",
            server_name=server_name, missions=current)
    # STALE-PAGE GUARD: the console posts the row's LOGICAL name beside its index, so a selection
    # whose list changed between render and POST is refused WHOLE — never acted on the wrong mission.
    for index in selected:
        wanted = expected_names.get(index) if expected_names else None
        reason = await _stale_mission_refusal(server, current[index - 1], wanted)
        if reason:
            return MissionListResult(success=False, message=reason, server_name=server_name,
                                     missions=current)
    current_file = await server.get_current_mission_file()
    current_logical = _logical_mission(current_file) if current_file else ""
    # DESCENDING — the largest index first: removing one cannot shift the index of another still to
    # come, in the list or in the disk step (which reads its filename from the SAME snapshot).
    result_list = current
    removed: list[int] = []
    for index in sorted(selected, reverse=True):
        entry_logical = _logical_mission(current[index - 1])
        # NAME THE MISSION, never a number (M9-fix): the console's tab no longer numbers its rows, so
        # a refusal that quoted one would name nothing the operator can see. The name is the mission's
        # logical file name — the same spelling the tab renders.
        name = os.path.basename(entry_logical)
        if current_logical and entry_logical == current_logical:
            # The running mission is not selectable; a stale page that names it is refused HERE, and
            # the OTHER selected missions are still removed (the honest per-mission report below).
            refused.append(f"the running mission ({name})")
            continue
        try:
            result_list = await server.deleteMission(index)
        except AttributeError as ex:
            # the bot's OWN refusal wording (the running-mission guard) — surfaced as itself
            refused.append(f"mission {name} could not be removed: {ex}")
        except (TimeoutError, asyncio.TimeoutError):
            refused.append(f"mission {name} could not be removed: timeout")
        except Exception as ex:
            log.exception("delete_missions failed")
            refused.append(f"mission {name} could not be removed: {ex}")
        else:
            removed.append(index)
    removed_n = len(removed)
    if not removed_n:
        return MissionListResult(
            success=False, message="No mission could be removed: " + "; ".join(refused) + ".",
            server_name=server_name, missions=result_list)
    total = removed_n + len(refused)
    message = (f"Removed {removed_n} of {total}. Refused: " + "; ".join(refused) + "." if refused
               else f"Removed {removed_n} mission(s) from the list.")
    removed_from_disk = False
    if delete_from_disk:
        notes: list[str] = []
        for index in sorted(removed):
            filename = current[index - 1]
            name = os.path.basename(_logical_mission(filename))
            try:
                await _delete_mission_files(server, filename)
                removed_from_disk = True
            except FileNotFoundError:
                notes.append(f"'{name}' was already deleted from disk")
            except PermissionError:
                notes.append(f"permission denied deleting '{name}' from disk")
            except Exception as ex:
                log.exception("delete_missions (disk) failed")
                notes.append(f"'{name}' could not be deleted from disk: {ex}")
        if notes:
            message += " " + "; ".join(notes) + "."
        elif removed_from_disk:
            message += " Their files were deleted from disk."
    return MissionListResult(
        success=True, message=message, server_name=server_name, missions=result_list,
        data={"missions": result_list, "removed": removed_n, "total": total, "refused": refused,
              "deleted_from_disk": removed_from_disk})


@action
async def reorder_mission(ctx: Any, server_name: str, mission: int | str,
                          direction: str, expected_name: str = "") -> MissionListResult:
    """Move ONE mission up or down the ``missionList`` — the offline-only reorder.

    ``mission`` is the 1-based ``missionList`` index (or its logical name); ``direction`` is ``"up"``
    or ``"down"``. The action takes ONE snapshot, resolves the mission, and builds the PERMUTED list
    (same members, new order) which it hands to ``Server.setMissionList`` — so the rotation pointer
    follows the mission, computed by path inside the writer.

    An ``up`` on the first row and a ``down`` on the last are NO-OPS, not errors. A WRITE, so it
    writes its own audit entry.

    ``expected_name`` is the row's LOGICAL name the console posts beside its index: a mismatch against
    the FRESH snapshot refuses the move with :data:`MISSION_LIST_CHANGED`. Empty → no such check.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = getattr(server, "display_name", None) or server.name
    if _mission_list_online(server):
        return MissionListResult(success=False, message=MISSION_LIST_OFFLINE_ONLY,
                                 server_name=server_name)
    try:
        current = [os.path.normpath(x) for x in await server.getMissionList()]
    except Exception as ex:
        log.exception("reorder_mission failed")
        return MissionListResult(success=False, message=f"Failed to list missions: {ex}",
                                 server_name=server_name)
    index = _resolve_mission_id(current, mission)
    if index is None:
        return MissionListResult(success=False,
                                 message=f"Mission '{mission}' not found on server '{display}'.",
                                 server_name=server_name, missions=current)
    # STALE-PAGE GUARD: the console posts the row's LOGICAL name beside its index, so a row whose list
    # changed between render and POST is refused — the write never moves the wrong mission.
    if expected_name:
        reason = await _stale_mission_refusal(server, current[index - 1], expected_name)
        if reason:
            return MissionListResult(success=False, message=reason, server_name=server_name,
                                     missions=current)
    move = str(direction or "").strip().lower()
    if move not in ("up", "down"):
        return MissionListResult(success=False,
                                 message="A move direction of 'up' or 'down' is required.",
                                 server_name=server_name, missions=current)
    name = os.path.basename(_logical_mission(current[index - 1]))
    target = index - 1 if move == "up" else index + 1
    if target < 1 or target > len(current):
        word = "first" if move == "up" else "last"
        return MissionListResult(success=True,
                                 message=f"Mission '{name}' is already {word} in the list.",
                                 server_name=server_name, missions=current,
                                 data={"missions": current, "index": index})
    reordered = list(current)
    reordered.insert(target - 1, reordered.pop(index - 1))
    try:
        result_list = await server.setMissionList(reordered)
    except AttributeError as ex:
        return MissionListResult(success=False, message=str(ex), server_name=server_name,
                                 missions=current)
    except (TimeoutError, asyncio.TimeoutError):
        return MissionListResult(success=False,
                                 message="Timeout while moving the mission.\n"
                                         "Please reconfirm that the list is in the order you wanted.",
                                 server_name=server_name)
    except Exception as ex:
        log.exception("reorder_mission failed")
        return MissionListResult(success=False,
                                 message=f"Failed to move mission '{name}': {ex}",
                                 server_name=server_name)
    return MissionListResult(
        success=True, message=f"Moved '{name}' {move}.", server_name=server_name,
        missions=result_list, data={"missions": result_list, "index": target})

