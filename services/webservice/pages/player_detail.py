"""One player's page: **Overview**, **Infractions** and **Statistics** — one real URL per tab.

A player is reached from the directory's own NAME link (`pages/players.PLAYERS_DETAIL_PREFIX`), so
this module's routes make an already-emitted link resolve; it contributes NO navigation entry. The
page is READ-ONLY: every control it renders is a declaration of `pages/actions` (the ban and watch
entries, the player session writes), posted through the same routes and dialogs the directory and the
dashboard use. Nothing here writes.

THE UCID IS THE KEY, so it is the page's own path segment and the shape is checked before anything
runs: a 32-hex UCID is the only value this page accepts, and a malformed one is refused without a
read at all. A well-formed UCID this installation does not know is the console's 404 page — never a
500 and never an empty page.

THE CONTROLS ARE KEYED BY WHAT THEY NEED, never by whether the player is online:

* the BAN and the WATCH ENTRY key on the UCID: the ban ADD resolves no server and looks no player up
  and the watch trio is the same shape, so both work for an offline player exactly as for one in a
  slot, and both are page-level controls (a ban being added, or a watch entry created, has no row);
* MESSAGE / POPUP / KICK / SCREENSHOT act on a LIVE session, so they are offered only while the
  player holds a slot and are ABSENT otherwise.

THE MODERATION CARD CARRIES ONE ENTRY PER RECORD. A ban and a watch entry expire by different rules
(one on its date, the other never), so lifting the ban leaves the watch entry untouched and removing
the watch leaves the ban running. The page says so in one sentence; the two are never merged into a
single "restricted" state.

THE IP IS THE HASH ONLY. ``/info`` prints a player's raw address to a DCS Admin in Discord; a web
page is one screenshot away from being shared, so this page carries the hash `core.utils.hash_ip_addr`
computes — the same value, never the address.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Mapping
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.actions import action_available, read_action

from .. import i18n, permissions, readmodels, session
from ..readmodels import bans as ban_model
from ..readmodels import players as player_model
from . import dashboard as dashboard_page
from . import players as players_page

__all__ = [
    "PLAYER_PATH", "PLAYER_INFRACTIONS_PATH", "PLAYER_STATISTICS_PATH", "PLAYER_TEMPLATE",
    "PLAYER_CAPABILITY", "PLAYER_ROLES", "PERIOD_PARAM", "PERIODS", "PERIOD_LABELS",
    "UCID_PATTERN", "TAB_OVERVIEW", "TAB_INFRACTIONS", "TAB_STATISTICS", "TABS",
    "NOT_FOUND_TITLE", "NOT_FOUND_NOTE", "NOT_FOUND_BACK",
    "player_path", "normalise_ucid", "period_view", "tab_views", "capabilities", "nav_items",
    "player_context", "add_routes",
]

#: One tab, one URL: a tab can be sent to another admin. The UCID is the path segment every one of
#: them shares.
PLAYER_PATH = "/players/{ucid}"
PLAYER_INFRACTIONS_PATH = "/players/{ucid}/infractions"
PLAYER_STATISTICS_PATH = "/players/{ucid}/statistics"
PLAYER_TEMPLATE = "player_detail.html"

#: The directory's own capability: this page is a player READ, so it takes the directory's predicate
#: rather than inventing a second one. The Moderation card narrows FURTHER, per block, on the
#: capability that owns each record (``bans.view`` / ``players.watch``).
PLAYER_CAPABILITY = players_page.PLAYERS_CAPABILITY
PLAYER_ROLES: tuple[str, ...] = players_page.PLAYERS_ROLES

#: The SHAPE of a UCID: 32 hexadecimal characters. Checked before any read runs — the value is the
#: route's own path segment, so nothing unshaped may reach a statement.
UCID_PATTERN = re.compile(r"[0-9a-fA-F]{32}")

#: The statistics tab's period switch. The names are the bot's own (``plugins/userstats/filter.py``,
#: ``PeriodFilter.list``) and the READ is resolved through ``StatisticsFilter.detect`` — this is the
#: set of words the page OFFERS, never a second definition of a period.
PERIOD_PARAM = "period"
PERIODS: tuple[str, ...] = ("all", "month", "quarter", "year")
PERIOD_LABELS: dict[str, str] = {
    "all": i18n._("All time"),
    "month": i18n._("This month"),
    "quarter": i18n._("This quarter"),
    "year": i18n._("This year"),
}

TAB_OVERVIEW = "overview"
TAB_INFRACTIONS = "infractions"
TAB_STATISTICS = "statistics"
#: the tabs in render order, with the path each one is served at
TABS: tuple[tuple[str, str], ...] = ((TAB_OVERVIEW, PLAYER_PATH),
                                     (TAB_INFRACTIONS, PLAYER_INFRACTIONS_PATH),
                                     (TAB_STATISTICS, PLAYER_STATISTICS_PATH))
TAB_LABELS: dict[str, str] = {TAB_OVERVIEW: i18n._("Overview"),
                              TAB_INFRACTIONS: i18n._("Infractions"),
                              TAB_STATISTICS: i18n._("Statistics")}

#: Where a write started on this page RETURNS TO: the directory. ``pages.actions.origin_path`` is an
#: allow-list of the registered PAGE paths, and this page contributes no nav entry (it is reached
#: from a row), so the directory is the section default every player write already resolves to. It
#: is passed EXPLICITLY rather than relying on that fallback, so a control never carries a value the
#: registry would discard.
WRITE_ORIGIN = players_page.PLAYERS_PATH

PAGE_TITLE = i18n._("Player")

#: The page-level words. The declarations own their labels; these are the page-head verbs the mockup
#: spells out, and the four session controls' words in the same voice.
WORD_BAN = i18n._("Ban")
WORD_WATCH = i18n._("Add to watchlist")
SESSION_WORDS: dict[str, str] = {"chat": i18n._("Message"), "popup": i18n._("Popup"),
                                 "kick": i18n._("Kick"), "camera": i18n._("Screenshot")}
#: which of ``PLAYER_ACTIONS`` a LIVE session is required for, by the control's own glyph. The Ban of
#: that list acts on a player row ON A SERVER; here the ban is the UCID-keyed ADD, so it is not one.
SESSION_GLYPHS: tuple[str, ...] = ("chat", "popup", "kick", "camera")

#: The target-type keys the kills chart groups by (``plugins/userstats``), as the words the page
#: reads. The READ reports the bot's own keys; the words are this page's presentation.
STATISTICS_LABELS: dict[str, str] = {"planes": i18n._("Aircraft"),
                                     "helicopters": i18n._("Helicopters"),
                                     "ships": i18n._("Ships"),
                                     "sams": i18n._("SAM sites"),
                                     "ground": i18n._("Ground units")}

#: The page head's control ORDER: the three session writes, the UCID-keyed ban, the watch entry, then
#: the screenshot — the mockup's row. ``ban``/``watch`` are the page-level pair, not session glyphs.
SESSION_HEAD_ORDER: tuple[str, ...] = ("chat", "popup", "kick", "ban", "watch", "camera")

#: The strips' words.
STRIP_BANNED = i18n._("Banned")
STRIP_WATCHED = i18n._("Watched")
STRIP_CLEAN = i18n._("No ban")
BAN_ENDS = i18n._("the ban ends")
BAN_NEVER = i18n._("no expiry")
WATCH_NEVER = i18n._("no expiry")
CLEAN_DETAIL = i18n._("and not on the watchlist · nothing is waiting here.")
ACTIVE_BAN_TAG = i18n._("1 active ban")
ACTIVE_WATCH_TAG = i18n._("1 watch entry")

#: the Moderation card's own labels
CARD_IDENTITY = i18n._("Identity & link")
CARD_MODERATION = i18n._("Moderation")
CARD_ACTIVITY = i18n._("Current activity")
ENTRY_BAN = i18n._("Ban")
ENTRY_WATCH = i18n._("Watch")
LABEL_REASON = i18n._("Reason")
LABEL_BANNED_BY = i18n._("Banned by")
LABEL_ADDED_BY = i18n._("Added by")
LABEL_SCOPE = i18n._("Scope")
LABEL_POINTS = i18n._("Penalty points")
LABEL_BANS_ALL = i18n._("Bans, all time")

#: the empty-state sentences this page owns
NO_LINK = i18n._("No Discord member is linked to this UCID. Linking happens in Discord, so this "
                 "page states the fact and offers no control for it.")
NOT_ON_SERVER = i18n._("Not on a server right now.")
NO_SESSIONS = i18n._("No session has been recorded for this player, so there is nothing to chart.")
NO_INFRACTIONS = i18n._("There are no infractions of this player logged.")
NO_PENALTIES = i18n._("This player has no penalty points.")
NO_BANS = i18n._("There are no bans logged for this player.")
#: the ONE sentence a moderator must not get wrong: the two records are two records.
TWO_RECORDS = i18n._("Lifting one leaves the other untouched: lifting the ban keeps the watch entry, "
                     "and removing the watch keeps the ban running until it expires.")


# ── the route's own values ──────────────────────────────────────────────────

def player_path(ucid: str, *suffix: str) -> str:
    """The URL of *ucid*'s page — the overview when nothing follows, else one TAB's path."""
    base = f"/players/{ucid}"
    return "/".join((base, *suffix)) if suffix else base


def normalise_ucid(value: Any) -> str:
    """*value* as a UCID, or ``''`` when it is not one. Never trims: a shaped id has no spaces."""
    text = str(value or "")
    return text.lower() if UCID_PATTERN.fullmatch(text) else ""


#: The page a UCID with no player answers: a well-formed 404 — the shell, the sentence and the way
#: back to the directory — never a 500 and never an empty body.
NOT_FOUND_TITLE = i18n._("No such player")
NOT_FOUND_NOTE = i18n._("No player on this installation carries this UCID. It may have been "
                        "removed, or the link may be mistyped.")
NOT_FOUND_BACK = i18n._("Back to the players directory")


def period_view(value: Any) -> str:
    """The period in force: a member of :data:`PERIODS`, or ``all``.

    The switch is a closed vocabulary like every other console parameter, and the READ resolves the
    period through the bot's own ``StatisticsFilter.detect`` — an unknown word is not an error the
    page can show, it is a view nobody can serve.
    """
    text = str(value or "").strip().lower()
    return text if text in PERIODS else "all"


def tab_views(ucid: str, tab: str) -> tuple[dict, ...]:
    """The three tabs as data: their label, their URL and whether they are the one in force."""
    return tuple({"label": TAB_LABELS[name], "href": player_path(ucid, *suffix), "active": name == tab}
                 for name, suffix in ((TAB_OVERVIEW, ()), (TAB_INFRACTIONS, ("infractions",)),
                                      (TAB_STATISTICS, ("statistics",))))


def period_views(ucid: str, active: str) -> tuple[dict, ...]:
    """The period switch as data: one GET link per period, the one in force marked."""
    return tuple({"label": PERIOD_LABELS[name], "active": name == active,
                  "href": f"{player_path(ucid, 'statistics')}?{urlencode({PERIOD_PARAM: name})}"}
                 for name in PERIODS)


# ── control records ─────────────────────────────────────────────────────────
#
# The shapes are `templates/_strip.html`'s control record and `templates/list.html`'s page-level one:
# a path, a word, a sentence for the native tooltip and the accessible name, the target in the BODY,
# and the session's CSRF token. A control opens its DIALOG (every one of them does), so its form is
# never intercepted.

def _sentence(msgid: str, **params) -> dict:
    """A per-request sentence record, rendered through the template environment's guarded ``say``."""
    return {"msgid": msgid, "params": params}


