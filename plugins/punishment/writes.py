"""The punishment plugin's ONE write path for penalty points.

``/punish`` and the website's penalty controls both call :func:`record_event`, and ``/forgive`` and
the website's forgive both call :func:`delete_points`, so the two surfaces cannot drift: a row
inserted without its publish records points that punish nobody, and a forgive that deletes without
unbanning leaves a ban nobody can lift.

The log table is ``pu_events (init_id, server_name, event, points)``. ``points`` may be negative —
a negative value is how points are taken back — and ``event`` carries the operator's reason text.
The plugin's ``decay`` task scales and deletes these same rows, so nothing here needs to know about
it.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

#: ONE inserted row per penalty event. The gradient of the reason/points pair is the caller's.
INSERT_EVENT_SQL = ("INSERT INTO pu_events (init_id, server_name, event, points) "
                    "VALUES (%s, %s, %s, %s)")

#: One UCID's rows, deleted by a forgive.
DELETE_EVENTS_SQL = "DELETE FROM pu_events WHERE init_id = %s"

#: A player row's Discord link, used to expand a UCID to the group the page shows.
LINKED_UCIDS_SQL = "SELECT ucid FROM players WHERE discord_id = %s"


class PunishmentPublishError(RuntimeError):
    """The penalty row was written, but publishing it on the punishment PubSub failed.

    The row exists, so a caller must report a PARTIAL outcome: the points are recorded and the
    punishment ladder did not run.
    """


def _ban_targets(server: Any, servers: dict) -> list[str]:
    """The servers a ban raised on *server* covers.

    Every server whose ``managed_by`` names one of *server*'s own; an empty list means *server*
    declares no ``managed_by`` and the ban is bot-wide.
    """
    managed_by = server.locals.get('managed_by') or []
    if not managed_by:
        return []
    return [name for name, srv in servers.items()
            if (srv.locals.get('managed_by') or [])
            and not set(srv.locals['managed_by']).isdisjoint(managed_by)]


async def record_event(pool: Any, publish: Callable[[dict], Awaitable[Any]], *, ucid: str,
                       server_name: str, event: str, points: int | float,
                       guild_id: int | None) -> None:
    """Write one penalty row and publish it on the punishment PubSub.

    The two steps are ONE operation and the order is fixed: the row is committed first, then the
    publish makes the consumer apply that server's punishment ladder. A failed publish is raised as
    :class:`PunishmentPublishError` — the row is already written, so the caller must say so rather
    than claim either a clean failure or a clean success.
    """
    from psycopg.types.json import Json

    async with pool.connection() as conn:
        await conn.execute(INSERT_EVENT_SQL, (ucid, server_name, event, points))
    payload = {"guild_id": guild_id, "node": "Master",
               "data": Json({"init_id": ucid, "server_name": server_name,
                             "event": event, "points": points})}
    try:
        await publish(payload)
    except Exception as ex:
        log.exception("penalty points for %s were written but could not be published", ucid)
        raise PunishmentPublishError(str(ex)) from ex


async def linked_ucids(conn: Any, ucid: str) -> list[str]:
    """Every UCID of the player's Discord link, *ucid* included — the group the player page shows.

    An unlinked player (``discord_id`` <= 0) is its own group. The given UCID is kept first and is
    never dropped even when the link read answers without it.
    """
    from psycopg.rows import dict_row

    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute("SELECT ucid, discord_id FROM players WHERE ucid = %s", (ucid,))
        row = await cursor.fetchone()
    if not row:
        return [ucid]
    try:
        discord_id = int(row.get("discord_id"))
    except (TypeError, ValueError):
        discord_id = -1
    if discord_id <= 0:
        return [ucid]
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(LINKED_UCIDS_SQL, (discord_id,))
        linked = [entry.get("ucid") for entry in await cursor.fetchall()]
    ucids = [value for value in linked if value]
    if ucid not in ucids:
        ucids.insert(0, ucid)
    return ucids


async def delete_points(conn: Any, ucids: list[str]) -> int:
    """Delete every penalty row of *ucids* and answer how many rows were removed."""
    removed = 0
    for ucid in ucids:
        cursor = await conn.execute(DELETE_EVENTS_SQL, (ucid,))
        removed += max(int(getattr(cursor, "rowcount", 0) or 0), 0)
    return removed
