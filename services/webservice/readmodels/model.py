"""Plain view records for the read-only dashboard.

Pure data: no I/O, no ``core`` import in THIS module, no HTTP, no Discord call — the records are
plain frozen dataclasses, so they can be built and asserted on without the bot's runtime
dependencies. Every figure a template renders is an attribute of one of these records, so "every
number comes from a named source" is a property of the types rather than a promise in a comment.

THE STATUS VOCABULARY is the mockups' one — RUNNING / PAUSED / STOPPED / SHUTDOWN as a coloured
dot plus a word (:data:`STATUS_WORDS`). Two deliberate additions, both documented rather than
silent, because a status page that lies is worse than one that has five words:

* ``Shutting down`` and ``Unregistered`` are not a live server state a reader acts on, so they
  fold into SHUTDOWN (a dead/red dot);
* ``Loading`` gets its own word with the amber dot instead of being reported as RUNNING — a
  server that is still loading is not a server players can join.

A value nobody mapped renders ``UNKNOWN`` with the stopped dot. Never blank, never an exception:
an empty string reaches the template as a missing dot and a reader who cannot tell "off" from
"the read model broke".
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "STATUS_WORDS", "DEFAULT_STATUS", "RAW_LABELS",
    "raw_status", "status_view", "StatusView",
    "SourceStatus", "NodeView", "InstanceView", "ServerView", "PlayerView",
    "LogLine", "LogTail", "Overview",
    "NO_LIVE_REASON", "NO_SERVERS_MESSAGE", "NO_PLAYERS_MESSAGE", "NO_NODES_MESSAGE",
    "NO_INSTANCES_MESSAGE", "NO_NODE_INSTANCES_MESSAGE", "LOG_MISSING_MESSAGE",
    "LOG_EMPTY_MESSAGE", "LOG_LEVEL_EMPTY_MESSAGE", "SCOPED_EMPTY_MESSAGE",
]

#: raw ``Status`` value (lower-cased) -> (word, dot class). The dot classes are the shell
#: stylesheet's: run / pause / stop / dead.
STATUS_WORDS: dict[str, tuple[str, str]] = {
    "running": ("RUNNING", "run"),
    "paused": ("PAUSED", "pause"),
    "stopped": ("STOPPED", "stop"),
    "shutdown": ("SHUTDOWN", "dead"),
    "shutting down": ("SHUTDOWN", "dead"),
    "unregistered": ("SHUTDOWN", "dead"),
    "loading": ("LOADING", "pause"),
}

#: what an unmapped status renders as (see the module docstring)
DEFAULT_STATUS: tuple[str, str] = ("UNKNOWN", "stop")

#: the transient states, listed so a reader can see which ones folded and which did not
RAW_LABELS: tuple[str, ...] = tuple(STATUS_WORDS)

# --------------------------------------------------------------------------- empty-state copy
# One sentence per empty state, defined HERE (next to the records, not in a template) so a test
# can assert the copy the page renders and a translation would have one owner.

NO_LIVE_REASON = "Live state is not available yet. The bot is still starting up."
NO_SERVERS_MESSAGE = "No servers are registered on this cluster."
#: the SCOPE-specific empty state (spec §10.6). A hoster whose ``managed_by`` matches no server, or
#: whose scope could not be resolved, gets THIS instead of the two sentences above: "no servers are
#: registered on this cluster" would be false (and would disclose cluster-wide state), and "live
#: state is not available yet" would read as a broken console. It is section-neutral on purpose —
#: every table is empty for the same reason, and it names the one action that fixes it.
SCOPED_EMPTY_MESSAGE = ("This view shows only the servers your roles manage, and none of them are "
                        "here. An administrator can add your role to a server's managed_by if you "
                        "should be seeing one.")
NO_PLAYERS_MESSAGE = "No players are online right now."
NO_NODES_MESSAGE = "No nodes are registered."
NO_INSTANCES_MESSAGE = "No instances are configured."
NO_NODE_INSTANCES_MESSAGE = "No instances on this node."
LOG_MISSING_MESSAGE = "The bot log file is not available yet."
LOG_EMPTY_MESSAGE = "The bot log file is empty."
#: the file has lines, but none at the level the panel is filtered to — never an empty frame
LOG_LEVEL_EMPTY_MESSAGE = "No log lines match the selected level."


# --------------------------------------------------------------------------------- status


@dataclass(frozen=True, slots=True)
class StatusView:
    """One status: the word the page shows, its dot class, and the raw value it came from."""
    word: str
    dot: str
    raw: str


def raw_status(value) -> str:
    """The unwrapped status value as text: an enum becomes its ``.value``, anything else ``str``."""
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw)


def status_view(value) -> StatusView:
    """Map any status value onto the shell's vocabulary (never raises, never blank)."""
    raw = raw_status(value)
    word, dot = STATUS_WORDS.get(raw.strip().lower(), DEFAULT_STATUS)
    return StatusView(word=word, dot=dot, raw=raw)