def _hidden(origin: str, ucid: str) -> tuple[tuple[str, str], ...]:
    """The hidden fields every control of this page carries: the page it returns to and its UCID."""
    from . import actions as actions_page

    return ((actions_page.ORIGIN_FIELD, origin), ("ucid", ucid))


def _page_control(request: Request, action, ucid: str, name: str, word: str) -> dict:
    """One page-level control, built from its DECLARATION — the page invents no write."""
    from . import actions as actions_page

    values = {"player": name}
    return {
        "path": action.path + actions_page.CONFIRM_SUFFIX if action.dialog else action.path,
        "label": word,
        "title": _sentence(action.aria, **values),
        "aria": _sentence(action.aria, **values),
        "danger": bool(action.danger),
        "hidden": _hidden(WRITE_ORIGIN, ucid),
        "csrf_field": session.CSRF_FIELD,
        "csrf_token": session.get_csrf_token(request),
    }


def _session_controls(request: Request, ucid: str, activity) -> tuple[dict, ...]:
    """The four SESSION-keyed controls, in the mockup's order — empty while the player is not flying.

    Built by ``pages/actions.player_controls`` and then narrowed to the four, so the capability, the
    scope, the server's own screenshot permission and the plugin's availability are the EXISTING
    predicates rather than a second reading of them. The BAN of that map is not here: this page's ban
    is the UCID-keyed ADD above, which needs no session.
    """
    if not activity.online or not activity.server:
        return ()
    from . import actions as actions_page

    record = actions_page.player_controls(request, WRITE_ORIGIN).get((activity.server, ucid))
    if not record:
        return ()
    offered = {control["icon"]: control for control in record.get("strip", ())}
    out: list[dict] = []
    for glyph in SESSION_GLYPHS:
        control = offered.get(glyph)
        if control is None:
            continue
        out.append(dict(control, label=SESSION_WORDS[glyph]))
    return tuple(out)


