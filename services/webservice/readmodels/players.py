"""Players: the live roster, and the database's three per-player reads.

``players_online`` — read from the servers' in-process player dictionaries.

The bot holds every connected player on the server object (``Server.players``, keyed by UCID), with
``active`` marking the ones actually sitting in a slot. That is the authoritative "online right
now" set and it costs nothing to read; nothing is queried, awaited or asked of another node.

WHY THERE IS NO PING AND NO CONNECT TIME. ``Player`` carries no ping and no connect timestamp — the
DCS broadcast that fills the object has neither — so the mockup's *Ping* and *Connected* columns are
omitted rather than filled with a plausible-looking figure. Every number the page renders has a
source; those two have none. Restoring them is a read-model change (a heartbeat/last_seen column),
not a template change.

THE DATABASE READS — ``directory_view``, ``player_detail``, ``player_statistics`` — shape what the
console's ``list_player_directory`` / ``get_player_detail`` / ``get_player_statistics`` actions hand
over. They are PURE like every other model here: the SQL lives in the action, and this module reads
the returned records and renders them. Three rules they all obey:

* a player is a DISCORD LINK, not a UCID. The players table is one row per UCID, so every UCID the
  same ``discord_id`` names is ONE player (an unlinked UCID, ``discord_id = -1``, is its own);
* the NAME is the source's own ``name``, never ``Player.display_name`` (the Discord-ESCAPED form);
* a value the source does not have renders as NOTHING. A null playtime is an empty cell, never
  ``0 h``, and a null name is an empty cell, never ``n/a`` — a placeholder that looks like a fact is
  worse than a blank.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .access import attr, safe, text
from .bans import ALL_SERVERS, NEVER, STAMP_FORMAT, permanent
from .model import NO_PLAYERS_MESSAGE, PlayerView
from .servers import server_url

__all__ = ["players_online", "player_views", "playtime_text",
           "DirectoryRow", "DirectoryView", "directory_view",
           "IdentityEntry", "BanState", "WatchState", "PlayerDetailView", "player_detail",
           "PlaytimeEntry", "CountEntry", "SessionEntry", "PlayerStatisticsView",
           "player_statistics",
           "InfractionEntry", "PenaltyEntry", "BanHistoryEntry", "PlayerInfractionsView",
           "player_infractions", "ActivityView", "player_activity"]


def _(message: str) -> str:
    """Mark a string as translatable for the extractor; return it UNCHANGED.

    See :func:`services.webservice.i18n._` — Babel reads the literal out of this module while the
    runtime call is an identity, so the record stays pure; the translation happens where the string
    is RENDERED, in the per-language template environment.
    """
    return message


#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_PLAYERS_MESSAGE


#: The seat a player currently occupies is read from ``unit_display_name`` ALONE — never
#: ``unit_type``. ``unit_type`` is DCS's own coarse bucket (``"Planes"`` for anything airborne) and
#: says nothing about the seat, so falling back to it printed "Planes" for a player sitting in NO
#: slot at all. ``unit_display_name`` is the type name of the occupied aircraft ("A-10C II"); DCS
#: reports ``"?"`` while a side is chosen but no seat is taken, and ``""`` before that, and neither
#: is a seat.
NO_SEAT = ("", "?", "Planes")


def _slot(player) -> str:
    """The seat a player occupies: ``A-10C II · Hog 1`` when both are known, else whichever is.

    An unreadable or empty seat is the EMPTY string, which the table renders as its own "nothing
    here" placeholder — the same treatment every other absent cell gets.
    """
    unit = text(attr(player, "unit_display_name", None))
    if unit in NO_SEAT:
        unit = ""
    group = text(attr(player, "group_name", None))
    if unit and group:
        return f"{unit} · {group}"
    return unit or group


def _coalition(player) -> str:
    """The side the player is on — ``Red``/``Blue``/``Neutral``, or the empty string.

    ``Player.side`` is the DCS side (``core.data.const.Side``); ``Side.UNKNOWN`` and a double that
    carries no side at all both read as the empty string, so a row never claims a side a player has
    not picked.
    """
    side = attr(player, "side", None)
    name = text(getattr(side, "name", "")).title()
    return "" if name in ("", "Unknown") else name


def player_views(server_name: str, players) -> list[PlayerView]:
    """The active players of ONE server, sorted by name."""
    out: list[PlayerView] = []
    for player in (players or {}).values():
        if not attr(player, "active", False):
            continue
        out.append(PlayerView(
            name=text(attr(player, "name", None)),
            ucid=text(attr(player, "ucid", None)),
            server_name=server_name,
            slot=safe(lambda: _slot(player), "") or "",
            coalition=safe(lambda: _coalition(player), "") or "",
            active=True,
            # the server the player is on is the link to its page, built from the same name the row
            # renders — a player row exists only for a server that survived the scope.
            link=server_url(server_name) if server_name else "",
        ))
    out.sort(key=lambda view: view.name)
    return out


def players_online(source) -> list[PlayerView]:
    """Every ACTIVE player of every server, sorted by server then name."""
    out: list[PlayerView] = []
    for server in getattr(source, "servers", ()) or ():
        server_name = text(attr(server, "name", None))
        out.extend(safe(lambda: player_views(server_name, attr(server, "players", {})), []) or [])
    out.sort(key=lambda view: (view.server_name, view.name))
    return out


# ── shared shaping ─────────────────────────────────────────────────────────


def _field(entry: Any, name: str) -> Any:
    """One field of *entry*, whether the read handed over a mapping or a duck-typed record."""
    if isinstance(entry, Mapping):
        return entry.get(name)
    return getattr(entry, name, None)


def _stamp(value: Any) -> str:
    """A naive-UTC timestamp as the console's rendered stamp — ``''`` when it is not one.

    The ONE stamp layout of the console (:data:`readmodels.bans.STAMP_FORMAT`), so a player's
    ``last seen`` cell and a ban's ``expires`` cell cannot disagree about how a time reads.
    """
    if not isinstance(value, datetime):
        return ""
    return value.strftime(STAMP_FORMAT)


def _iso(value: Any) -> str:
    """A timestamp's ISO text — the value the table SORTS by (``''`` when it is not a timestamp)."""
    if not isinstance(value, datetime):
        return ""
    return value.isoformat()


