"""The dashboard: the first page a signed-in staff member opens.

WHAT "READ-ONLY" MEANT HERE, and what is true now (adding the SERVER row strip
had to change this claim rather than leave it standing):

* the ROUTE is still a GET and there is no other method on it. No POST/PUT/PATCH/DELETE exists in
  this module, no WRITE action is called here, nothing is written to disk — the log tail opens
  the log file read-only and the read models only inspect objects that already exist. The page's OWN
  module owns no write handler, and that is still pinned by
  ``tests/test_webui_dashboard.py::test_the_dashboard_route_is_get_only``. The Load tab READS
  through ``get_load_statistics`` (a ``TRANSPORT_WEB`` read, the same door ``/statistics`` uses):
  a read is not a write, and the payload is rendered, never stored;
* the WRITES live in ``pages/actions.py``, reachable from a server row that may be operated, and only
  from a row the caller's scope contains. They are POSTs to another module's routes, with the
  session's CSRF token and the target in the body, and nothing about them is owned here: this module
  asks ``actions.controls_for`` for the ROW controls of the tab it is about to render (data, markup
  in ``templates/_table.html``) and ``actions.page_controls`` for that tab's PAGE control (markup in
  this page's own head) — one reading, so the dashboard and the list pages offer one set of controls;
* the players' Message/Kick/Ban and the node verbs are **omitted, not disabled** — a greyed-out
  control still tells a reader "this console can do that", and a control the viewer may not use is
  absent from the markup entirely. The player and node rows arrive in their own cards;
* the dashboard renders the SERVER strip only (the tab's own table decides): the Players tab's
  controls belong to the player strip's own page, and the two screens then stay in step
  because both read the same `pages/actions` declaration.

NO DISCORD CALL ON RENDER. The data comes from :mod:`services.webservice.readmodels`: the
``ServiceBus``' in-process server registry, the node's instance registry and the node registry —
never a Discord attribute. The page renders with ``bot = None`` (early start, after a takeover) and
with a bot that has no Discord connection at all; ``resolve_source`` degrades to an ``EmptySource``
carrying the reason, and every section renders its own empty message.

NAV IS DATA, FILTERED BY THE SAME PREDICATE AS THE ROUTE. This module's nav item declares the
capability its route declares, and the sidebar is rendered from ``permissions.visible_nav`` — the
one function that also decides whether the gate lets the request in, so the sidebar cannot offer a
door the route refuses.
"""
from __future__ import annotations

import logging
import time
import unicodedata
import zlib
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.actions import read_action

from .. import i18n, permissions, readmodels, session
from ..auth import routes as auth_routes
from ..auth import safe_avatar_url
from ..readmodels import logtail
from ..readmodels import views as view_layer
from ..registry import NavItem

__all__ = [
    "DASHBOARD_PATH", "DASHBOARD_CAPABILITY", "DASHBOARD_ROLES", "DASHBOARD_TEMPLATE",
    "DASHBOARD_TITLE", "DASHBOARD_LEAD", "NAV_LABEL", "NAV_GROUP",
    "TABS", "DEFAULT_TAB", "LOG_BOTTOM_MAX_ROWS", "CALM_MESSAGE", "NOT_RUNNING_WORDS",
    "LOAD_TAB", "LOAD_LABEL", "LOAD_SCALES", "LOAD_DEFAULT_SCALE", "LOAD_SCALE_PARAM",
    "LOAD_SCOPE_PARAM", "LOAD_REFRESH_SECONDS", "LOAD_GAUGES",
    "LOAD_GRAPH_PARAM", "LOAD_GRAPH_DEFAULT", "LOAD_GRAPHS",
    "load_scale_from", "load_graph_from", "load_scope", "load_scope_groups", "load_chips",
    "load_payload",
    "load_chart", "load_mini", "load_node_rows", "load_kpis", "load_view", "load_context",
    "reset_load_cache",
    "TONE_CALM", "TONE_ALERT", "TONE_ADVISORY", "ADVISORY_KINDS",
    "UPDATE_AUTO_MSGID", "UPDATE_MANUAL_MSGID",
    "capabilities", "nav_items", "add_routes", "identity_card", "identity_summary",
    "sign_out_context", "log_level", "view_params", "carry_params", "link_params", "view_state_for",
    "normalise_tab", "tab_from", "tab_capability", "allowed_tabs",
    "attention", "longest_list", "log_placement", "log_view",
    "console_context", "may_read_log", "asks_for_expanded_log", "log_refusal",
    "request_source", "scoped_server", "out_of_scope_refusal", "source_for_viewer",
    "row_controls_for",
]

log = logging.getLogger(__name__)

#: the site root. Reserved by the registrar for the shell, which is the owner that registers this.
DASHBOARD_PATH = "/"

#: the capability the route declares AND the nav item names — one string, so the two cannot drift.
DASHBOARD_CAPABILITY = "dashboard.view"

#: who may read the console: Admin, DCS Admin — and a MANAGER (an identity whose resolved scope
#: holds a server's ``managed_by``), which the declaration carries as ``scope_grants`` rather than as
#: a role name: the third kind of identity is DYNAMIC (it depends on the live cluster, not on a role
#: list), so it cannot be spelled here. ``DCS`` is NOT among them: it is the general member role, and
#: the console is not for the general public (the refusal line used to invite it in).
DASHBOARD_ROLES: tuple[str, ...] = ("Admin", "DCS Admin")

DASHBOARD_TEMPLATE = "dashboard.html"
DASHBOARD_TITLE = i18n._("Dashboard")
DASHBOARD_LEAD = i18n._("Live state of every node, instance and server in the cluster. A server "
                        "row you may operate carries its own controls.")
NAV_LABEL = i18n._("Dashboard")
NAV_GROUP = i18n._("Operations")

#: rendered in the top bar's breadcrumb (chrome; the page name itself comes from DASHBOARD_TITLE)
CRUMB_GROUP = i18n._("Cluster")


def crumb(*chrome: str, target: str | None = None) -> tuple[tuple[str, bool], ...]:
    """The top bar's breadcrumb as PARTS: chrome words, then an optional verbatim TARGET name.

    The breadcrumb was ONE composed string, which is why it stayed English: a composed string cannot
    be looked up in a catalog, and wrapping the whole thing per page would duplicate each page's own
    title as a second msgid (a duplicate is a translation that drifts). As PARTS, every chrome word
    is the page's OWN wrapped label reused — ``CRUMB_GROUP`` and ``DASHBOARD_TITLE``/``page.title``/
    an action's ``label`` — and ``base.html`` runs each chrome part through the request's ``_``, the
    same mechanism the sidebar's labels use. A target NAME (a server, a node, a mission) is operator
    data, not chrome: it is marked verbatim and printed as it is, never looked up.
    """
    parts = tuple((part, True) for part in chrome)
    if target is not None:
        return parts + ((target, False),)
    return parts

# ------------------------------------------------------------------------------- the console
#
# THE TABS, THE BANNER AND THE ADAPTIVE LOG — the dashboard's own vocabulary, owned HERE (the read
# models stay frozen): the tab keys are the read models' table names, so a tab can never point at a
# table that does not exist, and the counts come off the Overview rather than being typed anywhere.

#: the key of the ONE tab that is not a table, and its label. Declared here because
#: :data:`TABS` is built from it; the panel itself lives at the end of this module.
LOAD_TAB = "load"
LOAD_LABEL = i18n._("Load")

#: the tab keys, in render order: the read models' own table names, then the ONE tab that is not a
#: table. Derived from the read models' column table, so a fifth table would gain a tab without a
#: second list to keep in sync; :data:`LOAD_TAB` is appended rather than faked as a table spec.
TABS: tuple[str, ...] = tuple(view_layer.TABLE_COLUMNS) + (LOAD_TAB,)

#: the tab a page shows when the URL says nothing (Servers — the thing an operator looks at first)
DEFAULT_TAB = "servers"


def tab_capability(key: str) -> str | None:
    """The capability the tab's STANDALONE PAGE declares, or ``None`` for a tab with no page.

    THE POINT OF THIS FUNCTION: a tab renders a table a reader could also open as a page, so the tab
    must be gated by the *same* predicate that page is gated by. The capability is therefore read
    out of the page module itself — the string its route declares, its nav item names and its
    ``ListPage`` holds — rather than typed here, so the two doors cannot drift apart again (they
    did: a DCS member was refused by ``/nodes`` and served the same table by ``/?tab=nodes``).

    THE LOAD TAB HAS NO PAGE AND NO TABLE. It is offered to the callers that may open THIS console
    and to nobody else, so its capability is the console's own — the one its route declares. It is
    spelled here because there is no page module to read it from.

    The imports are inside the function on purpose: every one of those page modules imports THIS
    module, so a module-level import here would be a cycle.
    """
    if key == LOAD_TAB:
        return DASHBOARD_CAPABILITY
    from . import instances as instances_page
    from . import nodes as nodes_page
    from . import players as players_page
    from . import servers as servers_page
    return {
        "servers": servers_page.PAGE.capability,
        "nodes": nodes_page.PAGE.capability,
        "instances": instances_page.PAGE.capability,
        "players": players_page.PAGE.capability,
    }.get(key)


def allowed_tabs(roles, *, manager: bool = False) -> tuple[str, ...]:
    """The tabs a viewer holding *roles* (and *manager*) may be offered — and may RENDER.

    ONE answer for the strip and for the tab in force, read through :func:`permissions.allows`, so
    the link and the table it points at cannot disagree. ``roles=None`` means "no identity to
    resolve" (a standalone render with no request) and answers every tab, exactly like the log
    decision's default for such a render; a real request always passes the roles it resolved.

    ``manager`` is the third kind of identity (a viewer whose scope holds a server's ``managed_by``
    — see :func:`services.webservice.permissions.manages_console`): the console's pages are declared
    ``scope_grants``, so a manager's view of the cluster is the servers they manage, and they are
    offered exactly those tabs. It defaults to ``False`` (the stricter answer) so a standalone
    render cannot widen anything.
    """
    if roles is None:
        return TABS
    return tuple(key for key in TABS
                 if (capability := tab_capability(key)) is None
                 or permissions.allows(capability, roles, manager=manager))

#: the longest list that still fits above a BOTTOM log panel (see log_placement). One named
#: constant, printed on the page, so raising it is one edit and never a magic number in a template.
LOG_BOTTOM_MAX_ROWS = 8

#: the banner's calm line — the positive statement that replaces four silent sections when nothing
#: is wrong. Copy lives here (with an owner) and never in a template.
CALM_MESSAGE = i18n._("Nothing needs you — every node is heartbeating and no server is down.")

#: the status words that mean "a player cannot join" (everything but RUNNING and the deliberate
#: PAUSED). ONE definition: the banner, the tab dot and the count all read this.
NOT_RUNNING_WORDS: tuple[str, ...] = ("RUNNING", "PAUSED")

