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
    "SourceStatus", "NodeView", "InstanceView", "ServerView", "PlayerView", "WeatherView",
    "LogLine", "LogTail", "Overview",
    "NO_LIVE_REASON", "NO_SERVERS_MESSAGE", "NO_PLAYERS_MESSAGE", "NO_NODES_MESSAGE",
    "NO_INSTANCES_MESSAGE", "NO_NODE_INSTANCES_MESSAGE", "LOG_MISSING_MESSAGE",
    "LOG_EMPTY_MESSAGE", "LOG_LEVEL_EMPTY_MESSAGE", "SCOPED_EMPTY_MESSAGE",
    "NO_MISSION_MESSAGE", "NO_WEATHER_MESSAGE", "NOT_RUNNING_MESSAGE",
]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED.

    See :func:`services.webservice.i18n._` — Babel reads the literal out of this module while the
    runtime call is an identity, so the record stays pure; the translation happens where the string
    is RENDERED (``{{ _(row.status.word) }}`` in the per-language template environment).
    """
    return message


#: raw ``Status`` value (lower-cased) -> (word, dot class). The dot classes are the shell
#: stylesheet's: run / pause / stop / dead. The word is marked translatable here (the row renders it
#: through the per-language environment), while the raw key stays the source vocabulary.
STATUS_WORDS: dict[str, tuple[str, str]] = {
    "running": (_("RUNNING"), "run"),
    "paused": (_("PAUSED"), "pause"),
    "stopped": (_("STOPPED"), "stop"),
    "shutdown": (_("SHUTDOWN"), "dead"),
    "shutting down": (_("SHUTDOWN"), "dead"),
    "unregistered": (_("SHUTDOWN"), "dead"),
    "loading": (_("LOADING"), "pause"),
}

#: what an unmapped status renders as (see the module docstring)
DEFAULT_STATUS: tuple[str, str] = (_("UNKNOWN"), "stop")

#: the transient states, listed so a reader can see which ones folded and which did not
RAW_LABELS: tuple[str, ...] = tuple(STATUS_WORDS)

# --------------------------------------------------------------------------- empty-state copy
# One sentence per empty state, defined HERE (next to the records, not in a template) so a test
# can assert the copy the page renders and a translation would have one owner.

NO_LIVE_REASON = _("Live state is not available yet. The bot is still starting up.")
NO_SERVERS_MESSAGE = _("No servers are registered on this cluster.")
#: the SCOPE-specific empty state (spec §10.6). A hoster whose ``managed_by`` matches no server, or
#: whose scope could not be resolved, gets THIS instead of the two sentences above: "no servers are
#: registered on this cluster" would be false (and would disclose cluster-wide state), and "live
#: state is not available yet" would read as a broken console. It is section-neutral on purpose —
#: every table is empty for the same reason, and it names the one action that fixes it.
SCOPED_EMPTY_MESSAGE = _("This view shows only the servers your roles manage, and none of them are "
                         "here. An administrator can add your role to a server's managed_by if you "
                         "should be seeing one.")
NO_PLAYERS_MESSAGE = _("No players are online right now.")
NO_NODES_MESSAGE = _("No nodes are registered.")
NO_INSTANCES_MESSAGE = _("No instances are configured.")
NO_NODE_INSTANCES_MESSAGE = _("No instances on this node.")
LOG_MISSING_MESSAGE = _("The bot log file is not available yet.")
LOG_EMPTY_MESSAGE = _("The bot log file is empty.")
#: the file has lines, but none at the level the panel is filtered to — never an empty frame
LOG_LEVEL_EMPTY_MESSAGE = _("No log lines match the selected level.")

# --- the SERVER OVERVIEW page's honest empty states (one sentence per state, as above). They say
# what is MISSING on a server card, never a blank cell a reader could mistake for a broken render.
#: no mission is loaded on the server (a STOPPED/SHUTDOWN server, or one between missions)
NO_MISSION_MESSAGE = _("No mission is loaded.")
#: the server is up but the bot reports no weather for it (a mission without a weather block, or a
#: server that has not reported one yet) — the weather half of a card, never its whole
NO_WEATHER_MESSAGE = _("No weather is reported.")
#: the card's one-line substitute for the whole mission/weather/runtime block when the server is not
#: up: those facts do not exist for a server that is not running, and zeroes would read as figures
NOT_RUNNING_MESSAGE = _("This server is not running.")


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
    #: the link to the bound server's own page (``/fleet/<encoded-name>``), built by
    #: :func:`~.servers.server_url`. Empty when the instance is bound to no server — the name then
    #: renders as plain text.
    link: str = ""


@dataclass(frozen=True, slots=True)
class WeatherView:
    """One server's weather, in the same three facts the bot's status embed shows.

    THE FIELD SELECTION MIRRORS ``plugins/mission/reports/serverStatus.json``'s ``WeatherInfo``
    element (see ``readmodels.serveroverview`` and its parity test): a PRESET, a TEMPERATURE and a
    WIND, each already formatted for display — the read model owns the unit conversion and the
    rounding exactly as the embed's element does, so a page and the embed cannot round differently.
    Every field is an empty string when the bot did not report it; the card renders the empty state
    (:data:`NO_WEATHER_MESSAGE`) rather than a zero.
    """
    #: the cloud/weather preset ("Clear", "Dynamic", or a preset's own name), or ``''``
    preset: str = ""
    #: the mission's season temperature, e.g. ``"18.2 °C"``, or ``''``
    temperature: str = ""
    #: the surface wind, e.g. ``"180° / 5 kts"``, or ``''``
    wind: str = ""


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
    #: the link to the server's own page (``/fleet/<encoded-name>``), built by
    #: :func:`~.servers.server_url` — the affordance that opens the row. Empty renders plain text.
    link: str = ""
    #: the name the operator reads (``Server.display_name``, the DCS-side name), falling back to
    #: :attr:`name`. Read by the SERVER OVERVIEW page's card title; the tables keep using
    #: :attr:`name` (the identity every route and link keys on). Defaulted for the same reason as
    #: ``maintenance``.
    display_name: str = ""
    #: the slot ceiling (``settings.maxPlayers``), or ``None`` when the source does not carry it.
    #: The overview card renders the player count as ``used / max`` like the embed's author line.
    max_players: int | None = None
    #: the current map (``Mission.map``), or ``None``. An embed field the overview card mirrors.
    map_name: str | None = None
    #: the formatted mission clock the card shows as its runtime/uptime — the SAME
    #: ``Mission.mission_time`` :attr:`mission_label` formats, exposed as its own field so the card
    #: reads the embed's "Runtime" fact rather than re-deriving it. Defaulted to ``''``.
    runtime: str = ""
    #: the server's weather as the bot reports it, or ``None`` when the mission carries none. The
    #: overview card's weather block; ``None`` renders :data:`NO_WEATHER_MESSAGE`.
    weather: WeatherView | None = None
    #: the node's publicly reachable IP (``node.public_ip``), or ``''``. The overview card renders it
    #: together with :attr:`port` as ``ip:port``, mirroring the embed's per-server block
    #: (``plugins/mission/status.ServerInfo`` / ``All``). Defaulted for the same reason as the other
    #: late fields, and empty for a source that carries no ``public_ip``.
    public_ip: str = ""
    #: whether a PASSWORD is required to join (``settings.password`` non-empty). The overview card
    #: renders a LOCK INDICATOR only — never the password VALUE (the web page has no
    #: ``show_passwords`` gate to honour, so it errs on the side of never disclosing the cleartext),
    #: which is why this is a boolean and not the string. ``False`` renders no indicator.
    locked: bool = False
    #: the formatted time until the scheduled restart (``utils.format_time``, the same formatter the
    #: embed's per-server block uses), or ``''`` when no restart is scheduled or the server is in
    #: maintenance. The overview card shows it while the server is running. Defaulted to ``''``.
    restart_in: str = ""

    @property
    def running(self) -> bool:
        """Whether a player can join: RUNNING or the deliberate PAUSED. The ONE statement of the
        fact, read by the overview card and by :attr:`Overview.running_count` so the count and the
        cards cannot disagree. LOADING is NOT up (a server still loading is not joinable); SHUTDOWN,
        STOPPED and UNREGISTERED are not either.
        """
        return self.status.word in ("RUNNING", "PAUSED")

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
    #: the link to the own page of the server the player is on (``/fleet/<encoded-name>``), built
    #: by :func:`~.servers.server_url`. Empty renders the name as plain text.
    link: str = ""


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
        return sum(1 for server in self.servers if server.running)

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