def _seconds(value: Any) -> int | None:
    """A stored duration in whole seconds, or ``None`` when the source has none.

    ``None`` is NOT zero: a player with no statistics row has never flown, and a directory that
    printed ``0 h`` for them would state a measurement nobody made.
    """
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def playtime_text(seconds: Any) -> str:
    """A duration as the console renders it: ``182 h 41 m``, ``41 m``, ``12 s`` — ``''`` for none.

    Hours and minutes are the units a playtime is read in; a duration under an hour keeps the
    minute, one under a minute keeps the second rather than rounding to ``0 m``. An ABSENT duration
    is the empty string: the console's own ``format_duration`` answers ``0s`` for it, which is a
    placeholder dressed as a fact.
    """
    total = _seconds(seconds)
    if total is None:
        return ""
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes} m"
    if minutes:
        return f"{minutes} m"
    return f"{secs} s"


def _state_tokens(banned: bool, watched: bool, vip: bool) -> str:
    """The row's state as the SORTABLE token: ``banned``/``watched``/``vip``, in that order.

    The tokens are the sort cell, not a rendered label: the page draws its own chips from the three
    booleans beside them, so a translated chip word cannot change the order. The order is fixed
    (ban, then watch, then VIP) so a list can be read without opening a page.
    """
    return " ".join(token for token, present in (("banned", banned), ("watched", watched),
                                                ("vip", vip)) if present)


