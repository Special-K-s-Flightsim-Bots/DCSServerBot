"""The Audit page: the cluster's own trail, read-only, Admin only.

One row per entry of the ``audit`` table — the ledger the bot writes for every operation, whether it
came from Discord, the console, the REST surface, MCP or a plugin. The page is a VIEW of that table
and never a second writer: it has no control, no form that posts and no route other than this one.

WHY IT IS ADMIN ONLY. The trail names what everyone did, including a server manager's own writes on
their servers, so a scoped reader would meet rows about servers they do not manage. There is no scope
rule to apply — the table is per CLUSTER, not per server — so the honest answer is the narrowest one:
``Admin``, like the log panel. That is also why the page sits in the Cluster group.

THE READ IS THIS MODULE'S OWN (``_read``), deliberately. The console's other list pages await a
``read_action`` so the SQL and a SCOPE rule live in one place; here there is no scope rule to share
and no other consumer, so a plugin action would add a second home for one query and a dependency on
that plugin being loaded. ``pages/server_detail`` reads the database the same way, from the pool the
in-process service bus already holds — no second connection, and the page degrades to its refusal
sentence rather than to a 500 when the pool is not there yet.

THE FILTERS ARE GET PARAMETERS (node, server, actor, the event text, and a time range), so every
view is linkable, bookmarkable and works with JavaScript off. They are parsed by
:mod:`services.webservice.readmodels.audit`, which also shapes the rows — this module owns the SQL
and the ORDER BY, nothing else. The page is PAGED by the read (``LIMIT``/``OFFSET``), because the
trail grows without limit and a page that fetched everything to hide it would stop working the day
the install has been up for a year.

THE PAGER AND THE SORT LINKS CARRY THE FILTERS In every link, so ordering the trail or turning a
page never silently drops a filter the reader set.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import i18n, readmodels
from ..readmodels import audit as audit_model
from ..readmodels.views import DIRECTION_PARAM, PAGE_PARAM, SORT_PARAM, column_keys, \
    normalise_page, view_params
from . import lists as lists_page

__all__ = ["AUDIT_PATH", "AUDIT_CAPABILITY", "AUDIT_ROLES", "AUDIT_TABLE", "AUDIT_TITLE",
           "AUDIT_LEAD", "AUDIT_TEMPLATE", "PAGE", "capabilities", "nav_items", "audit_context",
           "add_routes"]

AUDIT_PATH = "/audit"
AUDIT_CAPABILITY = "audit.view"
#: Admin only — see the module docstring: the trail is per cluster and names everyone's writes, so a
#: scoped reader would meet servers outside their own.
AUDIT_ROLES: tuple[str, ...] = ("Admin",)
AUDIT_TABLE = "audit"
AUDIT_TITLE = i18n._("Audit")
AUDIT_LEAD = i18n._("Every operation this cluster recorded — who did it, to what, and when. "
                    "Read-only: the trail is written by the bot, never from here.")
AUDIT_TEMPLATE = "audit.html"

PAGE = lists_page.ListPage(table=AUDIT_TABLE, path=AUDIT_PATH, capability=AUDIT_CAPABILITY,
                           roles=AUDIT_ROLES, title=AUDIT_TITLE, lead=AUDIT_LEAD,
                           nav_label=AUDIT_TITLE, group="Cluster", glyph="lines")

log = logging.getLogger(__name__)

#: the filter form's own words, marked here (the page owns its own copy) and translated at render
FILTER_LABELS: dict[str, str] = {
    "node": i18n._("Node"),
    "server": i18n._("Server"),
    "actor": i18n._("Actor"),
    "q": i18n._("Event"),
    "since": i18n._("From"),
    "until": i18n._("Through"),
}
FILTER_ANY_NODE = i18n._("any node")
FILTER_ANY_SERVER = i18n._("any server")
FILTER_APPLY = i18n._("Filter")
FILTER_CLEAR = i18n._("Clear")
FILTER_HINT = i18n._("A date on its own means the whole day. Times are UTC.")
AUDIT_UNAVAILABLE = i18n._("The audit trail could not be read.")
AUDIT_NO_BUS = i18n._("The audit trail is not reachable from this process yet.")

AUDIT_DEFAULT_SORT = "time"
#: the trail opens NEWEST FIRST: an operator opening the page wants what just happened, not the
#: first entry the installation ever wrote
AUDIT_DEFAULT_DIRECTION = "desc"

#: the ORDER BY is a CLOSED LOOKUP on the table's own column allow-list: a sort key from the
#: request never reaches the statement. ``time`` is the default and the natural order of a trail.
AUDIT_ORDER: dict[str, str] = {
    "time": "a.time",
    "node": "a.node",
    "actor": "a.actor",
    "server": "a.server_name",
    "event": "a.event",
}

#: One statement for the page and its total: the window count rides on the same rows, so the page
#: never pays for a second COUNT(*) over a table that only grows.
AUDIT_SQL = """
SELECT a.id, a.time, a.node, a.actor, a.server_name, a.event, a.discord_id, a.ucid,
       count(*) OVER () AS total
  FROM audit a
 WHERE (%(node)s::text IS NULL OR a.node = %(node)s)
   AND (%(server)s::text IS NULL OR a.server_name = %(server)s)
   AND (%(actor)s::text IS NULL OR a.actor ILIKE %(actor_pattern)s)
   AND (%(pattern)s::text IS NULL OR a.event ILIKE %(pattern)s)
   AND (%(since)s::timestamp IS NULL OR a.time >= %(since)s::timestamp)
   AND (%(until)s::timestamp IS NULL OR a.time <= %(until)s::timestamp)
 ORDER BY {order} {direction}, a.id {direction}
 LIMIT %(limit)s OFFSET %(offset)s