def _entry_strip(request: Request, detail, ban_entry) -> tuple[dict | None, dict | None]:
    """The two ENTRY control records: the ban's (lift + edit) and the watch's (remove + edit).

    Each is the strip's own record (``{"strip": …, "wide": False}``) — the SAME component the table
    rows render — or ``None`` when the caller holds none of that entry's capabilities.
    """
    from . import actions as actions_page

    ban_strip = None
    if ban_entry is not None:
        records = actions_page.ban_controls(request, (ban_entry,), WRITE_ORIGIN)
        ban_strip = records.get(ban_entry.key)
    watch_strip = actions_page.watch_controls(request, detail, WRITE_ORIGIN)
    return ban_strip, watch_strip


# ── the page's facts ────────────────────────────────────────────────────────

def _initials(name: str) -> str:
    """The head's initials chip: the first character of the name's first two words or id segments."""
    parts = [part for part in re.split(r"[\s\-_.]+", name or "") if part]
    return "".join(part[0].upper() for part in parts[:2])


def _head(detail: player_model.PlayerDetailView) -> dict:
    """The page head as data: the name, the two identities and when the player was last seen."""
    linked = bool(detail.discord_id)
    return {"name": detail.name or detail.ucid,
            "initials": _initials(detail.name),
            "linked": linked,
            "member": detail.member,
            "discord_id": detail.discord_id,
            "last_seen": detail.last_seen,
            "vip": detail.vip,
            # the ONE UCID's own manual-validation flag: a player is one UCID, so the identity list
            # has at most the entry the route asked for
            "manual": bool(detail.identity[0].manual) if detail.identity else False}