#: the players tab's no-match line (a filtered search with zero hits is NOT "nobody is online")
NO_PLAYERS_MATCHED_MESSAGE = i18n._("No players match this search.")

#: the banner TONES, derived from the classes it lists (see :func:`attention`): nothing listed,
#: a fault, or only an advisory.
TONE_CALM = "calm"
TONE_ALERT = "alert"
TONE_ADVISORY = "advisory"

#: The classes that are NOT a fault. A waiting DCS update needs nobody: either the node installs it
#: inside its window or somebody presses the control at their convenience. It is therefore listed
#: with its own dot on the amber tone, never counted among the faults that redden the banner.
ADVISORY_KINDS: tuple[str, ...] = ("update",)

#: The waiting-update sentence, one per AUTO-UPDATE MODE, because the consequence differs: ON means
#: the node will do it itself inside its window, OFF means a control has to be pressed. Both carry
#: the count and the names, like every other class.
UPDATE_AUTO_MSGID = i18n._("DCS update waiting on %(count)d node(s), installed on its own within "
                           "the auto-update window: %(names)s")
UPDATE_MANUAL_MSGID = i18n._("DCS update waiting on %(count)d node(s), auto-update is off, so a "
                             "manual start is needed: %(names)s")

#: The placement overrides the log header offers, in render order. The empty value is "auto".
LOG_POSITION_LABELS: tuple[tuple[str, str], ...] = (
    ("", "auto"), ("bottom", "bottom"), ("right", "right"), ("expanded", "expanded"),
)


# ------------------------------------------------------------------------- who may read the log
#
# THE log decision, and the only one. The bot log is Admin-only (the capability `logs.view` in
# `pages/logs`), and FOUR readers must agree about it: this page's Recent log panel, the Logs page's
# gate, the nav item's visibility and the live stream. They all ask THIS function, which asks the
# capability table through `permissions.allows` — the same predicate the access gate uses, so
# "the panel is rendered" and "the route is reachable" cannot drift. Nothing compares a role name.

def may_read_log(roles) -> bool:
    """Whether a role set may read the bot log (the ONE answer, from the ONE declaration).

    The import is inside the function on purpose: ``pages.logs`` renders its page through this
    module's helpers, so a module-level import here would be a cycle. An undeclared `logs.view`
    answers ``False`` — deny by default, the safe direction.
    """
    from . import logs as logs_page
    return permissions.allows(logs_page.LOGS_CAPABILITY, roles)


def asks_for_expanded_log(request: Request) -> bool:
    """Whether this request asks for the log ITSELF rather than for a placement.

    ``?log=expanded`` hands the log the whole content area — that is a request for the log, not a
    preference about where to put a panel the caller may not have. The other placements
    (``bottom``/``right``) are preferences of a panel that may not exist, so they are validated and
    ignored rather than refused: a bookmarked ``?log=right`` must not turn into a 403 the day the
    reader's role changes. This is the ONE definition of "asked for the log"; the dashboard route
    and both live endpoints read it.
    """
    return view_params(request).position == "expanded"


def log_refusal() -> HTTPException:
    """The refusal a request for the log gets: the gate's own 403 shape, so both faces render it.

    Raised by a *handler* rather than by the gate (the dashboard route itself is reachable), which is
    why its details name the same requirement the gate would have: one wording for one decision.
    """
    from . import logs as logs_page
    return HTTPException(status_code=403,
                         detail=f"Not authorized (needs one of: {', '.join(logs_page.LOGS_ROLES)}).")


class NoLogSource:
    """A source that reports NO log file — what a viewer who may not read the log is given.

    Hiding the panel is not enough: the dashboard must not READ the bot log for a viewer who may not
    see it, and the attention banner counts ERROR lines out of it ("N ERROR line(s) in the visible
    log"), a sentence that is both a disclosure and a lie once there is no visible log. Everything
    else is delegated untouched, so the page's own data is exactly the same object it always read.
    """

    __slots__ = ("_source",)

    def __init__(self, source):
        object.__setattr__(self, "_source", source)

    def __getattr__(self, name):
        if name == "log_path":
            return None
        return getattr(object.__getattribute__(self, "_source"), name)


def source_for_viewer(source, *, may_read_log: bool):
    """*source*, or the same source with no log file when the viewer may not read the log."""
    return source if may_read_log else NoLogSource(source)


def _clean_word(value, limit: int = view_layer.MAX_SORT_LENGTH) -> str:
    """*value* as a control-free, trimmed, capped word — the same cleaning the view layer applies.

    A tab key is a closed vocabulary, but it still arrives in the query string: it is cleaned before
    it is compared, so an attacker-shaped value is dropped instead of compared against anything.
    """
    if value is None:
        return ""
    text = str(value)
    cleaned = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    return cleaned.strip()[:limit]


def normalise_tab(value) -> str:
    """The tab key in force: one of :data:`TABS`, or :data:`DEFAULT_TAB`. Nothing else is passed on."""
    key = _clean_word(value).lower()
    return key if key in TABS else DEFAULT_TAB


def tab_from(request: Request, roles=None, manager=None) -> str:
    """The tab THIS request renders: validated, and one this viewer may actually be shown.

    ``?tab=nonsense`` renders the default tab, and so does a tab whose page refuses this viewer
    (:func:`allowed_tabs`) — a parameter must not be able to open a table the standalone page for it
    would refuse. ``roles`` is passed by a caller that already resolved it (the page resolves the
    roles ONCE per render); ``None`` resolves them from the request through the same
    :func:`permissions.role_names_for` the access gate reads.

    ``manager`` is the third kind of identity: ``None`` (the default) resolves it FROM THE REQUEST,
    because this function always has one and every reader of "which tabs may this viewer see" must
    reach the same answer — a caller passing ``False`` by omission would silently render the default
    tab for a manager (and drop it from the links). The resolution is memoized on the request, so
    asking here does not read the cluster a second time.

    The fallback is the default tab WHEN IT IS OFFERED and otherwise the first tab that is, so the
    invariant holds in both directions: the table rendered is always one the strip offers, even for
    a viewer the default tab is not for (offered == rendered, never "one of them silently").
    """
    key = normalise_tab(request.query_params.get("tab"))
    if roles is None:
        roles = permissions.role_names_for(request)
    if manager is None:
        manager = permissions.manages_console(request)
    allowed = allowed_tabs(roles, manager=manager)
    if key in allowed:
        return key
    if DEFAULT_TAB in allowed:
        return DEFAULT_TAB
    return allowed[0] if allowed else DEFAULT_TAB


# ------------------------------------------------------------------------------- the attention

def _names(views) -> str:
    return ", ".join(view.name for view in views)


def _translated(msgid: str, **params) -> dict:
    """A sentence the template renders in the request's language.

    ``msgid`` is a ``i18n._``-marked literal (so Babel extracts it); ``params`` are its interpolation
    values. The template renders ``{{ _(msgid) % params }}`` through the per-language environment —
    the sentence cannot be translated at build time because its figures are per-request. ``text`` is
    the English rendering (msgid % params), kept for the readers that assert the sentence in words
    (the layout tests) and never drawn: the template prefers ``msgid``.
    """
    return {"msgid": msgid, "params": params, "text": msgid % params}


def attention(state: readmodels.Overview) -> dict:
    """What needs the reader, in WORDS, derived from fields the read models already carry.

    Four FAULT checks and no new data: ``NodeView.online`` (a node not heartbeating),
    ``ServerView.status.word`` (down or paused), ``LogLine.level`` (ERROR lines in the visible
    tail). An empty list is the healthy state and renders the CALM line — the page never shows a
    colour without its word, and never asserts a problem it cannot name.

    Then ONE advisory class, APPENDED LAST so it can never displace a fault line: the nodes whose
    cached DCS answer says a newer build exists (``NodeView.dcs.available is True`` — the CACHE the
    node row's chip reads, never a node call). It is emitted as ONE item per auto-update MODE
    present, because the consequence differs: an auto-updating node installs the build itself inside
    its window, while a node with the switch off waits for somebody to press the control.

    ``tone`` is the box's colour and is NOT ``ok``: ``calm`` when nothing is listed, ``alert`` when
    at least one fault is listed, ``advisory`` when the only items are advisories. ``ok`` keeps its
    meaning — "nothing at all is listed" — so the calm line still renders exactly when it did.
    """
    items: list[dict] = []
    offline = _offline_nodes(state)
    if offline:
        items.append({"kind": "dead", "dot": "dead",
                      **_translated(i18n._("%(count)d node(s) not heartbeating: %(names)s"),
                                    count=len(offline), names=_names(offline))})
    down = _broken_servers(state)
    if down:
        items.append({"kind": "dead", "dot": "dead",
                      **_translated(i18n._("%(count)d server(s) down: %(names)s"),
                                    count=len(down), names=_names(down))})
    paused = _paused_servers(state)
    if paused:
        items.append({"kind": "pause", "dot": "pause",
                      **_translated(i18n._("%(count)d server(s) paused: %(names)s"),
                                    count=len(paused), names=_names(paused))})
    errors = sum(1 for line in state.log.lines if line.level == "ERROR")
    if errors:
        items.append({"kind": "log", "dot": "dead",
                      **_translated(i18n._("%(count)d ERROR line(s) in the visible log"),
                                    count=errors)})
    # the advisory class, LAST: one item per mode, self-updating nodes first
    waiting = _waiting_updates(state)
    if waiting:
        declared = readmodels.declared_node_dcs()
        for automatic, msgid in ((True, UPDATE_AUTO_MSGID), (False, UPDATE_MANUAL_MSGID)):
            group = [node for node in waiting if _auto_update_on(node.name, declared) is automatic]
            if group:
                items.append({"kind": "update", "dot": "update",
                              **_translated(msgid, count=len(group), names=_names(group))})
    faults = [item for item in items if item["kind"] not in ADVISORY_KINDS]
    if not items:
        tone = TONE_CALM
    elif faults:
        tone = TONE_ALERT
    else:
        tone = TONE_ADVISORY
    return {"alerts": items, "ok": not items, "calm": CALM_MESSAGE,
            "count": len(items), "tone": tone}


def _tab_problem(state: readmodels.Overview, key: str):
    """The sentence behind a tab's problem dot — EMPTY when that tab holds nothing wrong.

    ONE classification, shared with :func:`attention`: the classes are the banner's own (a node not
    heartbeating on the Nodes tab, a server not running on the Servers tab), so a tab with a dot
    always names a problem the banner also names. The design of record's tab strip carries dots on
    exactly those two tabs, and an instance row that binds no server is NOT one of them — inventing
    a fourth class here would put a red dot on a tab the banner stays calm about.

    The Players tab has no class: a player count is never a problem, and the ERROR lines class has no
    tab to live on (the log is not a tab).

    The sentence is a RECORD (``{"msgid": …, "params": …}``), never a finished English string: the
    tooltip is CHROME rendered on the page, so its figure (the count) is interpolated at RENDER time
    through the template's guarded ``say`` (``templating.SENTENCE_GLOBAL``) in the request's
    language — the same one mechanism the dialog sentences use. The English rendering is unchanged
    (the msgid IS the source string), and an empty string still means "no problem".
    """
    if key == "nodes":
        offline = _offline_nodes(state)
        if offline:
            return {"msgid": i18n._("{count} node(s) offline"), "params": {"count": len(offline)}}
    elif key == "servers":
        broken = _broken_servers(state)
        if broken:
            return {"msgid": i18n._("{count} server(s) not running"),
                    "params": {"count": len(broken)}}
    return ""


