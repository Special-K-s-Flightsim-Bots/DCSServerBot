"""The Players page: the directory of every player this installation knows.

The page is a DATABASE read, one row per player: the Discord link groups a player's UCIDs into one
row, and the row carries the moderation state (ban, watch, VIP) beside the last-seen stamp and the
measured playtime. The live roster — everyone sitting in a slot right now — is one link away
(``?show=online``) and renders the dashboard's own table (``table="players"``) unchanged.

THE SHOW SWITCH IS A GET PARAMETER, never script: ``?show=all|online|banned|watched`` is a closed
vocabulary (anything else is ``all``), so the view is linkable, bookmarkable and works with
JavaScript off — the same shape the Bans page's DGSA option uses. ``online`` renders the in-memory
roster; the three directory views render this page's own table (``player_directory``), paged 50 rows
at a time by the read.

A RESTRICTION VIEW IS FILTERED BEFORE THE PAGE IS CUT. ``banned`` and ``watched`` travel to the read
as the state it keeps rows by, so the rows, the total, the count sentence and the pager all describe
the filtered set — this page never drops a row after the read, which would make a player on an
earlier page of the directory unreachable from the view.

THE ROWS CARRY THE MODERATION STRIP. A directory row's controls are the four writes that need no
live session — Ban / Lift ban and Watch / Unwatch — because all four key on the UCID: the ban ADD
resolves no server and looks no player up, and the watch trio is the same shape. Message, Popup, Kick
and Screenshot act on a LIVE session, so they stay on the player page, where the slot facts are. The
controls are built from the SAME scoped rows this page renders, so a player outside the caller's scope
has no control and a crafted POST naming one is refused like a UCID nothing answers to.

A LEADING STATUS COLUMN marks who is flying: the console's dot+word pair, matched on the UCID against
the in-memory roster (a database read cannot answer it). Grey ``dot stop`` for an offline player —
never the fault colour, because a player who is not in a slot is the normal case. The UCID is the
player's key, so a row shows exactly one: there is no per-row count of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from core.actions import read_action

from .. import i18n, readmodels
from ..readmodels import players as player_model
from ..readmodels.views import PAGE_PARAM, normalise_page, view_params
from . import lists as lists_page

__all__ = ["PLAYERS_PATH", "PLAYERS_CAPABILITY", "PLAYERS_ROLES", "PLAYERS_TABLE",
           "PLAYERS_ROSTER_TABLE", "PLAYERS_TITLE", "PLAYERS_LEAD", "PLAYERS_DETAIL_PREFIX",
           "SHOW_PARAM", "SHOW_ALL", "SHOW_ONLINE", "SHOW_BANNED", "SHOW_WATCHED", "SHOW_VIEWS",
           "show_view", "DirectoryRowView", "PAGE", "capabilities", "nav_items", "players_context",
           "add_routes"]

PLAYERS_PATH = "/players"
PLAYERS_CAPABILITY = "players.view"
#: a member who may read the console may read the player directory (the dashboard's own role model,
#: and a MANAGER reaches it through the capability's ``scope_grants`` — the read is scoped)
PLAYERS_ROLES: tuple[str, ...] = lists_page.READ_ONLY_ROLES
#: this page's OWN table: one row per player of the database. Deliberately not ``players`` — that one
#: is the dashboard's live roster, rebuilt from the in-memory source on every live tick.
PLAYERS_TABLE = "player_directory"
#: the live roster's table name, rendered by ``?show=online`` — the dashboard's Players tab unchanged
PLAYERS_ROSTER_TABLE = "players"
PLAYERS_TITLE = i18n._("Players")
PLAYERS_LEAD = i18n._("Every player this installation knows, from the database — not only the ones "
                      "online now. A name opens that player's page.")
#: the prefix of a row's name link; the player's page appends its UCID
PLAYERS_DETAIL_PREFIX = PLAYERS_PATH + "/"

#: THE VIEW OPTION's own vocabulary — the closed set of ``?show=`` values
SHOW_PARAM = "show"
SHOW_ALL = "all"
SHOW_ONLINE = "online"
SHOW_BANNED = "banned"
SHOW_WATCHED = "watched"
SHOW_VIEWS: tuple[str, ...] = (SHOW_ALL, SHOW_ONLINE, SHOW_BANNED, SHOW_WATCHED)

#: the switch's heading and its four link words, marked here (the page owns its own copy) and
#: translated at render, like every other page string
SHOW_HEADING = i18n._("Show")
SHOW_LABELS: dict[str, str] = {
    SHOW_ALL: i18n._("All"),
    SHOW_ONLINE: i18n._("Online now"),
    SHOW_BANNED: i18n._("Banned"),
    SHOW_WATCHED: i18n._("Watched"),
}

#: the empty-state copy this page owns (the table component reads ``empty[table.name]``)
PLAYERS_EMPTY = i18n._("No player is known to this installation yet.")
PLAYERS_EMPTY_MATCHED = i18n._("No player matches this search.")
PLAYERS_EMPTY_VIEW = i18n._("No player on this page matches this view.")

PAGE = lists_page.ListPage(table=PLAYERS_TABLE, path=PLAYERS_PATH, capability=PLAYERS_CAPABILITY,
                           roles=PLAYERS_ROLES, title=PLAYERS_TITLE, lead=PLAYERS_LEAD,
                           nav_label=PLAYERS_TITLE, scope_grants=True, writes=True)


@dataclass(frozen=True, slots=True)
class DirectoryRowView:
    """ONE directory row plus the live marker, as the table renders it.

    The directory read is a DATABASE read, so it cannot answer who holds a slot; ``online`` is
    overlaid from the in-memory roster and matched on the UCID — never on a name, which changes.
    Every other field is the read's own record, delegated unchanged so the table's sort cells and
    the row's chips read one source.
    """

    record: player_model.DirectoryRow
    online: bool

    def __getattr__(self, name: str):
        if name == "record":
            raise AttributeError(name)
        return getattr(self.record, name)


def show_view(value) -> str:
    """The ``?show=`` value in force: a member of :data:`SHOW_VIEWS`, or :data:`SHOW_ALL`.

    A view name is a closed vocabulary like every other console parameter: an unknown value is not an
    error the page can show, it is a view nobody can serve, so the reader is handed the directory.
    """
    text = str(value or "").strip().lower()
    return text if text in SHOW_VIEWS else SHOW_ALL


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers."""
    PAGE.declare()
    return {PLAYERS_PATH: PLAYERS_CAPABILITY}


