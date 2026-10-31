"""
Action functions for mission control operations.

Each action is transport-agnostic. It takes typed parameters + an ActionContext,
performs the operation, and returns a result dataclass that Discord/REST/MCP
wrap into their response formats.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, TypeVar, cast

from core import (Coalition, MizFile, Status, UnsupportedMizFileException, UploadStatus, utils,
                  get_translation)
from core.actions import (
    TRANSPORT_DISCORD,
    TRANSPORT_SERVICE,
    TRANSPORT_WEB,
    action,
    audit_action,
)
from core.action_results import (
    ActionResult,
    MissionControlResult,
    MissionDownloadResult,
    MissionListResult,
    MissionLoadResult,
    MissionUploadResult,
    PlayerActionResult,
    PlayerScreenshotResult,
    ServerControlResult,
)

log = logging.getLogger(__name__)

#: The mission catalog's translator — the SAME domain the plugin's commands use, so the sentences
#: this module composes for the console are translated where the bot's own are. It resolves the
#: language at CALL time (``core.translations``), never at import.
_ = get_translation(__name__.split('.')[1])


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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not stopped.",
            server_name=server_name,
        )
    try:
        await server.start()
        return ServerControlResult(
            success=True,
            message=f"Server '{_server_label(server, server_name)}' started.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while starting server '{_server_label(server, server_name)}'.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not running or paused.",
            server_name=server_name,
        )
    try:
        await server.stop()
        return ServerControlResult(
            success=True,
            message=f"Server '{_server_label(server, server_name)}' stopped.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while stopping server '{_server_label(server, server_name)}'.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}.",
            server_name=server_name,
        )
    try:
        if server.status in (Status.RUNNING, Status.PAUSED):
            await server.stop()
        await server.start()
        return ServerControlResult(
            success=True,
            message=f"Server '{_server_label(server, server_name)}' restarted.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while restarting server '{_server_label(server, server_name)}'.",
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


#: The sentence a launch that states a weather pick AND "do not run the extensions" is refused with.
#: A weather pick is a REQUEST TO APPLY, and only a launch that runs the extensions applies one; on a
#: ``modify_mission=False`` launch the node's ``preset`` means "this was already applied, record it"
#: (the scheduler's statement). A pick must therefore never travel into a launch that will not apply
#: it, or the node's record — and the label the server page renders off it — would claim weather no
#: launch produced. The launch is refused as a whole rather than run with the pick dropped: the
#: operator asked for two things that contradict each other, and silently honouring one of them is
#: how the false label was produced.
WEATHER_NEEDS_EXTENSIONS = (
    "Not done (a weather preset was picked and the extensions are off, so nothing would apply it — "
    "tick \"Run extensions on the mission\", or clear the weather pick)."
)


def states_unapplied_weather(modify_mission: bool | None, preset: Any) -> bool:
    """Whether this launch states a weather pick that its own extensions will not apply.

    The ONE predicate for the condition: the action guards on it (the backstop for direct / REST / MCP
    callers) and the console's launch routes reach it through this public name rather than re-stating
    the boolean — a second copy is exactly the drift this refusal exists to stop.
    """
    return not modify_mission and bool(preset)


@action
async def startup_server(ctx: Any, server_name: str, modify_mission: bool = True,
                         use_orig: bool = True, maintenance: bool = True,
                         preset: str | dict | list | None = "",
                         preset_name: str | None = None,
                         announce: bool = False) -> ServerControlResult:
    """Start up a SHUTDOWN DCS server (the DCS-level Startup; process-level Start is another action).

    ``modify_mission`` / ``use_orig`` mirror the arguments ``Server.startup`` takes and the Discord
    command passes (``plugins/scheduler/commands.py``), and both are REPRODUCED here: the values reach
    ``Server.startup`` unchanged, so a startup from the console runs the extensions exactly as
    ``/server startup`` does. The console's Startup dialog declares the two as its own checkboxes
    (``services/webservice/pages/actions.py``), so the operator makes the choice rather than the
    route deciding it.

    ``preset`` is the launch dialog's weather pick, and there are TWO shapes of it because the pick
    offers two ways to make ONE choice: a preset NAME (``str``) the NODE resolves against its own
    preset file (the node, not the master, owns that file), or a temporary preset DOCUMENT
    (``dict``/``list``) the console built from the dialog's weather fields for this launch alone.
    ``""`` — the dialog's empty option with no field filled — is passed on as ``None``, so a launch
    nobody picked a preset for behaves exactly as before. A name the instance does not carry is
    logged and ignored by the extension, as it always was; a document is applied as it stands and
    writes nothing to the node's preset file.

    ``preset_name`` names a preset whose ``preset`` VALUE is a DOCUMENT — a preset read out of a preset
    file carries the change itself and no name in it, so the name travels beside it and the NODE's
    record takes it (``launched_preset``) instead of recording a temporary preset. ``None`` (every
    caller that states a name or no pick at all) derives the record from ``preset`` exactly as before.

    The pick travels ONLY into a launch that applies it: with ``modify_mission`` off the extensions
    do not run, so the launch is REFUSED (:data:`WEATHER_NEEDS_EXTENSIONS`) instead of launching
    with the weather silently dropped. On that path the node's ``preset`` means "already applied,
    record it" (what the scheduler states), and a console pick sent there would be recorded as
    weather no launch produced.

    ``announce`` rides with the weather pick (the dialog's own ``announce`` box, OFF by default) and is
    handed to ``Server.startup`` unchanged: the NODE tells the players, and only when it really applied
    a preset. A launch that requests a NAME the instance does not carry announces nothing.

    ``maintenance`` mirrors ``/server startup``'s own default: a TRUE value
    CLEARS the maintenance flag on a successful start, so the server rejoins the rotation — a server
    that stays flagged after an explicit startup would be skipped by the scheduler while looking up.
    Pass ``False`` to leave the flag exactly as it is. A flag that could not be cleared is reported
    in the message instead of being claimed. The ``delay`` warning popups of that command are still
    NOT reproduced here — see the W-R3 marker above.
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not shut down.",
            server_name=server_name,
        )
    if states_unapplied_weather(modify_mission, preset):
        return ServerControlResult(
            success=False,
            message=WEATHER_NEEDS_EXTENSIONS,
            server_name=server_name,
        )
    # The pick travels ONLY into a launch that applies it: with the extensions off the node treats
    # ``preset`` as "already applied, record it", so a pick sent there would be recorded as weather
    # no extension produced. ``None`` is what such a launch states — it clears the record.
    weather_pick = (preset or None) if modify_mission else None
    # ``announce`` travels only when it is asked for: a launch nobody ticked the box on calls
    # ``Server.startup`` exactly as it did before the box existed.
    startup_args: dict[str, Any] = dict(modify_mission=modify_mission, use_orig=use_orig,
                                        preset=weather_pick)
    # ``preset_name`` names a DOCUMENT, so it travels only beside a pick the launch really carries —
    # and only when it is stated, so a launch with no name is the same call as before the keyword.
    if preset_name is not None and weather_pick is not None:
        startup_args["preset_name"] = preset_name
    if announce:
        startup_args["announce"] = True
    try:
        await server.startup(**startup_args)
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while starting up server '{_server_label(server, server_name)}'.",
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
                message=(f"Server '{_server_label(server, server_name)}' started up, but the maintenance flag "
                         f"could not be cleared: {failure}"),
                server_name=server_name,
            )
    label = _server_label(server, server_name)
    # THE MESSAGE IS THE AUDIT TEXT (``call_action`` -> ``audit_action``), so naming the preset here
    # is the whole trail — there is no second record to write. The console cannot know whether the
    # NODE really applied a NAME (one the instance does not carry is ignored there), so a name is
    # reported as the launch's REQUEST, never as an applied fact; the record the node writes is the
    # truth, and the server page renders it. A temporary preset document is applied as it stands, so
    # it is named as applied; a launch nobody picked a preset for keeps the sentence it always had.
    if preset_name and weather_pick:
        message = f"Server '{label}' started up — weather preset '{preset_name}' requested."
    elif isinstance(weather_pick, str) and weather_pick:
        message = f"Server '{label}' started up — weather preset '{weather_pick}' requested."
    elif weather_pick:
        message = f"Server '{label}' started up with a temporary weather preset."
    else:
        message = f"Server '{label}' started up."
    return ServerControlResult(
        success=True,
        message=message,
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not running or paused.",
            server_name=server_name,
        )
    try:
        await server.shutdown(force=force)
    except (TimeoutError, asyncio.TimeoutError):
        return ServerControlResult(
            success=False,
            message=f"Timeout while shutting down server '{_server_label(server, server_name)}'.",
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
            message=(f"Server '{_server_label(server, server_name)}' shut down, but the maintenance flag could "
                     f"not be {verb}: {failure}"),
            server_name=server_name,
        )
    return ServerControlResult(
        success=True,
        message=f"Server '{_server_label(server, server_name)}' shut down.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not running.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    try:
        await server.current_mission.pause()
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{_server_label(server, server_name)}' paused.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}, not paused.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    try:
        await server.current_mission.unpause()
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{_server_label(server, server_name)}' unpaused.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}.",
            server_name=server_name,
        )
    if server.current_mission is None:
        return MissionControlResult(
            success=False,
            message=f"No active mission on server '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    try:
        if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
            await server.stop()
        await server.restart(modify_mission=modify_mission)
        return MissionControlResult(
            success=True,
            message=f"Mission on server '{_server_label(server, server_name)}' restarted.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return MissionControlResult(
            success=False,
            message=f"Timeout while restarting mission on '{_server_label(server, server_name)}'.",
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}.",
            server_name=server_name,
        )
    try:
        if not server.locals.get("mission_rewrite", True) and server.status != Status.STOPPED:
            await server.stop()
        await server.loadNextMission(modify_mission=modify_mission, use_orig=use_orig)
        return MissionControlResult(
            success=True,
            message=f"Mission rotated on server '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return MissionControlResult(
            success=False,
            message=f"Timeout while rotating mission on '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    except Exception as ex:
        log.exception("rotate_mission failed")
        return MissionControlResult(
            success=False,
            message=f"Failed to rotate mission: {ex}",
            server_name=server_name,
        )


def _loaded_message(mission_name: str, verb: str, preset, preset_name: str | None = None) -> str:
    """The load action's result sentence — and, because the result IS the audit text, the whole trail.

    A weather preset picked on the Load dialog is NAMED here, exactly as ``startup_server`` names one.
    A preset NAME is reported as the load's REQUEST, never as an applied fact: the console cannot know
    whether the NODE really applied it (a name the instance does not carry is logged and ignored
    there). A preset DOCUMENT built on the spot (``preset_name`` absent) is applied as it stands, so
    it is named as applied; a document read out of a preset file is NAMED by the key it came from,
    which ``preset_name`` carries. No pick keeps the sentence the load has always had.
    """
    if preset_name and preset:
        return f"Mission '{mission_name}' {verb} — weather preset '{preset_name}' requested."
    if isinstance(preset, str) and preset:
        return f"Mission '{mission_name}' {verb} — weather preset '{preset}' requested."
    if preset:
        return f"Mission '{mission_name}' {verb} with a temporary weather preset."
    return f"Mission '{mission_name}' {verb}."


@action
async def load_mission(
    ctx: Any,
    server_name: str,
    mission: str | int,
    modify_mission: bool = True,
    use_orig: bool = True,
    expected_name: str = "",
    preset: str | dict | list | None = "",
    preset_name: str | None = None,
    announce: bool = False,
) -> MissionLoadResult:
    """Load a specific mission on a DCS server.

    ``preset`` / ``announce`` are the LAUNCH FORM the console's Load dialog carries — the SAME
    weather pick and announce box the Startup dialog declares (``services/webservice/pages/
    actions.py``). ``preset`` has TWO shapes because the pick offers two ways to make ONE choice: a
    preset NAME (``str``) the NODE resolves against its own preset file, or a temporary preset
    DOCUMENT (``dict``/``list``) the console built for this load alone; ``""`` means no pick and
    behaves exactly as before. ``announce`` is the dialog's own box (``False`` by default) and the
    NODE tells the players, and only when it really applied a preset. Both are threaded through BOTH
    of the action's load paths — ``loadMission`` and the ``restart`` branch for the mission that is
    ALREADY running — so a preset picked on Load is never silently dropped.

    ``preset_name`` names a preset whose ``preset`` VALUE is a DOCUMENT: the name travels beside the
    document so the NODE's record takes the name instead of recording a temporary preset, and it is
    threaded through BOTH branches for the same reason ``preset`` is.

    The pick travels ONLY into a load that applies it: with ``modify_mission`` off the extensions do
    not run, so the load is REFUSED (:data:`WEATHER_NEEDS_EXTENSIONS`) instead of loading with the
    weather silently dropped. On that path the node's ``preset`` means "already applied, record it"
    (what the scheduler states), and a console pick sent there would be recorded as weather no load
    produced.
    """
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
            message=f"Server '{_server_label(server, server_name)}' is {server.status.value}.",
            server_name=server_name,
        )
    if states_unapplied_weather(modify_mission, preset):
        return MissionLoadResult(
            success=False,
            message=WEATHER_NEEDS_EXTENSIONS,
            server_name=server_name,
        )
    # The pick travels ONLY into a load that applies it: with the extensions off the node treats
    # ``preset`` as "already applied, record it", so a pick sent there would be recorded as weather
    # no extension produced. ``None`` is what such a load states — it clears the record.
    weather_pick = (preset or None) if modify_mission else None
    weather_name = preset_name if weather_pick is not None else None

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
            # THE ALREADY-RUNNING MISSION: the load is a RESTART. ``preset`` / ``preset_name`` /
            # ``announce`` are threaded through ``restart`` too (``core/data``), so a weather pick
            # made for the mission that is already running is APPLIED by the same path — never
            # silently dropped — and its NAME reaches the record on that branch as well.
            await server.restart(modify_mission=modify_mission, preset=weather_pick,
                                 preset_name=weather_name, announce=announce)
            return MissionLoadResult(
                success=True,
                message=_loaded_message(mission_name, "restarted", weather_pick, weather_name),
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
            mission_file, modify_mission=modify_mission, use_orig=use_orig,
            preset=weather_pick, preset_name=weather_name, announce=announce
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
            message=_loaded_message(mission_name, "loaded", weather_pick),
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
    display = _server_label(server, server_name)
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
            success=False,
            message=f"Player not found on server '{_server_label(server, server_name)}'.",
            ucid=ucid), server)

    name = _player_label(player, ucid)
    label = _server_label(server, server_name)
    try:
        if mode == "chat":
            await player.sendChatMessage(body, sender)
            sent = f"Chat message sent to {name} on server '{label}'."
        else:
            # ``timeout`` stays at the domain method's own default (-1 = the server's configured
            # ``message_timeout``): the Discord commands pass an explicit duration for popups, and
            # the console's control has no field for one, so the default is the honest choice.
            await player.sendPopupMessage(body, sender=sender)
            sent = f"Popup sent to {name} on server '{label}'."
        result = PlayerActionResult(success=True, message=sent, player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(
            success=False,
            message=f"Timeout while messaging {name} on server '{label}'.",
            player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("message_player failed")
        result = PlayerActionResult(success=False, message=f"Failed to send the message: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


#: How long the console waits for the screenshot it asked the server to take, in seconds — the
#: numbers the Discord command uses. DCS writes the capture itself, so the file appears a moment
#: after the request; a node flagged ``slow_system`` is given three times the wait.
SCREENSHOT_TIMEOUT = 10
SCREENSHOT_TIMEOUT_SLOW = 30

#: How often the wait above looks for the new file, in seconds. One constant, because the cadence is
#: part of the behaviour (a capture appears within a second or two, and polling faster than DCS writes
#: only burns calls on the node).
SCREENSHOT_POLL_SECONDS = 1

#: The settings path that decides whether a server may be asked for a screenshot at all
#: (``servers.yaml``: ``advanced.server_can_screenshot``). The server-side capture is a DCS feature
#: of its own, so a server without it cannot answer — a refusal, not a failure.
SCREENSHOT_SETTING = "advanced.server_can_screenshot"


@action
async def screenshot_player(ctx: Any, server_name: str, ucid: str) -> PlayerScreenshotResult:
    """Take a screenshot of ONE player's screen, on a RUNNING server that allows it.

    Mirrors the Discord command that does the same thing (``plugins/mission/commands.py``): the same
    ``Player.makeScreenshot`` call, the same "wait for a NEW file to appear" loop and the same
    ``SCREENSHOT_SETTING`` gate — with the console's TYPED refusals instead of a followup message.

    WHICH FILE IS NEW is decided by comparing the server's screenshot list before and after the
    request, never by taking the newest name: the server's list is written by DCS on its own
    schedule, so a name alone cannot tell a fresh capture from one that was already there.

    The KEY (not the image) comes back in the result — see
    :class:`~core.action_results.PlayerScreenshotResult`. Reading the bytes is the transport's own
    step, because the file is served by the SERVER's web GUI, not by this action's process.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, PlayerScreenshotResult(
            success=False, message=f"Server '{server_name}' not found.", ucid=ucid))

    label = _server_label(server, server_name)
    if server.status != Status.RUNNING:
        return await _audited(ctx, PlayerScreenshotResult(
            success=False,
            message=f"Server '{label}' is {server.status.value}, not running — a screenshot needs "
                    f"the mission up.",
            ucid=ucid), server)

    if not server.settings.get('advanced', {}).get('server_can_screenshot', False):
        return await _audited(ctx, PlayerScreenshotResult(
            success=False,
            message=f"Server '{label}' can not take screenshots (advanced.server_can_screenshot).",
            ucid=ucid), server)

    player = server.get_player(ucid=ucid, active=True)
    if player is None:
        return await _audited(ctx, PlayerScreenshotResult(
            success=False, message=f"Player not found on server '{label}'.", ucid=ucid), server)

    name = _player_label(player, ucid)
    try:
        before = await player.getScreenshots()
        await player.makeScreenshot()
        wait = (SCREENSHOT_TIMEOUT_SLOW if server.node.locals.get('slow_system', False)
                else SCREENSHOT_TIMEOUT)
        after: list[str] = []
        for _ in range(wait):
            await asyncio.sleep(SCREENSHOT_POLL_SECONDS)
            after = await player.getScreenshots()
            if len(after) > len(before):
                break
        if len(after) <= len(before):
            result = PlayerScreenshotResult(
                success=False,
                message=f"Timeout while waiting for the screenshot of {name} on server '{label}'.",
                player_name=name, ucid=ucid)
        else:
            key = after[-1]
            # DCS 2.9.11+ answers with the file's name only; older servers answer with a path.
            if 'screenshots' not in key:
                key = '/screenshots/' + key
            result = PlayerScreenshotResult(
                success=True, message=f"Screenshot of {name} on server '{label}' taken.",
                player_name=name, ucid=ucid, screenshot=key)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerScreenshotResult(
            success=False,
            message=f"Timeout while taking the screenshot of {name} on server '{label}'.",
            player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("screenshot_player failed")
        result = PlayerScreenshotResult(success=False, message=f"Failed to take the screenshot: {ex}",
                                        player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


_R = TypeVar("_R", bound=ActionResult)


async def _audited(ctx: Any, result: _R, server: Any = None) -> _R:
    """Write the trail for *result* and return it (design §5.3 D1).

    ONE place, so every exit of :func:`message_player` and :func:`message_server` — the successes,
    the typed refusals and the failed sends — leaves exactly one entry, and none of them can forget
    the one mechanism.
    """
    await audit_action(ctx, result, server=server)
    return result


# ── Server-wide messages ────────────────────────────────────────────────────
#
# The SERVER twin of ``message_player``: ONE message to everybody on a server, or to one coalition
# of it. WHICH recipients a mode can reach is a DOMAIN fact, not a console choice:
#
# * ``popup`` -> ``Server.sendPopupMessage``, which hands the coalition on to DCS
#   (``to: 'coalition', id: <value>``), so ``all``, ``red`` and ``blue`` all arrive;
# * ``chat`` -> ``Server.sendChatMessage``, which sends ``Coalition.ALL`` as one server-wide line and
#   FANS OUT every other coalition to the active players of that side — one TARGETED line per player
#   (``net.send_chat_to``, the primitive ``Player.sendChatMessage`` posts). DCS has no
#   coalition-scoped chat broadcast, so the fan-out is what delivers the choice; both modes therefore
#   reach the same three recipients.

#: The recipients each mode can reach: the allow-list this action holds, mirrored by the console's
#: dialogs. Both modes carry the same three - see the section note above.
SERVER_MESSAGE_RECIPIENTS: dict[str, tuple[str, ...]] = {
    "chat": ("all", "red", "blue"),
    "popup": ("all", "red", "blue"),
}

#: The ``Coalition`` each recipient word names.
SERVER_MESSAGE_COALITIONS: dict[str, Coalition] = {
    "all": Coalition.ALL,
    "red": Coalition.RED,
    "blue": Coalition.BLUE,
}

#: How each recipient is NAMED in a result's sentence.
SERVER_MESSAGE_LABELS: dict[str, str] = {
    "all": "everyone",
    "red": "the Red coalition",
    "blue": "the Blue coalition",
}


def _recipient_choices(allowed: tuple[str, ...]) -> str:
    """The recipient allow-list as a sentence fragment: ``'all', 'red' or 'blue'``."""
    quoted = [f"'{word}'" for word in allowed]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + f" or {quoted[-1]}"


@action
async def message_server(ctx: Any, server_name: str, message: str, mode: str = "popup",
                         coalition: str = "all", sender: str | None = None) -> ServerControlResult:
    """Send a message to EVERYBODY on a server, or to one of its coalitions.

    Mirrors :func:`message_player` one level up — the same domain calls, the same mode split
    (``popup`` default, ``chat``), the same length limit — addressed to the whole server instead of
    one player. ``coalition`` picks the audience: ``all``, ``red`` or ``blue``.

    A message that is empty or only whitespace is refused WITHOUT calling the domain method, exactly
    as the player twin does. ``coalition`` is validated against the mode's OWN allow-list
    (:data:`SERVER_MESSAGE_RECIPIENTS`), so a crafted body cannot address a message to something the
    mode cannot deliver; every recipient of that list is deliverable on any server whose process can
    take the message (the popup addresses the coalition in DCS, the chat line fans out per player of
    the side), so no server-side configuration is required to pick one.

    ``sender`` is the label the message appears to come from; ``None`` sends it anonymous, exactly as
    the domain methods' own default does.

    The trail is written HERE, once, whatever the transport — including for a REFUSAL, because one
    entry per attempt is what an operator wants after an incident.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Server '{server_name}' not found.", server_name=server_name))

    body = (message or "").strip()
    if not body:
        return await _audited(ctx, ServerControlResult(
            success=False, message="Message must not be empty.", server_name=server_name), server)
    if len(body) > MAX_MESSAGE_LENGTH:
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Message is longer than {MAX_MESSAGE_LENGTH} characters.",
            server_name=server_name), server)

    mode = (mode or "").strip().lower() or MESSAGE_MODES[0]
    if mode not in MESSAGE_MODES:
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Unknown message mode '{mode}' (use 'popup' or 'chat').",
            server_name=server_name), server)

    recipient = (coalition or "").strip().lower() or "all"
    allowed = SERVER_MESSAGE_RECIPIENTS[mode]
    if recipient not in allowed:
        return await _audited(ctx, ServerControlResult(
            success=False,
            message=f"Unknown recipient '{recipient}' (use {_recipient_choices(allowed)}).",
            server_name=server_name), server)

    name = _server_label(server, server_name)
    label = SERVER_MESSAGE_LABELS[recipient]

    target = SERVER_MESSAGE_COALITIONS[recipient]
    try:
        if mode == "chat":
            await server.sendChatMessage(target, body, sender)
            sent = f"Chat message sent to {label} on server '{name}'."
        else:
            # ``timeout`` stays at the domain method's own default (-1 = the server's configured
            # ``message_timeout``): the console's control has no field for one.
            await server.sendPopupMessage(target, body, sender=sender)
            sent = f"Popup sent to {label} on server '{name}'."
        result = ServerControlResult(success=True, message=sent, server_name=server_name)
    except (TimeoutError, asyncio.TimeoutError):
        result = ServerControlResult(
            success=False, message=f"Timeout while messaging {label} on server '{name}'.",
            server_name=server_name)
    except Exception as ex:
        log.exception("message_server failed")
        result = ServerControlResult(success=False, message=f"Failed to send the message: {ex}",
                                     server_name=server_name)
    return await _audited(ctx, result, server)


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