def _strips(detail: player_model.PlayerDetailView, watch_visible: bool) -> tuple[dict, ...]:
    """One strip per restriction the caller may read, ban first; a clean player gets the quiet strip.

    The strip states the POSTURE and the one fact that decides when it changes (a ban's end; a watch
    has none), and the entry below it carries the facts — nothing is stated twice.

    ``watch_visible`` is the caller's watch capability, the same flag that gates the watch ENTRY: a
    reader who may not read the watch entry is drawn no watch strip either, and no clean strip in its
    place — the quiet strip says the player is not on the watchlist, which this page cannot say to a
    reader who may not look. Ban strips stay ungated: they are the page's own state, not the ban list.
    """
    strips: list[dict] = []
    if detail.ban.active:
        strips.append({"kind": "banned", "dot": "dead", "word": STRIP_BANNED,
                       "until": detail.ban.banned_until, "permanent": detail.ban.permanent,
                       "tag": ACTIVE_BAN_TAG})
    if detail.watch.active and watch_visible:
        strips.append({"kind": "watch", "dot": "update", "word": STRIP_WATCHED,
                       "until": "", "permanent": True, "tag": ACTIVE_WATCH_TAG})
    if not strips and not detail.watch.active:
        strips.append({"kind": "clean", "dot": "run", "word": STRIP_CLEAN, "until": "",
                       "permanent": True, "tag": ""})
    return tuple(strips)