"""


#: The total behind the filters, asked for ONLY when a past-the-end page came back empty: the window
#: count rides on the rows otherwise, so the ordinary page never pays for a second statement.
AUDIT_COUNT_SQL = """
SELECT count(*) AS total
  FROM audit a
 WHERE (%(node)s::text IS NULL OR a.node = %(node)s)
   AND (%(server)s::text IS NULL OR a.server_name = %(server)s)
   AND (%(actor)s::text IS NULL OR a.actor ILIKE %(actor_pattern)s)
   AND (%(pattern)s::text IS NULL OR a.event ILIKE %(pattern)s)
   AND (%(since)s::timestamp IS NULL OR a.time >= %(since)s::timestamp)
   AND (%(until)s::timestamp IS NULL OR a.time <= %(until)s::timestamp)
"""


def capabilities() -> dict[str, str]:
    """The capability declaration for the path this module registers."""
    PAGE.declare()
    return {AUDIT_PATH: AUDIT_CAPABILITY}


def nav_items():
    """The sidebar entry for this page — the capability's own name, never a second spelling."""
    return (PAGE.nav_item(),)


def _like(pattern: str) -> str:
    """*pattern* escaped for ``ILIKE`` so a typed ``%`` matches a literal ``%``, not everything."""
    return "%" + pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(filters: audit_model.AuditFilters) -> dict:
    """The WHERE's bound parameters — every value BOUND, never formatted into the SQL."""
    return {
        "node": filters.node or None,
        "server": filters.server or None,
        "actor": filters.actor or None,
        "actor_pattern": _like(filters.actor) if filters.actor else None,
        "pattern": _like(filters.query) if filters.query else None,
        "since": filters.since, "until": filters.until,
    }


def _bind(filters: audit_model.AuditFilters, *, order: str, direction: str,
          limit: int, offset: int) -> dict:
    """The statement's bound parameters: the WHERE plus the page it asks for."""
    del order, direction  # part of the statement's text, never a bound value
    return {**_where(filters), "limit": limit, "offset": offset}


def _pool():
    """The pool this process already holds, or ``None`` before the bus is up."""
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        return getattr(ServiceRegistry.get(ServiceBus), "apool", None)
    except Exception:
        return None