#: The EXACT text layout the EDIT dialog's absolute expiration states: a UTC wall-clock stamp, with
#: an optional trailing suffix the parser strips — EXACTLY ONE of `` UTC`` / ``Z``. NAIVE — the value
#: is UTC by convention. The SECONDS are part of the layout: a days-created ban stores them, so a
#: reason-only save that posts the entry's own end back writes the IDENTICAL instant instead of
#: truncating to the minute. It is the LONGEST accepted shape — :func:`parse_ban_until` also reads the
#: shorter ones below.
BAN_UNTIL_FORMAT = "%Y-%m-%d %H:%M:%S"

#: The accepted expiration shapes, as ONE strict pattern: the date, then optionally the hour, then
#: optionally the minute, then optionally the second — every field ZERO-PADDED and separated by a
#: single space. A shape the field does not spell out (``2029-3-4``, a tab, a double space) is a
#: typed refusal, never a lenient ``strptime`` read. The time parts are optional: an hour shape
#: zeroes the minutes AND seconds, and the date alone takes the time of day from the reference.
_UNTIL_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?: (?P<hour>\d{2})(?::(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?)?$"
)

#: The ``banned_until`` a ban with no expiry carries — the SAME year-9999 sentinel ``ServiceBus.ban``
#: writes for ``days=None``, so a blank expiration and an empty day count mean the identical value.
BAN_PERMANENT_UNTIL = datetime(year=9999, month=12, day=31)