def nav_items():
    """The sidebar entry for this page — the capability's own name, never a second spelling."""
    return (PAGE.nav_item(),)


def _link(carry: dict, show: str, page_number: int | None = None) -> str:
    """One view link: the validated carry, the view in force and (optionally) a page number."""
    query = dict(carry)
    if show != SHOW_ALL:
        query[SHOW_PARAM] = show
    if page_number is not None and page_number > 1:
        query[PAGE_PARAM] = str(page_number)
    return f"{PLAYERS_PATH}?{urlencode(query)}" if query else PLAYERS_PATH


def _view_options(carry: dict, show: str) -> dict:
    """The Show switch as data: its heading plus one record per view, with its link and whether it
    is the one in force. The component renders it and computes nothing."""
    return {"heading": SHOW_HEADING,
            "options": [{"label": SHOW_LABELS[name], "link": _link(carry, name), "active": name == show}
                        for name in SHOW_VIEWS]}


def _paging(carry: dict, show: str, view: player_model.DirectoryView) -> dict:
    """The pager as data: where in the range this page sits, and the two links that move it."""
    return {
        "page": view.page,
        "pages": max(int(view.pages or 1), 1),
        "prev": _link(carry, show, view.page - 1) if view.page > 1 else None,
        "next": _link(carry, show, view.page + 1) if view.page < max(view.pages or 1, 1) else None,
    }


async def _read(request: Request, *, page: int, sort: str, direction: str, query: str,
                state: str = SHOW_ALL) -> player_model.DirectoryView:
    """ONE page of the directory, through the console's own scoped action.

    *state* is the restriction view in force; the read keeps only that view's rows, so the page is
    cut from the filtered set and the total it answers is the filtered one.

    The import is inside the function on purpose: ``pages.actions`` reaches this module for the
    page's own path, so a module-level import would be a cycle.
    """
    from . import actions as actions_page

    result = await read_action("list_player_directory", actions_page.context_for(request),
                               q=query, page=page, sort=sort, direction=direction, state=state)
    data = getattr(result, "data", None) if getattr(result, "success", False) else None
    return player_model.directory_view(data or {})