#: the problems, each ONE predicate over a read-model field — the banner and the tab dots both call
#: these, so the two renderings cannot disagree about what a problem is.

def _offline_nodes(state: readmodels.Overview) -> list:
    """``NodeView.online`` — a node that stopped heartbeating."""
    return [node for node in state.nodes if not node.online]


def _broken_servers(state: readmodels.Overview) -> list:
    """``ServerView.status.word`` — a server a player cannot join (RUNNING and the deliberate PAUSED
    are the two words that are not a fault; PAUSED is reported as its own class)."""
    return [server for server in state.servers if server.status.word not in NOT_RUNNING_WORDS]


def _paused_servers(state: readmodels.Overview) -> list:
    return [server for server in state.servers if server.status.word == "PAUSED"]


def _waiting_updates(state: readmodels.Overview) -> list:
    """``NodeView.dcs.available is True`` — a node whose CACHE says a newer DCS build exists.

    ``NodeView.dcs is None`` for a node that declares no ``DCS:`` block, and an OFFLINE node's record
    carries no answer at all, so neither can appear here: the list holds checked, reachable nodes and
    nothing else. ``False`` (checked, current), ``None`` (never checked, or the last check raised) and
    an absent installation are all silent — the banner names no line, counts nothing and claims no
    "up to date".
    """
    return [node for node in state.nodes
            if node.dcs is not None and node.dcs.available is True]


def _auto_update_on(node_name: str, declared: dict | None = None) -> bool:
    """Whether *node_name* will install a waiting DCS update by itself.

    THE MODE, read from CONFIGURATION: ``nodes.yaml``'s ``DCS.autoupdate`` for that node, the same
    block the node row is declared from, so the banner and the row cannot disagree. The key is
    written only when the switch is ON (``core.utils.setup_config.build_node``), so its ABSENCE is
    OFF — the mode that waits for a control to be pressed. No node is asked, and no file is read
    here: the map arrives already parsed (``readmodels.declared_node_dcs``).
    """
    blocks = readmodels.declared_node_dcs() if declared is None else declared
    wanted = readmodels.text(node_name).casefold()
    for name, block in (blocks or {}).items():
        if readmodels.text(name).casefold() == wanted:
            return bool((block or {}).get("autoupdate"))
    return False


def _tab_count(state: readmodels.Overview, key: str):
    """How many rows a tab counts — ``None`` for a tab that counts nothing (the Load tab).

    ``None`` and not ``0``: the strip omits the badge entirely, because a "0" on a chart tab would
    read as an empty chart rather than as a tab that has no count to state.
    """
    if key == LOAD_TAB:
        return None
    return {"servers": state.server_count, "nodes": state.node_count,
            "instances": state.instance_count, "players": state.player_count}[key]


def tab_views(state: readmodels.Overview, tab: str, carry: dict | None = None,
              *, roles=None, manager: bool = False) -> list[dict]:
    """The tab strip as data: the key, its label, its count, its problem and the link it renders.

    The strip is FILTERED by :func:`allowed_tabs`: the tabs this viewer may be offered are exactly
    the tabs this viewer may render, because one function answers both. A tab whose page would
    refuse the viewer is not merely unlinked — it is not in the strip, so it cannot be discovered by
    reading the markup either.

    The link carries the level, the tab and (Finding 2) the active search, so changing tabs keeps
    the filter the page rendered — the same one-mechanism rule as the sort links (the URL is the
    state, and nothing lives in the DOM). ``carry`` is the validated pair set the caller wants kept;
    a caller that passes none gets a link that carries only the tab itself.
    """
    carried = dict(carry or {})
    strip: list[dict] = []
    for key in allowed_tabs(roles, manager=manager):
        query = dict(carried)
        query["tab"] = key
        problem = _tab_problem(state, key)
        strip.append({
            "key": key,
            "label": LOAD_LABEL if key == LOAD_TAB else view_layer.TABLE_LABELS.get(key, key),
            "count": _tab_count(state, key),
            "active": key == tab,
            "problem": bool(problem),
            "problem_text": problem,
            "link": f"{DASHBOARD_PATH}?{urlencode(query)}",
        })
    return strip


# ------------------------------------------------------------------------------- the Load tab
#
# THE FIRST TAB THAT IS NOT A TABLE. Every other tab key is a read-model table name, because a tab
# renders the table a reader could also open as a page. Load renders a CHART, so it has no table, no
# count and no problem class — the three places below say so instead of faking a table spec:
# :func:`tab_capability` (no page of its own, gated by the console's capability),
# :func:`_tab_count` (no badge) and :func:`tab_views` (no dot, and the module's own label).
#
# ITS NUMBERS ARE A LIVE WINDOW, NOT A PERIOD. The bot writes one row per SERVER per minute
# (``services/monitoring/service.py``), so a node's series is the SUM of its servers' rows and the
# tick can be no faster than :data:`LOAD_REFRESH_SECONDS`. The series itself is aggregated in SQL by
# the ``get_load_statistics`` action; this module only shapes it into markup.

#: the tab key. Deliberately NOT a key of ``readmodels.views.TABLE_COLUMNS``.
#: (:data:`LOAD_TAB` / :data:`LOAD_LABEL` are declared above, where :data:`TABS` is built.)

#: the scales the load read publishes, in render order: the read's own name and the chip word.
LOAD_SCALES: tuple[tuple[str, str], ...] = (
    ("1h", i18n._("1 h")), ("6h", i18n._("6 h")), ("24h", i18n._("24 h")),
    ("7d", i18n._("7 d")), ("30d", i18n._("30 d")),
)
LOAD_DEFAULT_SCALE = "24h"
LOAD_SCALE_PARAM = "scale"
LOAD_SCOPE_PARAM = "scope"

#: the three scope kinds, spelled in the selector's own values.
LOAD_SCOPE_CLUSTER = "cluster"
LOAD_SCOPE_NODE = "node:"
LOAD_SCOPE_SERVER = "server:"

#: THE TICK. One fetch per minute: a faster page says nothing new, because the bot writes one row
#: per server per minute. The payload is CACHED for this long, so every render in between (the live
#: stream ticks every ``live.DEFAULT_REFRESH_SECONDS``) produces the SAME markup — and the stream's
#: hash comparison then sends nothing at all.
LOAD_REFRESH_SECONDS = 60

#: The chart geometry, in SVG user units. One place, so the page and its mini charts agree.
LOAD_CHART_WIDTH = 1086
LOAD_CHART_HEIGHT = 250
LOAD_CHART_LEFT = 40
LOAD_CHART_RIGHT = 1040
LOAD_CHART_TOP = 12
LOAD_CHART_BOTTOM = 226
LOAD_MINI_WIDTH = 520
LOAD_MINI_HEIGHT = 124
LOAD_MINI_LEFT = 40
LOAD_MINI_RIGHT = 510
LOAD_MINI_TOP = 12
LOAD_MINI_BOTTOM = 102
LOAD_SPARK_WIDTH = 118
LOAD_SPARK_HEIGHT = 26

NO_VALUE = "\u2014"

#: The words and sentences of the panel.
LOAD_SCOPE_LABEL = i18n._("Scope")
SCOPE_CLUSTER = i18n._("Cluster (all servers)")
SCOPE_NODE_GROUP = i18n._("Node")
SCOPE_SERVER_GROUP = i18n._("Server")
SCOPE_NODE_OPTION = i18n._("(node)")
SCOPE_APPLY = i18n._("Show")
LOAD_LIVE = i18n._("window ends {time} \u00b7 a sample every minute")
LOAD_EMPTY = i18n._("No load samples were written in this window yet.")
LOAD_CARD = i18n._("Server load")
LOAD_META_CLUSTER = i18n._("all servers, summed \u00b7 last {window} \u00b7 {bucket} buckets")
LOAD_META_NODE = i18n._("{node}, summed \u00b7 last {window} \u00b7 {bucket} buckets")
LOAD_META_SERVER = i18n._("{server} \u00b7 last {window} \u00b7 {bucket} buckets")
LOAD_LEGEND_PLAYERS = i18n._("Players")
LOAD_LEGEND_CPU = i18n._("CPU %")
LOAD_NOW = i18n._("now")

KPI_PLAYERS = i18n._("Players now")
KPI_CPU = i18n._("CPU")
KPI_FPS = i18n._("FPS")
KPI_PING = i18n._("Ping")
NOTE_PEAK = i18n._("peak {peak} in this window")
NOTE_LAST_HOUR = i18n._("last hour, average")
NOTE_INSTANCES = i18n._("average across instances")
NOTE_AVERAGE = i18n._("average")

NODES_CARD = i18n._("Nodes")
NODES_META = i18n._("pool, queues and wait-time \u00b7 now")
COL_NODE = i18n._("Node")
COL_STATE = i18n._("State")
COL_POOL = i18n._("Pool")
COL_QUEUED = i18n._("Queued")
COL_WAIT = i18n._("Wait")
COL_DCS_QUEUE = i18n._("DCS queue")
COL_ASYNCIO_QUEUE = i18n._("asyncio queue")
COL_LAST_HOUR = i18n._("Last hour")
STATE_ONLINE = i18n._("online")
STATE_OFFLINE = i18n._("offline")
MASTER_TAG = i18n._("master")

MINI_FPS = i18n._("FPS / Ping")
MINI_FPS_META = i18n._("average across instances")
MINI_MEMORY = i18n._("Memory")
MINI_MEMORY_META = i18n._("RAM + paged, MB")
MINI_DISK = i18n._("Disk")
MINI_DISK_META = i18n._("KB/s read (blue) \u00b7 write (orange)")
MINI_NETWORK = i18n._("Network")
MINI_NETWORK_META = i18n._("KB/s sent (blue) \u00b7 recv (green)")

#: The gauges a load row carries, in payload order (the action's own list).
LOAD_GAUGES = ("players", "cpu", "mem_ram", "mem_paged", "read", "write", "sent", "recv", "fps",
               "ping")

#: the graph the big chart draws. The default is the players/CPU pair; the four mini cards carry
#: the other names, so picking one swaps it into the big chart.
LOAD_GRAPH_PARAM = "graph"
LOAD_GRAPH_DEFAULT = "players"