def _utc_naive(moment: datetime) -> datetime:
    """*moment* as a NAIVE UTC datetime — an aware value is converted, a naive one is read as UTC."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_until_stamp(text: str, reference: datetime) -> datetime | None:
    """The UTC instant *text* states, filling the missing parts from *reference*, or ``None``.

    :data:`_UNTIL_PATTERN` must match *text* WHOLE; a shorter shape leaves the fields it does not
    carry at their zero (``HH`` -> ``:00:00``, ``HH:MM`` -> ``:00``), and the date-only shape takes
    the time of day from *reference* ("ban them until that date, same time"). A text that matches no
    shape — a bad month/day/hour/minute/second, a bare ``YYYY-MM``, trailing junk, a non-padded field
    — answers ``None``, which the caller turns into a typed refusal.
    """
    match = _UNTIL_PATTERN.match(text)
    if match is None:
        return None
    parts = match.groupdict()
    if parts["hour"] is None:
        hour, minute, second = reference.hour, reference.minute, reference.second
    else:
        hour = int(parts["hour"])
        minute = int(parts["minute"]) if parts["minute"] is not None else 0
        second = int(parts["second"]) if parts["second"] is not None else 0
    try:
        return datetime(int(parts["year"]), int(parts["month"]), int(parts["day"]),
                        hour, minute, second)
    except ValueError:
        return None


def parse_ban_until(value: Any, now: datetime | None = None) -> tuple[datetime | None, str]:
    """The absolute UTC expiration the EDIT form states: ``(until, refusal)``.

    The mirror of :func:`parse_ban_days` for an ABSOLUTE end, so a reason-only edit writes the ban's
    own instant back instead of re-deriving it from a day count (which would drift). The answers:

    * empty / absent / whitespace -> the PERMANENT sentinel (:data:`BAN_PERMANENT_UNTIL`), the same
      value ``days=None`` writes;
    * a UTC date/time matching :data:`_UNTIL_PATTERN` — EXACTLY ONE trailing suffix token, either a
      bare ``Z`` or ``UTC`` with at most one space before it, is accepted and stripped; the remainder
      after that single peel must match :data:`_UNTIL_PATTERN` WHOLE, so a doubled suffix (``…ZUTC``,
      ``…Z UTC``, ``…ZZ``, ``…UTC UTC``) is refused. Read as NAIVE UTC. The LENIENT shapes fill their
      missing parts: the seconds, then the minutes, then the whole time of day (a date alone takes the
      wall-clock time of *now* on that date);
    * a stamp at or before *now* is a TYPED refusal: an expiration in the past IS a lift, and the row
      carries its own Lift control, so the edit will not expire a ban by accident;
    * anything else — a text whose remainder does not match :data:`_UNTIL_PATTERN` whole — is a TYPED
      refusal with nothing written.

    The current time is INJECTED through *now* (a naive or aware datetime; absent means the current
    UTC time), so the date-only fill and the past check read ONE instant and a test can pin both.
    """
    raw = str(value if value is not None else "").strip()
    if not raw:
        return BAN_PERMANENT_UNTIL, ""
    text = raw
    if text.endswith(" UTC"):
        text = text[:-4]
    elif text.endswith("UTC"):
        text = text[:-3]
    elif text.endswith("Z"):
        text = text[:-1]
    reference = (_utc_naive(now) if now is not None
                 else datetime.now(tz=timezone.utc).replace(tzinfo=None))
    parsed = _parse_until_stamp(text, reference)
    if parsed is None:
        return None, _("Expires must be a UTC date/time such as YYYY-MM-DD HH:MM:SS — leave it "
                       "empty for a permanent ban.")
    if parsed <= reference:
        return None, _("Expires must be in the future — a past date would lift the ban. Leave it "
                       "empty for a permanent ban.")
    return parsed, ""


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
    """The name a player write's copy uses — the object's own PLAIN name, never anything the browser sent.

    The domain carries the name twice: ``name`` is the plain string and ``display_name`` is that same
    string ESCAPED for Discord's markdown (``core/data/player.py``: ``utils.escape_string``), so
    ``[TAG] Test Pilot`` reads back as ``\\[TAG\\] Test Pilot``. The sentence built from this label is
    read in the console (:class:`~core.action_results.PlayerActionResult`'s message is what its notice
    renders) and the Discord commands compose their own wording with ``display_name``, so the plain
    attribute comes FIRST; a display name stays the last candidate before the ucid, for a double that
    carries nothing else.
    """
    return getattr(player, "name", "") or getattr(player, "display_name", None) or ucid


def _server_label(server: Any, fallback: str) -> str:
    """The server name these sentences use — the plain name, read the way :func:`_player_label` does.

    ``Server.display_name`` is likewise the name escaped for Discord's markdown (``core/data/server.py``:
    ``SRS-1`` reads back as ``SRS\\-1``), and every sentence built here is read in the console. The
    caller's own ``server_name`` is the last resort.
    """
    return getattr(server, "name", "") or getattr(server, "display_name", None) or fallback


def _mission_label(mission: Any, fallback: str) -> str:
    """The mission name these sentences use — the plain name, read the way :func:`_server_label` does.

    ``Mission.display_name`` is the mission name escaped for Discord's markdown
    (``core/data/mission.py``: ``utils.escape_string``), so ``[TAG]_Test*Pilot`` reads back as
    ``\\[TAG\\]\\_Test\\*Pilot``; the console renders it into a browser. The caller's own last resort
    — the mission's filename — is used when the double carries neither name.
    """
    return getattr(mission, "name", "") or getattr(mission, "display_name", None) or fallback


def _caller_ban_targets(ctx: Any) -> list[str]:
    """The servers a ban issued through *ctx* covers.

    Every server in the caller's view whose ``managed_by`` names one of the caller's roles; an empty
    list means the caller is unscoped and the ban is bot-wide.
    """
    scope = getattr(ctx, "_scope", None)
    if scope is None or getattr(scope, "unrestricted", False):
        return []
    names: list[str] = []
    for name, server in ctx.servers.items():
        try:
            managed_by = server.locals.get("managed_by")
        except AttributeError:
            managed_by = None
        if not managed_by:
            continue
        try:
            if scope.allows(managed_by):
                names.append(name)
        except Exception:
            log.exception("ban scope: the scope could not be applied to '%s', skipping it.", name)
    return names


def _caller_ban_unrestricted(ctx: Any) -> bool:
    """Whether *ctx*'s caller is UNSCOPED — it reaches every server in its view.

    The scope's own ``unrestricted`` flag, read the same way :func:`_caller_ban_targets` reads it
    (``None`` is an unscoped context too). This is the ONE predicate that tells an unscoped caller
    apart from a narrowed one whose own target set happens to be empty: the two share an empty set,
    but only the unscoped caller holds a licence to name any server.
    """
    scope = getattr(ctx, "_scope", None)
    return scope is None or bool(getattr(scope, "unrestricted", False))


def _caller_ban_servers(ctx: Any) -> list[str]:
    """The servers a ban issued through *ctx* may NAME as its one-server scope.

    An UNSCOPED caller may name any server in its view (``ctx.servers``, already the caller's own
    view — the console hands the seam its scoped cluster); a narrowed caller may name only the
    servers of its own target set (:func:`_caller_ban_targets`). It is what the ADD dialog's picker
    offers AND what the action re-checks, so the two cannot disagree about which name is allowed.
    """
    if _caller_ban_unrestricted(ctx):
        return [name for name in ctx.servers]
    return _caller_ban_targets(ctx)


def _ban_scope_label(targets: list[str]) -> str:
    """The human-readable scope of a ban the ``server_name`` rows of *targets* cover."""
    return "all servers" if not targets else ", ".join(targets)


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
            success=False,
            message=f"Player not found on server '{_server_label(server, server_name)}'.",
            ucid=ucid), server)

    name = _player_label(player, ucid)
    label = _server_label(server, server_name)
    try:
        await server.kick(player, body)
        result = PlayerActionResult(
            success=True,
            message=f"Player {name} (ucid={ucid}) kicked from server '{label}'.",
            player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(
            success=False,
            message=f"Timeout while kicking {name} from server '{label}'.",
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

    The rows written are the CALLER's own scope — every server in the caller's view whose
    ``managed_by`` names one of the caller's roles, one row each, or a single bot-wide row when the
    caller is unscoped or manages no tagged server. A narrowed caller banning an online player on a
    server OUTSIDE their set therefore writes the rows on their OWN servers, so the ban does not land
    where that player is.
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
            success=False,
            message=f"Player not found on server '{_server_label(server, server_name)}'.",
            ucid=ucid), server)

    service = _ban_service(ctx)
    if service is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message="The ban list is not available in this installation.", ucid=ucid),
            server)

    name = _player_label(player, ucid)
    banned_by = (sender or "").strip() or ctx.audit_actor
    targets = _caller_ban_targets(ctx)
    scope = _ban_scope_label(targets)
    stamp = datetime.now(tz=timezone.utc)
    try:
        for target in targets or [None]:
            await service.ban(ucid, banned_by, body, period, server_name=target, banned_at=stamp)
        result = PlayerActionResult(
            success=True,
            message=(f"Player {name} (ucid={ucid}) banned for {period} days on {scope}." if period
                     else f"Player {name} (ucid={ucid}) banned permanently on {scope}."),
            player_name=name, ucid=ucid)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(success=False, message=f"Timeout while banning {name}.",
                                    player_name=name, ucid=ucid)
    except Exception as ex:
        log.exception("ban_player failed")
        result = PlayerActionResult(success=False, message=f"Failed to ban the player: {ex}",
                                    player_name=name, ucid=ucid)
    return await _audited(ctx, result, server)


# ── The ban list: the console's scoped READ, the console's ADD and the LIFT ──
#
# All three are CONSOLE-TRANSPORT ONLY. Their whole scope rule is ``ctx._scope`` (through
# :func:`_caller_ban_targets`), and a transport that carries no scope — REST, MCP — would read as
# GLOBAL: a fleet-wide read, a fleet-wide ban and a fleet-wide unban nobody asked for. They refuse
# such a caller rather than guess, and the refusal is typed.

#: The refusal a non-console caller meets (the whole ban surface is the console's own).
BANS_CONSOLE_ONLY = _("The ban list is only available in the web console.")

#: The ``banned_by`` value the cloud plugin writes for a DGSA ban — the plugin's OWN name
#: (``plugins/cloud/commands.py`` writes ``banned_by=self.plugin_name``, and ``core/plugin.py``
#: derives that from the plugin directory name ``cloud``). A DGSA entry is NEVER editable: rewriting
#: its ``reason`` would destroy the listener's own ``reason.startswith('DGSA')`` marker, and
#: rewriting ``banned_by`` would hide the row from the cloud's own bookkeeping and let the next sync
#: pass re-add the ban. The plugin keeps its own copy of the word; a test pins the two equal.
DGSA_BANNED_BY = "cloud"


def _entry_field(entry: Any, name: str) -> Any:
    """One field of a ban ENTRY, whether the bus handed over a mapping or a record."""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _entry_stamp(entry: Any) -> str:
    """The ISO identity of a ban ENTRY's own timestamp — the key half an edit posts back."""
    stamp = _entry_field(entry, "banned_at")
    return stamp.isoformat() if isinstance(stamp, datetime) else ""


def _entry_servers(entry: Any) -> list[str] | None:
    """The server names ONE ban entry covers — ``None`` for a bot-wide entry (no server row)."""
    raw = _entry_field(entry, "server_name")
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw] if raw else []
    return [str(name) for name in raw]


def _entry_in_targets(servers: list[str] | None, targets: list[str]) -> bool:
    """Whether EVERY row of a ban entry falls inside *targets* — an empty set means GLOBAL.

    A bot-wide entry carries no server row, so it is never inside a narrowed caller's named set; a
    global caller (empty *targets*) reaches it, the same "empty means global" the ban commands use.
    """
    if not targets:
        return True
    if not servers:
        return False
    return all(name in targets for name in servers)


@action
async def ban_ucid(ctx: Any, ucid: str, reason: str, days: str | int | None = None,
                   sender: str | None = None, scope: str | None = None) -> PlayerActionResult:
    """Add a ban BY UCID — the console's ADD, mirroring the Discord ``/dcs ban``.

    NOT :func:`ban_player`. That action resolves a server and requires the player to be ONLINE on it
    (it is ``/mission player ban``); ``/dcs ban`` and this action ban a ucid the installation may
    never have seen, with no server resolved and no player looked up.

    ``days`` is the form's own value and is parsed by :func:`parse_ban_days`: empty is a PERMANENT
    ban, ``N`` is N days, and anything else is a TYPED refusal with nothing written.

    ``scope`` is the dialog's own scope choice: the name of ONE server the caller may name, or empty
    for the caller's whole target set — one row per server in it, or a single bot-wide row
    (``server_name IS NULL``) when that set is empty. An UNSCOPED caller may name any server in its
    view (:func:`_caller_ban_servers`); a narrowed one only servers of its own set. A name outside
    what the caller may name is refused before anything is written. Every row shares ONE ``banned_at``
    stamp, so the rows group back into a single entry in the list.

    ``sender`` is who the ban list names as ``banned_by`` (the acting person's shown name);
    ``None`` falls back to the transport's own audit actor, so a ban is never attributed to nobody.
    """
    if ctx.transport != TRANSPORT_WEB:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=BANS_CONSOLE_ONLY, ucid=ucid))
    value = (ucid or "").strip()
    if not utils.is_ucid(value):
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("'{}' is not a valid UCID.").format(value), ucid=value))

    body = (reason or "").strip()
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("A reason is required to ban a player."), ucid=value))
    if len(body) > MAX_BAN_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("The reason is longer than {} characters.").format(MAX_BAN_REASON_LENGTH),
            ucid=value))

    period, refusal = parse_ban_days(days)
    if refusal:
        return await _audited(ctx, PlayerActionResult(success=False, message=refusal, ucid=value))

    service = _ban_service(ctx)
    if service is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The ban list is not available in this installation."),
            ucid=value))

    targets = _caller_ban_targets(ctx)
    chosen = (scope or "").strip()
    if chosen:
        if chosen not in _caller_ban_servers(ctx):
            return await _audited(ctx, PlayerActionResult(
                success=False, message=_("Server '{}' is outside your scope.").format(chosen),
                ucid=value))
        targets = [chosen]

    banned_by = (sender or "").strip() or ctx.audit_actor
    label = _ban_scope_label(targets)
    stamp = datetime.now(tz=timezone.utc)
    try:
        for target in targets or [None]:
            await service.ban(value, banned_by, body, period, server_name=target, banned_at=stamp)
        result = PlayerActionResult(
            success=True,
            message=(_("Player {} banned for {} days on {}.").format(value, period, label) if period
                     else _("Player {} banned permanently on {}.").format(value, label)),
            ucid=value)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(success=False, message=_("Timeout while adding the ban."),
                                    ucid=value)
    except Exception as ex:
        log.exception("ban_ucid failed")
        result = PlayerActionResult(success=False, message=_("Failed to add the ban: {}").format(ex),
                                    ucid=value)
    return await _audited(ctx, result)