# ── 1. the directory ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DirectoryRow:
    """One PLAYER of the directory: every UCID a Discord link groups, and its moderation state.

    ``ucids`` holds every UCID the player is known by, ``ucid`` the first of them (the stable key the
    row's own link is built from) and ``ucid_count`` how many there are. ``discord_id`` is the number
    (``-1`` for an unlinked player, which is also what the column SORTS by), ``discord`` its text
    form — empty for an unlinked player, because ``-1`` is a sentinel and not an id. The linked
    member's name is deliberately NOT here: the directory is a database read, and resolving a member
    per row would turn one render into a burst of Discord calls.
    """
    key: str
    name: str
    ucids: tuple[str, ...]
    ucid: str
    ucid_count: int
    discord_id: int
    discord: str
    first_seen: str
    last_seen: str
    last_seen_at: str
    playtime_seconds: int | None
    playtime: str
    banned: bool
    watched: bool
    vip: bool
    state: str


@dataclass(frozen=True, slots=True)
class DirectoryView:
    """The directory PAGE: its rows, the total behind them, and where in the range they sit.

    ``total`` counts every player the caller's scoped read holds AFTER the search, while ``rows`` is
    only the current page, so the page can state ``50 of 1,284`` without a second read.
    """
    rows: tuple[DirectoryRow, ...]
    total: int
    page: int
    pages: int
    per_page: int
    query: str

    @property
    def shown(self) -> int:
        return len(self.rows)

    @property
    def label(self) -> str:
        """The one statement of the range, in words (the template renders it, never composes it)."""
        return f"{self.shown} of {self.total} players"


def _directory_row(entry: Any) -> DirectoryRow:
    """One raw directory record as the table reads it."""
    raw_ucids = _field(entry, "ucids") or ()
    ucids = tuple(item for item in (text(ucid) for ucid in raw_ucids) if item)
    raw_names = _field(entry, "names") or ()
    names = tuple(text(name) for name in raw_names if text(name))
    discord_id = _field(entry, "discord_id")
    try:
        discord_number = int(discord_id)
    except (TypeError, ValueError):
        discord_number = -1
    ucid = text(_field(entry, "ucid")) or (ucids[0] if ucids else "")
    banned = bool(_field(entry, "banned"))
    watched = bool(_field(entry, "watched"))
    vip = bool(_field(entry, "vip"))
    seconds = _seconds(_field(entry, "playtime"))
    return DirectoryRow(
        key=text(_field(entry, "player_key")) or ucid,
        # the source's own name; the read orders the group by ``last_seen`` so the first is the
        # name the player last used. Never ``display_name`` (that is the Discord-ESCAPED form).
        name=names[0] if names else "",
        ucids=ucids,
        ucid=ucid,
        ucid_count=len(ucids),
        discord_id=discord_number,
        discord="" if discord_number < 0 else str(discord_number),
        first_seen=_stamp(_field(entry, "first_seen")),
        last_seen=_stamp(_field(entry, "last_seen")),
        last_seen_at=_iso(_field(entry, "last_seen")),
        playtime_seconds=seconds,
        playtime=playtime_text(seconds),
        banned=banned,
        watched=watched,
        vip=vip,
        state=_state_tokens(banned, watched, vip),
    )


def directory_view(data: Mapping | None) -> DirectoryView:
    """The directory read's answer as one page: rows in the read's order, plus its range.

    *data* is the action's ``data`` mapping. A missing or unreadable answer is an EMPTY page of one
    (``0 of 0``), never an exception: the console renders the same shape whatever the read said.
    """
    source = data if isinstance(data, Mapping) else {}
    rows = tuple(_directory_row(entry) for entry in (source.get("rows") or ()))
    try:
        total = max(int(source.get("total") or 0), 0)
    except (TypeError, ValueError):
        total = 0
    try:
        page = max(int(source.get("page") or 1), 1)
    except (TypeError, ValueError):
        page = 1
    per_page = max(int(source.get("per_page") or len(rows) or 1), 1)
    pages = max(int(source.get("pages") or 0), 1) if total else 1
    return DirectoryView(rows=rows, total=total, page=page, pages=pages, per_page=per_page,
                         query=text(source.get("query")))


