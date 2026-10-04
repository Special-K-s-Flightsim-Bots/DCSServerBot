"""
The window protocol's state: taking a shared resource, and who has stepped down for it.

Named for the ``resource_window`` table it writes — a window on a **resource**, never a window of a file
(``Node.read_file_window`` is the other sense of "window" in this tree).

What lives here is *taking* and *releasing* a resource, and the reading a follower makes to decide whether it may come
back. What a node does with a window — stopping its servers, running the installer — is not here: that is the
maintenance manager's job, and this module only owns the state those steps move between.

Two death signals, two different jobs, deliberately not one mechanism used twice:

* the **advisory lock** decides *who* runs the update. It is atomic and it dies with its session, which is what
  ``last_seen`` cannot do: a heartbeat lags by design, so without the lock two nodes would both read "nobody holds it".
* the holder's ``last_seen`` decides *the taker is gone*, and is the signal every follower restores on.

The row is the durable half of the lock: it names who holds the resource, what action and what scope, and it survives the
session that wrote it — which is exactly what a member that was down while the window opened needs.
"""
from __future__ import annotations

import logging

from typing import TYPE_CHECKING, NamedTuple

from psycopg import AsyncConnection, sql

if TYPE_CHECKING:
    from core.data.impl.nodeimpl import NodeImpl

log = logging.getLogger(__name__)

#: How many bytes of the resource hash become the advisory-lock key.
LOCK_KEY_BYTES = 8


def lock_key(resource_id: str) -> int:
    """The advisory-lock key of a resource, as a signed int8.

    Advisory locks share one flat namespace, so the key is *derived* from the resource id rather than handed out — and
    eight bytes carry the same collision odds as the id they come from. A collision would cost a window, never
    correctness: the node that finds the lock taken returns without stopping anything.
    """
    return int.from_bytes(bytes.fromhex(resource_id)[:LOCK_KEY_BYTES], 'big', signed=True)


class OpenWindow(NamedTuple):
    """The current window row, plus whether its holder is still alive."""
    holder_guild: int
    holder_node: str
    action: str
    scope: str
    holder_live: bool


class ResourceWindows:
    """One per node. Owns the taker's dedicated connection and reads the window for everybody else."""

    def __init__(self, node: "NodeImpl"):
        self.node = node
        self.log = node.log
        self._held: dict[str, AsyncConnection] = {}

    @property
    def heartbeat(self) -> int:
        """The cluster's own staleness rule, reused rather than reinvented (see the registry)."""
        return self.node.locals.get('cluster', {}).get('heartbeat', 30)

    def _pool(self):
        if self.node.cpool is None:
            raise RuntimeError("The cluster pool is not initialized: windows are read after init_db().")
        return self.node.cpool

    def holds(self, resource_id: str) -> bool:
        """Whether *this* process is the taker for *resource_id*."""
        return resource_id in self._held

    async def take(self, resource_id: str, action: str, scope: str) -> bool:
        """Try to become the taker for *resource_id*, and say whether this node got it.

        Not granted means this node is a follower that powered off nothing, so there is nothing to undo — the caller
        simply returns.

        :param action: what the window is for, 'update' | 'repair' | 'module'.
        :param scope: what the window may stop, copied from the resource type.
        """
        if resource_id in self._held:
            return True
        cluster_url, _ = self.node.get_database_urls()
        # A DEDICATED connection, deliberately not one from the pool: an advisory lock is session-scoped, and a pooled
        # connection may be handed to someone else or closed by max_idle while the window is still open.
        conn = await AsyncConnection.connect(cluster_url, autocommit=True)
        try:
            cursor = await conn.execute("SELECT pg_try_advisory_lock(%s)", (lock_key(resource_id),))
            row = await cursor.fetchone()
            if not row or not row[0]:
                await conn.close()
                return False
            # The acks belong to one window, so the previous window's must not make this one look already answered.
            await conn.execute("DELETE FROM resource_ack WHERE resource_id = %s", (resource_id,))
            await conn.execute("""
                INSERT INTO resource_window (resource_id, holder_guild, holder_node, action, scope)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (resource_id) DO UPDATE
                SET holder_guild = EXCLUDED.holder_guild, holder_node = EXCLUDED.holder_node,
                    action = EXCLUDED.action, scope = EXCLUDED.scope,
                    started = (now() AT TIME ZONE 'utc')
            """, (resource_id, self.node.guild_id, self.node.name, action, scope))
        except Exception:
            # Closing the session is what releases the lock. A grant followed by a failed write would otherwise leave
            # the resource held with no window to show for it — blocking every other cluster for nothing.
            await conn.close()
            raise
        self._held[resource_id] = conn
        return True

    async def release(self, resource_id: str) -> None:
        """Give the resource back: remove the row, then drop the session, which releases the lock.

        The row is deleted first on purpose — a follower restores when the row is gone, and the lock must not be free
        while a row still claims somebody holds the resource. The delete names this holder, so a confused process can
        never take away somebody else's window.
        """
        conn = self._held.pop(resource_id, None)
        if conn is None:
            return
        try:
            await conn.execute("""
                DELETE FROM resource_window
                WHERE resource_id = %s AND holder_guild = %s AND holder_node = %s
            """, (resource_id, self.node.guild_id, self.node.name))
        finally:
            await conn.close()

    async def state(self, resource_id: str) -> OpenWindow | None:
        """The window that is open on *resource_id* right now, or ``None``.

        ``holder_live`` is the cluster's own staleness rule applied to the holder's node row, which is what makes the
        restore rule work: a taker that died leaves its row behind, so presence alone would keep every follower dark
        forever.

        A follower must read this when it decides to restore, never a value it remembered — a new taker can open a new
        window while the old followers are still down, and they have to stay down.
        """
        async with self._pool().connection() as conn:
            query = sql.SQL("""
                SELECT w.holder_guild, w.holder_node, w.action, w.scope,
                       coalesce(n.last_seen > (NOW() AT TIME ZONE 'UTC' - interval {grace}), FALSE) AS holder_live
                FROM resource_window w
                LEFT JOIN nodes n ON n.guild_id = w.holder_guild AND n.node = w.holder_node
                WHERE w.resource_id = %s
            """).format(grace=sql.Literal(f"{self.heartbeat} seconds"))
            cursor = await conn.execute(query, (resource_id,))
            row = await cursor.fetchone()
            return OpenWindow(*row) if row else None

    async def ack(self, resource_id: str) -> None:
        """Record that this node has stepped down for the window that is open now."""
        async with self._pool().connection() as conn:
            await conn.execute("""
                INSERT INTO resource_ack (resource_id, guild_id, node)
                VALUES (%s, %s, %s)
                ON CONFLICT (resource_id, guild_id, node) DO UPDATE SET acked = (now() AT TIME ZONE 'utc')
            """, (resource_id, self.node.guild_id, self.node.name))

    async def acks(self, resource_id: str) -> list[tuple[int, str]]:
        """Every member that has acknowledged the current window, as ``(guild_id, node)``.

        Only ever used to name who is still missing in the taker's log and audit: the count that decides whether the
        installer may run is the members' own ``servers_up``, not this.
        """
        async with self._pool().connection() as conn:
            cursor = await conn.execute("""
                SELECT guild_id, node FROM resource_ack WHERE resource_id = %s ORDER BY guild_id, node
            """, (resource_id,))
            return [(row[0], row[1]) async for row in cursor]