# ---------------------------------------------------------------------------- source status


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """Whether the read models had live state to read, and how to say so when they did not.

    ``live`` is False for every ``bot is None`` / early-start / import-unavailable case; the page
    then renders :data:`NO_LIVE_REASON` plus each section's own empty message, instead of a
    traceback or a zero that looks like a measurement.
    """
    live: bool
    reason: str = ""
    node_name: str | None = None
    master: bool = False


# ----------------------------------------------------------------------------------- views

@dataclass(frozen=True, slots=True)
class NodeView:
    name: str
    master: bool
    online: bool
    instances: int
    servers: int
    players: int


@dataclass(frozen=True, slots=True)
class InstanceView:
    name: str
    node_name: str
    server_name: str | None
    dcs_port: int | None
    webgui_port: int | None


@dataclass(frozen=True, slots=True)
class ServerView:
    name: str
    node_name: str
    instance_name: str | None
    status: StatusView
    port: int | None
    mission_name: str | None
    mission_seconds: int
    players: int
    active_players: int
    remote: bool
    #: the server's MAINTENANCE FLAG (``servers.maintenance``) — the persisted switch that keeps a
    #: server out of service, read here so the ROW can say it and the row's flag control
    #: (``pages/actions``) can be gated on it. It is NOT part of :attr:`status`: a flag is not a
    #: power state, and the two are independent (``MAINTENANCE.md`` §1). Defaulted, so a producer
    #: that predates the field still builds a row.
    maintenance: bool = False

    @property
    def mission_label(self) -> str:
        """``2h14m`` / ``41m`` / ``12s`` — one rendering, so the page cannot format it twice."""
        return format_duration(self.mission_seconds)


@dataclass(frozen=True, slots=True)
class PlayerView:
    name: str
    ucid: str
    server_name: str
    slot: str
    active: bool


@dataclass(frozen=True, slots=True)
class LogLine:
    time: str
    level: str
    css: str
    text: str


@dataclass(frozen=True, slots=True)
class LogTail:
    path: str
    available: bool
    message: str
    lines: tuple[LogLine, ...]
    #: the level filter IN FORCE (a :data:`~.logtail.LEVEL_FILTERS` key), and the choices the panel
    #: offers as ``(key, label)`` pairs. On the record so the template reads the panel's state from
    #: the same source as its lines, instead of a second list typed into the markup.
    level: str = ""
    levels: tuple[tuple[str, str], ...] = ()


# ------------------------------------------------------------------------------ aggregation


@dataclass(frozen=True, slots=True)
class Overview:
    """Everything the dashboard renders, in one record — the page's only data source."""
    status: SourceStatus
    nodes: tuple[NodeView, ...]
    instances: tuple[InstanceView, ...]
    servers: tuple[ServerView, ...]
    players: tuple[PlayerView, ...]
    log: LogTail
    #: True when the view is empty BECAUSE OF THE CALLER'S SCOPE (``managed_by``): the source wrapper
    #: sets it, and the empty-state copy reads it so a hoster is told the truth instead of "no
    #: servers are registered on this cluster" (spec §10.6). False for a genuinely empty cluster and
    #: for an unscoped viewer.
    scoped_empty: bool = False

    @property
    def server_count(self) -> int:
        return len(self.servers)

    @property
    def running_count(self) -> int:
        return sum(1 for server in self.servers if server.status.word in ("RUNNING", "PAUSED"))

    @property
    def paused_count(self) -> int:
        return sum(1 for server in self.servers if server.status.word == "PAUSED")

    @property
    def player_count(self) -> int:
        return len(self.players)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def online_node_count(self) -> int:
        return sum(1 for node in self.nodes if node.online)

    @property
    def instance_count(self) -> int:
        return len(self.instances)


def format_duration(seconds: int | None) -> str:
    """A compact duration for a mission/population figure. ``0s`` for nothing, never blank."""
    total = int(seconds or 0)
    if total < 0:
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"