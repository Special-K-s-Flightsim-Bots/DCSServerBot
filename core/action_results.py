"""
Action result dataclasses — shared return types for all transports.

Each action returns a dataclass with a consistent interface:
  - success: bool
  - message: str  (human-readable, safe to show in any UI)
  - data: dict    (structured data for programmatic consumers)

Transports wrap these into their response format:
  - Discord: interaction.followup.send(result.message)
  - REST:    {"status": "ok"/"error", "message": ..., **result.data}
  - MCP:     {"status": "ok"/"error", "message": ..., **result.data}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionResult:
    """Base result for all actions."""
    success: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.success else "error",
            "message": self.message,
            **self.data,
        }


@dataclass
class ServerControlResult(ActionResult):
    """Result for server start/stop/restart operations."""
    server_name: str = ""


@dataclass
class MissionControlResult(ActionResult):
    """Result for mission pause/unpause/restart/rotate operations."""
    server_name: str = ""


@dataclass
class MissionLoadResult(ActionResult):
    """Result for mission load operation."""
    server_name: str = ""
    mission_name: str = ""


@dataclass
class MissionListResult(ActionResult):
    """Result for mission list queries."""
    server_name: str = ""
    missions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ServerInfoResult(ActionResult):
    """Result for server info queries."""
    server_name: str = ""
    status: str = ""
    mission_name: str = ""
    players_online: int = 0


@dataclass
class PlayerActionResult(ActionResult):
    """Result for player ban/unban operations."""
    player_name: str = ""
    ucid: str = ""


@dataclass
class NodeControlResult(ActionResult):
    """Result for node lifecycle operations (restart / shutdown / upgrade — W4c-revised).

    Its own record rather than a bare :class:`ActionResult` because every node transport names its
    target back: a Discord follow-up, an MCP tool result and the console's banner all read the node
    the operation was about, and a second field cannot drift from the message it was built with.
    """
    node_name: str = ""
