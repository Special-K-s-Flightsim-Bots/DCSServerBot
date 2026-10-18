"""The SERVER OVERVIEW page — every server of the cluster at a glance, one card each.

WHY THIS PAGE EXISTS. The dashboard (``/``) is a deliberate attention surface (one table at a time,
its log placement following the data) and the standalone list pages are the full inventory as
TABLES. This page is the third reading an operator wants: the whole fleet at once, in the spirit of
the bot's own server-status embed (``/status`` on the mission plugin), each server a CARD carrying
the same facts the embed shows — status, mission, players, weather, runtime — plus the link to that
server's own page (``/servers/<encoded-name>``).

READ-ONLY, AND ONE SOURCE. The route is a GET, there is no other method on it, nothing is written,
and every figure on a card is an attribute of a
:class:`~services.webservice.readmodels.model.ServerView` (``readmodels.servers.server_view``) — the
SAME per-server read model the dashboard's Servers tab and the ``/servers`` list already render. The
overview reads the LIVE state through the same scoped source every page uses
(:func:`services.webservice.pages.dashboard.request_source`), so the hoster scope, the empty states
and the top bar's pills come for free and no page can forget the scope.

THE EMBED PARITY IS DECLARED HERE (see :data:`EMBED_FIELD_COVERAGE` / :data:`EMBED_FIELDS_NOT_COVERED`
and :func:`embed_field_selection`): the facts the page mirrors are listed beside the ones it
deliberately does not, the embed's own builder
(``plugins/mission/reports/serverStatus.json`` -> ``plugins/mission/status.py``) is the authority, and
``tests/test_webui_server_overview.py`` fails the moment the embed grows a field the page neither
shows nor documents. The FIELD SELECTION is shared rather than copied: the read model produces the
same three weather facts the embed's ``WeatherInfo`` element does, in the same units and rounding.

THE EMBED'S DYNAMICALLY-NAMED PER-SERVER BLOCK IS COVERED TOO. Beside the LITERAL field names above,
the embed's per-server block (``status.All`` / ``status.ServerInfo``) carries three facts under names
built at runtime (so :func:`embed_field_selection` only COUNTS them): the ``IP/Port``, the ``Restart``
countdown and the password-lock glyph. The card shows all three — ``s.public_ip`` + ``s.port``
(parity with the embed), ``s.restart_in`` and the ``s.locked`` lock indicator — rather than declaring
them omitted. The one part of that block the card deliberately does NOT show is the password VALUE:
the lock indicator says a password is set, the cleartext is never rendered (the web page has no
``show_passwords`` gate to honour, so it errs on the side of never disclosing it).

HONEST EMPTY STATES (required): no servers configured (the read models' own sentence,
or the scope-aware one for a hoster), a server that is not running (its card says so rather than
printing zeroes), and a node that has not reported (the cluster header names only the nodes the
source knows). The copy lives in the read models / here, never typed into a template.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, permissions, readmodels
from ..registry import NavItem
from . import dashboard as dashboard_page

__all__ = [
    "OVERVIEW_PATH", "OVERVIEW_CAPABILITY", "OVERVIEW_ROLES", "OVERVIEW_TEMPLATE",
    "OVERVIEW_TITLE", "OVERVIEW_LEAD", "NAV_LABEL", "EMBED_BUILDER_PATH",
    "EMBED_FIELD_COVERAGE", "EMBED_FIELDS_NOT_COVERED", "ServerCard",
    "embed_field_selection", "cards", "cluster_view", "page_context",
    "capabilities", "nav_items", "add_routes",
]

#: the page's path — its own top-level route beside ``/servers``.
OVERVIEW_PATH = "/overview"

#: the capability the route declares AND the nav item names — one string, so the two cannot drift.
OVERVIEW_CAPABILITY = "overview.view"

#: the SAME read roles as the servers list page (a member who may read the fleet may read the
#: overview), reused rather than re-spelled.
OVERVIEW_ROLES: tuple[str, ...] = dashboard_page.DASHBOARD_ROLES

OVERVIEW_TEMPLATE = "overview.html"
OVERVIEW_TITLE = i18n._("Server overview")
OVERVIEW_LEAD = i18n._("Every server of the cluster at a glance: status, mission, players, weather "
                       "and runtime, each with a link to its own page.")
#: The sidebar label. Its OWN msgid, NOT ``Overview``: the dashboard's nav item already resolves
#: ``Dashboard`` -> "Übersicht" (de), and reusing the ``Overview`` msgid here made the German menu
#: show "Übersicht" twice. "Server overview" is the page's own title, so the two menu entries read
#: apart in every language (``tests/test_webui_nav.py`` pins that no two visible labels are equal).
NAV_LABEL = i18n._("Server overview")

#: The EMBED BUILDER whose field selection the page mirrors — the bot's server-status report. Its
#: element list is ``plugins/mission/reports/serverStatus.json``; the element classes that call
#: ``add_field`` live beside it in ``status.py``. Read by :func:`embed_field_selection` and by the
#: parity test, from ONE place.
EMBED_BUILDER_PATH = Path(__file__).resolve().parents[3] / "plugins" / "mission" / "status.py"

#: ``add_field(name=...)`` where the name is a LITERAL string (single- or double-quoted). Calls whose
#: name is an expression (a variable, ``ext['name']``, ``utils.print_ruler(...)``) do not match and
#: are counted separately — see :func:`embed_field_selection`.
_ADD_FIELD = re.compile(r"add_field\(\s*name\s*=\s*(?P<name>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")")

#: embed field label -> the page fact (an i18n msgid) that CARRIES it. The value is what the overview
#: renders, so the parity test can assert the label reaches the page. Keyed by the embed's own field
#: name, exactly as ``status.py`` writes it.
EMBED_FIELD_COVERAGE: dict[str, str] = {
    "Map": i18n._("Map"),
    "Temperature": i18n._("Temperature"),
    "Clouds": i18n._("Weather"),
    "Weather": i18n._("Weather"),
    "Visibility": i18n._("Wind"),
    "Date / Time in Mission": i18n._("Runtime"),
}

#: embed field labels the page deliberately does NOT show, each with the reason a reviewer can check.
#: Every literal name in the builder must appear in ONE of the two maps — that is the drift guard.
EMBED_FIELDS_NOT_COVERED: dict[str, str] = {
    "Client Limits": (
        "the IntegrityCheck element renders the server's client-integrity policy flags (pure "
        "clients/scripts/models, trial clients) — a per-server policy list, not a runtime fact the "
        "overview card carries"),
    "_ _": "the Ruler/blank spacer fields every element appends — layout, not a fact",
    "This server runs on the following schedule:": (
        "the ScheduleInfo element — reads the scheduler plugin and a per-server schedule; not a "
        "runtime fact the card carries"),
    "🇲|🇹|🇼|🇹|🇫|🇸|🇸": "the ScheduleInfo day grid (see above)",
}


def embed_field_selection(source: str | None = None) -> tuple[tuple[str, ...], int]:
    """``(literal field labels, count of dynamically-named add_field calls)`` for the embed builder.

    The labels are the ``add_field(name='…')`` string literals of ``plugins/mission/status.py``, in
    source order and deduplicated. The count names the calls whose name is an EXPRESSION (the
    extensions list and the schedule ruler build their names at runtime): their labels are not
    visible to a static read, which is exactly one of the things the parity test states it does NOT
    cover. Raises ``FileNotFoundError`` if the builder moved — a silent empty set would pass a
    subset test for the wrong reason.
    """
    text = source if source is not None else EMBED_BUILDER_PATH.read_text(encoding="utf-8")
    labels: list[str] = []
    dynamic = 0
    for match in _ADD_FIELD.finditer(text):
        raw = match.group("name")
        labels.append(raw[1:-1])
    for call in re.finditer(r"add_field\(", text):
        window = text[call.end():call.end() + 60]
        if not re.match(r"\s*name\s*=\s*['\"]", window):
            dynamic += 1
    seen: dict[str, None] = {}
    for label in labels:
        seen.setdefault(label, None)
    return tuple(seen), dynamic


@dataclass(frozen=True, slots=True)
class ServerCard:
    """One server's card: the read model's view, plus the two booleans the markup branches on.

    Derived HERE (not in the template) so the card's markup only reads: ``running`` collapses the
    status vocabulary to the one question the card asks ("are the mission/weather facts there?"),
    and ``has_weather`` is ``weather is not None`` with any fact present — so an all-empty
    ``WeatherView`` renders the empty state rather than three blank rows.
    """
    view: readmodels.ServerView
    running: bool
    has_weather: bool


def cards(state: readmodels.Overview) -> tuple[ServerCard, ...]:
    """The per-server cards of the overview, in the read model's own (name-sorted) order."""
    out: list[ServerCard] = []
    for view in state.servers:
        weather = view.weather
        has_weather = weather is not None and bool(weather.preset or weather.temperature or weather.wind)
        out.append(ServerCard(view=view, running=view.running, has_weather=has_weather))
    return tuple(out)