# ── 2. one player ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IdentityEntry:
    """One UCID of a player: the id, the DCS name it last used, and its manual-validation flag."""
    ucid: str
    name: str
    manual: bool


@dataclass(frozen=True, slots=True)
class BanState:
    """The player's ACTIVE ban, or the empty state of one that has none.

    EVERY field is always present, so the page renders one shape for all three states (clean,
    banned, banned-and-watched). ``reason``, ``banned_by``, ``banned_at``, ``banned_until`` and
    ``scope`` are empty (or, for the scope, empty) for a player who is not banned; ``permanent`` is
    the ONE reading of the year-9999 sentinel, through :func:`readmodels.bans.permanent`, so a ban
    with no expiry states the word ``never`` rather than a date nobody can act on.
    """
    active: bool = False
    reason: str = ""
    banned_by: str = ""
    banned_at: str = ""
    banned_until: str = ""
    permanent: bool = False
    scope: str = ""
    servers: tuple[str, ...] = ()
    ucid: str = ""


@dataclass(frozen=True, slots=True)
class WatchState:
    """The player's watchlist entry, or the empty state of one that has none (every key present).

    ``watchlist`` carries no expiry at all (one row per UCID, ``reason``/``created_by``/
    ``created_at``), so there is no "until" here to render and none to invent.
    """
    active: bool = False
    reason: str = ""
    created_by: str = ""
    created_at: str = ""
    ucid: str = ""


@dataclass(frozen=True, slots=True)
class PlayerDetailView:
    """ONE player: identity, link, presence, totals and both moderation states."""
    ucid: str
    found: bool
    identity: tuple[IdentityEntry, ...]
    name: str
    discord_id: str
    member: str
    vip: bool
    first_seen: str
    last_seen: str
    playtime: str
    playtime_seconds: int | None
    sessions: int | None
    ban: BanState
    watch: WatchState


def _ban_state(entry: Any) -> BanState:
    """The active ban ENTRY as the page reads it — the empty state when there is none.

    ``entry`` is one entry as ``ServiceBus.bans`` groups it: ``server_name`` is a LIST of server names
    for a scoped ban or ``None`` for a bot-wide one, which is the ONE spelling of "this ban covers
    every server" the console has.
    """
    if not entry:
        return BanState()
    raw_servers = _field(entry, "server_name")
    if raw_servers is None:
        servers: tuple[str, ...] = ()
    elif isinstance(raw_servers, str):
        servers = (raw_servers,) if raw_servers else ()
    else:
        servers = tuple(text(name) for name in raw_servers if text(name))
    # the ONE reading of the year-9999 sentinel, shared with the Bans page
    is_permanent = permanent(entry)
    return BanState(
        active=True,
        reason=text(_field(entry, "reason")),
        banned_by=text(_field(entry, "banned_by")),
        banned_at=_stamp(_field(entry, "banned_at")),
        banned_until=NEVER if is_permanent else _stamp(_field(entry, "banned_until")),
        permanent=is_permanent,
        scope=ALL_SERVERS if not servers else ", ".join(servers),
        servers=servers,
        ucid=text(_field(entry, "ucid")),
    )


def _watch_state(entry: Any) -> WatchState:
    """The watchlist ENTRY as the page reads it — the empty state when there is none."""
    if not entry:
        return WatchState()
    return WatchState(active=True,
                      reason=text(_field(entry, "reason")),
                      created_by=text(_field(entry, "created_by")),
                      created_at=_stamp(_field(entry, "created_at")),
                      ucid=text(_field(entry, "ucid")) or text(_field(entry, "player_ucid")))