async def _detail(request: Request, ucid: str) -> tuple[player_model.PlayerDetailView, dict]:
    """The player's own read, plus its RAW answer (the ban entry's identity lives there)."""
    from . import actions as actions_page

    result = await read_action("get_player_detail", actions_page.context_for(request), ucid=ucid)
    data = getattr(result, "data", None)
    if not getattr(result, "success", False) or not isinstance(data, Mapping):
        return player_model.player_detail(None), {}
    return player_model.player_detail(data), dict(data)


async def _infractions(request: Request, ucid: str) -> player_model.PlayerInfractionsView:
    """The infractions read — the penalty total and the ban count come from the same answer."""
    from . import actions as actions_page

    result = await read_action("get_player_infractions", actions_page.context_for(request),
                               ucid=ucid)
    data = getattr(result, "data", None)
    if not getattr(result, "success", False) or not isinstance(data, Mapping):
        return player_model.player_infractions(None)
    return player_model.player_infractions(data)


async def _statistics(request: Request, ucid: str, period: str) -> player_model.PlayerStatisticsView:
    """The statistics read for ONE period, resolved by the bot's own period filter in the action."""
    from . import actions as actions_page

    result = await read_action("get_player_statistics", actions_page.context_for(request),
                               ucid=ucid, period=period)
    data = getattr(result, "data", None)
    if not getattr(result, "success", False) or not isinstance(data, Mapping):
        return player_model.player_statistics(None)
    return player_model.player_statistics(data)


def _ban_entry(detail: player_model.PlayerDetailView, data: Mapping):
    """The active ban as a ban ROW — the record ``ban_controls`` builds its two glyphs from.

    The read's ban entry names no player (it is keyed by UCID), so the page's resolved name is
    carried onto it: a control's tooltip names the person, never a bare id.
    """
    if not detail.ban.active:
        return None
    raw = data.get("ban") if isinstance(data, Mapping) else None
    if not raw:
        return None
    rows = ban_model.ban_views([dict(raw, name=detail.name or detail.ucid)])
    return rows[0] if rows else None


def _moderation(request: Request, detail: player_model.PlayerDetailView, data: Mapping,
                infractions: player_model.PlayerInfractionsView) -> dict:
    """The Moderation card: one entry per record, each with its own controls, plus the two totals.

    Each block's FACTS are gated by the capability that owns the record, resolved here and carried to
    the template — never in the read model, so a page render cannot hand a block to a caller the
    record's own page would refuse.
    """
    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    ban_row = _ban_entry(detail, data)
    ban_strip, watch_strip = _entry_strip(request, detail, ban_row)
    return {
        # the BAN block renders only for a caller who may read the ban list at all — the player page
        # must never become a way around the bans page's own scope
        "ban_visible": bool(detail.ban.active
                            and permissions.allows("bans.view", roles, manager=manager)),
        # the WATCH block renders only for a caller holding the watch capability, the same rule: the
        # page must not become a way around the watchlist's own scope
        "watch_visible": bool(detail.watch.active
                              and permissions.allows("players.watch", roles, manager=manager)),
        "ban": detail.ban,
        "ban_controls": ban_strip,
        "watch": detail.watch,
        "watch_controls": watch_strip,
        "points": infractions.points_total,
        "bans": infractions.bans,
    }