#: The big chart's graphs, by name: the pair of series each draws and the card words it wears.
#: ``right`` is a second axis' unit and top — only the players/CPU graph has one, because a player
#: count and a percentage are not the same quantity; every other graph draws both series on ONE
#: scale. ``factor`` converts a stored figure into the unit the card states (bytes/s -> KB/s),
#: exactly as ``load_mini`` does, so the big chart and the mini card cannot disagree.
LOAD_GRAPHS: dict[str, dict] = {
    "players": {"title": LOAD_CARD, "meta": None, "right": ("%", 100.0),
                "series": (("players", 1.0), ("cpu", 1.0))},
    "fps": {"title": MINI_FPS, "meta": MINI_FPS_META,
            "series": (("fps", 1.0), ("ping", 1.0))},
    "memory": {"title": MINI_MEMORY, "meta": MINI_MEMORY_META,
               "series": (("mem_ram", 1.0), ("mem_paged", 1.0))},
    "disk": {"title": MINI_DISK, "meta": MINI_DISK_META,
             "series": (("read", 1.0), ("write", 1.0))},
    "network": {"title": MINI_NETWORK, "meta": MINI_NETWORK_META,
                "series": (("sent", 1 / 1024), ("recv", 1 / 1024))},
}


def load_scale_from(value) -> str:
    """The scale a request names, or :data:`LOAD_DEFAULT_SCALE` for anything the read does not publish."""
    wanted = _clean_word(value).lower()
    return wanted if wanted in {name for name, _label in LOAD_SCALES} else LOAD_DEFAULT_SCALE


def load_graph_from(value) -> str:
    """The graph a request names, or :data:`LOAD_GRAPH_DEFAULT` for anything the page does not draw."""
    wanted = _clean_word(value).lower()
    return wanted if wanted in LOAD_GRAPHS else LOAD_GRAPH_DEFAULT


def _visible_names(entries) -> list[str]:
    """The names of *entries*, deduplicated and in order — the caller's SCOPED view names."""
    names: list[str] = []
    for entry in entries or ():
        name = readmodels.text(readmodels.safe(lambda entry=entry: getattr(entry, "name", None)))
        if name and name not in names:
            names.append(name)
    return names


def _match(name: str, names: list[str]) -> str | None:
    """*name* as spelled in *names* (case-insensitive), or ``None`` when it is not one of them."""
    wanted = readmodels.text(name).casefold()
    for candidate in names:
        if candidate.casefold() == wanted:
            return candidate
    return None


def load_scope(request: Request, state: readmodels.Overview) -> dict:
    """The scope in force: ``{token, kind, node, server}``, bounded by the caller's OWN view.

    THE BOUND LIVES HERE, not in the action: the read accepts any node or server name, so the route
    is where a name must be proven to be inside the caller's scope before it is passed on. A name
    the caller cannot see is REFUSED with the console's own 403 (:func:`out_of_scope_refusal`) —
    the same refusal for a name that exists out of scope and one that does not exist at all.

    CLUSTER AND NODE SCOPE NEED THE WHOLE INSTALLATION. The series is summed by NODE, so a node's
    line contains every server on that node — including servers a restricted caller may not read.
    A caller whose scope is restricted is therefore offered SERVER scope only (one line per server
    they hold); naming ``cluster`` or ``node:…`` is refused rather than narrowed, because a narrowed
    answer would still have read the other servers' rows.
    """
    nodes = _visible_names(getattr(state, "nodes", ()) or ())
    servers = _visible_names(getattr(state, "servers", ()) or ())
    unrestricted = permissions.scope_for(request).unrestricted
    raw = _clean_word(request.query_params.get(LOAD_SCOPE_PARAM))

    def refused() -> HTTPException:
        return out_of_scope_refusal()

    if not unrestricted:
        # a restricted caller: only a server they hold is a scope this page can read for them
        if raw.startswith(LOAD_SCOPE_CLUSTER) or raw.startswith(LOAD_SCOPE_NODE):
            raise refused()
        if raw.startswith(LOAD_SCOPE_SERVER):
            name = _match(raw[len(LOAD_SCOPE_SERVER):], servers)
            if name is None:
                raise refused()
            return {"token": f"{LOAD_SCOPE_SERVER}{name}", "kind": "server", "node": None,
                    "server": name}
        choice = servers[0] if servers else None
        return {"token": f"{LOAD_SCOPE_SERVER}{choice}" if choice else "",
                "kind": "server" if choice else "none", "node": None, "server": choice}

    if raw.startswith(LOAD_SCOPE_NODE):
        name = _match(raw[len(LOAD_SCOPE_NODE):], nodes)
        if name is None:
            raise refused()
        return {"token": f"{LOAD_SCOPE_NODE}{name}", "kind": "node", "node": name, "server": None}
    if raw.startswith(LOAD_SCOPE_SERVER):
        name = _match(raw[len(LOAD_SCOPE_SERVER):], servers)
        if name is None:
            raise refused()
        return {"token": f"{LOAD_SCOPE_SERVER}{name}", "kind": "server", "node": None,
                "server": name}
    if raw and raw != LOAD_SCOPE_CLUSTER:
        raise refused()
    return {"token": LOAD_SCOPE_CLUSTER, "kind": "cluster", "node": None, "server": None}


def load_scope_groups(request: Request, state: readmodels.Overview, scope: dict) -> list[dict]:
    """The selector's groups: Cluster then the nodes then the servers, each bounded by the view."""
    unrestricted = permissions.scope_for(request).unrestricted
    groups: list[dict] = []
    if unrestricted:
        groups.append({"label": "", "options": [
            {"value": LOAD_SCOPE_CLUSTER, "label": SCOPE_CLUSTER, "name": "", "suffix": "",
             "active": scope["kind"] == "cluster"}]})
        nodes = _visible_names(getattr(state, "nodes", ()) or ())
        if nodes:
            groups.append({"label": SCOPE_NODE_GROUP, "options": [
                {"value": f"{LOAD_SCOPE_NODE}{name}", "label": "", "name": name,
                 "suffix": SCOPE_NODE_OPTION,
                 "active": scope["kind"] == "node" and scope["node"] == name}
                for name in nodes]})
    servers = _visible_names(getattr(state, "servers", ()) or ())
    if servers:
        groups.append({"label": SCOPE_SERVER_GROUP, "options": [
            {"value": f"{LOAD_SCOPE_SERVER}{name}", "label": "", "name": name, "suffix": "",
             "active": scope["kind"] == "server" and scope["server"] == name}
            for name in servers]})
    return groups


def load_chips(scale: str, scope: dict, graph: str = LOAD_GRAPH_DEFAULT) -> list[dict]:
    """The scale chips as links, the scale in force marked active, the scope and graph carried."""
    chips = []
    for name, label in LOAD_SCALES:
        query = {"tab": LOAD_TAB, LOAD_SCALE_PARAM: name}
        if scope["token"]:
            query[LOAD_SCOPE_PARAM] = scope["token"]
        if graph != LOAD_GRAPH_DEFAULT:
            query[LOAD_GRAPH_PARAM] = graph
        chips.append({"label": label, "active": name == scale,
                      "url": f"{DASHBOARD_PATH}?{urlencode(query)}"})
    return chips


def load_payload_key(scale: str, scope: dict) -> tuple:
    """The cache key of one scope's payload — one row of the read, not per caller."""
    return (scale, scope["node"] or "", scope["server"] or "")


#: last SUCCESSFUL payload per key, with the monotonic second it was fetched at.
_LOAD_CACHE: dict[tuple, tuple[float, dict]] = {}


def reset_load_cache() -> None:
    """Drop the cached payloads (the clock is process-wide state, like every other cache here)."""
    _LOAD_CACHE.clear()


async def load_payload(request: Request, scale: str, scope: dict) -> dict | None:
    """The load action's payload for *scale*/*scope*, re-read at most once per :data:`LOAD_REFRESH_SECONDS`.

    The cache is what makes the 60 s tick the ONLY cadence: the live stream re-renders every couple
    of seconds, and inside one minute every render answers the SAME payload, so the rendered fragment
    hashes the same and nothing goes on the wire. A failed read is never cached — the next tick
    retries it.
    """
    from . import actions as actions_page  # local: ``pages.actions`` reaches this module

    key = load_payload_key(scale, scope)
    now = time.monotonic()
    cached = _LOAD_CACHE.get(key)
    if cached is not None and now - cached[0] < LOAD_REFRESH_SECONDS:
        return cached[1]
    result = await read_action("get_load_statistics", actions_page.context_for(request),
                               scale=scale, node=scope["node"], server=scope["server"])
    data = getattr(result, "data", None)
    if not (getattr(result, "success", False) and isinstance(data, Mapping)):
        return None
    _LOAD_CACHE[key] = (now, data)
    return data


# ------------------------------------------------------------------------------- the geometry
#
# Everything a chart needs is computed HERE and handed to the template as numbers, so the template
# computes nothing and the page ships no charting library. Ids are derived with ``zlib.crc32``
# because ``hash()`` is salted per process: an id that changes between two renders of one page is a
# broken reference (an SVG gradient that resolves to nothing).

def _svg_id(*parts: Any) -> str:
    """A stable element id for *parts* — same parts, same id, in every process."""
    seed = "|".join(str(part) for part in parts).encode("utf-8")
    return f"ld{format(zlib.crc32(seed), '08x')}"


def _value(value: Any) -> float | None:
    """A payload figure as a float, or ``None`` — a missing bucket is a GAP, never a zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nice_maximum(values, *, minimum: float = 1.0) -> float:
    """>= max(values), rounded up to 1/2/5 × 10ⁿ — an axis a reader can divide in the head."""
    peak = max((value for value in values if value is not None), default=0.0)
    peak = max(peak, minimum)
    magnitude = 10.0 ** max(0, len(str(int(peak))) - 1)
    for factor in (1, 2, 5, 10):
        if peak <= factor * magnitude:
            return factor * magnitude
    return 10 * magnitude


def _segments(values, *, left: float, right: float, top: float, bottom: float,
              maximum: float) -> list[list[tuple[float, float]]]:
    """``values`` as drawn runs — a ``None`` ends a run, so the line shows a gap, not a dip to 0."""
    count = len(values)
    runs: list[list[tuple[float, float]]] = []
    run: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        number = _value(value)
        if number is None:
            if run:
                runs.append(run)
                run = []
            continue
        x = left if count <= 1 else left + (right - left) * index / (count - 1)
        ratio = 0.0 if maximum <= 0 else min(max(number, 0.0), maximum) / maximum
        run.append((x, bottom - (bottom - top) * ratio))
    if run:
        runs.append(run)
    return runs


def _points(run) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in run)


def _areas(runs, *, baseline: float) -> list[str]:
    """One closed polygon per run: down to the baseline, across, and back up."""
    return [f"{_points(run)} {run[-1][0]:.1f},{baseline:.1f} {run[0][0]:.1f},{baseline:.1f}"
            for run in runs if run]


def _x_labels(times: list[str], bucket_seconds: int, *, left: float, right: float,
              baseline: float, slots: int = 12) -> list[dict]:
    """Up to *slots* evenly spaced time labels under the x axis."""
    count = len(times)
    if count == 0:
        return []
    step = max(1, count // slots)
    labels = []
    for index in range(0, count, step):
        x = left if count <= 1 else left + (right - left) * index / (count - 1)
        label = _clock(times[index], bucket_seconds)
        if label:
            labels.append({"x": f"{x:.1f}", "y": f"{baseline + 15:.1f}", "label": label})
    return labels


def _moment(value: Any) -> datetime | None:
    """A payload time (an ISO string, or a datetime from a direct read) as a datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _clock(value: Any, bucket_seconds: int) -> str:
    """A bucket time as the axis label its scale needs: a clock for minutes, a day+hour for hours."""
    moment = _moment(value)
    if moment is None:
        return ""
    return moment.strftime("%d.%m %H:00" if bucket_seconds >= 3600 else "%H:%M")


