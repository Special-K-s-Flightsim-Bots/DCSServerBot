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


@dataclass
class ServerConfigResult(ActionResult):
    """Result for a per-server CONFIGURATION operation (the DCS face, ``CONFIGURATION.md`` §4.2).

    Its own record rather than a bare :class:`ActionResult` for the same reason
    :class:`NodeControlResult` has one — every transport reads the fields back. The four extras are
    the design's; the shapes are deliberate:

    * ``refused`` — a WHOLE-ACTION typed reason. It is set only where the action did nothing at all:
      the caller is not an Admin (§6), the name is unknown (the seam's own refusal), or the server is
      up (§5.3, D4). When it is set, ``applied``/``skipped`` are empty.
    * ``applied`` — ``{key: {"from": …, "to": …}}`` for every key that WAS written, so the console can
      offer a revert through this same action (§8.2). A SECRET key's ``from``/``to`` are the redacted
      sentinels (§7), never the value — a revert of a secret is a re-submission, not a re-print.
    * ``skipped`` — ``{key: reason}`` for the keys that could not be written: not an editable setting,
      a type/range failure, a ``unique`` sequence with duplicates, or a key pinned by an F3 override.
    * ``ok`` — the card's spelling of the base :attr:`success` flag.

    Nothing here ever carries a secret VALUE (§7.4): the audit line and the message name the KEYS.
    """
    server_name: str = ""
    refused: str | None = None
    applied: dict[str, Any] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The card's name for :attr:`success`."""
        return self.success

    def to_dict(self) -> dict[str, Any]:
        """The base transport shape plus the config fields, so REST/MCP callers see the outcome."""
        out = super().to_dict()
        out.setdefault("server_name", self.server_name)
        out.setdefault("refused", self.refused)
        out.setdefault("applied", self.applied)
        out.setdefault("skipped", self.skipped)
        return out