def _page_controls(request: Request, detail: player_model.PlayerDetailView,
                   activity: player_model.ActivityView) -> tuple:
    """The page-head controls, in the mockup's order: the three session writes, the UCID pair, the
    screenshot.

    Ban and Add to watchlist are offered while they APPLY (no active ban / not watched) and need no
    session; Message, Popup, Kick and Screenshot are offered only while the player holds a slot. An
    unusable control is ABSENT, never disabled.
    """
    from . import actions as actions_page

    roles = permissions.role_names_for(request)
    manager = permissions.manages_console(request)
    ucid = detail.ucid
    name = detail.name or detail.ucid
    offered: list[dict] = []
    session = {control["icon"]: control for control in _session_controls(request, ucid, activity)}
    for glyph in SESSION_HEAD_ORDER:
        if glyph == "ban":
            action = actions_page.BAN_ADD_ACTION
            if not detail.ban.active and _allowed(request, action):
                offered.append(_page_control(request, action, ucid, name, WORD_BAN))
        elif glyph == "watch":
            action = actions_page.WATCH_ADD_ACTION
            if not detail.watch.active and _allowed(request, action):
                offered.append(_page_control(request, action, ucid, name, WORD_WATCH))
        elif glyph in session:
            offered.append(session[glyph])
    return tuple(offered)


def _allowed(request: Request, action) -> bool:
    """Whether this caller may be offered ONE declared control: its capability AND its action."""
    return (permissions.allows(action.capability, permissions.role_names_for(request),
                              manager=permissions.manages_console(request))
            and action_available(action.qualname))


# ── the context ─────────────────────────────────────────────────────────────