def _load_number(value: Any, *, digits: int = 0) -> str:
    """A figure as printed text, or :data:`NO_VALUE`."""
    number = _value(value)
    if number is None:
        return NO_VALUE
    return f"{number:,.{digits}f}"


def _load_percent(value: Any) -> str:
    number = _value(value)
    return NO_VALUE if number is None else f"{round(number)} %"


def _mean(values) -> float | None:
    """The mean of the figures that are present, or ``None`` when none is."""
    present = [number for number in (_value(value) for value in values) if number is not None]
    return sum(present) / len(present) if present else None


def _bucket_word(bucket_seconds: int) -> str:
    """"1 min" / "5 min" / "1 h" — the bucket width a reader sees, unit included."""
    return f"{bucket_seconds // 60} min" if bucket_seconds < 3600 else f"{bucket_seconds // 3600} h"


def _scaled(value: Any, factor: float) -> float | None:
    """A payload figure times the graph's own factor — the unit the card states — or ``None``."""
    number = _value(value)
    return None if number is None else number * factor


def load_chart(rows: list[Mapping], window: Mapping, key: str,
               graph: str = LOAD_GRAPH_DEFAULT) -> dict:
    """The main chart: the selected graph's two series, drawn over the window with a now edge.

    The players graph is the one with TWO axes: players as a filled area on a count axis and CPU %
    dashed on a percentage axis, because a count and a percentage are not the same quantity. Every
    other graph — the mini cards' own names — draws its pair on ONE scale with the same factor the
    mini uses, so the big chart says exactly what the card said.
    """
    spec = LOAD_GRAPHS.get(graph) or LOAD_GRAPHS[LOAD_GRAPH_DEFAULT]
    (first_key, first_factor), (second_key, second_factor) = spec["series"]
    times = [row.get("time") for row in rows]
    first = [_scaled(row.get(first_key), first_factor) for row in rows]
    second = [_scaled(row.get(second_key), second_factor) for row in rows]
    bucket_seconds = int(window.get("bucket_seconds") or 60)
    right = spec.get("right")
    if right:
        first_max = _nice_maximum(first, minimum=4)
        second_max = float(right[1])
    else:
        first_max = second_max = _nice_maximum(list(first) + list(second), minimum=1.0)
    runs = _segments(first, left=LOAD_CHART_LEFT, right=LOAD_CHART_RIGHT, top=LOAD_CHART_TOP,
                     bottom=LOAD_CHART_BOTTOM, maximum=first_max)
    second_runs = _segments(second, left=LOAD_CHART_LEFT, right=LOAD_CHART_RIGHT,
                            top=LOAD_CHART_TOP, bottom=LOAD_CHART_BOTTOM, maximum=second_max)
    grid = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = LOAD_CHART_BOTTOM - (LOAD_CHART_BOTTOM - LOAD_CHART_TOP) * fraction
        grid.append({"y": f"{y:.1f}", "ty": f"{y + 3.4:.1f}", "x1": LOAD_CHART_LEFT,
                     "x2": LOAD_CHART_RIGHT, "left": f"{round(first_max * fraction)}",
                     "right": (f"{round(second_max * fraction)} {right[0]}" if right else "")})
    return {
        "id": _svg_id(key, graph, "load"),
        "gradient": _svg_id(key, graph, "area"),
        "width": LOAD_CHART_WIDTH,
        "height": LOAD_CHART_HEIGHT,
        "grid": grid,
        "areas": _areas(runs, baseline=LOAD_CHART_BOTTOM),
        "players": [_points(run) for run in runs],
        "cpu": [_points(run) for run in second_runs],
        "now": {"x": LOAD_CHART_RIGHT, "top": LOAD_CHART_TOP, "bottom": LOAD_CHART_BOTTOM},
        "x_labels": _x_labels(times, bucket_seconds, left=LOAD_CHART_LEFT, right=LOAD_CHART_RIGHT,
                              baseline=LOAD_CHART_BOTTOM),
        "axis": {"left": [{"x": LOAD_CHART_LEFT - 7, "y": row["y"], "ty": row["ty"],
                           "text": row["left"]} for row in grid],
                 "right": [{"x": LOAD_CHART_RIGHT + 8, "y": row["y"], "ty": row["ty"],
                            "text": row["right"]} for row in grid] if right else []},
    }


def load_mini(rows: list[Mapping], window: Mapping, key: str, *, title, meta,
              series: tuple[tuple[str, str, float], ...]) -> dict:
    """One mini chart: *series* is ``((row key, colour, factor), …)`` on one shared y scale.

    ``factor`` converts a stored figure into the unit the card states (bytes/s -> KB/s), so the
    conversion lives with the definition of the card rather than in the template.
    """
    bucket_seconds = int(window.get("bucket_seconds") or 60)
    times = [row.get("time") for row in rows]
    values = [[None if _value(row.get(name)) is None else _value(row.get(name)) * factor
               for row in rows] for name, _colour, factor in series]
    maximum = _nice_maximum([number for column in values for number in column], minimum=1.0)
    drawn = []
    for (name, colour, _factor), column in zip(series, values):
        runs = _segments(column, left=LOAD_MINI_LEFT, right=LOAD_MINI_RIGHT, top=LOAD_MINI_TOP,
                         bottom=LOAD_MINI_BOTTOM, maximum=maximum)
        drawn.append({"colour": colour, "points": [_points(run) for run in runs]})
    grid = []
    for fraction in (0.0, 0.5, 1.0):
        y = LOAD_MINI_BOTTOM - (LOAD_MINI_BOTTOM - LOAD_MINI_TOP) * fraction
        grid.append({"y": f"{y:.1f}", "ty": f"{y + 3.4:.1f}", "x1": LOAD_MINI_LEFT,
                     "x2": LOAD_MINI_RIGHT, "x": LOAD_MINI_LEFT - 7,
                     "text": _load_number(maximum * fraction)})
    return {"id": _svg_id(key, "mini", title), "title": title, "meta": meta,
            "width": LOAD_MINI_WIDTH, "height": LOAD_MINI_HEIGHT, "grid": grid,
            "series": drawn,
            "x_labels": _x_labels(times, bucket_seconds, left=LOAD_MINI_LEFT,
                                  right=LOAD_MINI_RIGHT, baseline=LOAD_MINI_BOTTOM, slots=8)}


def _spark(values, *, width: float = LOAD_SPARK_WIDTH, height: float = LOAD_SPARK_HEIGHT) -> list[str]:
    """A node's last-hour sparkline as drawn runs (a missing minute is a gap)."""
    runs = _segments(values, left=0.0, right=width, top=3.0, bottom=height - 9.0,
                     maximum=_nice_maximum(values, minimum=1.0))
    return [_points(run) for run in runs]


def load_node_rows(payload_nodes, scope: dict) -> list[dict]:
    """The per-node table: one row per node in the payload, DASHES and "offline" when it has no rows.

    A node with no rows in the window has ``None`` in every figure — never ``0``. Printing zeros
    would read as an idle node; the row says ``offline`` and shows dashes instead.
    """
    rows = []
    for entry in payload_nodes or ():
        if not isinstance(entry, Mapping):
            continue
        figures = {gauge: entry.get(gauge) for gauge in
                   ("pool_available", "requests_queued", "requests_wait_ms", "dcs_queue",
                    "asyncio_queue")}
        online = any(_value(value) is not None for value in figures.values())
        rows.append({
            "name": readmodels.text(entry.get("node")),
            "online": online,
            "state": STATE_ONLINE if online else STATE_OFFLINE,
            "pool": _load_number(figures["pool_available"]),
            "queued": _load_number(figures["requests_queued"]),
            "wait": (NO_VALUE if _value(figures["requests_wait_ms"]) is None
                     else f"{_load_number(figures['requests_wait_ms'])} ms"),
            "dcs_queue": _load_number(figures["dcs_queue"]),
            "asyncio_queue": _load_number(figures["asyncio_queue"]),
            "spark": _spark(entry.get("spark") or []),
        })
    return rows


def load_kpis(rows: list[Mapping], window: Mapping) -> list[dict]:
    """The four KPI tiles and the sentence that qualifies each figure."""
    present = [row for row in rows if _value(row.get("players")) is not None]
    now = _value(present[-1].get("players")) if present else None
    peak = max((_value(row.get("players")) for row in present), default=None)
    end = _moment(window.get("end"))
    hour = [row for row in rows
            if _moment(row.get("time")) is not None and end is not None
            and (_moment(row.get("time")) - end).total_seconds() >= -3600]
    return [
        {"label": KPI_PLAYERS, "value": _load_number(now), "unit": "",
         "note": {"msgid": NOTE_PEAK, "params": {"peak": _load_number(peak)}}},
        {"label": KPI_CPU, "value": _load_percent(_mean(row.get("cpu") for row in hour)), "unit": "",
         "note": {"msgid": NOTE_LAST_HOUR, "params": {}}},
        {"label": KPI_FPS, "value": _load_number(_mean(row.get("fps") for row in rows), digits=0),
         "unit": "", "note": {"msgid": NOTE_INSTANCES, "params": {}}},
        {"label": KPI_PING, "value": _load_number(_mean(row.get("ping") for row in rows),
                                                  digits=0),
         "unit": i18n._("ms"), "note": {"msgid": NOTE_AVERAGE, "params": {}}},
    ]


def _meta_sentence(scope: dict, window: Mapping) -> dict:
    """The card's meta line: which scope, which window and which bucket width."""
    window_word = dict(LOAD_SCALES).get(str(window.get("scale") or ""), "")
    bucket = _bucket_word(int(window.get("bucket_seconds") or 60))
    params = {"window": window_word, "bucket": bucket}
    if scope["kind"] == "node":
        return {"msgid": LOAD_META_NODE, "params": dict(params, node=scope["node"])}
    if scope["kind"] == "server":
        return {"msgid": LOAD_META_SERVER, "params": dict(params, server=scope["server"])}
    return {"msgid": LOAD_META_CLUSTER, "params": params}


