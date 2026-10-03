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
class MissionUploadResult(ActionResult):
    """Result for a mission UPLOAD (the ``upload_mission`` action).

    Carries the bot's own typed outcome through untouched (``upload_status`` is the
    :class:`core.data.node.UploadStatus` NAME — ``OK`` / ``FILE_EXISTS`` / ``FILE_IN_USE`` /
    ``READ_ERROR`` / ``WRITE_ERROR``), so a transport reports what the ONE writer reported instead
    of inventing its own success rule.

    * ``mission_name`` — the logical name (``.orig`` / ``.dcssb`` stripped), what a caller shows.
    * ``filename`` — the validated destination filename inside ``missions_dir`` (a leaf), so a caller
      that wants to load it right after does not rebuild the path itself.
    """
    server_name: str = ""
    mission_name: str = ""
    filename: str = ""
    upload_status: str = ""


@dataclass
class MissionDownloadResult(ActionResult):
    """Result for a mission DOWNLOAD (the ``download_mission`` action).

    The BYTES ride this result (``content``) together with the LOGICAL filename to stream
    (``filename``, ``.orig`` / ``.dcssb`` stripped) — the resolution and the path validation both
    happen inside the action, so every caller streams the same bytes under the same name and none
    of them re-resolves the newest copy.

    ``content`` is a FIELD and not part of ``data``, so :meth:`ActionResult.to_dict` stays
    JSON-serialisable for a transport that only wants the outcome.
    """
    server_name: str = ""
    mission_name: str = ""
    filename: str = ""
    content: bytes = b""


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
    """Result for node lifecycle operations (restart / shutdown / upgrade).

    Carries the node the operation was about, which every transport names back; a field stops it
    drifting from the message it was built with.
    """
    node_name: str = ""


@dataclass
class ServerConfigResult(ActionResult):
    """Result for a per-server CONFIGURATION operation (the DCS face).

    * ``refused`` — a WHOLE-ACTION typed reason, set only when the action did nothing at all (the
      caller is not an Admin, the name is unknown, or the server is up). When it is set,
      ``applied``/``skipped`` are empty.
    * ``applied`` — ``{key: {"from": …, "to": …}}`` for every key that WAS written, so a caller can
      offer a revert. A SECRET key's two sides are the redacted sentinels, never the value.
    * ``skipped`` — ``{key: reason}`` for the keys that could not be written.
    * ``ok`` — alias for the base :attr:`success` flag.

    Never carries a secret VALUE: the audit line and the message name the KEYS.
    """
    server_name: str = ""
    refused: str | None = None
    applied: dict[str, Any] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Alias for :attr:`success`."""
        return self.success

    def to_dict(self) -> dict[str, Any]:
        """The base transport shape plus the config fields, so REST/MCP callers see the outcome."""
        out = super().to_dict()
        out.setdefault("server_name", self.server_name)
        out.setdefault("refused", self.refused)
        out.setdefault("applied", self.applied)
        out.setdefault("skipped", self.skipped)
        return out