@action
async def list_bans(ctx: Any) -> ActionResult:
    """The bans the caller may see, built FROM the caller's target set.

    The scope is a scoped SOURCE, never a post-hoc filter: an Admin (or any caller whose target set
    is empty — the "empty means global" rule the ban commands already use) reads every ban, while a
    ``managed_by`` holder reads exactly the rows on their managed servers. The grouping of one ban's
    rows into one entry stays in the bus.

    ``data["targets"]`` is the SET the bot-wide option writes (one row per server, or a single
    bot-wide row when the set is empty), reported beside the rows. ``data["servers"]`` is what the
    ADD dialog's scope picker offers as per-server options AND what :func:`ban_ucid` re-checks a
    chosen name against (:func:`_caller_ban_servers`): the caller's whole view for an unscoped
    caller, its own target set for a narrowed one. Both are read from the same call that built the
    rows, so the picker and the list cannot drift.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=BANS_CONSOLE_ONLY)
    service = _ban_service(ctx)
    if service is None:
        return ActionResult(success=False,
                            message=_("The ban list is not available in this installation."))
    targets = _caller_ban_targets(ctx)
    servers = _caller_ban_servers(ctx)
    try:
        entries = await service.bans(servers=tuple(targets) or None)
    except Exception as ex:
        log.exception("list_bans failed")
        return ActionResult(success=False, message=_("The ban list could not be read: {}").format(ex))
    return ActionResult(success=True, data={"bans": entries, "targets": tuple(targets),
                                            "servers": tuple(servers)})


# ── The player reads: the directory, one player, one player's statistics ──
#
# All three are CONSOLE-TRANSPORT ONLY, for the reason the ban reads are: their scope rule is
# ``ctx._scope`` (through :func:`_caller_ban_targets` / :func:`_caller_ban_unrestricted`), and a
# transport that carries no scope — REST, MCP — would read as GLOBAL, i.e. every player of the
# installation. They refuse such a caller instead of guessing, and the refusal is typed.
#
# A PLAYER IS A DISCORD LINK, NOT A UCID: ``players`` holds one row per UCID, and every UCID the
# same ``discord_id`` names is the same person (an unlinked UCID, ``discord_id = -1``, is its own).
# The rows are grouped in the SAME statement that paginates them, so "one row per player" and "50
# rows a page" cannot disagree — the total comes back with the page (``count(*) OVER ()``).
#
# SCOPE. ``players`` carries no server column, so the only reading of "a player of mine" the schema
# can express is "seen on a server of my set" — the ``statistics`` rows joined to ``missions``. That
# is the same presence bound the console's player search is held to, and it is a WHERE term, never a
# filter applied after the read. A narrowed caller whose own set is EMPTY therefore reads nothing
# (``servers = []``), which is what tells it apart from an unscoped caller (``servers = None``).

#: The refusal a non-console caller of a player read meets. Untranslated on purpose: a msgid with no
#: catalog entry would read as English anyway, and this module's catalogs are not this read's to
#: extend.
PLAYERS_CONSOLE_ONLY = "The player list is only available in the web console."

#: The refusal when this process has no connection pool to read from.
PLAYERS_UNAVAILABLE = "The player list is not available in this installation."

#: How many players one directory page holds. The console's view layer publishes the same number for
#: its pager (``readmodels.views.PLAYER_DIRECTORY_PAGE_SIZE``); a test pins the two equal rather than
#: letting one move without the other.
PLAYER_DIRECTORY_PAGE_SIZE = 50

#: How many of a player's last sessions the statistics read returns.
PLAYER_RECENT_SESSIONS = 10

#: How many of a player's most recent infraction events the infractions read returns — the report's
#: own ``limit`` input, and the same ten its Discord render draws.
PLAYER_INFRACTION_EVENTS = 10

#: The SHAPE of a UCID: 32 hexadecimal characters, which is what every player key is. Checked before
#: a UCID reaches a statement, and the only shape the console's player page accepts either.
_PLAYER_UCID_SHAPE = re.compile(r"[0-9a-f]{32}")

#: The directory table's own key in the console's view layer (``readmodels.views.PAGE_COLUMNS``).
_DIRECTORY_TABLE = "player_directory"

#: The directory's ORDER BY, keyed by the SORTABLE COLUMN KEYS of ``_DIRECTORY_TABLE``. The value is
#: the closed lookup: a ``?sort=`` that is not one of these keys never reaches the statement (the
#: read normalises it against the SAME allow-list first, and the fallback here is the default column).
_DIRECTORY_ORDER: dict[str, str] = {
    "name": "lower((g.names)[1])",
    "ucid": "lower((g.ucids)[1])",
    "discord": "g.discord_id",
    "last_seen": "g.last_seen",
    "playtime": "g.playtime",
    "state": "lower(g.state)",
}

#: The directory page. Parameterised everywhere except the ORDER BY, whose expression comes from
#: :data:`_DIRECTORY_ORDER` and whose direction is ``ASC``/``DESC`` from the validated pair.
#
# The playtime aggregate runs over ``statistics`` ONCE, grouped per UCID, and is summed per player by
# the grouping CTE — the same aggregate answers the column, and nothing re-queries per row. A player
# with no statistics row keeps a NULL playtime (never ``0``), so the page can render an empty cell.
_DIRECTORY_SQL = """
WITH playtime AS (
    SELECT s.player_ucid AS ucid,
           SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on)))
               ::bigint AS seconds
      FROM statistics s
     GROUP BY s.player_ucid
),
scoped AS (
    SELECT DISTINCT s.player_ucid AS ucid
      FROM statistics s
      JOIN missions m ON m.id = s.mission_id
     WHERE m.server_name = ANY(%(servers)s::text[])
),
per_ucid AS (
    SELECT p.ucid, p.name, p.discord_id, p.first_seen, p.last_seen, p.vip,
           pt.seconds AS playtime,
           EXISTS (SELECT 1 FROM bans b
                    WHERE b.ucid = p.ucid
                      AND b.banned_until >= (now() AT TIME ZONE 'utc')
                      AND (%(servers)s::text[] IS NULL
                           OR b.server_name = ANY(%(servers)s::text[]))) AS banned,
           EXISTS (SELECT 1 FROM watchlist w WHERE w.player_ucid = p.ucid) AS watched
      FROM players p
      LEFT OUTER JOIN playtime pt ON pt.ucid = p.ucid
     WHERE %(servers)s::text[] IS NULL OR p.ucid IN (SELECT ucid FROM scoped)
),
grouped AS (
    SELECT COALESCE(NULLIF(discord_id, -1)::text, ucid) AS player_key,
           array_agg(name ORDER BY last_seen DESC NULLS LAST) AS names,
           array_agg(DISTINCT ucid) AS ucids,
           MAX(discord_id) AS discord_id,
           MIN(first_seen) AS first_seen,
           MAX(last_seen) AS last_seen,
           bool_or(vip) AS vip,
           bool_or(banned) AS banned,
           bool_or(watched) AS watched,
           SUM(playtime) AS playtime,
           concat_ws(' ', CASE WHEN bool_or(banned) THEN 'banned' END,
                          CASE WHEN bool_or(watched) THEN 'watched' END,
                          CASE WHEN bool_or(vip) THEN 'vip' END) AS state
      FROM per_ucid
     GROUP BY 1
)
SELECT g.player_key, g.names, g.ucids, g.discord_id, g.first_seen, g.last_seen,
       g.vip, g.banned, g.watched, g.playtime, g.state,
       count(*) OVER () AS total
  FROM grouped g
 WHERE (%(needle)s = ''
    OR EXISTS (SELECT 1 FROM unnest(g.ucids) AS u WHERE u ILIKE %(pattern)s ESCAPE '\\')
    OR EXISTS (SELECT 1 FROM unnest(g.names) AS n WHERE n ILIKE %(pattern)s ESCAPE '\\'))
{state} ORDER BY {order}
 LIMIT %(limit)s OFFSET %(offset)s
"""

#: The directory's STATE views, keyed by the ``?show=`` vocabulary the console serves. The
#: value is the predicate ADDED to the statement's WHERE, BEFORE the page is cut, so the rows,
#: the total, the count sentence and the pager all describe the FILTERED set. ``all`` adds
#: nothing, i.e. the whole directory; a key outside this allow-list never reaches the SQL.
_DIRECTORY_STATES: dict[str, str] = {
    "all": "",
    "banned": "   AND g.banned\n",
    "watched": "   AND g.watched\n",
}

#: The player's own row, by the ucid the page asked for.
_PLAYER_SQL = """
SELECT ucid, name, discord_id, manual, first_seen, last_seen, vip
  FROM players
 WHERE ucid = %(ucid)s
"""

#: Every UCID of ONE Discord link, most recently seen first — the identity list of a player page.
_PLAYER_UCIDS_SQL = """
SELECT ucid, name, manual
  FROM players
 WHERE discord_id = %(discord_id)s
 ORDER BY last_seen DESC NULLS LAST, ucid
"""

#: The presence bound, for a NARROWED caller: is any UCID of this player seen on a server of the set?
_PLAYER_SCOPE_SQL = """
SELECT EXISTS (
    SELECT 1
      FROM statistics s
      JOIN missions m ON m.id = s.mission_id
     WHERE s.player_ucid = ANY(%(ucids)s::text[])
       AND m.server_name = ANY(%(servers)s::text[])
) AS present
"""

#: A player's playtime and session count over every UCID of the link.
_PLAYER_TOTALS_SQL = """
SELECT SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on)))
           ::bigint AS playtime,
       COUNT(*) AS sessions
  FROM statistics s
 WHERE s.player_ucid = ANY(%(ucids)s::text[])
"""

#: The player's watchlist entry — one row per UCID, so the newest of the link's rows is the entry.
_WATCH_SQL = """
SELECT player_ucid AS ucid, reason, created_by, created_at
  FROM watchlist
 WHERE player_ucid = ANY(%(ucids)s::text[])
 ORDER BY created_at DESC NULLS LAST
 LIMIT 1
"""

# ── the player's infractions ────────────────────────────────────────────────
#
# THE DEFINITIONS ARE THE PUNISHMENT PLUGIN'S, NOT THIS MODULE'S. "An infraction" is the report the
# plugin renders (``plugins/punishment/reports/events.json``) and "a penalty point" is the row the
# ``/penalty`` command lists (``plugins/punishment/commands.py``). Both are read from their own
# owners and only FORMATTED here, so the website cannot drift from Discord about what counts:
#
# * the friendly-fire and the ban-history statements are the report file's OWN SQL strings, loaded
#   where the bot's report loader looks for them (a deployment's ``reports/punishment/`` override
#   first, the shipped file second) and formatted with the same two placeholders the report fills;
# * the penalty statement is the ``/penalty`` command's query, verbatim.
#
# The report's colour escapes are Discord EMBED presentation (``ansi_colors``), so they are stripped
# where the rows reach the console.

#: The punishment plugin's report that defines the infractions, and its shipped location.
_PUNISHMENT_REPORT_PLUGIN = "punishment"
_PUNISHMENT_REPORT_FILE = "events.json"

#: Which statement of that report answers which list, keyed by a fragment every one of its bodies
#: carries. The report's own element ORDER is what the lookup walks, so a reordered file still
#: resolves to the same two readings.
_REPORT_MARKERS: tuple[tuple[str, str], ...] = (("infractions", "missionstats"),
                                                ("ban_history", " FROM bans "))

#: The ``/penalty`` command's query, verbatim: one row per decayed event, newest first.
_PENALTY_SQL = """
SELECT event, points, time
  FROM pu_events
 WHERE init_id = %(ucid)s
 ORDER BY time DESC