def player_detail(data: Mapping | None) -> PlayerDetailView:
    """The detail read's answer as ONE player page, in every state.

    A player the read did not find is ``found`` False with the same keys as a found one, so the page
    decides between "not found" and "no data" on one flag instead of on missing attributes.
    """
    source = data if isinstance(data, Mapping) else {}
    raw_identity = source.get("identity") or ()
    identity = tuple(IdentityEntry(ucid=text(_field(item, "ucid")),
                                   name=text(_field(item, "name")),
                                   manual=bool(_field(item, "manual")))
                     for item in raw_identity)
    discord_id = _field(source, "discord_id")
    try:
        discord_number = int(discord_id)
    except (TypeError, ValueError):
        discord_number = -1
    seconds = _seconds(source.get("playtime"))
    raw_sessions = source.get("sessions")
    try:
        sessions = None if raw_sessions is None else max(int(raw_sessions), 0)
    except (TypeError, ValueError):
        sessions = None
    return PlayerDetailView(
        ucid=text(source.get("ucid")),
        found=bool(source.get("found")),
        identity=identity,
        # the source's own name for the player's most recent UCID (never ``display_name``)
        name=text(source.get("name")),
        discord_id="" if discord_number < 0 else str(discord_number),
        member=text(source.get("member")),
        vip=bool(source.get("vip")),
        first_seen=_stamp(source.get("first_seen")),
        last_seen=_stamp(source.get("last_seen")),
        playtime=playtime_text(seconds),
        playtime_seconds=seconds,
        sessions=sessions,
        ban=_ban_state(source.get("ban")),
        watch=_watch_state(source.get("watch")),
    )


# ── 3. one player's statistics ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PlaytimeEntry:
    """One bar of a per-something breakdown: the label the read grouped by and its duration.

    ``share`` is the bar's own length as a whole percentage of the LONGEST entry, computed here so
    the template renders a width instead of dividing two figures.
    """
    label: str
    seconds: int
    playtime: str
    share: int = 0


@dataclass(frozen=True, slots=True)
class CountEntry:
    """One bar of a COUNTED breakdown — kills by target type — with its label and its count.

    ``share`` is the bar's length as a whole percentage of the longest entry, like a playtime bar's.
    """
    label: str
    count: int
    share: int = 0


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One statistics row of the player's last sessions: when, how long, where, in what."""
    started: str
    started_at: str
    ended: str
    playtime: str
    seconds: int | None
    slot: str
    server: str
    map: str


@dataclass(frozen=True, slots=True)
class PlayerStatisticsView:
    """One player's ``/statistics`` numbers for ONE period.

    The figures are the bot's own definitions (``plugins/userstats``): ``kills`` counts every kill,
    ``ai_kills`` the ones that were not players, ``deaths_ai``/``deaths_player`` split the deaths the
    same way, and ``crashes`` is the pilot-dead count (``crashes - ejections``) the Discord embed
    draws. ``period`` is the RESOLVED period name — a value the bot does not know reads ``all``.
    """
    ucid: str
    period: str
    playtime: str
    playtime_seconds: int | None
    sessions: int
    kills: int
    ai_kills: int
    player_kills: int
    deaths: int
    deaths_ai: int
    deaths_player: int
    selfkills: int
    teamkills: int
    takeoffs: int
    landings: int
    ejections: int
    crashes: int
    kills_by_type: tuple[CountEntry, ...]
    per_module: tuple[PlaytimeEntry, ...]
    per_server: tuple[PlaytimeEntry, ...]
    per_map: tuple[PlaytimeEntry, ...]
    recent: tuple[SessionEntry, ...]
    deaths_by_cause: tuple[CountEntry, ...] = ()
    kd: str = ""
    kd_players: str = ""
    landing_rate: str = ""

    @property
    def has_sessions(self) -> bool:
        """Whether the read found any session at all — an empty statistics tab says so in one line."""
        return bool(self.sessions) or self.playtime_seconds is not None