def cluster_view(state: readmodels.Overview) -> dict:
    """The cluster header as DATA: the nodes with their liveness, the server count and how many are up.

    Every figure is a property of the same :class:`~services.webservice.readmodels.model.Overview`
    the cards are built from, so the header cannot disagree with the cards under it. Each node keeps
    its OWN liveness (``online``) — the node read model already renders a node the cluster knows but
    cannot reach as offline, and the header must not present such a node as if it had reported. The
    node names come from the node registry; an installation with no node registry renders the read
    models' own empty sentence for it, never a blank.

    The source's own node (the one this console runs on) is added when the registry does not carry
    it — without duplicating a name the registry already holds — and is online BY DEFINITION (the
    console is answering from it).
    """
    nodes: list[dict] = [{"name": node.name, "online": node.online, "master": node.master}
                         for node in state.nodes]
    known = {node["name"] for node in nodes}
    local = getattr(state.status, "node_name", None)
    if local and local not in known:
        nodes.insert(0, {"name": local, "online": True, "master": bool(state.status.master)})
    return {
        "nodes": nodes,
        "has_nodes": bool(nodes),
        "server_count": state.server_count,
        "up_count": state.running_count,
        "scoped_empty": bool(state.scoped_empty),
    }


def declare() -> None:
    """Ensure this page's capability is declared. Idempotent for the same roles AND scope rule."""
    permissions.declare_capability(OVERVIEW_CAPABILITY, OVERVIEW_ROLES, scope_grants=True)


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers."""
    declare()
    return {OVERVIEW_PATH: OVERVIEW_CAPABILITY}


def nav_items():
    """The sidebar entry this page contributes — data, not markup."""
    return (NavItem(label=NAV_LABEL, url=OVERVIEW_PATH, capability=OVERVIEW_CAPABILITY),)


def page_context(request: Request) -> dict:
    """EVERYTHING the overview template reads, in one value — same shape as the list pages'.

    The state is read through :func:`dashboard.request_source`, so the hoster scope and the live/
    no-live degradation apply exactly as they do on every other page, and the chrome (nav, pills,
    environment marker, user chip) is handed in from the SAME helpers the list pages use, so the
    sidebar, the top bar and this page can never drift from the rest of the console.
    """
    registrar = getattr(request.app.state, "webui_registrar", None)
    state = readmodels.overview(dashboard_page.request_source(request),
                                level=dashboard_page.log_level(request))
    # the empty-state copy, one sentence per state the overview can actually be in. The servers line
    # is scope-aware (a hoster whose managed_by matches nothing must not read a cluster-wide claim);
    # the other three are the card-level states the read models own.
    empty = {
        "servers": readmodels.SCOPED_EMPTY_MESSAGE if state.scoped_empty
                   else readmodels.NO_SERVERS_MESSAGE,
        "nodes": readmodels.NO_NODES_MESSAGE,
        "mission": readmodels.NO_MISSION_MESSAGE,
        "weather": readmodels.NO_WEATHER_MESSAGE,
        "not_running": readmodels.NOT_RUNNING_MESSAGE,
    }
    return {
        "title": OVERVIEW_TITLE,
        "page_title": OVERVIEW_TITLE,
        "lead": OVERVIEW_LEAD,
        "crumb": dashboard_page.crumb(dashboard_page.CRUMB_GROUP, OVERVIEW_TITLE),
        "cards": cards(state),
        "cluster": cluster_view(state),
        "empty": empty,
        "nav_groups": dashboard_page.nav_groups(registrar, permissions.role_names_for(request),
                                                current=OVERVIEW_PATH,
                                                manager=permissions.manages_console(request)),
        "user": dashboard_page.identity_summary(request),
        "env": dashboard_page.environment_marker(state),
        "pills": dashboard_page.status_pills(state),
    }


def add_routes(router: APIRouter) -> APIRouter:
    """Add the overview route to the shell's own router and return it."""
    declare()

    @router.get(OVERVIEW_PATH, response_class=HTMLResponse, name="page-overview")
    async def _overview_page(request: Request):
        return HTMLResponse(i18n.render(request, OVERVIEW_TEMPLATE, **page_context(request)),
                            headers={"Cache-Control": "no-store"})

    return router
