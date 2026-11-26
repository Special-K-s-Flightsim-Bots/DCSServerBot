"""The punishment plugin's ONE write path for penalty points.

``/punish`` and the website's penalty controls both call :func:`record_event`, and ``/forgive`` and
the website's forgive both call :func:`delete_points`, so the two surfaces cannot drift: a row
inserted without its publish records points that punish nobody, and a forgive that deletes without
unbanning leaves a ban nobody can lift.

The log table is ``pu_events (init_id, server_name, event, points)``. ``points`` may be negative —
a negative value is how points are taken back — and ``event`` carries the operator's reason text.
The plugin's ``decay`` task scales and deletes these same rows, so nothing here needs to know about
it.

It also owns the BAN-LENGTH rules: :func:`served_ban_count` reads a player's ban history and
:func:`escalated_days` picks one entry of a ban ladder from it, so an escalating ladder is decided in
one place rather than at each caller.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

#: The ban length used when a punishment names none.
DEFAULT_BAN_DAYS = 3

#: FLOWN days without a served ban after which an escalating ladder starts over at its first entry.
#: A flown day is a calendar day with at least one session, so a player who stays away does not
#: clear their ladder by doing nothing.
DEFAULT_BAN_RESET_FLIGHT_DAYS = 14

#: ONE inserted row per penalty event. The gradient of the reason/points pair is the caller's.
INSERT_EVENT_SQL = ("INSERT INTO pu_events (init_id, server_name, event, points) "
                    "VALUES (%s, %s, %s, %s)")

#: One UCID's rows, deleted by a forgive.
DELETE_EVENTS_SQL = "DELETE FROM pu_events WHERE init_id = %s"

#: A player row's Discord link, used to expand a UCID to the group the page shows.
LINKED_UCIDS_SQL = "SELECT ucid FROM players WHERE discord_id = %s"

#: How many bans of one UCID were SERVED since the player last flew ``reset_after_days`` days — the
#: escalation level of a ban ladder.
#:
#: ``flown`` is the cutoff: the OLDEST of the player's last N flown days, a flown day being a
#: distinct calendar day with at least one session. Fewer than N flown days in total leaves the
#: cutoff at the player's first day, and no flight at all leaves it NULL — ``-infinity`` — so a
#: player who never flew has nothing to clear and every served ban counts.
#:
#: A ban is measured by its LIFT time (``banned_until``): ``bans`` holds the ban in force (its end
#: lies ahead, so it counts), and the archive trigger writes the UNBAN moment into ``bans_hist``,
#: which is what makes the window start when the ban ends rather than when it began. ``auto_unban``
#: states whether an archived ban ran to its end (TRUE) or was lifted early (FALSE, which the
#: infractions report prints as "Forgiven") — a ban the player never served must not escalate
#: anything. ``DISTINCT banned_at`` because one ban covering several servers is stored as one row
#: per server.
SERVED_BAN_COUNT_SQL = """
    WITH flown AS (
        SELECT min(day) AS since FROM (
            SELECT DISTINCT hop_on::date AS day FROM statistics
            WHERE player_ucid = %s
            ORDER BY day DESC LIMIT %s
        ) d
    )
    SELECT (SELECT count(DISTINCT b.banned_at) FROM (
        SELECT ucid, banned_at, banned_until, banned_by, TRUE AS served FROM bans
        UNION ALL
        SELECT ucid, banned_at, banned_until, banned_by, coalesce(auto_unban, FALSE)
        FROM bans_hist
    ) b
    WHERE b.ucid = %s AND b.banned_by = %s AND b.served
      AND b.banned_until >= coalesce(flown.since, DATE '-infinity'))
    FROM flown
"""


async def served_ban_count(pool: Any, ucid: str, *, flight_days: int, banned_by: str) -> int:
    """How many bans of *ucid* were served since their last *flight_days* flown days, by *banned_by*.

    The window is measured in days the player actually FLEW, counted back from now, and from each
    ban's LIFT — so a ladder resets once the player has flown *flight_days* days without being
    banned, and staying away clears nothing.
    """
    async with pool.connection() as conn:
        cursor = await conn.execute(SERVED_BAN_COUNT_SQL, (ucid, flight_days, ucid, banned_by))
        row = await cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def escalated_days(days: Any, prior_bans: int) -> int:
    """The ban length *days* gives a player with *prior_bans* served bans.

    A plain number is that many days. A ladder is indexed by *prior_bans* and clamped to its last
    entry, so the first ban takes the first entry and every ban beyond the ladder's length takes the
    longest one.
    """
    if isinstance(days, (list, tuple)):
        if not days:
            return DEFAULT_BAN_DAYS
        return int(days[min(max(int(prior_bans), 0), len(days) - 1)])
    return int(days) if days else DEFAULT_BAN_DAYS


async def ban_days(pool: Any, ucid: str, punishment: dict, *, plugin_name: str) -> int:
    """The ban length a punishment asks for: a fixed number of days, or the next ladder step.

    ``days: [3, 5, 7, 10]`` is indexed by how many bans of this player were SERVED since they last
    flew ``reset_after_days`` days — a ban lifted before it expired does not count — and the last
    entry is the ceiling. A count that fails falls back to the first entry: the ban itself must still
    be raised, and a teamkiller nobody bans because a history read failed is the worse outcome.
    """
    days = punishment.get('days', DEFAULT_BAN_DAYS)
    if not isinstance(days, (list, tuple)):
        return escalated_days(days, 0)
    window = int(punishment.get('reset_after_days', DEFAULT_BAN_RESET_FLIGHT_DAYS))
    try:
        prior = await served_ban_count(pool, ucid, flight_days=window, banned_by=plugin_name)
    except Exception:
        log.exception(f"Could not count the served bans of {ucid}, using the first ban ladder entry.")
        prior = 0
    return escalated_days(days, prior)


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