def _number(value: Any) -> int:
    """A stored counter as an int — ``0`` for a value the source does not have (a SUM of no rows)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _shares(values: Sequence[int]) -> list[int]:
    """Whole-percentage bar lengths relative to the LONGEST value — all zero for an empty set.

    One definition for every bar the tab draws, so two cards cannot scale their bars differently.
    """
    largest = max(values, default=0)
    if largest <= 0:
        return [0 for _ in values]
    return [int(round(value * 100 / largest)) for value in values]


def _breakdown(entries: Sequence[Any] | None) -> tuple[PlaytimeEntry, ...]:
    """A per-module/server/map breakdown, in the order the read returned it, with its bar shares."""
    rows = list(entries or ())
    seconds_list = [(_seconds(_field(entry, "playtime")) or 0) for entry in rows]
    shares = _shares(seconds_list)
    return tuple(PlaytimeEntry(label=text(_field(entry, "label")), seconds=seconds,
                              playtime=playtime_text(seconds), share=share)
                 for entry, seconds, share in zip(rows, seconds_list, shares))


def _counts(entries: Sequence[Any] | None) -> tuple[CountEntry, ...]:
    """A counted breakdown (kills by target type), in the read's order, with its bar shares."""
    rows = list(entries or ())
    counts = [_number(_field(entry, "count")) for entry in rows]
    shares = _shares(counts)
    return tuple(CountEntry(label=text(_field(entry, "label")), count=count, share=share)
                 for entry, count, share in zip(rows, counts, shares))


#: The death causes the tab states, from the three counts the read already splits.
DEATH_CAUSE_LABELS: tuple[str, ...] = (_("Killed by AI"), _("Killed by a player"),
                                       _("Crash / own fault"))


def _ratio(numerator: int, denominator: int) -> str:
    """*numerator* over *denominator*, two decimals — ``''`` when the denominator is zero.

    A zero denominator is NOT ``0.00``: a player with no deaths has no ratio, and a figure nobody can
    compute is worse than an absent one.
    """
    if not denominator:
        return ""
    return f"{numerator / denominator:.2f}"


def _percentage(numerator: int, denominator: int) -> str:
    """*numerator* of *denominator* in whole percent with a percent sign — ``''`` for neither."""
    if not denominator:
        return ""
    return f"{round(numerator * 100 / denominator)} %"


def _sessions(entries: Sequence[Any] | None) -> tuple[SessionEntry, ...]:
    """The last sessions, in the order the read returned them (newest first)."""
    out: list[SessionEntry] = []
    for entry in entries or ():
        seconds = _seconds(_field(entry, "playtime"))
        out.append(SessionEntry(started=_stamp(_field(entry, "hop_on")),
                                started_at=_iso(_field(entry, "hop_on")),
                                ended=_stamp(_field(entry, "hop_off")),
                                playtime=playtime_text(seconds),
                                seconds=seconds,
                                slot=text(_field(entry, "slot")),
                                server=text(_field(entry, "server_name")),
                                map=text(_field(entry, "mission_theatre"))))
    return tuple(out)