"""

#: Discord's ANSI colouring, as the report's SQL builds it — an escape sequence, an embed concern.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _punishment_report() -> dict:
    """The punishment plugin's infraction report, parsed.

    Read the way the bot's own report loader resolves a report: a deployment's
    ``reports/<plugin>/<file>`` override wins over the shipped ``plugins/<plugin>/reports/<file>``,
    and the source tree is the last resort (so a console started from another working directory
    still finds the file it ships with). Raises for a file that is absent or not JSON — the caller
    turns that into a failed read, never into a page that invents infractions.
    """
    from pathlib import Path

    candidates = (Path("reports") / _PUNISHMENT_REPORT_PLUGIN / _PUNISHMENT_REPORT_FILE,
                  Path("plugins") / _PUNISHMENT_REPORT_PLUGIN / "reports" / _PUNISHMENT_REPORT_FILE,
                  Path(__file__).resolve().parents[2] / "plugins" / _PUNISHMENT_REPORT_PLUGIN
                  / "reports" / _PUNISHMENT_REPORT_FILE)
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open(encoding="utf-8") as handle:
                return json.load(handle)
    raise FileNotFoundError(_PUNISHMENT_REPORT_FILE)


def report_statements(report: dict) -> dict[str, str]:
    """The report's two player statements, keyed ``infractions`` / ``ban_history``.

    Every ``SQLTable`` element of the report is a candidate and the marker decides which list it
    answers, so the two readings are found by what their SQL is ABOUT rather than by an index a
    report edit would move.
    """
    found: dict[str, str] = {}
    for element in report.get("elements") or ():
        if not isinstance(element, dict) or element.get("type") != "SQLTable":
            continue
        sql = str((element.get("params") or {}).get("sql") or "")
        for name, marker in _REPORT_MARKERS:
            if marker in sql:
                found.setdefault(name, sql)
    missing = [name for name, _ in _REPORT_MARKERS if name not in found]
    if missing:
        raise ValueError(f"the infraction report carries no {', '.join(missing)} statement")
    return found

#: The statistics totals. Every figure is the bot's own definition (``plugins/userstats``): the AI
#: and player kill/death split, and ``selfkills`` as the deaths no listed cause explains.
_STATS_TOTALS_SQL = """
SELECT SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on)))
           ::bigint AS playtime,
       COUNT(*) AS sessions,
       COALESCE(SUM(s.kills), 0) AS kills,
       COALESCE(SUM(s.pvp), 0) AS pvp,
       COALESCE(SUM(s.deaths), 0) AS deaths,
       COALESCE(SUM(s.ejections), 0) AS ejections,
       COALESCE(SUM(s.crashes), 0) AS crashes,
       COALESCE(SUM(s.takeoffs), 0) AS takeoffs,
       COALESCE(SUM(s.landings), 0) AS landings,
       COALESCE(SUM(s.teamkills), 0) AS teamkills,
       COALESCE(SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams
                    + s.deaths_ground - s.deaths_pvp), 0) AS deaths_ai,
       COALESCE(SUM(s.deaths_pvp), 0) AS deaths_pvp,
       COALESCE(SUM(GREATEST(s.deaths, s.crashes) - s.deaths_planes - s.deaths_helicopters
                    - s.deaths_ships - s.deaths_sams - s.deaths_ground), 0) AS selfkills
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[]) AND {period}
"""

#: Kills by the TYPE of the target, as the Discord chart groups them.
_STATS_KILL_TYPES_SQL = """
SELECT COALESCE(SUM(s.kills_planes), 0) AS planes,
       COALESCE(SUM(s.kills_helicopters), 0) AS helicopters,
       COALESCE(SUM(s.kills_ships), 0) AS ships,
       COALESCE(SUM(s.kills_sams), 0) AS sams,
       COALESCE(SUM(s.kills_ground), 0) AS ground
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[]) AND {period}
"""

#: One playtime breakdown. ``{column}`` is ONE of the three closed group-by expressions below, never
#: a value from the request.
_STATS_PLAYTIME_SQL = """
SELECT {column} AS label,
       SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on)))
           ::bigint AS playtime
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[]) AND {period}
 GROUP BY 1
 ORDER BY 2 DESC NULLS LAST, 1
"""

#: The closed set of breakdown columns, keyed by the name the action reports them under.
_STATS_BREAKDOWNS: dict[str, str] = {"per_module": "s.slot", "per_server": "m.server_name",
                                     "per_map": "m.mission_theatre"}

#: The player's last sessions, newest first.
_STATS_RECENT_SQL = """
SELECT s.hop_on, s.hop_off, s.slot, m.server_name, m.mission_theatre,
       EXTRACT(EPOCH FROM (COALESCE(s.hop_off, (now() AT TIME ZONE 'utc')) - s.hop_on))::bigint
           AS playtime
  FROM statistics s
  JOIN missions m ON m.id = s.mission_id
 WHERE s.player_ucid = ANY(%(ucids)s::text[]) AND {period}
 ORDER BY s.hop_on DESC
 LIMIT %(limit)s
"""


def _like_escape(value: str) -> str:
    """*value* with the LIKE metacharacters escaped, for a BOUND ``ILIKE`` parameter.

    The statement declares ``ESCAPE '\\'``, so a search for ``%`` or ``_`` matches those characters
    instead of acting as a wildcard. Escaping is a formatting concern of the PATTERN, not of the
    value: the value itself still travels as a bound parameter.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _directory_order(key: str, direction: str) -> str:
    """The directory's ORDER BY clause: a closed-lookup expression and a validated direction.

    Nothing from the request is interpolated: *key* is already normalised against the table's column
    allow-list and *direction* is one of ``asc``/``desc``, so the clause can only be one this module
    wrote. The player key is the tie-break, so paging cannot repeat or skip a row.
    """
    expression = _DIRECTORY_ORDER.get(key) or _DIRECTORY_ORDER["name"]
    return f"{expression} {direction.upper()} NULLS LAST, g.player_key"


def _directory_state(value: Any) -> str:
    """The directory's state view in force: a key of :data:`_DIRECTORY_STATES`.

    A closed vocabulary like the sort keys: a value the console does not serve is the whole
    directory, so no request text ever reaches the statement.
    """
    wanted = str(value or "").strip().lower()
    return wanted if wanted in _DIRECTORY_STATES else "all"


async def _fetch_all(conn: Any, statement: str, params: Any) -> list[dict]:
    """Every row of *statement* as a plain dict (the read models must not see driver row objects)."""
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(statement, params)
        return [dict(row) async for row in cursor]


async def _fetch_one(conn: Any, statement: str, params: Any) -> dict:
    """The first row of *statement* as a dict, or ``{}`` when it answered none."""
    rows = await _fetch_all(conn, statement, params)
    return rows[0] if rows else {}


def _player_pool(ctx: Any) -> Any:
    """The pool the player reads run on: this process node's ``apool``, or ``None``."""
    return getattr(getattr(ctx, "node", None), "apool", None)


async def _member_names(ctx: Any, discord_ids: Iterable[Any], fetch: bool = False) -> dict[int, str]:
    """The Discord member names for *discord_ids*, keyed by id.

    The value is the member's SHOWN name (nickname, else global name, else username). Names are UI,
    not data: a member this process cannot resolve is an ABSENT entry, never an error, so a caller
    without a Discord connection renders the same page with the id alone.

    ``fetch`` True allows a bounded REST lookup for ids the cache does not hold; it is for reads of
    ONE player. A list keeps it False so a page render never becomes a burst of API calls.
    """
    from services.webservice.readmodels.serverconfig import live_bot

    wanted: set[int] = set()
    for item in discord_ids:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            wanted.add(value)
    if not wanted:
        return {}
    bot = live_bot()
    if bot is None:
        return {}
    try:
        members = await bot.get_members(wanted, fetch=fetch)
    except Exception:
        log.exception("player read: the linked members could not be read")
        return {}
    return {int(user_id): str(getattr(member, "display_name", "") or "")
            for user_id, member in (members or {}).items() if member is not None}


def _player_servers(ctx: Any) -> list[str] | None:
    """The server names a player read is bound to: ``None`` for an unscoped caller, else the set."""
    return None if _caller_ban_unrestricted(ctx) else _caller_ban_targets(ctx)


async def _player_ucids(conn: Any, row: dict) -> list[dict]:
    """Every UCID of the player *row* names, most recently seen first (the row itself if unlinked)."""
    try:
        discord_id = int(row.get("discord_id"))
    except (TypeError, ValueError):
        discord_id = -1
    if discord_id <= 0:
        return [{"ucid": row.get("ucid"), "name": row.get("name"), "manual": row.get("manual")}]
    linked = await _fetch_all(conn, _PLAYER_UCIDS_SQL, {"discord_id": discord_id})
    if not any(entry.get("ucid") == row.get("ucid") for entry in linked):
        linked.insert(0, {"ucid": row.get("ucid"), "name": row.get("name"),
                          "manual": row.get("manual")})
    return linked


async def _player_in_scope(conn: Any, ucids: list[str], servers: list[str] | None) -> bool:
    """Whether a player the caller may see: always for an unscoped caller, presence-bound otherwise.

    ``players`` carries no server column, so the bound is the player's ``statistics`` rows joined to
    ``missions`` — the same reading of "a player of mine" the directory uses. A narrowed caller whose
    set is empty reaches nobody.
    """
    if servers is None:
        return True
    if not servers or not ucids:
        return False
    found = await _fetch_one(conn, _PLAYER_SCOPE_SQL, {"ucids": ucids, "servers": servers})
    return bool(found.get("present"))


def _period_filter(node: Any, period: str) -> Any:
    """The bot's OWN period filter for *period*, and the period name it resolved to.

    ``StatisticsFilter.detect`` is the bot's one definition of a period, so the console reads it
    rather than inventing a second one. Only the PLAIN period vocabulary is accepted here (the names
    ``PeriodFilter.list()`` publishes): a mission/campaign/month/theatre name would need the bot's
    pools and would mean something different from what the page's switch offers, so anything detect
    answers with a non-period filter — or with a name outside the vocabulary — is the ``all`` period.

    Returns ``(filter, name)``.
    """
    from plugins.userstats.filter import PeriodFilter, StatisticsFilter

    wanted = (period or "").strip().lower()
    known = PeriodFilter.list(node)
    if wanted not in known:
        wanted = "all"
    try:
        # the adapter is what ``detect`` expects of a bot: a ``node`` (some filters read its pools).
        # A context that cannot answer that raises nothing out of this function — the plain period
        # filter is the same answer for every name this action accepts.
        detected = StatisticsFilter.detect(cast(Any, types.SimpleNamespace(node=node)), wanted)
    except Exception:
        log.warning("player statistics: the period filter could not be detected, using '%s'", wanted)
        detected = None
    if type(detected) is not PeriodFilter or detected.period != wanted:
        detected = PeriodFilter(wanted)
    return detected, wanted


@action
async def list_player_directory(ctx: Any, q: str = "", page: str | int | None = None,
                                sort: str = "", direction: str = "",
                                state: str = "all") -> ActionResult:
    """One row per PLAYER of the caller's scope, one PAGE at a time — the directory's read.

    *q* is the ONE search box: it matches a DCS name or a UCID, trimmed and capped by the console's
    own ``readmodels.views.normalise_query`` (never a second trimming rule) and matched as a BOUND
    parameter. *page* is resolved by ``readmodels.views.normalise_page``: a non-numeric, negative or
    past-the-end value is page 1, and the answer states the page it actually served.

    *sort*/*direction* are validated against the ``player_directory`` table's own column allow-list,
    and the ORDER BY is a closed lookup on that allow-list — the request never reaches the statement.

    *state* is the restriction view in force (``all``/``banned``/``watched``), a closed lookup of
    predicates ADDED to the same statement, so a restricted page is cut from the restricted set:
    the rows, the total and the page count all describe what the view shows.

    The answer carries the page's rows AND the total behind them (``total``, ``pages``,
    ``per_page``), so the page can state "50 of 1,284" without a second read, and every row carries
    the moderation state the row's chips are drawn from.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=PLAYERS_CONSOLE_ONLY)
    try:
        from services.webservice.readmodels import views as view_layer
    except Exception as ex:
        log.exception("list_player_directory: the console's view layer could not be imported")
        return ActionResult(success=False, message=f"The player list could not be read: {ex}")
    query = view_layer.normalise_query(q)
    key = view_layer.normalise_sort(_DIRECTORY_TABLE, sort)
    wanted_direction = view_layer.normalise_direction(direction)
    requested = view_layer.normalise_page(page)
    pool = _player_pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=PLAYERS_UNAVAILABLE)

    servers = _player_servers(ctx)
    wanted_state = _directory_state(state)
    statement = _DIRECTORY_SQL.format(order=_directory_order(key, wanted_direction),
                                      state=_DIRECTORY_STATES[wanted_state])
    params = {"needle": query,
              "pattern": f"%{_like_escape(query)}%" if query else "%",
              "servers": servers, "limit": PLAYER_DIRECTORY_PAGE_SIZE}
    served = requested
    rows: list[dict] = []
    total = 0
    try:
        async with pool.connection() as conn:
            # the CLOSED RANGE: a page past the end is answered with the first one, so the reader is
            # never handed an empty table for a number they typed. Only that case costs a second
            # statement.
            for attempt in dict.fromkeys((requested, 1)):
                page_rows = await _fetch_all(conn, statement,
                                             {**params, "offset": (attempt - 1) * PLAYER_DIRECTORY_PAGE_SIZE})
                served = attempt
                if page_rows:
                    rows, total = page_rows, int(page_rows[0].get("total") or 0)
                    break
    except Exception as ex:
        log.exception("list_player_directory failed")
        return ActionResult(success=False, message=f"The player list could not be read: {ex}")

    for row in rows:
        row.pop("total", None)
        row["ucid"] = (row.get("ucids") or [""])[0]
    pages = max((total + PLAYER_DIRECTORY_PAGE_SIZE - 1) // PLAYER_DIRECTORY_PAGE_SIZE, 1)
    return ActionResult(success=True, data={
        "rows": rows, "total": total, "page": served, "pages": pages,
        "per_page": PLAYER_DIRECTORY_PAGE_SIZE, "query": query,
        "sort": key, "direction": wanted_direction, "state": wanted_state})


@action
async def get_player_detail(ctx: Any, ucid: str) -> ActionResult:
    """ONE player by UCID: identity, link, presence, totals, and both moderation states.

    The ucids the player's Discord link groups are read together, so a player with two UCIDs answers
    with both (identity list, playtime summed, bans and watch searched over all of them).

    The ACTIVE ban is the entry ``ServiceBus.bans`` already groups — its ``server_name`` is the list
    of servers the ban covers, or ``None`` for a bot-wide one — and the ban read is SCOPED the way
    the Bans page's is, so a manager sees the bans of their own servers and no others. The watch
    entry is the newest ``watchlist`` row of the link (the table holds one per UCID and no expiry).

    A player who is not found, or is outside a narrowed caller's scope, answers ``found`` False with
    the SAME keys as a found one: the page renders one shape for every state, and a name outside the
    caller's scope is not disclosed either way.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=PLAYERS_CONSOLE_ONLY)
    wanted = (ucid or "").strip()
    if not wanted:
        return ActionResult(success=False, message="A UCID is required.")
    pool = _player_pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=PLAYERS_UNAVAILABLE)

    empty = {"ucid": wanted, "found": False, "identity": [], "name": "", "discord_id": -1,
             "member": "", "vip": False, "first_seen": None, "last_seen": None,
             "playtime": None, "sessions": None, "ban": None, "watch": None}
    try:
        async with pool.connection() as conn:
            row = await _fetch_one(conn, _PLAYER_SQL, {"ucid": wanted})
            if not row:
                return ActionResult(success=True, data=empty)
            identity = await _player_ucids(conn, row)
            ucids = [entry["ucid"] for entry in identity if entry.get("ucid")]
            servers = _player_servers(ctx)
            if not await _player_in_scope(conn, ucids, servers):
                return ActionResult(success=True, data=empty)
            totals = await _fetch_one(conn, _PLAYER_TOTALS_SQL, {"ucids": ucids})
            watch = await _fetch_one(conn, _WATCH_SQL, {"ucids": ucids})
    except Exception as ex:
        log.exception("get_player_detail failed")
        return ActionResult(success=False, message=f"The player could not be read: {ex}")

    ban = await _active_ban(ctx, ucids, servers)
    names = await _member_names(ctx, [row.get("discord_id")], fetch=True)
    return ActionResult(success=True, data={
        "ucid": wanted,
        "found": True,
        "identity": identity,
        "name": (identity[0].get("name") if identity else "") or row.get("name") or "",
        "discord_id": row.get("discord_id", -1),
        "member": names.get(int(row.get("discord_id") or -1), ""),
        "vip": bool(row.get("vip")),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "playtime": totals.get("playtime"),
        "sessions": totals.get("sessions"),
        "ban": ban,
        "watch": watch or None,
    })