async def _read(*, order_key: str, direction: str, page: int,
                filters: audit_model.AuditFilters) -> dict:
    """ONE page of the trail, and the total behind it.

    A page number PAST THE END is answered with the LAST page rather than an empty table: the reader
    typed a number, and an empty trail reads as "nothing happened". Only that case costs a second
    statement. Any failure is the page's typed refusal, never a 500.
    """
    pool = _pool()
    if pool is None:
        return {"rows": (), "total": 0, "page": 1, "pages": 1,
                "per_page": audit_model.PAGE_SIZE, "filters": filters, "error": AUDIT_NO_BUS}
    order = AUDIT_ORDER.get(order_key, AUDIT_ORDER["time"])
    direction_sql = "DESC" if direction == "desc" else "ASC"
    statement = AUDIT_SQL.format(order=order, direction=direction_sql)
    size = audit_model.PAGE_SIZE
    try:
        from psycopg.rows import dict_row

        async with pool.connection() as conn:
            async def fetch(statement: str, number: int, params: dict) -> list[dict]:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(statement, params)
                    return [dict(row) async for row in cursor]

            rows = await fetch(statement, page,
                               _bind(filters, order=order, direction=direction_sql,
                                     limit=size, offset=(page - 1) * size))
            total = int(rows[0]["total"]) if rows else 0
            pages = max((total + size - 1) // size, 1)
            if not rows and page > 1:
                # a page past the end: the trail's own size answers where the last page is, and the
                # reader lands there rather than on an empty table that reads as "nothing happened"
                counted = await fetch(AUDIT_COUNT_SQL, 1, _where(filters))
                total = int(counted[0]["total"]) if counted else 0
                pages = max((total + size - 1) // size, 1)
                page = pages
                rows = await fetch(statement, page,
                                   _bind(filters, order=order, direction=direction_sql,
                                         limit=size, offset=(page - 1) * size)) if total else []
    except Exception:  # noqa: BLE001 - an unreadable trail renders its refusal, never a 500
        log.exception("The audit trail could not be read")
        return {"rows": (), "total": 0, "page": 1, "pages": 1, "per_page": size,
                "filters": filters, "error": AUDIT_UNAVAILABLE}
    for row in rows:
        row.pop("total", None)
    return {"rows": rows, "total": total, "page": page, "pages": pages, "per_page": size,
            "filters": filters}


def _link(filters: audit_model.AuditFilters, *, page: int | None, sort: str,
          direction: str) -> str:
    """A link to this page carrying the filters, the order and the page number asked for."""
    query = dict(filters.carry)
    query[SORT_PARAM] = sort
    query[DIRECTION_PARAM] = direction
    if page is not None and page > 1:
        query[PAGE_PARAM] = str(page)
    return f"{AUDIT_PATH}?{urlencode(query)}"


def _paging(filters: audit_model.AuditFilters, view: audit_model.AuditView, *,
            sort: str, direction: str) -> dict:
    """The pager as data: only the two directions that exist, so no link leads past the trail."""
    return {"page": view.page, "pages": view.pages,
            "prev": _link(filters, page=view.page - 1, sort=sort, direction=direction)
            if view.page > 1 else None,
            "next": _link(filters, page=view.page + 1, sort=sort, direction=direction)
            if view.page < max(view.pages, 1) else None}


def _overview_state(request: Request):
    """The caller's own Overview, for the two filter selects. ``None`` when it cannot be read.

    The Overview is the dashboard's in-process data set — no I/O, no RPC — so reading it here costs a
    rebuild rather than a query. It is read for the NAMES only: the trail is filtered in SQL, never
    by narrowing a list of rows this process happens to hold.
    """
    try:
        from . import dashboard as dashboard_page

        return readmodels.overview(dashboard_page.request_source(request),
                                   level=dashboard_page.log_level(request))
    except Exception:  # noqa: BLE001 - a select with no choices still filters by URL
        log.debug("The filter choices could not be read", exc_info=True)
        return None


async def audit_context(request: Request) -> dict:
    """Everything the Audit page's template reads, in one value."""
    filters = audit_model.filters_from(request.query_params)
    params = view_params(request.query_params)
    # an UNSTATED order opens the trail newest first; a stated one is honoured as stated, and its
    # direction is the request's own (the shared layer's default is ``name``/``asc``, which is the
    # right default for an inventory and the wrong one for a trail)
    asked = params.sort in column_keys(AUDIT_TABLE)
    sort = params.sort if asked else AUDIT_DEFAULT_SORT
    direction = params.direction if asked else AUDIT_DEFAULT_DIRECTION
    answer = await _read(order_key=sort, direction=direction,
                         page=normalise_page(request.query_params.get(PAGE_PARAM)), filters=filters)
    view = audit_model.audit_view(answer)
    rows = view.rows
    context = lists_page.page_context(request, PAGE, rows=rows)
    # the table is the console's own component; only its rows, its order and its empty state are
    # this page's business
    context["table"] = readmodels.table_view(
        PAGE.path, AUDIT_TABLE, rows, sort=sort, direction=direction,
        params={**filters.carry, SORT_PARAM: sort, DIRECTION_PARAM: direction},
        label=AUDIT_TITLE)
    context["empty"] = dict(context.get("empty") or {}, **{AUDIT_TABLE: audit_model.empty_message()})
    nodes, servers = audit_model.filter_options(_overview_state(request))
    context["audit"] = {
        "filters": filters.carry,
        "nodes": audit_model.select_values(nodes, filters.node),
        "servers": audit_model.select_values(servers, filters.server),
        "labels": FILTER_LABELS,
        "any_node": FILTER_ANY_NODE, "any_server": FILTER_ANY_SERVER,
        "apply": FILTER_APPLY, "clear_label": FILTER_CLEAR, "hint": FILTER_HINT,
        "clear_link": AUDIT_PATH, "path": AUDIT_PATH,
        "summary": audit_model.range_sentence(view),
        # a read that failed says so in the page's own words, above the (empty) table
        "error": answer.get("error"),
        "paging": _paging(filters, view, sort=sort, direction=direction),
    }
    return context


def add_routes(router: APIRouter) -> APIRouter:
    """Add the Audit route to the shell's own router and return it."""
    PAGE.declare()

    @router.get(AUDIT_PATH, response_class=HTMLResponse, name=f"page-{AUDIT_TABLE}")
    async def _audit_page(request: Request):
        # no-store: the trail is written by the running bot, so a cached render would show entries
        # that are not the newest the process has
        return HTMLResponse(i18n.render(request, AUDIT_TEMPLATE, **await audit_context(request)),
                            headers={"Cache-Control": "no-store"})

    return router