async def player_context(request: Request, ucid: str, tab: str) -> dict:
    """Everything the player page's template reads, for ONE tab.

    The head, the strips, the tabs and the Moderation card are the SAME on all three tabs (a tab that
    dropped the player's state would make the moderation facts a property of the URL); the tab body
    is the one thing that differs.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    detail, data = await _detail(request, ucid)
    if not detail.found:
        raise LookupError("no player with this UCID")
    infractions = await _infractions(request, ucid)
    source = dashboard_page.request_source(request)
    activity = player_model.player_activity(source, ucid)
    state = readmodels.overview(source, level=dashboard_page.log_level(request))
    # the Moderation card resolves what the caller may read ONCE, and the strips follow the same
    # answer — resolving it twice could draw a strip for a block the card withheld
    moderation = _moderation(request, detail, data, infractions)
    context = {
        "title": detail.name or ucid,
        "page_title": PAGE_TITLE,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, players_page.PLAYERS_TITLE,
                                      target=detail.name or ucid),
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=players_page.PLAYERS_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "ucid": ucid,
        "player": detail,
        "head": _head(detail),
        "activity": activity,
        "strips": _strips(detail, moderation["watch_visible"]),
        "tabs": tab_views(ucid, tab),
        "controls": _page_controls(request, detail, activity),
        "moderation": moderation,
        "tab": tab,
        "no_link": NO_LINK,
        "not_on_server": NOT_ON_SERVER,
        "labels": {"identity": CARD_IDENTITY, "moderation": CARD_MODERATION,
                   "activity": CARD_ACTIVITY, "entry_ban": ENTRY_BAN,
                   "entry_watch": ENTRY_WATCH, "reason": LABEL_REASON,
                   "banned_by": LABEL_BANNED_BY, "added_by": LABEL_ADDED_BY,
                   "scope": LABEL_SCOPE, "points": LABEL_POINTS, "bans_all": LABEL_BANS_ALL,
                   "ban_ends": BAN_ENDS, "ban_never": BAN_NEVER, "watch_never": WATCH_NEVER,
                   "clean_detail": CLEAN_DETAIL, "two_records": TWO_RECORDS},
        "infractions": None,
        "statistics": None,
        "kill_types": (),
        "periods": (),
        "empty": {"infractions": NO_INFRACTIONS, "penalties": NO_PENALTIES, "bans": NO_BANS,
                  "sessions": NO_SESSIONS},
    }
    if tab == TAB_INFRACTIONS:
        context["infractions"] = infractions
    elif tab == TAB_STATISTICS:
        period = period_view(request.query_params.get(PERIOD_PARAM))
        statistics = await _statistics(request, ucid, period)
        context["statistics"] = statistics
        # the kill types are the bot's own keys in the read; the page states them as words
        context["kill_types"] = tuple(
            replace(entry, label=STATISTICS_LABELS.get(entry.label, entry.label))
            for entry in statistics.kills_by_type)
        context["periods"] = period_views(ucid, period_view(statistics.period))
    return context


def capabilities() -> dict[str, str]:
    """The capability declaration for the three paths this module registers.

    The page reads a player, so it declares the DIRECTORY's capability (``players.view``) for each of
    its three routes — the tabs are one page and cannot be reached by a narrower right. The
    Moderation card's two blocks narrow on their own capabilities in the context builder.
    """
    players_page.PAGE.declare()
    return {PLAYER_PATH: PLAYER_CAPABILITY,
            PLAYER_INFRACTIONS_PATH: PLAYER_CAPABILITY,
            PLAYER_STATISTICS_PATH: PLAYER_CAPABILITY}


def nav_items() -> tuple:
    """No sidebar entry: this page is reached from a directory row."""
    return ()


def _not_found_context(request: Request) -> dict:
    """The chrome a 404 renders through, with ``not_found`` set — the template's own branch.

    The console's refusal pages are registered for its refusals (a 403 and the retryable status), so
    this page answers its own missing player: the shell, one sentence, and the way back.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    source = dashboard_page.request_source(request)
    state = readmodels.overview(source, level=dashboard_page.log_level(request))
    return {
        "title": NOT_FOUND_TITLE,
        "page_title": NOT_FOUND_TITLE,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, players_page.PLAYERS_TITLE),
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                               current=players_page.PLAYERS_PATH,
                                               manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
        "not_found": True,
        "not_found_note": NOT_FOUND_NOTE,
        "not_found_back": NOT_FOUND_BACK,
        "not_found_href": players_page.PLAYERS_PATH,
    }


def add_routes(router: APIRouter) -> APIRouter:
    """Add the three tab routes to the shell's own router and return it."""
    players_page.PAGE.declare()

    async def _page(request: Request, ucid: str, tab: str) -> HTMLResponse:
        # no-store on every answer: the moderation state and the live activity block are state, so a
        # browser must not serve a stale render when the reader comes back to the page.
        headers = {"Cache-Control": "no-store"}
        wanted = normalise_ucid(ucid)
        if not wanted:
            return HTMLResponse(i18n.render(request, PLAYER_TEMPLATE, **_not_found_context(request)),
                                status_code=404, headers=headers)
        try:
            context = await player_context(request, wanted, tab)
        except LookupError:
            return HTMLResponse(i18n.render(request, PLAYER_TEMPLATE, **_not_found_context(request)),
                                status_code=404, headers=headers)
        return HTMLResponse(i18n.render(request, PLAYER_TEMPLATE, **context), headers=headers)

    @router.get(PLAYER_PATH, response_class=HTMLResponse, name="page-player")
    async def _overview_route(request: Request, ucid: str):
        return await _page(request, ucid, TAB_OVERVIEW)

    @router.get(PLAYER_INFRACTIONS_PATH, response_class=HTMLResponse,
                name="page-player-infractions")
    async def _infractions_route(request: Request, ucid: str):
        return await _page(request, ucid, TAB_INFRACTIONS)

    @router.get(PLAYER_STATISTICS_PATH, response_class=HTMLResponse, name="page-player-statistics")
    async def _statistics_route(request: Request, ucid: str):
        return await _page(request, ucid, TAB_STATISTICS)

    return router