async def _active_ban(ctx: Any, ucids: list[str], servers: list[str] | None) -> dict | None:
    """The player's ACTIVE ban entry, grouped and scoped by the bus — ``None`` when there is none.

    The ban list itself is the bus's own read, so the grouping of a ban's rows into one entry (its
    ``server_name`` as a list, or ``None`` for a bot-wide ban) is not restated here. A ban list this
    process cannot reach is an ABSENT ban, never a failed player page.
    """
    service = _ban_service(ctx)
    if service is None:
        return None
    try:
        entries = await service.bans(servers=tuple(servers) if servers is not None else None)
    except Exception:
        log.exception("get_player_detail: the ban list could not be read")
        return None
    mine = [entry for entry in entries if _entry_field(entry, "ucid") in ucids]
    if not mine:
        return None
    return max(mine, key=lambda entry: _entry_field(entry, "banned_at") or datetime.min)


@action
async def get_player_statistics(ctx: Any, ucid: str, period: str = "all") -> ActionResult:
    """The ``/statistics`` numbers of ONE player for ONE period — the statistics tab's read.

    A SEPARATE action from :func:`get_player_detail` on purpose: the period re-reads on every switch,
    so it must not drag the identity, the link and the moderation reads along with it.

    *period* accepts exactly the names :meth:`plugins.userstats.filter.PeriodFilter.list` publishes
    (``all``, ``today``, ``yesterday``, ``day``, ``week``, ``month``, ``quarter``, ``halfyear``,
    ``year``) and ANYTHING else is ``all``; the filter itself is the bot's own
    :func:`_period_filter`, so the console has no second definition of "a period".

    Every figure is the bot's own (``plugins/userstats``): kills and the AI/player split, deaths by
    AI/player/self, teamkills, the flight figures, kills by target type, the playtime breakdowns by
    module/server/map, and the last ``PLAYER_RECENT_SESSIONS`` sessions.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=PLAYERS_CONSOLE_ONLY)
    wanted = (ucid or "").strip()
    if not wanted:
        return ActionResult(success=False, message="A UCID is required.")
    pool = _player_pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=PLAYERS_UNAVAILABLE)

    node = getattr(ctx, "node", None)
    flt, resolved = _period_filter(node, period)
    clause = flt.filter(node)
    try:
        async with pool.connection() as conn:
            row = await _fetch_one(conn, _PLAYER_SQL, {"ucid": wanted})
            if not row:
                return ActionResult(success=True, data={"ucid": wanted, "period": resolved,
                                                        "totals": {}, "found": False})
            identity = await _player_ucids(conn, row)
            ucids = [entry["ucid"] for entry in identity if entry.get("ucid")]
            if not await _player_in_scope(conn, ucids, _player_servers(ctx)):
                return ActionResult(success=True, data={"ucid": wanted, "period": resolved,
                                                        "totals": {}, "found": False})
            params = {"ucids": ucids}
            totals = await _fetch_one(conn, _STATS_TOTALS_SQL.format(period=clause), params)
            kill_types = await _fetch_one(conn, _STATS_KILL_TYPES_SQL.format(period=clause), params)
            breakdowns = {}
            for name, column in _STATS_BREAKDOWNS.items():
                breakdowns[name] = await _fetch_all(
                    conn, _STATS_PLAYTIME_SQL.format(column=column, period=clause), params)
            recent = await _fetch_all(conn, _STATS_RECENT_SQL.format(period=clause),
                                      {**params, "limit": PLAYER_RECENT_SESSIONS})
    except Exception as ex:
        log.exception("get_player_statistics failed")
        return ActionResult(success=False, message=f"The statistics could not be read: {ex}")

    kills = int(totals.get("kills") or 0)
    pvp = int(totals.get("pvp") or 0)
    crashes = int(totals.get("crashes") or 0)
    ejections = int(totals.get("ejections") or 0)
    figures = {
        "playtime": totals.get("playtime"),
        "sessions": int(totals.get("sessions") or 0),
        "kills": kills,
        "ai_kills": kills - pvp,
        "player_kills": pvp,
        "deaths": int(totals.get("deaths") or 0),
        "deaths_ai": int(totals.get("deaths_ai") or 0),
        "deaths_player": int(totals.get("deaths_pvp") or 0),
        "selfkills": int(totals.get("selfkills") or 0),
        "teamkills": int(totals.get("teamkills") or 0),
        "takeoffs": int(totals.get("takeoffs") or 0),
        "landings": int(totals.get("landings") or 0),
        "ejections": ejections,
        # the pilot-dead count the Discord embed draws: a crash that never left the aircraft
        "crashes": max(crashes - ejections, 0),
    }
    return ActionResult(success=True, data={
        "ucid": wanted, "period": resolved, "found": True, "totals": figures,
        "kills_by_type": [{"label": label, "count": int(kill_types.get(label) or 0)}
                          for label in ("planes", "helicopters", "ships", "sams", "ground")],
        **breakdowns,
        "recent": recent,
    })


@action
async def get_player_infractions(ctx: Any, ucid: str) -> ActionResult:
    """ONE player's infractions: the friendly-fire events, the penalty log and the ban history.

    THE THREE LISTS COME FROM THE PUNISHMENT PLUGIN'S OWN DEFINITIONS — the report file for the
    events and the ban history, the ``/penalty`` command's statement for the points (see the module
    constants above), so the console and Discord cannot disagree about what an infraction is.

    The report reads ONE UCID (``init_id``), so this reads the page's UCID and nothing wider: a
    player's other UCIDs are other rows in every one of these tables.

    The scope is the player read's own bound (``_player_in_scope``): a player outside a narrowed
    caller's servers answers the same empty answer a UCID nothing knows does, so infractions are not
    a way around the directory's scope.

    ``penalty_total`` is the SUM of the log's own rows, exactly as ``/penalty`` computes it — the
    points decay over time, so the total is today's value and the page says so.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=PLAYERS_CONSOLE_ONLY)
    wanted = (ucid or "").strip().lower()
    if not _PLAYER_UCID_SHAPE.fullmatch(wanted):
        return ActionResult(success=False, message="A UCID is 32 hexadecimal characters.")
    pool = _player_pool(ctx)
    if pool is None:
        return ActionResult(success=False, message=PLAYERS_UNAVAILABLE)
    empty = {"ucid": wanted, "found": False, "infractions": [], "penalties": [],
             "penalty_total": 0.0, "ban_history": []}
    try:
        statements = report_statements(_punishment_report())
        infractions_sql = statements["infractions"].format(ucid=wanted,
                                                           limit=PLAYER_INFRACTION_EVENTS)
        history_sql = statements["ban_history"].format(ucid=wanted)
        async with pool.connection() as conn:
            row = await _fetch_one(conn, _PLAYER_SQL, {"ucid": wanted})
            if not row:
                return ActionResult(success=True, data=empty)
            identity = await _player_ucids(conn, row)
            ucids = [entry["ucid"] for entry in identity if entry.get("ucid")]
            if not await _player_in_scope(conn, ucids, _player_servers(ctx)):
                return ActionResult(success=True, data=empty)
            events = await _fetch_all(conn, infractions_sql, None)
            penalties = await _fetch_all(conn, _PENALTY_SQL, {"ucid": wanted})
            history = await _fetch_all(conn, history_sql, None)
    except Exception as ex:
        log.exception("get_player_infractions failed")
        return ActionResult(success=False, message=f"The infractions could not be read: {ex}")

    total = 0.0
    for penalty in penalties:
        try:
            total += float(penalty.get("points") or 0)
        except (TypeError, ValueError):
            continue
    return ActionResult(success=True, data={
        "ucid": wanted,
        "found": True,
        "infractions": [{"time": item.get("Time"), "infraction": _plain(item.get("Infraction")),
                         "count": item.get("Count")} for item in events],
        "penalties": [{"time": item.get("time"), "event": _plain(item.get("event")),
                       "points": item.get("points")} for item in penalties],
        "penalty_total": total,
        "ban_history": [{"timeframe": _plain(item.get("Timeframe")),
                         "reason": _plain(item.get("Reason")),
                         "status": _plain(item.get("Status"))} for item in history],
    })


def _plain(value: Any) -> str:
    """One report cell as the console renders it: Discord's colour escapes removed, whitespace folded.

    The report's SQL paints its columns for a Discord embed; the console's tables are plain text, so
    the escapes are dropped here rather than in a second copy of the query.
    """
    return " ".join(_ANSI.sub("", str(value or "")).split())


def _active_players(server: Any) -> list[Any]:
    """The players SITTING IN A SLOT on *server*, or ``[]`` when nothing can be read.

    ``Server.get_active_players`` is the domain's own accessor; a double that carries only the
    ``players`` mapping is read the same way the players read model reads it (``active`` marks the
    ones in a slot). A server whose players cannot be read contributes nobody rather than failing the
    search: a search is an enhancement, and a partly-readable cluster must not turn it into an error.
    """
    getter = getattr(server, "get_active_players", None)
    if callable(getter):
        try:
            found = cast(Iterable[Any], getter())
        except Exception:
            log.exception("player search: the active players of '%s' could not be read.",
                          getattr(server, "name", "?"))
            return []
        return list(found) if found else []
    players = getattr(server, "players", None) or {}
    try:
        return [player for player in players.values() if getattr(player, "active", False)]
    except Exception:
        log.exception("player search: the players of '%s' could not be read.",
                      getattr(server, "name", "?"))
        return []


def _present_ucids(ctx: Any) -> list[str]:
    """The UCIDs of the players PRESENT on the servers of *ctx*'s own view, in no particular order.

    ``ctx.servers`` is the console's SCOPED cluster, so this is the presence set of exactly the
    servers the caller may see. It is the bound a narrowed caller's player search is held to, because
    ``players`` carries no server column: the players PRESENT on those servers is the only reading of
    "a player of mine" the schema can express. Read off the live server objects, never queried.
    """
    out: list[str] = []
    for server in (getattr(ctx, "servers", None) or {}).values():
        for player in _active_players(server):
            ucid = getattr(player, "ucid", "")
            if isinstance(ucid, str) and ucid and ucid not in out:
                out.append(ucid)
    return out


