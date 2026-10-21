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

import re
from dataclasses import dataclass

__all__ = [
    "STATUS_WORDS", "DEFAULT_STATUS", "RAW_LABELS",
    "raw_status", "status_view", "StatusView",
    "SourceStatus", "NodeView", "InstanceView", "ServerView", "PlayerView", "WeatherView",
    "ExtensionView", "LimitView", "ValuePart", "value_parts",
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
    #: the altimeter setting: ``"1013 hPa / 29.92 inHg"``, or ``''``. The two figures are ALSO
    #: carried separately (:attr:`qnh_hpa` / :attr:`qnh_inhg`) for a card that stacks them.
    qnh: str = ""
    qnh_hpa: str = ""
    qnh_inhg: str = ""
    #: the cloud base, e.g. ``"1,000 ft"``, or ``''``
    cloudbase: str = ""
    #: the DYNAMIC weather's own two figures (``"4"`` and ``"1,000 ft"``), or ``''`` for a named
    #: preset and for clear skies — a preset carries no density or thickness of its own.
    density: str = ""
    thickness: str = ""
    #: the mission's visibility, e.g. ``"10,000 m / 6.21 SM"`` or ``"10 km / 6 SM (+)"``, or ``''``.
    #: The mission's OWN figure; a live server's fog reading is a page-level override (it needs the
    #: bot's ``getFog`` RPC, which a read model must not await).
    visibility: str = ""
    #: the wind at 6600 ft and at 26000 ft, same shape as :attr:`wind`, or ``''``
    wind_low: str = ""
    wind_high: str = ""
    #: the preset THUMBNAIL's path **relative to the DCS installation directory**
    #: (``clouds.preset.thumbnailName``, e.g. ``"Bazar/Effects/Clouds/Thumbnails/cloud_2.png"``), or
    #: ``''``. The bot reports it with the weather, and the picture itself sits in the GAME host's
    #: installation — not under the web root — so the page serves it through its own thumbnail route
    #: instead of putting a host path in ``<img src>``. A reported name that escapes that directory is
    #: refused by the route, never resolved.
    thumbnail: str = ""


@dataclass(frozen=True, slots=True)
class ExtensionView:
    """One extension as the server reports it: its NAME, its VALUE line and its version.

    Mirrors one entry of the bot's extension render (``Server.render_extensions``), which the status
    embed's ``ExtensionsInfo`` element prints as one field per extension. ``version`` is empty for an
    extension that reports none — the card then shows the value alone rather than a bare ``v``.
    """
    name: str = ""
    value: str = ""
    version: str = ""
    #: the value line split into TEXT and LINK parts (see :func:`link_parts`), so an extension that
    #: prints a URL renders it as a link the reader can press. ``value`` stays the raw line.
    parts: tuple[ValuePart, ...] = ()


@dataclass(frozen=True, slots=True)
class ValuePart:
    """One run of an extension's value line: TEXT, or text wearing a LINK, BOLD or ITALIC.

    ``href`` is always an absolute ``http(s)`` URL — the parser refuses every other scheme — and
    ``style`` is ``''``, ``'bold'`` or ``'italic'``. ``text`` is what the reader sees (the markdown
    label, or the URL itself for a bare one). The template renders a link as an anchor with
    ``target="_blank" rel="noopener noreferrer"``, a style as ``<strong>``/``<em>``, and everything
    else through Jinja's autoescaping, so an extension's own text can never become markup.
    """
    text: str = ""
    href: str = ""
    style: str = ""


#: The formatting an extension's value line may carry, in the order it is tried: a markdown LINK
#: ``[label](https://…)``, ``**bold**``, ``__italic__``, or a bare ``https://…`` URL. ONLY http(s)
#: matches a link, which is what keeps ``javascript:`` / ``data:`` / ``file:`` out of an href built
#: from operator data. Bold and italic must be NON-SPACE bounded (``**x**``, never ``** x **``) and
#: stay on ONE line, so ordinary prose — a stray ``*``, a multiplication, a snake_case key — is left
#: alone. Deliberately NOT supported: single ``*``/``_`` italic (the snake_case trap above), and
#: nesting (a link inside a bold run is bold text, not a link).
_MARKUP = re.compile(r"\[(?P<label>[^\]\n]+)\]\((?P<markdown>https?://[^\s)]+)\)"
                     r"|(?P<bold>\*\*(?=\S)(?P<bold_text>[^\n]+?)(?<=\S)\*\*)"
                     r"|(?P<italic>__(?=\S)(?P<italic_text>[^\n]+?)(?<=\S)__)"
                     r"|(?P<bare>https?://[^\s<>\"']+)")

#: punctuation a BARE url must not swallow when it ends a sentence ("see https://x.example." )
_TRAILING = ".,;:!?"


def value_parts(value) -> tuple[ValuePart, ...]:
    """An extension's value line as TEXT, LINK, BOLD and ITALIC parts, newlines included.

    The bot's own extensions write DISCORD formatting and links into their value lines (Tacview
    prints ``**Remote Ctrl [52675]**``, a kneeboard prints a markdown link), and rendering those
    verbatim leaves the operator reading asterisks and unable to open the link. Splitting the line
    into parts keeps the escaping with the template: only what the PARSER recognised is dressed, and
    every other character of the extension's own text is escaped as it always was.
    """
    text = str(value or "")
    if not text:
        return ()
    parts: list[ValuePart] = []
    cursor = 0
    for match in _MARKUP.finditer(text):
        if match.start() > cursor:
            parts.append(ValuePart(text=text[cursor:match.start()]))
        if match.group("markdown"):
            parts.append(ValuePart(text=match.group("label"), href=match.group("markdown")))
        elif match.group("bold"):
            parts.append(ValuePart(text=match.group("bold_text"), style="bold"))
        elif match.group("italic"):
            parts.append(ValuePart(text=match.group("italic_text"), style="italic"))
        else:
            href = match.group("bare")
            trailing = ""
            while href and href[-1] in _TRAILING:
                trailing = href[-1] + trailing
                href = href[:-1]
            parts.append(ValuePart(text=href, href=href))
            if trailing:
                parts.append(ValuePart(text=trailing))
        cursor = match.end()
    if cursor < len(text):
        parts.append(ValuePart(text=text[cursor:]))
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class LimitView:
    """One anti-cheat switch: its NAME, whether it is in force, and which family it belongs to.

    ``kind`` is ``"integrity"`` (the client-integrity flags: trial clients, pure clients/scripts/
    models/textures) or ``"export"`` (the DCS data-export switches, which external cheat tools read
    the mission through). The family matters because the two read OPPOSITE ways — an integrity flag
    in force PROTECTS the server, an export switch in force OPENS it — so the card labels the two
    groups rather than leaving a bare tick to be misread.
    """
    label: str = ""
    enabled: bool = False
    kind: str = "integrity"


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
    #: the mission's own clock as a reader reads it — ``Mission.date`` + ``start_time`` + the elapsed
    #: ``mission_time``, formatted exactly as the embed's "Date / Time in Mission" field does, or
    #: ``''``. The embed puts the runtime/restart pair in that same field; the card shows
    #: :attr:`runtime` and :attr:`restart_in` instead, which are the same two figures.
    mission_date: str = ""
    #: the seats in use per side — ``"1 / 20"`` for blue and for red — for a server whose coalitions
    #: are DCS-side. Empty when the mission reports no slot counts.
    slots_blue: str = ""
    slots_red: str = ""
    #: whether this server drives DISCORD coalition roles (``server.locals['coalitions']``). The
    #: embed prints "Coalitions: Yes" INSTEAD of the slot counts in that case, because the seats are
    #: assigned by the role switch rather than by the mission; the card does the same.
    coalitions: bool = False
    #: the anti-cheat switches this server runs with — the client-integrity flags the embed's
    #: "Client Limits" field lists AND the DCS data-export switches, each row carrying whether it is
    #: IN FORCE. ALL of them are present, so a reader can see what is off as clearly as what is on:
    #: the embed only prints the flags that are set, which hides the holes in a server's protection.
    restrictions: tuple[LimitView, ...] = ()
    #: the DCS build the server's node reports (``node.dcs_version``), or ``''``. Half of the embed's
    #: footer; the other half, the bot's own version, belongs to the console and is read there.
    dcs_version: str = ""

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
    #: the side the player is on (``Red``/``Blue``/``Neutral``), read from ``Player.side``. EMPTY for
    #: a player who has not picked one — the row then renders its "nothing here" placeholder rather
    #: than a side the player is not on.
    coalition: str = ""


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