def _graph_url(scope: dict, window: Mapping, graph: str) -> str:
    """The link that redraws the big chart as *graph*, carrying the scale and the scope."""
    query = {"tab": LOAD_TAB, LOAD_SCALE_PARAM: window.get("scale") or LOAD_DEFAULT_SCALE}
    if scope["token"]:
        query[LOAD_SCOPE_PARAM] = scope["token"]
    if graph != LOAD_GRAPH_DEFAULT:
        query[LOAD_GRAPH_PARAM] = graph
    return f"{DASHBOARD_PATH}?{urlencode(query)}"


def load_view(payload: Mapping | None, *, scale: str, scope: dict, groups: list[dict],
              masters: set[str], graph: str = LOAD_GRAPH_DEFAULT) -> dict:
    """Everything ``_load_panel.html`` reads: chips, selector, KPIs, charts and node table.

    A missing payload renders ONE sentence and no chart: a chart with no series is the "designed
    but nothing feeds it" shape, and reading it as "an idle cluster" would be a lie.

    *graph* is which pair the big chart draws. The default (players/CPU) keeps the card's own title
    and the two-legend; any other graph wears the mini card's title and colour note, because it is
    the same pair of series at a larger size.
    """
    window = dict((payload or {}).get("window") or {})
    rows = [row for row in ((payload or {}).get("series") or []) if isinstance(row, Mapping)]
    window["scale"] = (payload or {}).get("scale") or scale
    bucket_seconds = int(window.get("bucket_seconds") or 60)
    has_data = any(_value(row.get("players")) is not None for row in rows)
    spec = LOAD_GRAPHS.get(graph) or LOAD_GRAPHS[LOAD_GRAPH_DEFAULT]
    key = f"{scope['token']}|{window['scale']}|{graph}"
    nodes = load_node_rows((payload or {}).get("nodes"), scope)
    for row in nodes:
        row["master"] = row["name"] in masters
    return {
        "scale": scale,
        "path": DASHBOARD_PATH,
        "tab": LOAD_TAB,
        "scale_param": LOAD_SCALE_PARAM,
        "scope_param": LOAD_SCOPE_PARAM,
        "graph_param": LOAD_GRAPH_PARAM,
        "graph": graph,
        "back": LOAD_CARD,
        "chips": load_chips(scale, scope, graph),
        "scope": scope,
        "groups": groups,
        "labels": {"scope": LOAD_SCOPE_LABEL, "apply": SCOPE_APPLY},
        "live": {"msgid": LOAD_LIVE,
                 "params": {"time": _clock(window.get("end"), 0) or NO_VALUE}},
        "card": {"title": spec["title"], "meta": _meta_sentence(scope, window),
                 "series": spec.get("meta")},
        "legend": {"on": bool(spec.get("right")), "players": LOAD_LEGEND_PLAYERS,
                   "cpu": LOAD_LEGEND_CPU, "now": LOAD_NOW},
        "kpis": load_kpis(rows, window) if rows else [],
        "chart": load_chart(rows, window, key, graph) if rows else None,
        "nodes": nodes,
        "nodes_card": {"title": NODES_CARD, "meta": NODES_META, "master": MASTER_TAG},
        "node_labels": {"node": COL_NODE, "state": COL_STATE, "pool": COL_POOL,
                        "queued": COL_QUEUED, "wait": COL_WAIT, "dcs_queue": COL_DCS_QUEUE,
                        "asyncio_queue": COL_ASYNCIO_QUEUE, "last_hour": COL_LAST_HOUR},
        "no_value": NO_VALUE,
        "minis": ([
            {"name": "fps", "title": MINI_FPS, "meta": MINI_FPS_META,
             "url": _graph_url(scope, window, "fps"), "active": graph == "fps",
             "chart": load_mini(rows, window, key, title="fps", meta=MINI_FPS_META,
                                series=(("fps", "blue", 1.0), ("ping", "red", 1.0)))},
            {"name": "memory", "title": MINI_MEMORY, "meta": MINI_MEMORY_META,
             "url": _graph_url(scope, window, "memory"), "active": graph == "memory",
             "chart": load_mini(rows, window, key, title="memory", meta=MINI_MEMORY_META,
                                series=(("mem_ram", "blue", 1.0), ("mem_paged", "orange", 1.0)))},
            {"name": "disk", "title": MINI_DISK, "meta": MINI_DISK_META,
             "url": _graph_url(scope, window, "disk"), "active": graph == "disk",
             "chart": load_mini(rows, window, key, title="disk", meta=MINI_DISK_META,
                                series=(("read", "blue", 1.0), ("write", "orange", 1.0)))},
            {"name": "network", "title": MINI_NETWORK, "meta": MINI_NETWORK_META,
             "url": _graph_url(scope, window, "network"), "active": graph == "network",
             "chart": load_mini(rows, window, key, title="network", meta=MINI_NETWORK_META,
                                series=(("sent", "blue", 1 / 1024), ("recv", "green", 1 / 1024)))},
        ] if has_data and rows else []),
        "empty": LOAD_EMPTY,
        "has_data": has_data,
        "bucket_seconds": bucket_seconds,
        "players_url": _graph_url(scope, window, LOAD_GRAPH_DEFAULT),
    }


async def load_context(request: Request, state: readmodels.Overview) -> dict:
    """The Load tab's whole state for THIS request: the scope it may read and the read's payload."""
    scale = load_scale_from(request.query_params.get(LOAD_SCALE_PARAM))
    graph = load_graph_from(request.query_params.get(LOAD_GRAPH_PARAM))
    scope = load_scope(request, state)
    groups = load_scope_groups(request, state, scope)
    masters = {readmodels.text(readmodels.safe(lambda entry=entry: getattr(entry, "name", None)))
               for entry in getattr(state, "nodes", ()) or ()
               if readmodels.safe(lambda entry=entry: getattr(entry, "master", False))}
    payload = None
    if scope["kind"] != "none":
        payload = await load_payload(request, scale, scope)
    return load_view(payload, scale=scale, scope=scope, groups=groups, masters=masters,
                     graph=graph)


# ------------------------------------------------------------------------- the adaptive log

def longest_list(state: readmodels.Overview) -> int:
    """The longest tab list — the ONE input the placement rule reads (never the viewport)."""
    return max(len(state.nodes), len(state.instances), len(state.servers))


def log_placement(state: readmodels.Overview, override=None) -> str:
    """Where the log column goes: ``bottom`` / ``right`` / ``expanded``.

    SERVER-SIDE and a pure function of the COUNTS, never a viewport media query — the same 1440px
    screen is a one-server and a fifty-server installation, and only the data knows which. An
    explicit ``?log=`` (validated against :data:`~..readmodels.views.LOG_POSITIONS`) always wins; an
    unknown value is not an error, it falls back to the rule.
    """
    stated = view_layer.normalise_position(override)
    if stated:
        return stated
    return "bottom" if longest_list(state) <= LOG_BOTTOM_MAX_ROWS else "right"


def log_view(state: readmodels.Overview, params, carry: dict | None = None,
             path: str = DASHBOARD_PATH) -> dict:
    """The log card's state: the placement in force, the count that produced it, and the controls.

    The level control's hrefs are built here (one place), the placement tags are plain links built
    from the same carried pairs — no JavaScript, and the choice is linkable and survives the
    refresh. ``path`` is the page the controls live on (the dashboard's or the Logs page's).
    """
    carried = dict(carry or {})
    position = params.position
    levels = []
    for key, label in (state.log.levels or ()):
        levels.append({"key": key, "label": label, "href": f"{path}?level={key}",
                       "active": key == state.log.level})
    links = []
    for value, label in LOG_POSITION_LABELS:
        query = dict(carried)
        if value:
            query["log"] = value
        else:
            query.pop("log", None)
        links.append({"value": value, "label": label, "active": value == (position or ""),
                      "href": f"{DASHBOARD_PATH}?{urlencode(query)}"})
    return {
        "position": log_placement(state, position),
        "stated": position or "",
        "rows": longest_list(state),
        "threshold": LOG_BOTTOM_MAX_ROWS,
        # a SENTENCE RECORD, not a composed string: the threshold is a per-render figure, so the
        # template translates the English template FIRST and interpolates AFTER, through the
        # environment's guarded ``say`` (a bad catalog line degrades to English, never a 500).
        "rule": {"msgid": i18n._("bottom when \u2264 {rows} rows, right when more"),
                 "params": {"rows": LOG_BOTTOM_MAX_ROWS}},
        "levels": levels,
        "links": links,
    }


def console_context(state: readmodels.Overview, request: Request | None = None,
                    view=None, *, log_allowed: bool | None = None, roles=None,
                    manager: bool | None = None) -> dict:
    """EVERYTHING the console chrome reads, in one value: the banner, the tabs and the log card.

    Built from the request so the tab and the level the page rendered are the ones the tabs and the
    sort/search links carry (the live fragments call it with their own request, which is what makes
    the tab survive the refresh). With ``request=None`` it answers the defaults, so a standalone
    render (a test, a harness) needs nothing.

    ``view`` is the :func:`view_state_for` result when the caller already has one: the live stream
    resolves the view ONCE per tick, and resolving it a second time here would double that work for
    no gain (and make "one resolve per tick" untrue).

    ``log`` is ``None`` for a viewer who may not read the bot log (see :func:`may_read_log`) — the
    panel is OMITTED rather than emptied, so the template tests it for existence. ``log_allowed``
    overrides the decision for a caller that already resolved it (the live renderer resolves the
    roles once per connection), and it defaults to the ADMIN view when there is no request at all:
    a standalone render has no identity to resolve, and every request-serving caller passes one.

    ``roles`` are the request's role names and ``manager`` is the third kind of identity (an
    identity whose scope holds a server's ``managed_by``), each resolved ONCE here — the tab scope
    and the log decision both read them, so a render cannot ask a resolver twice and get two
    answers. ``manager=None`` resolves it from the request (memoized there, so the gate and this
    render share one answer); with no request at all there is no identity to resolve and it is
    ``False``, the stricter answer.
    """
    roles = None if request is None else (
        permissions.role_names_for(request) if roles is None else roles)
    if manager is None:
        manager = False if request is None else permissions.manages_console(request)
    if log_allowed is None:
        log_allowed = (True if request is None else may_read_log(roles))
    params = view_params(request) if request is not None else readmodels.ViewParams()
    if view is None and request is not None:
        view = view_state_for(request, state)
    carry = link_params(request, params, roles, manager) if request is not None else {}
    tab = tab_from(request, roles, manager) if request is not None else DEFAULT_TAB
    hidden = {}
    if view is not None:
        hidden = {key: value for key, value in view.params.items() if key != "q"}
    hidden.pop("log", None)
    return {
        "tab": tab,
        "tabs": tab_views(state, tab, carry, roles=roles, manager=manager),
        "banner": attention(state),
        "empty": empty_messages(state),
        "search_action": DASHBOARD_PATH,
        "search_clear": f"{DASHBOARD_PATH}?tab=players",
        "search_hidden": hidden,
        "log": log_view(state, params, carry) if log_allowed else None,
    }