@action
async def search_players(ctx: Any, query: str = "") -> ActionResult:
    """The players whose NAME or UCID matches *query* — the console's ban ADD autocompletes on it.

    The search itself is ``utils.get_all_players``, the function ``/info``'s autocomplete is fed by:
    at most 25 rows, most recently seen first. There is no second spelling of "find a player by name"
    for the console to drift from.

    An EMPTY (or whitespace-only) *query* answers NOTHING — never every player the caller can reach.

    An UNSCOPED caller (an ``Admin``, break-glass — ``_caller_ban_unrestricted``) gets that list
    whole. Every OTHER caller is bound to the players PRESENT on the servers of its own view
    (:func:`_present_ucids`): the console may never show a caller a player outside its servers, and
    the bound is a WHERE term, never a filter applied after the read.
    """
    if ctx.transport != TRANSPORT_WEB:
        return ActionResult(success=False, message=BANS_CONSOLE_ONLY)
    wanted = (query or "").strip()
    if not wanted:
        return ActionResult(success=True, data={"players": []})
    present = None if _caller_ban_unrestricted(ctx) else _present_ucids(ctx)
    try:
        rows = utils.get_all_players(ctx.node, search=wanted, present=present)
    except Exception as ex:
        log.exception("search_players failed")
        return ActionResult(success=False,
                            message=_("The player list could not be read: {}").format(ex))
    return ActionResult(success=True, data={"players": [{"name": name, "ucid": ucid}
                                                       for ucid, name in rows]})


@action
async def unban(ctx: Any, ucid: str, banned_at: str = "") -> PlayerActionResult:
    """Lift the ban on ONE player — the console's LIFT, scoped to the caller's own servers.

    The rows expired are exactly those INSIDE the caller's target set, so lifting a partially-owned
    ban leaves the servers outside it banned. An empty target set is GLOBAL ("empty means global"):
    every row of the ucid is expired, which is what a bot-wide (DGSA) entry needs. ``banned_at`` is
    the entry's own identity (it names WHICH ban of the ucid the operator acted on); the expiry
    itself keys on the ucid and the server, because the bans table holds one row per
    ``(ucid, server)``.
    """
    if ctx.transport != TRANSPORT_WEB:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=BANS_CONSOLE_ONLY, ucid=ucid))
    service = _ban_service(ctx)
    if service is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The ban list is not available in this installation."),
            ucid=ucid))
    targets = _caller_ban_targets(ctx)
    scope = _ban_scope_label(targets)
    try:
        cleared = 0
        for target in targets or [None]:
            cleared += await service.unban(ucid, target)
    except (TimeoutError, asyncio.TimeoutError):
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("Timeout while lifting the ban."), ucid=ucid))
    except Exception as ex:
        log.exception("unban failed")
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("Failed to lift the ban: {}").format(ex), ucid=ucid))
    if not cleared:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("No active ban for {} in your scope.").format(ucid), ucid=ucid))
    return await _audited(ctx, PlayerActionResult(
        success=True, message=_("Ban lifted on {}.").format(scope), ucid=ucid))


@action
async def edit_ban(ctx: Any, ucid: str, banned_at: str, reason: str,
                   until: str | None = None) -> PlayerActionResult:
    """Change ONE ban ENTRY's reason and expiration — the console's EDIT.

    The entry is identified by its ``ucid`` plus its OWN ``banned_at`` (the pair the list renders as
    the row's key). The re-upsert passes that same ``banned_at`` back, so the entry does NOT move in
    time and keeps its identity and its history key.

    WHAT AN EDIT MAY CHANGE IS REASON AND EXPIRATION ONLY. Which servers a ban covers is not an edit:
    a scoped ban is one row per server, so re-scoping is delete-and-re-add, and neither the ucid, nor
    the ``banned_by`` (a fact about who placed it) nor the ``banned_at`` is written.

    THE WRITE IS THE ENTRY'S FULL ROW SET AND IT IS ALL-OR-NOTHING. The entry's rows are re-read
    server-side (an UNSCOPED read, because a scoped one would hide exactly the foreign rows the check
    exists for) and the edit is refused unless EVERY row is inside the caller's target set — a
    partially-owned entry is refused WHOLE with nothing written. The entry's grouping key includes
    the reason, so re-writing the reason on some rows and not others would split one ban into two
    entries; that is why this is not a per-row operation.

    A DGSA entry (``banned_by == DGSA_BANNED_BY``) is refused: rewriting its reason or its
    ``banned_by`` would break the cloud's own bookkeeping. Every refusal is typed and writes nothing.

    ``until`` is the form's own value — an ABSOLUTE UTC datetime — and is parsed by
    :func:`parse_ban_until`: empty is the PERMANENT sentinel, a malformed or past stamp is a TYPED
    refusal writing nothing. It is passed to ``ServiceBus.ban(..., until=...)`` so the ban's own end
    is written verbatim; a reason-only save posts the entry's own end back and does not move it.
    """
    if ctx.transport != TRANSPORT_WEB:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=BANS_CONSOLE_ONLY, ucid=ucid))
    value = (ucid or "").strip()
    if not utils.is_ucid(value):
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("'{}' is not a valid UCID.").format(value), ucid=value))

    body = (reason or "").strip()
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("A reason is required to ban a player."), ucid=value))
    if len(body) > MAX_BAN_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("The reason is longer than {} characters.").format(MAX_BAN_REASON_LENGTH),
            ucid=value))

    end, refusal = parse_ban_until(until)
    if refusal:
        return await _audited(ctx, PlayerActionResult(success=False, message=refusal, ucid=value))

    key = (banned_at or "").strip()
    if not key:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The ban could not be identified — open it from the list."),
            ucid=value))

    service = _ban_service(ctx)
    if service is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The ban list is not available in this installation."),
            ucid=value))

    try:
        entries = await service.bans()
    except Exception as ex:
        log.exception("edit_ban failed to read the ban list")
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The ban list could not be read: {}").format(ex), ucid=value))

    entry = next((candidate for candidate in entries
                  if _entry_field(candidate, "ucid") == value
                  and _entry_stamp(candidate) == key), None)
    if entry is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("No active ban for {} in your scope.").format(value), ucid=value))
    if _entry_field(entry, "banned_by") == DGSA_BANNED_BY:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("A DGSA ban cannot be edited — it can only be lifted here."), ucid=value))

    servers = _entry_servers(entry)
    targets = _caller_ban_targets(ctx)
    if not _entry_in_targets(servers, targets):
        # The SAME sentence as the "no such entry" refusal above: a ban that stands only on foreign
        # servers and a ucid+stamp that names no ban must be indistinguishable, or a holder of this
        # capability could probe whether a ban exists outside their scope.
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("No active ban for {} in your scope.").format(value),
            ucid=value))

    banned_by = _entry_field(entry, "banned_by") or ctx.audit_actor
    stamp = _entry_field(entry, "banned_at")
    try:
        for target in (servers or [None]):
            await service.ban(value, banned_by, body, server_name=target, banned_at=stamp,
                              until=end)
        result = PlayerActionResult(
            success=True, message=_("Ban on {} updated.").format(value), ucid=value)
    except (TimeoutError, asyncio.TimeoutError):
        result = PlayerActionResult(success=False, message=_("Timeout while editing the ban."),
                                    ucid=value)
    except Exception as ex:
        log.exception("edit_ban failed")
        result = PlayerActionResult(success=False, message=_("Failed to edit the ban: {}").format(ex),
                                    ucid=value)
    return await _audited(ctx, result)


# ── The watchlist writes ────────────────────────────────────────────────────
#
# A watch ENTRY is ONE row per UCID (``plugins/mission/db/tables.sql``): ``reason``, ``created_by``,
# ``created_at`` — and NO expiry, so no write here states one. The trio mirrors the ban entry's
# add/lift/edit: the ADD inserts the row, the REMOVE deletes it, and the EDIT rewrites ``reason``
# alone, because ``created_by``/``created_at`` record who watched the player and when (re-stamping
# them on an edit would rewrite that history).
#
# NONE OF THEM needs a server or a live session: a watch entry is a bot-wide fact keyed on the UCID,
# so all three work for a player who is OFFLINE.
#
# BOTH TRANSPORTS: the console's three controls and the Discord ``watch`` group call these SAME
# functions (``plugins/mission/views.py``), so one write path serves both surfaces and the one that
# needs no Discord at all is not the poorer one.

#: The longest watch REASON a write accepts — the limit the Discord modal's own field carries
#: (``plugins/mission/views.py``), so a typed reason and a posted one are bounded alike.
MAX_WATCH_REASON_LENGTH = 80

#: The statement the REMOVE and the EDIT refusals share: an entry that does not exist, a player
#: outside the caller's scope and a crafted UCID must be indistinguishable, or the capability becomes
#: a probe.
_WATCH_NO_ENTRY = "No watch entry for {} in your scope."


async def _watch_scope_refusal(conn: Any, ctx: Any, ucid: str, refusal: str = "") -> str:
    """The refusal for a watch write *ctx* may not make, or ``""`` when it may run.

    An UNSCOPED caller passes unconditionally — it may watch a player this installation has never
    seen, which is the case the ADD exists for. A NARROWED one is bound to the players seen on the
    servers of its own view (:func:`_player_in_scope`): ``players`` carries no server column, so
    presence is the only reading of "a player of mine" the schema can express.

    *refusal* is the sentence this write answers when the player is unknown or outside the scope;
    empty means the REMOVE/EDIT wording, which is the same for both so a holder of the capability
    cannot probe what exists.
    """
    servers = _player_servers(ctx)
    if servers is None:
        return ""
    message = refusal or _(_WATCH_NO_ENTRY).format(ucid)
    row = await _fetch_one(conn, _PLAYER_SQL, {"ucid": ucid})
    if not row:
        return message
    ucids = [entry["ucid"] for entry in await _player_ucids(conn, row) if entry.get("ucid")]
    if not await _player_in_scope(conn, ucids, servers):
        return message
    return ""


@action
async def watch_player(ctx: Any, ucid: str, reason: str, sender: str = "") -> PlayerActionResult:
    """Put ONE player on the watchlist — the console's Add and Discord's ``watch add``.

    The CREATE path, and the only one: the INSERT is a single statement with
    ``ON CONFLICT (player_ucid) DO NOTHING``, so its affected-row count IS the answer to "was this
    player already on the list" — no read-then-write window, and no ``UniqueViolation`` for a caller
    to catch. A UCID that already holds an entry is refused with a sentence and nothing is rewritten.

    ``created_by`` records who watched the player and when the row is stamped: the acting person's
    shown name (*sender*, read off the request's identity on the console and never from the body), or
    the transport's own audit actor when the caller states none.
    """
    value = (ucid or "").strip()
    body = (reason or "").strip()
    if not value:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("A UCID is required."), ucid=value))
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("A reason is required to put a player on the watchlist."), ucid=value))
    if len(body) > MAX_WATCH_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("The watch reason is longer than {} characters.")
                        .format(MAX_WATCH_REASON_LENGTH),
            ucid=value))
    pool = _player_pool(ctx)
    if pool is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=PLAYERS_UNAVAILABLE, ucid=value))
    created_by = (sender or "").strip() or ctx.audit_actor
    try:
        async with pool.connection() as conn:
            refusal = await _watch_scope_refusal(
                conn, ctx, value, _("Player {} is not one of your players.").format(value))
            if refusal:
                return await _audited(ctx, PlayerActionResult(
                    success=False, message=refusal, ucid=value))
            cursor = await conn.execute(
                "INSERT INTO watchlist (player_ucid, reason, created_by) VALUES (%s, %s, %s) "
                "ON CONFLICT (player_ucid) DO NOTHING",
                (value, body, created_by))
            added = cursor.rowcount or 0
    except Exception as ex:
        log.exception("watch_player failed")
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The watchlist could not be written: {}").format(ex),
            ucid=value))
    if not added:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("Player {} was already on the watchlist.").format(value), ucid=value))
    return await _audited(ctx, PlayerActionResult(
        success=True, message=_("Player {} is now on the watchlist.").format(value), ucid=value))


@action
async def unwatch_player(ctx: Any, ucid: str) -> PlayerActionResult:
    """Remove ONE player's watchlist entry — the console's Remove and Discord's watch removal.

    ONE row per UCID, so the delete names the clicked UCID and leaves the player's other UCIDs
    untouched. A caller bound to its own servers may only change an entry of a player it may see
    (:func:`_watch_scope_refusal`).

    A refusal writes NOTHING: an empty UCID, a player this caller may not see, and a UCID holding no
    watch entry all answer the same sentence, and every outcome — refusals included — leaves exactly
    one audit entry.
    """
    value = (ucid or "").strip()
    pool = _player_pool(ctx)
    if not value:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("A UCID is required."), ucid=value))
    if pool is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=PLAYERS_UNAVAILABLE, ucid=value))
    try:
        async with pool.connection() as conn:
            refusal = await _watch_scope_refusal(conn, ctx, value)
            if refusal:
                return await _audited(ctx, PlayerActionResult(
                    success=False, message=refusal, ucid=value))
            cursor = await conn.execute("DELETE FROM watchlist WHERE player_ucid = %s", (value,))
            removed = cursor.rowcount or 0
    except Exception as ex:
        log.exception("unwatch_player failed")
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The watchlist could not be written: {}").format(ex),
            ucid=value))
    if not removed:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_(_WATCH_NO_ENTRY).format(value), ucid=value))
    return await _audited(ctx, PlayerActionResult(
        success=True, message=_("Watch entry for {} removed.").format(value), ucid=value))


