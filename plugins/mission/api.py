"""The mission plugin's ``/api/v1`` rows — one row per exposed action.

A row names an action by QUALNAME and declares its HTTP shape; the parameters are derived from the
action's own signature (``core.action_surface.parameters_for``), so a row cannot disagree with the
action it names. Paths are RELATIVE to the transport prefix: no ``/api/v1`` leads a path here.

Nothing is mounted from this module. It is imported by ``discover_endpoints``, which registers the
rows and validates them; a transport builds its routes from ``endpoint_rows()`` afterwards.
"""
from __future__ import annotations

from core.action_surface import Risk, api_v1


# ── Reads ───────────────────────────────────────────────────────────────────

@api_v1.get("/servers", action="list_servers", risk=Risk.READ)
async def _servers() -> None:
    """List every DCS server with its status and running mission."""


@api_v1.get("/servers/{server_name}", action="get_server_status", risk=Risk.READ)
async def _server_status() -> None:
    """One server's status, mission, map and player count."""


@api_v1.get("/servers/{server_name}/mission", action="get_mission_info", risk=Risk.READ)
async def _mission_info() -> None:
    """The mission currently running on a server."""


@api_v1.get("/servers/{server_name}/missions", action="get_mission_list", risk=Risk.READ)
async def _mission_list() -> None:
    """A server's configured missions, its rotation state and the 1-based active index."""


@api_v1.get("/servers/{server_name}/mission/weather", action="read_mission_weather",
            risk=Risk.READ, mcp=False)
async def _mission_weather() -> None:
    """A mission's own weather as a MizEdit preset document."""


# ── Writes ──────────────────────────────────────────────────────────────────

@api_v1.post("/servers/{server_name}/start", action="start_server", risk=Risk.LIFECYCLE, mcp=False)
async def _start() -> None:
    """Start a server whose instance is STOPPED. A SHUTDOWN server needs startup_server."""


@api_v1.post("/servers/{server_name}/startup", action="startup_server", risk=Risk.LIFECYCLE)
async def _startup() -> None:
    """Boot a SHUTDOWN server (the DCS-level Startup). For a STOPPED server use start_server."""


@api_v1.post("/servers/{server_name}/stop", action="stop_server", risk=Risk.LIFECYCLE, mcp=False)
async def _stop() -> None:
    """Stop a running or paused DCS server."""


@api_v1.post("/servers/{server_name}/shutdown", action="shutdown_server", risk=Risk.LIFECYCLE)
async def _shutdown() -> None:
    """Shut a running or paused server down to SHUTDOWN. stop_server ends the process instead."""


@api_v1.post("/servers/{server_name}/restart", action="restart_server", risk=Risk.LIFECYCLE)
async def _restart() -> None:
    """Restart a running, paused or stopped server. A SHUTDOWN server needs startup_server."""


@api_v1.post("/servers/{server_name}/mission/pause", action="pause_mission", risk=Risk.LIFECYCLE)
async def _pause() -> None:
    """Pause the running mission on a server."""


@api_v1.post("/servers/{server_name}/mission/unpause", action="unpause_mission", risk=Risk.LIFECYCLE)
async def _unpause() -> None:
    """Unpause the paused mission on a server."""


@api_v1.post("/servers/{server_name}/mission/restart", action="restart_mission", risk=Risk.LIFECYCLE)
async def _restart_mission() -> None:
    """Restart the current mission on a server."""


@api_v1.post("/servers/{server_name}/mission/rotate", action="rotate_mission", risk=Risk.LIFECYCLE)
async def _rotate_mission() -> None:
    """Rotate to the next mission in the server's rotation."""


@api_v1.post("/servers/{server_name}/mission/load", action="load_mission", risk=Risk.LIFECYCLE)
async def _load_mission() -> None:
    """Load a specific mission on a server."""
