"""The dashboard: the first page a signed-in staff member opens.

WHAT "READ-ONLY" MEANT HERE, and what is true now (the card that added the SERVER row strip, W4a,
had to change this claim rather than leave it standing):

* the ROUTE is still a GET and there is no other method on it. No POST/PUT/PATCH/DELETE exists in
  this module, no action-layer call is imported here, nothing is written to disk — the log tail opens
  the log file read-only and the read models only inspect objects that already exist. The page's OWN
  module owns no write handler, and that is still pinned by
  ``tests/test_webui_dashboard.py::test_the_dashboard_route_is_get_only``;
* the WRITES live in ``pages/actions.py``, reachable from a server row that may be operated, and only
  from a row the caller's scope contains. They are POSTs to another module's routes, with the
  session's CSRF token and the target in the body, and nothing about them is owned here: this module
  asks ``actions.controls_for`` for the CONTROLS of the tab it is about to render (data), and the
  markup is ``templates/_table.html``'s;
* the players' Message/Kick/Ban and the node verbs are **omitted, not disabled** — a greyed-out
  control still tells a reader "this console can do that", and a control the viewer may not use is
  absent from the markup entirely. The player and node rows arrive in their own cards;
* the dashboard renders the SERVER strip only (the tab's own table decides): the Players tab's
  controls belong to the card that owns the player strip, and the two screens then stay in step
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
import unicodedata
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import permissions, readmodels, session
from ..auth import routes as auth_routes
from ..auth import safe_avatar_url
from ..readmodels import logtail
from ..readmodels import views as view_layer
from ..registry import NavItem

__all__ = [
    "DASHBOARD_PATH", "DASHBOARD_CAPABILITY", "DASHBOARD_ROLES", "DASHBOARD_TEMPLATE",
    "DASHBOARD_TITLE", "DASHBOARD_LEAD", "NAV_LABEL", "NAV_GROUP",
    "TABS", "DEFAULT_TAB", "LOG_BOTTOM_MAX_ROWS", "CALM_MESSAGE", "NOT_RUNNING_WORDS",
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
DASHBOARD_TITLE = "Dashboard"
DASHBOARD_LEAD = ("Live state of every node, instance and server in the cluster. A server row you "
                  "may operate carries its controls: Pause/Unpause for the mission, "
                  "Restart/Shutdown for the server, Start/Stop in the row's menu.")
NAV_LABEL = "Dashboard"
NAV_GROUP = "Operations"

#: rendered in the top bar's breadcrumb (chrome; the page name itself comes from DASHBOARD_TITLE)
CRUMB_GROUP = "Cluster"

# ------------------------------------------------------------------------------- the console
#
# THE TABS, THE BANNER AND THE ADAPTIVE LOG — the dashboard's own vocabulary, owned HERE (the read
# models stay frozen): the tab keys are the read models' table names, so a tab can never point at a
# table that does not exist, and the counts come off the Overview rather than being typed anywhere.

#: the four tab keys, in render order. Derived from the read models' own column table, so a fifth
#: table would gain a tab without a second list to keep in sync.
TABS: tuple[str, ...] = tuple(view_layer.TABLE_COLUMNS)

#: the tab a page shows when the URL says nothing (Servers — the thing an operator looks at first)
DEFAULT_TAB = "servers"


def tab_capability(key: str) -> str | None:
    """The capability the tab's STANDALONE PAGE declares, or ``None`` for a tab with no page.

    THE POINT OF THIS FUNCTION: a tab renders a table a reader could also open as a page, so the tab
    must be gated by the *same* predicate that page is gated by. The capability is therefore read
    out of the page module itself — the string its route declares, its nav item names and its
    ``ListPage`` holds — rather than typed here, so the two doors cannot drift apart again (they
    did: a DCS member was refused by ``/nodes`` and served the same table by ``/?tab=nodes``).

    The imports are inside the function on purpose: every one of those page modules imports THIS
    module, so a module-level import here would be a cycle.
    """
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
CALM_MESSAGE = "Nothing needs you — every node is heartbeating and no server is down."

#: the status words that mean "a player cannot join" (everything but RUNNING and the deliberate
#: PAUSED). ONE definition: the banner, the tab dot and the count all read this.
NOT_RUNNING_WORDS: tuple[str, ...] = ("RUNNING", "PAUSED")

#: the players tab's no-match line (a filtered search with zero hits is NOT "nobody is online")
NO_PLAYERS_MATCHED_MESSAGE = "No players match this search."

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


def attention(state: readmodels.Overview) -> dict:
    """What is wrong, in WORDS, derived from fields the read models already carry.

    Four checks and no new data: ``NodeView.online`` (a node not heartbeating),
    ``ServerView.status.word`` (down or paused), ``LogLine.level`` (ERROR lines in the visible
    tail). An empty list is the healthy state and renders the CALM line — the page never shows a
    colour without its word, and never asserts a problem it cannot name.
    """
    items: list[dict] = []
    offline = _offline_nodes(state)
    if offline:
        items.append({"kind": "dead", "dot": "dead",
                      "text": f"{len(offline)} node(s) not heartbeating: {_names(offline)}"})
    down = _broken_servers(state)
    if down:
        items.append({"kind": "dead", "dot": "dead",
                      "text": f"{len(down)} server(s) down: {_names(down)}"})
    paused = _paused_servers(state)
    if paused:
        items.append({"kind": "pause", "dot": "pause",
                      "text": f"{len(paused)} server(s) paused: {_names(paused)}"})
    errors = sum(1 for line in state.log.lines if line.level == "ERROR")
    if errors:
        items.append({"kind": "log", "dot": "dead",
                      "text": f"{errors} ERROR line(s) in the visible log"})
    return {"alerts": items, "ok": not items, "calm": CALM_MESSAGE,
            "count": len(items)}


def _tab_problem(state: readmodels.Overview, key: str) -> str:
    """The sentence behind a tab's problem dot — empty when that tab holds nothing wrong.

    ONE classification, shared with :func:`attention`: the classes are the banner's own (a node not
    heartbeating on the Nodes tab, a server not running on the Servers tab), so a tab with a dot
    always names a problem the banner also names. The design of record's tab strip carries dots on
    exactly those two tabs, and an instance row that binds no server is NOT one of them — inventing
    a fourth class here would put a red dot on a tab the banner stays calm about.

    The Players tab has no class: a player count is never a problem, and the ERROR lines class has no
    tab to live on (the log is not a tab).
    """
    if key == "nodes":
        offline = _offline_nodes(state)
        return f"{len(offline)} node(s) offline" if offline else ""
    if key == "servers":
        broken = _broken_servers(state)
        return f"{len(broken)} server(s) not running" if broken else ""
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


def _tab_count(state: readmodels.Overview, key: str) -> int:
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
            "label": view_layer.TABLE_LABELS.get(key, key),
            "count": _tab_count(state, key),
            "active": key == tab,
            "problem": bool(problem),
            "problem_text": problem,
            "link": f"{DASHBOARD_PATH}?{urlencode(query)}",
        })
    return strip


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
        "rule": f"bottom when \u2264 {LOG_BOTTOM_MAX_ROWS} rows, right when more",
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
    ``pages/actions.controls_for`` — read by the /servers and /players pages too, so both screens
    offer one set of controls. The import is inside the function because ``pages/actions`` reaches
    THIS module for the page's own paths, and a module-level import would be a cycle.

    The ORIGIN is this page's own path (Defect 1): the table here is the DASHBOARD's, on
    the initial render and on every streamed fragment alike, so a write returns to ``/`` — and that
    is why this cannot be read off the request, whose path is an ``/api/…`` stream endpoint.
    """
    if request is None:
        return {}
    from . import actions as actions_page

    return actions_page.controls_for(request, tab, DASHBOARD_PATH)

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
        environment = getattr(request.app.state, "webui_templates", None)
        if environment is None:  # pragma: no cover - installed by the shell
            raise HTTPException(status_code=503,
                                detail="The admin web UI templates are not installed.")
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
        html = environment.get_template(DASHBOARD_TEMPLATE).render(
            title=DASHBOARD_TITLE,
            page_title=DASHBOARD_TITLE,
            lead=DASHBOARD_LEAD,
            crumb=f"{CRUMB_GROUP} / {DASHBOARD_TITLE}",
            state=state,
            view=view,
            empty=empty_messages(state),
            console=console,
            notice=notice,
            # the tab's OWN controls (W4a) — the strip on a server row, nothing for a table with no
            # write; the same call the live stream makes on every tick, so a refreshed table cannot
            # lose the column the initial page drew.
            row_controls=row_controls_for(request, console["tab"]),
            nav_groups=nav_groups(registrar, roles, current=DASHBOARD_PATH, manager=manager),
            user=identity_summary(request),
            env=environment_marker(state),
            pills=status_pills(state),
            live=live_controls(request),
        )
        return HTMLResponse(html)

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
    wanted = readmodels.text(name)
    source = request_source(request)
    for server in getattr(source, "servers", ()) or ():
        # tolerant read: a half-initialised server must not 500 a name lookup
        name_of = readmodels.safe(lambda: getattr(server, "name", None))
        if readmodels.text(name_of) == wanted:
            return server
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
    return HTTPException(status_code=403, detail="Not authorized (this server is outside your scope).")


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

    The template renders these instead of an empty container, which is the card's requirement:
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
    """
    status = state.status
    parts = [status.node_name or "no node", "master" if status.master else "agent"]
    parts.append("live state" if status.live else "no live state")
    return {"text": " · ".join(parts), "live": status.live, "reason": status.reason}


def status_pills(state: readmodels.Overview) -> list[str]:
    """The top bar's pills — every figure built here from the Overview, none typed in the template."""
    return [
        f"{state.online_node_count}/{state.node_count} nodes online",
        f"{state.running_count}/{state.server_count} servers up",
        f"{state.player_count} players online",
    ]