def player_statistics(data: Mapping | None) -> PlayerStatisticsView:
    """The statistics read's answer as one period's page."""
    source = data if isinstance(data, Mapping) else {}
    totals = source.get("totals")
    if not isinstance(totals, Mapping):
        totals = {}
    seconds = _seconds(totals.get("playtime"))
    kills = _number(totals.get("kills"))
    deaths = _number(totals.get("deaths"))
    player_kills = _number(totals.get("player_kills"))
    deaths_player = _number(totals.get("deaths_player"))
    takeoffs = _number(totals.get("takeoffs"))
    landings = _number(totals.get("landings"))
    causes = (_number(totals.get("deaths_ai")), deaths_player, _number(totals.get("selfkills")))
    cause_shares = _shares(list(causes))
    return PlayerStatisticsView(
        ucid=text(source.get("ucid")),
        period=text(source.get("period")) or "all",
        playtime=playtime_text(seconds),
        playtime_seconds=seconds,
        sessions=_number(totals.get("sessions")),
        kills=kills,
        ai_kills=_number(totals.get("ai_kills")),
        player_kills=player_kills,
        deaths=deaths,
        deaths_ai=_number(totals.get("deaths_ai")),
        deaths_player=deaths_player,
        selfkills=_number(totals.get("selfkills")),
        teamkills=_number(totals.get("teamkills")),
        takeoffs=takeoffs,
        landings=landings,
        ejections=_number(totals.get("ejections")),
        crashes=_number(totals.get("crashes")),
        kills_by_type=_counts(source.get("kills_by_type")),
        per_module=_breakdown(source.get("per_module")),
        per_server=_breakdown(source.get("per_server")),
        per_map=_breakdown(source.get("per_map")),
        recent=_sessions(source.get("recent")),
        deaths_by_cause=tuple(
            CountEntry(label=label, count=count, share=share)
            for label, count, share in zip(DEATH_CAUSE_LABELS, causes, cause_shares)),
        kd=_ratio(kills, deaths),
        kd_players=_ratio(player_kills, deaths_player),
        landing_rate=_percentage(landings, takeoffs),
    )



# ── 4. one player's infractions ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InfractionEntry:
    """One friendly-fire event: when, what happened, and how many times in that minute."""
    time: str
    infraction: str
    count: int


@dataclass(frozen=True, slots=True)
class PenaltyEntry:
    """One row of the penalty-point log: when, which event, and its points as the command prints them."""
    time: str
    event: str
    points: str


@dataclass(frozen=True, slots=True)
class BanHistoryEntry:
    """One ban on record: its timeframe, its reason and its status word."""
    timeframe: str
    reason: str
    status: str


@dataclass(frozen=True, slots=True)
class PlayerInfractionsView:
    """One player's infractions: the friendly-fire events, the penalty log and the ban history.

    ``points_total`` is the SUM of ``penalties`` as the bot's ``/penalty`` prints it — the points
    decay over time, so it is today's value. ``bans`` counts every entry of the history, which is
    the page's "all time" figure.
    """
    ucid: str
    found: bool
    infractions: tuple[InfractionEntry, ...]
    penalties: tuple[PenaltyEntry, ...]
    points_total: str
    ban_history: tuple[BanHistoryEntry, ...]

    @property
    def bans(self) -> int:
        return len(self.ban_history)


#: A ban-history status word the report emits, as its translatable msgid. A word outside the set is
#: printed as it came — the report is the authority on the states it can write.
BAN_STATUS_WORDS: dict[str, str] = {"Active": _("Active"), "Forgiven": _("Forgiven"),
                                    "Auto-Unbanned": _("Auto-Unbanned")}