@action
async def edit_watch(ctx: Any, ucid: str, reason: str) -> PlayerActionResult:
    """Change ONE watch entry's REASON — the console's Edit and Discord's ``watch edit``.

    ``reason`` travels as a BOUND parameter and the statement names the ``reason`` column alone, so
    ``created_by``, ``created_at`` and the entry's UCID come out of the write byte-identical: an edit
    corrects what the watchlist says, never who watched the player or when.

    An EMPTY reason is refused rather than written: an empty watch entry is not an edit, it is a
    removal, and the sentence says so. Everything else refuses the way :func:`unwatch_player` does —
    same sentence for a missing entry and a player outside the caller's scope, nothing written, one
    audit entry per outcome.
    """
    value = (ucid or "").strip()
    body = (reason or "").strip()
    if not value:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("A UCID is required."), ucid=value))
    if not body:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("An empty reason is not an edit — remove the watch entry instead."),
            ucid=value))
    if len(body) > MAX_WATCH_REASON_LENGTH:
        return await _audited(ctx, PlayerActionResult(
            success=False,
            message=_("The watch reason is longer than {} characters.")
                        .format(MAX_WATCH_REASON_LENGTH),
            ucid=value))
    pool = _player_pool(ctx)
    if pool is None:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=PLAYERS_UNAVAILABLE, ucid=value))
    try:
        async with pool.connection() as conn:
            refusal = await _watch_scope_refusal(conn, ctx, value)
            if refusal:
                return await _audited(ctx, PlayerActionResult(
                    success=False, message=refusal, ucid=value))
            cursor = await conn.execute(
                "UPDATE watchlist SET reason = %s WHERE player_ucid = %s", (body, value))
            changed = cursor.rowcount or 0
    except Exception as ex:
        log.exception("edit_watch failed")
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_("The watchlist could not be written: {}").format(ex),
            ucid=value))
    if not changed:
        return await _audited(ctx, PlayerActionResult(
            success=False, message=_(_WATCH_NO_ENTRY).format(value), ucid=value))
    return await _audited(ctx, PlayerActionResult(
        success=True, message=_("The watch reason for {} was updated.").format(value), ucid=value))


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
            success=False,
            message=f"Player not found on server '{_server_label(server, server_name)}'.",
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
            message=f"Player {name} on server '{_server_label(server, server_name)}' {verb}.",
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
            message=f"No active mission on server '{_server_label(server, server_name)}'.",
            server_name=server_name,
        )
    m = server.current_mission
    label = _mission_label(m, m.filename)
    return MissionListResult(
        success=True,
        message=f"Active mission: {label}",
        server_name=server_name,
        data={
            "name": label,
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

    ``autostart`` and ``load`` are the two LOAD options the console offers — the
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
    display = _server_label(server, server_name)
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
    display = _server_label(server, server_name)
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
    display = _server_label(server, server_name)
    try:
        base_dir = os.path.expandvars(await server.get_missions_dir())
        configured = await server.getMissionList()
        files = await server.getAllMissionFiles()
        start = int(await server.getStartIndex() or 1)
        current_file = await _running_mission_file(server)
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


# ── Mission WEATHER: reading the current mission's own weather ──────────────
# The console's launch dialog reads the mission it is about to apply a preset to, so the operator
# starts from the mission's OWN weather instead of a blank form. The read is a READ action (a sibling
# of ``get_mission_list``): it resolves the mission against the SERVER'S OWN list, reads the bytes
# over the node, parses them with ``MizFile`` and builds the preset document through the SAME mapping
# ``/mission save_preset`` writes (``MizFile.preset_document``) — ONE mapping home.

#: every failure is its OWN plain sentence. The node-read failures mirror the console's node-read
#: routes (``services/webservice/pages/server_detail.py``); the action states WHAT failed and the
#: route renders it with its own status code.
MISSION_WEATHER_NO_MISSION = "Server '{server}' has no current mission to read."
MISSION_WEATHER_PATH_REFUSED = ("A mission is named by index or logical name, never by a path: "
                                "'{mission}' was refused.")
MISSION_WEATHER_UNKNOWN = "Mission '{mission}' not found on server '{server}'."
MISSION_WEATHER_INDEX_RANGE = "Mission index {mission} out of range (1-{last})."
MISSION_WEATHER_MISSING = ("Mission '{mission}' was gone before it could be read "
                           "(looked for '{path}').")
MISSION_WEATHER_PERMISSION = "Node '{node}' could not read mission '{mission}' (permission denied)."
MISSION_WEATHER_TIMEOUT = "Node '{node}' did not answer in time, so mission '{mission}' was not read."
MISSION_WEATHER_RAISED = "Node '{node}' could not hand over mission '{mission}' ({error})."
MISSION_WEATHER_FAILURE_CODE = ("Node '{node}' answered with a failure code instead of mission "
                                "'{mission}'.")
MISSION_WEATHER_UNSUPPORTED = ("The mission '{mission}' on server '{server}' is not a DCS mission "
                               "file this console can read.")
MISSION_WEATHER_UNREADABLE = ("The mission '{mission}' on server '{server}' carries no weather this "
                              "console can read.")


@dataclass
class MissionWeatherResult(ActionResult):
    """Result for the mission-weather READ (``read_mission_weather``).

    ``mission_name`` is the resolved mission's FILE name (what the launch dialog's line names) and
    ``document`` the mission's own weather as a MizEdit preset document — the SAME document
    ``/mission save_preset`` writes. ``status`` is the HTTP status a console route answers a FAILURE
    with, so no caller has to parse the sentence to pick one.
    """
    server_name: str = ""
    mission_name: str = ""
    document: dict[str, Any] = field(default_factory=dict)
    status: int = 200


def _plain_mission_name(value: str) -> bool:
    """Whether *value* is a mission NAME and not a PATH — one leaf, no directory component.

    The read names a mission by index or logical name; a value carrying either separator, an absolute
    lead or a bare ``.``/``..`` is a PATH and is refused before anything is resolved, so nothing a
    request states is ever joined to a directory.
    """
    if not value or value in (".", ".."):
        return False
    if "/" in value or "\\" in value:
        return False
    if os.path.isabs(value):
        return False
    return True


def _mission_file_name(path: Any) -> str:
    """The mission's FILE name — its basename with the ``.orig`` marker and ``.dcssb`` copy stripped."""
    return os.path.basename(utils.logical_mission_path(str(path)))


def _parse_mission_document(payload: bytes, node: Any) -> dict:
    """Parse *payload* — a mission archive's bytes — into its preset document, or raise.

    ``MizFile`` opens a LOCAL ``zipfile``, so the bytes are materialised to a temp file that is
    deleted after the parse. The file's weather is read against the SERVER's own DCS version (fog
    lives in ``fog2`` from 2.9.10 on), so ``node`` is the server's node. Raises
    :class:`UnsupportedMizFileException` for an archive ``MizFile`` cannot open and any other error
    for a mission whose weather cannot be read.
    """
    fd, tmp = tempfile.mkstemp(suffix=".miz")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        miz = MizFile(tmp)
        miz.node = node
        return miz.preset_document()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@action
async def read_mission_weather(ctx: Any, server_name: str,
                               mission: int | str = "") -> MissionWeatherResult:
    """The CURRENT mission's own weather, as a MizEdit preset document — a READ, never audited.

    ``mission`` is the LOGICAL mission: a 1-based INDEX into the server's ``missionList`` (the
    position :func:`_resolve_mission_id` / :func:`set_active_mission` take; a NUMERIC STRING is the
    same index, because a route query parameter is always a string) or a NAME (the logical name or
    its basename, with or without the ``.miz`` extension, case-insensitive), resolved against the
    server's OWN list. Empty reads the mission ``server.get_current_mission_file()`` names. A PATH is
    never accepted: nothing a request states is joined to a directory.

    The bytes come over ``server.node.read_file`` and are materialised to a temp file for the parse,
    which is deleted afterwards. The document is built by ``MizFile.preset_document`` — the SAME
    mapping ``/mission save_preset`` writes — and handed to the preset reader by the caller. Every
    failure answers a typed refusal with its own sentence, never a document of zeros or defaults.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionWeatherResult(success=False, status=404,
                                    message=f"Server '{server_name}' not found.",
                                    server_name=server_name)
    display = _server_label(server, server_name)
    node = getattr(server, "node", None)
    node_name = str(getattr(node, "name", "") or display)
    if node is None:
        return MissionWeatherResult(
            success=False, status=404, server_name=server_name,
            message=f"Node '{node_name}' is offline or unknown, so the mission cannot be read "
                    f"from this console.")
    try:
        mission_list = await server.getMissionList()
    except Exception as ex:
        log.exception("read_mission_weather failed")
        return MissionWeatherResult(success=False, status=502,
                                    message=f"Failed to list missions: {ex}",
                                    server_name=server_name)
    if isinstance(mission, bool):
        mission = ""
    elif isinstance(mission, str) and mission.strip().isdigit():
        # THE INDEX OVER HTTP: a route query parameter is ALWAYS a string, so a numeric string is the
        # 1-based INDEX itself — without this, ``?mission=2`` would fall down the NAME path and be
        # refused as an unknown name. ONE convention: 1-based, the one ``_resolve_mission_id`` and the
        # Missions tab's rows carry.
        mission = int(mission.strip())
    if isinstance(mission, int):
        if mission < 1 or mission > len(mission_list):
            return MissionWeatherResult(
                success=False, status=404, server_name=server_name,
                message=MISSION_WEATHER_INDEX_RANGE.format(mission=mission,
                                                           last=len(mission_list)))
        mission_file = str(mission_list[mission - 1])
    elif not mission:
        current = await server.get_current_mission_file()
        if not current:
            return MissionWeatherResult(success=False, status=404, server_name=server_name,
                                        message=MISSION_WEATHER_NO_MISSION.format(server=display))
        mission_file = os.path.normpath(str(current))
    else:
        wanted = str(mission).strip()
        if not _plain_mission_name(wanted):
            return MissionWeatherResult(
                success=False, status=400, server_name=server_name,
                message=MISSION_WEATHER_PATH_REFUSED.format(mission=mission))
        index = _resolve_mission_id(mission_list, wanted)
        if index is None:
            return MissionWeatherResult(
                success=False, status=404, server_name=server_name,
                message=MISSION_WEATHER_UNKNOWN.format(mission=wanted, server=display))
        mission_file = str(mission_list[index - 1])
    name = _mission_file_name(mission_file)
    try:
        content = await node.read_file(str(mission_file))
    except FileNotFoundError:
        return MissionWeatherResult(
            success=False, status=404, server_name=server_name,
            message=MISSION_WEATHER_MISSING.format(mission=name, path=mission_file))
    except PermissionError:
        return MissionWeatherResult(
            success=False, status=403, server_name=server_name,
            message=MISSION_WEATHER_PERMISSION.format(node=node_name, mission=name))
    except (TimeoutError, asyncio.TimeoutError):
        return MissionWeatherResult(
            success=False, status=504, server_name=server_name,
            message=MISSION_WEATHER_TIMEOUT.format(node=node_name, mission=name))
    except Exception as ex:
        log.exception("read_mission_weather failed")
        return MissionWeatherResult(
            success=False, status=502, server_name=server_name,
            message=MISSION_WEATHER_RAISED.format(node=node_name, mission=name,
                                                  error=type(ex).__name__))
    if not isinstance(content, (bytes, bytearray)):
        # ``read_file`` is ``bytes | int`` and an ``int`` is a FAILURE CODE, never content.
        return MissionWeatherResult(
            success=False, status=502, server_name=server_name,
            message=MISSION_WEATHER_FAILURE_CODE.format(node=node_name, mission=name))
    try:
        document = await asyncio.to_thread(_parse_mission_document, bytes(content), node)
    except UnsupportedMizFileException:
        return MissionWeatherResult(
            success=False, status=422, server_name=server_name,
            message=MISSION_WEATHER_UNSUPPORTED.format(mission=name, server=display))
    except Exception:
        log.exception("read_mission_weather failed")
        return MissionWeatherResult(
            success=False, status=422, server_name=server_name,
            message=MISSION_WEATHER_UNREADABLE.format(mission=name, server=display))
    return MissionWeatherResult(
        success=True, status=200, server_name=server_name, mission_name=name, document=document,
        message=f"Mission '{name}' on server '{display}'.")


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
    checked through ``utils.get_cached_mission_list(server)`` against BOTH the
    plain path and its ``.dcssb/<basename>`` spelling. Each entry's ``path`` is the
    missions-dir-relative path (exactly the value ``/mission add`` autocompletes), which is what a
    console form posts back. A READ: it is NOT audited.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return MissionListResult(success=False, message=f"Server '{server_name}' not found.",
                                 server_name=server_name)
    display = _server_label(server, server_name)
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
    display = _server_label(server, server_name)
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
    display = _server_label(server, server_name)
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
    running = await _running_mission_file(server)
    if running and os.path.normpath(str(filename)) == os.path.normpath(running):
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
# file from its own copy, so a file write made then is SILENTLY REVERTED — and The rule is that
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


async def _running_mission_file(server: Any) -> str:
    """The mission the server is RUNNING right now — ``""`` when it is not running one.

    ``get_current_mission_file`` alone cannot answer this: when no mission is loaded it FALLS BACK to
    the START mission (``missionList`` at ``listStartIndex``), which is the right answer for "what
    would start" and the wrong one for "what is running". Used as the running marker, that fallback
    kept the last mission flagged — and therefore undeletable — on a server that was stopped.

    So the STATUS gates it: only ``RUNNING`` and ``PAUSED`` have a mission loaded. ``LOADING`` has
    none yet, ``SHUTTING_DOWN`` is losing it, and the three down states have nothing loaded at all.
    """
    if server.status not in (Status.RUNNING, Status.PAUSED):
        return ""
    return await server.get_current_mission_file() or ""


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
    current_file = await _running_mission_file(server)
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
    display = _server_label(server, server_name)
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