def _online_ucids(request: Request) -> set[str]:
    """Every UCID sitting in a slot right now, read off the in-memory roster.

    THE SAME SOURCE the dashboard's Players tab renders (the bot's servers → their active players),
    resolved through the console's one scoped provider. The database cannot answer this: a directory
    row is a player the installation knows, not a player currently flying.
    """
    from . import dashboard as dashboard_page

    views = readmodels.players_online(dashboard_page.request_source(request))
    return {readmodels.text(view.ucid) for view in views if readmodels.text(view.ucid)}


async def players_context(request: Request) -> dict:
    """Everything the Players page's template reads, in one value.

    Two sources behind one page. ``?show=online`` renders the Overview's live roster table — the same
    table, in the same order, the dashboard's Players tab draws — while the three directory views
    await the console's own ``list_player_directory`` action, so the SQL and the scope rule live in
    ONE place. A page number past the last one is answered with the last page (the read's own closed
    range answers it with the first), so a typed number never lands the reader on an empty table.
    """
    show = show_view(request.query_params.get(SHOW_PARAM))
    params = view_params(request.query_params)
    sort = params.sort_for(PLAYERS_TABLE)
    direction = params.direction
    if show == SHOW_ONLINE:
        context = lists_page.page_context(request, PAGE, rows=())
        context["table"] = context["view"].tables[PLAYERS_ROSTER_TABLE]
        context["directory"] = None
        # the switch stays on this view too: "Online now" is one link away from the directory, and
        # without it here the reader could not come back.
        context["view_options"] = _view_options(params.carry(table=PLAYERS_ROSTER_TABLE), show)
        # the search form is the component's own: it carries the view in force, so a search typed
        # inside "Online now" stays inside it instead of falling back to the directory.
        context["search_hidden"] = {SHOW_PARAM: SHOW_ONLINE}
        context["search_clear"] = _link({}, SHOW_ONLINE)
        return context

    requested = normalise_page(request.query_params.get(PAGE_PARAM))
    view = await _read(request, page=requested, sort=sort, direction=direction, query=params.query,
                       state=show)
    if requested > 1 and requested > max(view.pages or 1, 1):
        view = await _read(request, page=max(view.pages or 1, 1), sort=sort, direction=direction,
                           query=params.query, state=show)
    # the restriction views are filtered by the READ, before its LIMIT: this page renders the rows
    # it was served, and every figure below (total, count sentence, pager) is the filtered one.
    roster = _online_ucids(request)
    rows = tuple(DirectoryRowView(row, row.ucid in roster) for row in view.rows)
    carry = params.carry(table=PLAYERS_TABLE)
    # the control builder reads the SAME scoped rows the table renders, so a control exists for a
    # player of this page and for no other (``controls_for`` asks for them by table).
    request.state.player_directory_rows = rows
    context = lists_page.page_context(request, PAGE, rows=rows)
    context["empty"] = dict(context["empty"], **{PLAYERS_TABLE: PLAYERS_EMPTY})
    # a search typed inside a filtered view stays inside it (the form carries the view), and the
    # Clear link returns to the view rather than to the whole directory.
    context["search_hidden"] = {} if show == SHOW_ALL else {SHOW_PARAM: show}
    context["search_clear"] = _link({}, show)
    context["view_options"] = _view_options(carry, show)
    context["directory"] = {
        "query": view.query,
        "total": view.total,
        "shown": len(rows),
        "filtered": show != SHOW_ALL,
        "detail_prefix": PLAYERS_DETAIL_PREFIX,
        "paging": _paging(carry, show, view),
        "empty_matched": PLAYERS_EMPTY_MATCHED,
        "empty_view": PLAYERS_EMPTY_VIEW,
    }
    return context


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Players route to the shell's own router and return it."""
    PAGE.declare()

    @router.get(PLAYERS_PATH, response_class=HTMLResponse, name=f"page-{PLAYERS_TABLE}")
    async def _players_page(request: Request):
        # no-store: the directory is a database read and the roster is live state, so the browser
        # must not serve a stale render when the reader comes back to the page.
        return HTMLResponse(i18n.render(request, "players.html", **await players_context(request)),
                            headers={"Cache-Control": "no-store"})

    return router