def _points(value: Any) -> str:
    """A stored points value as the bot prints it (two decimals) — ``''`` when it is not a number."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def player_infractions(data: Mapping | None) -> PlayerInfractionsView:
    """The infractions read's answer as one page: three lists, already rendered.

    The action hands over the report's own rows; this only formats them (a stamp, a count, two
    decimals) so the template renders values instead of computing them.
    """
    source = data if isinstance(data, Mapping) else {}
    infractions = tuple(
        InfractionEntry(time=_stamp(_field(item, "time")) or text(_field(item, "time")),
                        infraction=text(_field(item, "infraction")),
                        count=_number(_field(item, "count")))
        for item in (source.get("infractions") or ()))
    penalties = tuple(
        PenaltyEntry(time=_stamp(_field(item, "time")) or text(_field(item, "time")),
                     # the /penalty command's own word: the event key with its separators as spaces,
                     # title-cased — one rendering, so the log reads the same in both places. Not a
                     # msgid: the event names come from the punishment plugin's configuration.
                     event=" ".join(str(_field(item, "event") or "").split("_")).title(),
                     points=_points(_field(item, "points")))
        for item in (source.get("penalties") or ()))
    history = tuple(
        BanHistoryEntry(timeframe=text(_field(item, "timeframe")),
                        reason=text(_field(item, "reason")),
                        status=BAN_STATUS_WORDS.get(text(_field(item, "status")),
                                                     text(_field(item, "status"))))
        for item in (source.get("ban_history") or ()))
    return PlayerInfractionsView(ucid=text(source.get("ucid")),
                                 found=bool(source.get("found")),
                                 infractions=infractions, penalties=penalties,
                                 points_total=_points(source.get("penalty_total")),
                                 ban_history=history)


# ── 5. the live half of one player ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ActivityView:
    """Where a player is RIGHT NOW, or the empty record of one who is not flying.

    Every field is always present, so the page renders one shape. ``ip_hash`` is the HASH of the
    player's address, never the address: ``/info`` prints the raw one to a DCS Admin in Discord, and
    a web page is one screenshot away from being shared. It is also the only field that exists off
    the live session at all — the address is not stored, so an offline player has no hash.
    """
    online: bool = False
    server: str = ""
    side: str = ""
    slot: str = ""
    module: str = ""
    radios: tuple[str, ...] = ()
    ip_hash: str = ""


def _ip_hash(player) -> str:
    """The player's address as the console's own hash — ``''`` when it has none or cannot be read.

    The hash is :func:`core.utils.hash_ip_addr`, the SAME function ``/info`` labels "IP Hash", so
    the two surfaces can never print different hashes for one address.
    """
    address = text(attr(player, "ipaddr", None))
    if not address:
        return ""
    from core.utils import hash_ip_addr

    return safe(lambda: hash_ip_addr(address), "") or ""


def _radios(player) -> tuple[str, ...]:
    """The frequencies the player's SRS client reports, formatted — empty when SRS cannot answer.

    Read from the SRS plugin's own cache, the map ``/info`` reads, and only when this process runs
    the plugin and the player is in it. Anything unreadable is an empty list: a radio list is a
    detail, and it must never cost the page.
    """
    from .serverconfig import live_bot

    bot = live_bot()
    if bot is None:
        return ()
    try:
        plugin = (getattr(bot, "cogs", None) or {}).get("SRS")
        listener = getattr(plugin, "eventlistener", None)
        users = getattr(listener, "srs_users", None) or {}
        server = getattr(getattr(player, "server", None), "name", "")
        entry = (users.get(server) or {}).get(text(attr(player, "name", None))) or {}
        from core import utils

        return tuple(utils.format_frequency(freq) for freq in (entry.get("radios") or ()))
    except Exception:  # noqa: BLE001 - a radio list is a detail, never a page failure
        return ()


def player_activity(source, ucid: str) -> ActivityView:
    """The player *ucid* on the live roster, or ``ActivityView()`` when they are not in a slot.

    Read off the caller's own SCOPED servers (``pages.dashboard.request_source``), never queried:
    the live roster is the one source that knows a session, and the control split the page draws —
    which controls need a UCID and which need a session — is decided by this record's ``online``.
    """
    wanted = text(ucid)
    if not wanted:
        return ActivityView()
    for server in getattr(source, "servers", ()) or ():
        players = safe(lambda: getattr(server, "players", None), {}) or {}
        for player in (players or {}).values():
            if text(attr(player, "ucid", None)) != wanted or not attr(player, "active", False):
                continue
            return ActivityView(
                online=True,
                server=text(attr(server, "name", None)),
                side=safe(lambda: _coalition(player), "") or "",
                slot=text(attr(player, "group_name", None)),
                module=text(attr(player, "unit_display_name", None)),
                radios=safe(lambda: _radios(player), ()) or (),
                ip_hash=safe(lambda: _ip_hash(player), "") or "",
            )
    return ActivityView()