def row_controls_for(request: Request | None, tab: str) -> dict:
    """The controls the tab's table may render — the strip on a server row, ``{}`` for a table with
    no write, and ``{}`` for a standalone render (a caller-less render has no controls to offer, the
    stricter answer, and it keeps a harness render free of a session).

    THE BRIDGE between the console's page and its write surface, and the ONLY one: the page asks this
    for the tab it is about to render and hands the answer to ``_table.html`` as ``row_controls``,
    while the live stream asks the SAME function on every tick (``pages/live.render_for``) so a
    refreshed table cannot lose the column the initial page drew. The map itself belongs to
    ``pages/actions.controls_for`` — read by the /fleet and /players pages too, so both screens
    offer one set of controls. The import is inside the function because ``pages/actions`` reaches
    THIS module for the page's own paths, and a module-level import would be a cycle.

    The ORIGIN is this page's own path and the TAB IN FORCE travels beside it: the dashboard's four
    tables share ``/`` and differ by ``?tab=``, while the registry resolves an origin by page PATH
    alone, so ``/?tab=players`` is not a value it can return (Defect 1). The tab is therefore carried
    in its own field (``pages/actions.TAB_FIELD``) and re-attached by ``pages/actions.origin_url`` on
    the way back, so a write pressed on the Players tab returns to the Players tab — and the DEFAULT
    tab states nothing, the way an unadorned page does. The path cannot be read off the request: the
    live stream renders these tables through its own ``/api/…`` request.
    """
    if request is None:
        return {}
    from . import actions as actions_page

    return actions_page.controls_for(request, tab, DASHBOARD_PATH,
                                     tab="" if tab == DEFAULT_TAB else tab)


#: declared at import (see :func:`declare`), and again from :func:`capabilities`
def declare() -> None:
    """Ensure the capability is declared. Idempotent for the same role set AND scope rule.

    Called at import AND from :func:`capabilities`, because the capability table is process state:
    a test fixture that snapshots and restores it (conftest's ``clean_capability_state``) can drop
    this declaration between two shell installs, and the registrar would then refuse the very route
    this module owns. Re-declaring with the same roles and scope rule is a no-op; re-declaring with
    DIFFERENT ones still raises, which is what keeps two declarations of one capability from
    diverging silently.

    ``scope_grants=True`` is the console's access rule, stated where the capability is declared: a
    MANAGER (an identity whose resolved scope holds a server's ``managed_by``) may read the console
    even though no role of theirs says so, and the only one that has to be expressed outside a role
    tuple because it is dynamic.
    """
    permissions.declare_capability(DASHBOARD_CAPABILITY, DASHBOARD_ROLES, scope_grants=True)


declare()


# ------------------------------------------------------------------------------- registration

def capabilities() -> dict[str, str]:
    """The capability declaration for the paths this module registers."""
    declare()
    return {DASHBOARD_PATH: DASHBOARD_CAPABILITY}


def nav_items() -> tuple[NavItem, ...]:
    """The sidebar entries this page contributes — data, not markup."""
    return (NavItem(label=NAV_LABEL, url=DASHBOARD_PATH, capability=DASHBOARD_CAPABILITY),)


def add_routes(router: APIRouter) -> APIRouter:
    """Add the dashboard route to the shell's own router and return it."""

    @router.get(DASHBOARD_PATH, response_class=HTMLResponse)
    async def dashboard(request: Request):
        registrar = getattr(request.app.state, "webui_registrar", None)
        roles = permissions.role_names_for(request)
        # the third kind of identity, resolved ONCE for the whole render (and memoized on the
        # request, so the access gate that already let this request in shared this very answer):
        # the sidebar, the tab strip and the tab in force all read it.
        manager = permissions.manages_console(request)
        # A request for the log itself (``?log=expanded``) is REFUSED for a viewer who may not read
        # it — the panel's absence is not enough, because expanded mode would otherwise render an
        # empty page and, worse, hand this route a reason to reach for the log tail. The refusal is
        # the console's own 403 (a page for a browser, JSON for an API caller).
        if not may_read_log(roles) and asks_for_expanded_log(request):
            raise log_refusal()
        allowed = may_read_log(roles)
        state = readmodels.overview(source_for_viewer(request_source(request), may_read_log=allowed),
                                    level=log_level(request))
        view = view_state_for(request, state)
        console = console_context(state, request, view, log_allowed=allowed, roles=roles,
                                 manager=manager)
        # THE ONE-SHOT NOTICE of a write: the Dashboard renders the same server/player/node strips
        # the list pages do, so a write pressed here redirects back here (Defect 1) and its
        # outcome has to be shown here too — otherwise the notice would sit in the session and pop on
        # whatever page the person opened next. The import is inside the route because
        # ``pages/actions`` imports THIS module.
        from . import actions as actions_page
        notice = actions_page.pop_notice(request)
        # THE LOAD TAB'S OWN READ, resolved ONCE per render and handed to the same partial the
        # live stream renders (``live.LOAD_TARGET``). Any other tab passes ``None`` and the partial
        # is not rendered at all, so a viewer on the Servers tab never pays for the chart's read.
        load = await load_context(request, state) if console["tab"] == LOAD_TAB else None
        html = i18n.render(
            request, DASHBOARD_TEMPLATE,
            title=DASHBOARD_TITLE,
            page_title=DASHBOARD_TITLE,
            lead=DASHBOARD_LEAD,
            crumb=crumb(CRUMB_GROUP, DASHBOARD_TITLE),
            state=state,
            view=view,
            empty=empty_messages(state),
            console=console,
            load=load,
            notice=notice,
            # the tab's OWN controls — the strip on a server row, nothing for a table with no
            # write; the same call the live stream makes on every tick, so a refreshed table cannot
            # lose the column the initial page drew.
            row_controls=row_controls_for(request, console["tab"]),
            # the PAGE-LEVEL control of the tab in force (the fleet's "Add server"): the SAME call
            # the Servers page makes, asked BY TABLE, so the tab whose table is `servers` offers it
            # and every other tab renders none. It lives in the page head, OUTSIDE the swap targets,
            # because the tab only changes by navigation (its links are plain `href`s), never on a
            # stream tick.
            add_control=actions_page.page_controls(request, console["tab"], DASHBOARD_PATH),
            nav_groups=nav_groups(registrar, roles, current=DASHBOARD_PATH, manager=manager),
            user=identity_summary(request),
            env=environment_marker(state),
            pills=status_pills(state),
            live=live_controls(request),
        )
        # THE PAGE MUST RE-RENDER ON EVERY NAVIGATION back to it: a write now lands here only once
        # its own route has answered, and the browser must not answer that navigation out of its
        # cache with the pre-write page. `live.py` sets this for its poll; every console page sets it
        # for itself.
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    return router


def live_controls(request: Request) -> dict | None:
    """What the shell needs to load the live-update script, or ``None`` when it is off.

    The import is inside the function on purpose: :mod:`services.webservice.pages.live` renders its
    fragments through THIS module's helpers, so a module-level import here would be a cycle.
    """
    from . import live as live_page
    return live_page.page_context(request)


# ---------------------------------------------------------------------------------- the data

def request_source(request: Request):
    """The source this request reads — THE ONE PLACE THE HOSTER VIEW IS APPLIED.

    ``readmodels.console_source`` resolves where the data comes from (the application's provider
    seam — which is how the tests hand in a stub source, and how an installation could pin the
    console to one node — else the live in-process source, which itself degrades to an
    ``EmptySource`` rather than raising). The same function answers the identity layer's question
    "does this identity manage a server", so the access decision and the rendered view are made
    about ONE cluster.

    WHATEVER the provider (or ``resolve_source``) returns is then wrapped in
    :func:`readmodels.scoped_source` with THIS request's scope, resolved per request and never
    cached — exactly like the roles. EVERY page and the live stream read their data through this
    function (``pages/lists``, ``pages/live``, ``pages/logs``, this module), so the tables, the top
    bar's pills, the tab badges, the attention banner and every streamed fragment are scoped BY
    CONSTRUCTION and no page can forget the scope: a page that called ``readmodels.resolve_source``
    itself would be building a second, unserialized path — the test
    ``tests/test_webui_scope_view.py`` walks the page modules and fails if one appears.
    """
    source = readmodels.console_source(getattr(getattr(request, "app", None), "state", None))
    return readmodels.scoped_source(source, permissions.scope_for(request))


def server_named(request: Request, name: str):
    """The server *name* names in the caller's SCOPED view — or ``None``, NEVER raising.

    The non-raising twin of :func:`scoped_server`: the SAME scoped source
    (:func:`request_source`) and the SAME exact, tolerant name comparison, so a caller that must
    ask ONLY "is this a server the caller can see" (and then fall back to its own answer when it
    is not) reads the one resolution rather than a second copy of it. :func:`scoped_server` is
    built on THIS function, so the two can never disagree about which names are servers or about
    the scope: a name the caller cannot see resolves to ``None`` here exactly as it would refuse
    there.
    """
    wanted = readmodels.text(name)
    source = request_source(request)
    for server in getattr(source, "servers", ()) or ():
        # tolerant read: a half-initialised server must not 500 a name lookup
        name_of = readmodels.safe(lambda: getattr(server, "name", None))
        if readmodels.text(name_of) == wanted:
            return server
    return None


def scoped_server(request: Request, name: str):
    """The server *name* names, resolved through the SCOPED source — or the console's 403 refusal.

    THE ONE place a name becomes a server for a console READ, so a future server-detail route cannot
    resolve one outside the caller's scope (spec §10.5). The lookup goes through
    :func:`request_source`, i.e. the same wrapped source the page renders from, so a name the caller
    cannot see cannot be resolved however it is spelled or cased.

    REFUSAL vs NOT-FOUND, and why the answer depends on the caller:

    * a caller whose view is SCOPED (restricted or fail-closed) gets the SAME 403 for a real
      out-of-scope server and for a name that does not exist at all — the console must never disclose
      whether a name it cannot show exists, and a 404 would do exactly that (spec §10.5/§6.3);
    * an UNSCOPED caller (``Admin``, break-glass) sees every server, so a name absent from their view
      genuinely does not exist: the route's own 404 is the honest answer and is what the action layer
      calls "not found".
    """
    server = server_named(request, name)
    if server is not None:
        return server
    wanted = readmodels.text(name)
    if permissions.scope_for(request).unrestricted:
        raise HTTPException(status_code=404, detail=f"No server named '{wanted}'.")
    raise out_of_scope_refusal()


def out_of_scope_refusal() -> HTTPException:
    """The console's refusal for a named target outside the caller's scope.

    A 403 raised by a *handler* (the route itself is reachable), whose ``detail`` names the reason for
    a JSON consumer while a browser gets the shell's refusal page — the two faces are decided once, by
    ``permissions.wants_error_page`` in the shell's ONE handler factory. The wording discloses
    nothing about the target: it is the same refusal for a server that exists and one that does not.
    """
    return HTTPException(status_code=403,
                         detail=i18n._("Not authorized (this server is outside your scope)."))


def log_level(request: Request) -> str:
    """The level filter in force: the URL says it, the config supplies the default.

    Both inputs go through ONE validator (:func:`services.webservice.readmodels.logtail.
    normalise_level`): the query parameter because it is attacker-shaped, the configured default
    because a hand-edited file must not be able to widen or narrow the panel to a value nobody
    defined. An unknown value is REFUSED and the configured default is used instead, so no
    arbitrary string ever reaches the filter.
    """
    config = getattr(getattr(request.app, "state", None), "webui_config", None) or {}
    default = logtail.normalise_level(config.get("log_level")) or logtail.DEFAULT_LEVEL
    return logtail.normalise_level(request.query_params.get("level")) or default


def view_params(request: Request) -> readmodels.ViewParams:
    """The validated view parameters of THIS request (``sort``/``dir``/``q``/``log``).

    One call, one place: the query string is attacker-shaped input, so it goes through the read
    models' allow-list layer rather than being read where it is used. An unknown ``sort``, an
    unknown ``dir`` and a garbage ``log`` are refused there and the defaults apply — this function
    never raises for a bad URL and never hands a raw value on.
    """
    return readmodels.view_params(request.query_params)


def carry_params(request: Request, roles=None, manager=None) -> dict[str, str]:
    """The validated pairs a link (or the live stream URL) must keep: the level, and the tab.

    The level is resolved through :func:`log_level` (the URL value validated against the config
    default), so a link renders the level the page rendered and a garbage ``?level=`` cannot be
    laundered into a sort link. The tab rides along ONLY when it is not the default: an unadorned
    page's stream URL stays exactly ``?level=…`` (the contract two existing tests pin), while a
    page on another tab keeps that tab across the live refresh. The tab carried is the one this
    viewer actually renders (:func:`tab_from`), so a tab they may not open is never handed on.

    ``roles`` is the caller's already-resolved role set (see :func:`console_context`); ``None``
    resolves it from the request.
    """
    carried = {"level": log_level(request)}
    tab = tab_from(request, roles, manager)
    if tab != DEFAULT_TAB:
        carried["tab"] = tab
    return carried


def link_params(request: Request, params: readmodels.ViewParams, roles=None, manager=None) -> dict[str, str]:
    """The validated pairs a LINK that is not a sort link must keep, ``q`` included.

    Finding 2, and the reason this is one function rather than a line at each call site: the sort
    links already kept an active search (the view layer carries ``q``) and so did the stream URL,
    but the tab links and the log placement links did not — so with a search running, clicking
    another tab silently dropped it while the field still echoed it. Now every link the console
    renders states the same filter the field shows.

    The query comes from the VALIDATED :class:`~..readmodels.ViewParams` (never the raw URL), so a
    hostile value cannot be laundered into a link this way.
    """
    carried = dict(carry_params(request, roles, manager))
    if params.query:
        carried["q"] = params.query
    return carried


def view_state_for(request: Request, state: readmodels.Overview | None = None) ->\
        readmodels.ViewState:
    """The sorted tables and the players search for THIS request — one value the template reads.

    Built from the SAME source and the SAME validated parameters the page renders with, and
    ``state`` can be passed in when the caller already built it (the page does, to avoid reading
    the source twice). The live fragments call this on every tick with their own request, which is
    what makes the sort/search survive the refresh: it is re-derived from the URL, never remembered
    in the page.
    """
    if state is None:
        state = readmodels.overview(request_source(request), level=log_level(request))
    return readmodels.view_state(
        DASHBOARD_PATH,
        {"servers": state.servers, "nodes": state.nodes, "instances": state.instances,
         "players": state.players},
        params=view_params(request), carry=carry_params(request))


def empty_messages(state: readmodels.Overview | None = None) -> dict[str, str]:
    """The empty-state copy, taken from the read models that own it (never typed in the template).

    The template renders these instead of an empty container, as required:
    0 servers / 0 players / a node with no instances / a missing log file each say so in words.

    ``state`` matters for ONE case, and it is the hoster view's: when the view is empty BECAUSE OF
    THE CALLER'S SCOPE (``state.scoped_empty``, set by the source wrapper), the four list messages
    are replaced by :data:`readmodels.SCOPED_EMPTY_MESSAGE`. Every one of them would otherwise state
    something false about this caller — "No servers are registered on this cluster", "No nodes are
    registered", "No instances are configured", "No players are online right now" are all
    cluster-wide claims they are not entitled to make or read (spec §10.6). The players SEARCH line
    is left alone: a search over an empty set really does match nothing.
    """
    messages = {
        "nodes": readmodels.NO_NODES_MESSAGE,
        "node_instances": readmodels.NO_NODE_INSTANCES_MESSAGE,
        "instances": readmodels.NO_INSTANCES_MESSAGE,
        "servers": readmodels.NO_SERVERS_MESSAGE,
        "players": readmodels.NO_PLAYERS_MESSAGE,
        "players_matched": NO_PLAYERS_MATCHED_MESSAGE,
        #: the Bans page's table (``_table.html`` indexes ``empty[table.name]``); it is never a
        #: dashboard tab, so this key only ever reaches that page's own card.
        "bans": readmodels.NO_BANS_MESSAGE,
    }
    if getattr(state, "scoped_empty", False):
        for key in ("nodes", "instances", "servers", "players"):
            messages[key] = readmodels.SCOPED_EMPTY_MESSAGE
    return messages


def nav_groups(registrar, roles, *, current: str, manager: bool = False) -> list[dict]:
    """The sidebar, filtered by capability, as plain data for the template.

    Grouped because the mockups' sidebar is grouped; with one page there is one group, and the
    group's label is chrome while its ITEMS are data (``registrar.nav``).

    ``manager`` is the third kind of identity, and it is passed in rather than resolved here because
    the caller holds the request (whose own resolution is memoized — see
    :func:`services.webservice.permissions.manages_console`). It defaults to ``False``: a caller that
    cannot say who the viewer is gets the sidebar a roleless, scopeless viewer gets.
    """
    items = permissions.visible_nav(getattr(registrar, "nav", ()) or (), roles, manager=manager)
    if not items:
        return []
    return [{
        "label": NAV_GROUP,
        # NOTE the key is `links`, not `items`: in Jinja, `group.items` resolves to the dict
        # METHOD (attribute lookup wins over item lookup) and iterating it is a TypeError, not an
        # empty loop. A dict key named `items`/`keys`/`values` is a trap in this template engine.
        "links": [{"label": item.label, "url": item.url, "active": item.url == current}
                  for item in items],
    }]


def identity_summary(request: Request) -> dict | None:
    """``{name, initials, avatar, subtitle, sign_out}`` for the top bar's chip, or ``None`` anonymous.

    ``sign_out`` is added HERE and not in :func:`identity_card` because it needs the request (the
    CSRF token is minted into the session). It is present exactly when an identity is, which is what
    makes "signed in" the ONE condition for the chip and the control it carries: the login page and
    the anonymous render pass no ``user`` at all, so neither can render a sign-out form.
    """
    manager = getattr(getattr(request.app, "state", None), "webui_auth", None)
    identity = manager.authenticate(request) if manager is not None else None
    if identity is None:
        return None
    card = identity_card(identity)
    card["sign_out"] = sign_out_context(request)
    return card


def sign_out_context(request: Request) -> dict[str, str]:
    """What ``base.html`` needs for the sign-out FORM: the path, the field name and the token.

    The path is ``LOGOUT_PATH``, the module constant the logout route is registered at, imported by
    name and never a string typed into a template; the token comes from
    :func:`services.webservice.session.get_csrf_token`, the SAME one-token-per-session helper the
    login form uses, so the control and every other write on the page post one token instead of a
    second mechanism that could drift.

    It is a form and not a link on purpose: the route accepts POST only, and a logout link would let
    any page sign the visitor out with one image tag.
    """
    return {"path": auth_routes.LOGOUT_PATH,
            "csrf_field": session.CSRF_FIELD,
            "csrf_token": session.get_csrf_token(request)}


def identity_card(identity) -> dict:
    """The chip's data for a RESOLVED identity: the name the console shows, its initials and avatar.

    The name is the identity's display name (:attr:`Identity.shown_name`) — never the subject, which
    for the Discord backend is a numeric id — and the initials are derived from THAT name, so a real
    name gives real initials. The avatar is emitted only when it survives
    :func:`services.webservice.auth.safe_avatar_url` (an absolute http(s) URL); otherwise it is an
    empty string and the template draws the initials chip. The rule is applied here as well as in
    the backend because the value is user-influenced and ends up in an ``img`` attribute: one
    function, both sides of the trip.
    """
    name = identity.shown_name
    roles = ", ".join(sorted(identity.roles)) or "no role"
    return {"name": name,
            "initials": _initials(name),
            "avatar": safe_avatar_url(getattr(identity, "avatar_url", "")),
            "subtitle": f"{identity.backend} · {roles}"}


def _initials(name: str) -> str:
    parts = [part for part in str(name or "").replace("_", " ").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def environment_marker(state: readmodels.Overview) -> dict:
    """The real environment/instance marker, where the mockup had its "MOCKUP" banner.

    It answers the two questions a console with more than one installation must answer at a
    glance: which node am I looking at, and is what I see live. Both come from the source's status
    record, so the marker cannot disagree with the figures under it.

    It is PARTS, not one composed string (the same shape as :func:`crumb`): the chrome words
    (``master``/``agent``, ``live state``/``no live state``, and ``no node`` when the source names
    none) are ``i18n._``-marked and rendered through the request's ``_`` by ``base.html``, while a
    node's own NAME is OPERATOR DATA printed verbatim — never looked up as a msgid.
    """
    status = state.status
    node = (status.node_name, False) if status.node_name else (i18n._("no node"), True)
    role = (i18n._("master") if status.master else i18n._("agent"), True)
    live = (i18n._("live state") if status.live else i18n._("no live state"), True)
    return {"parts": (node, role, live), "live": status.live, "reason": status.reason}


def status_pills(state: readmodels.Overview) -> list[dict]:
    """The top bar's pills — every figure built here from the Overview, none typed in the template.

    Each pill is a :func:`_translated` record (a marked ``msgid``, its ``params`` and the English
    ``text``): the template renders the sentence through the per-language environment, so the words
    around the counts follow the reader's language while the figures stay the read models'.
    """
    return [
        _translated(i18n._("%(online)d/%(total)d nodes online"),
                    online=state.online_node_count, total=state.node_count),
        _translated(i18n._("%(running)d/%(total)d servers up"),
                    running=state.running_count, total=state.server_count),
        _translated(i18n._("%(count)d players online"), count=state.player_count),
    ]