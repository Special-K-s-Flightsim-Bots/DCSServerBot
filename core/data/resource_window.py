"""
The window protocol's state: taking a shared resource, and who has stepped down for it.

Named for the ``resource_window`` table it writes — a window on a **resource**, never a window of a file
(``Node.read_file_window`` is the other sense of "window" in this tree).

What lives here is *taking* and *releasing* a resource, the reading a follower makes to decide whether it may come
back, and the two actions that reading leads to. The stopping itself is the maintenance manager's — never re-implemented
here — because this module owns only *which* servers and *when*; running the installer belongs to the update path.

Two death signals, two different jobs, deliberately not one mechanism used twice:

* the **advisory lock** decides *who* runs the update. It is atomic and it dies with its session, which is what
  ``last_seen`` cannot do: a heartbeat lags by design, so without the lock two nodes would both read "nobody holds it".
* the holder's ``last_seen`` decides *the taker is gone*, and is the signal every follower restores on.

The row is the durable half of the lock: it names who holds the resource, what action and what scope, and it survives the
session that wrote it — which is exactly what a member that was down while the window opened needs.
"""
from __future__ import annotations

import asyncio
import logging
import time

from typing import TYPE_CHECKING, NamedTuple

from psycopg import AsyncConnection, sql

from core.data.maintenance import ServerMaintenanceManager
from core.data.resources import ResourceRegistry

if TYPE_CHECKING:
    from core.data.impl.nodeimpl import NodeImpl

log = logging.getLogger(__name__)

#: How many bytes of the resource hash become the advisory-lock key.
LOCK_KEY_BYTES = 8

#: How often a follower re-reads the window and a taker re-reads the residue while it waits.
POLL_INTERVAL = 2.0

#: How long a taker waits for the other clusters to stand down before it refuses to update. Long enough for a
#: follower's own popup chain (the manager's default warn times), short enough that a stuck cluster is visible.
WINDOW_WAIT = 300.0

#: What the players of a follower's servers are told when a shared installation is taken down.
WINDOW_MESSAGE = "The DCS installation is being updated - this server is going down in {}"


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

    async def acked(self, resource_id: str) -> bool:
        """Whether THIS node has acknowledged the window that is open now — the follower's "am I done?" flag.

        Per window, because ``take()`` clears every ack: it cannot answer for a window that has already been
        replaced, which is exactly what the follower's re-reading rule needs.
        """
        return (self.node.guild_id, self.node.name) in await self.acks(resource_id)

    async def unack(self, resource_id: str) -> None:
        """Drop this node's acknowledgement again: a node that has restored is owed none.

        Restore is only ever reached with no live window, so no taker is waiting on this row — and leaving it
        behind would keep reading as stepped down, so the next cycle would restore again instead of settling.
        """
        async with self._pool().connection() as conn:
            await conn.execute("""
                DELETE FROM resource_ack WHERE resource_id = %s AND guild_id = %s AND node = %s
            """, (resource_id, self.node.guild_id, self.node.name))


def follower_action(window: OpenWindow | None, stepped_down: bool) -> str:
    """What a follower does right now, from ONE reading of the window: ``step_down`` | ``restore`` | ``idle``.

    A window counts only while its holder is **alive** — the same predicate the start gate uses. A taker that died
    leaves its row behind forever, so trusting presence alone would keep every cluster dark; and a *new* taker can open
    a new window while the old followers are still down, which is why they re-read rather than remember.
    """
    if window and window.holder_live:
        return 'idle' if stepped_down else 'step_down'
    return 'restore' if stepped_down else 'idle'


class ResourceWindowProtocol:
    """What a node DOES about a window: take its own dependents down, wait, and bring them back.

    The stopping is the maintenance manager's — its popups, its players, its stop — and is never re-implemented here.
    What this class owns is *which* servers, *when*, and the durable record that survives the reboot an update usually
    happens in. Running the installer belongs to the update path, not here.
    """

    def __init__(self, node: "NodeImpl", registry: ResourceRegistry | None = None,
                 windows: ResourceWindows | None = None):
        self.node = node
        self.log = node.log
        self.registry = registry or ResourceRegistry(node)
        self.windows = windows or ResourceWindows(node)

    async def step_down(self, resource_id: str, scope: str = 'server', warn_times: list[int] | None = None) -> int:
        """Stop this node's own dependents on the resource, and record what it actually stopped.

        Only one dependent kind is implemented (``server``); the schema and the window carry the others from day one,
        so a later kind is an addition here rather than a redesign.

        :param warn_times: the popup chain to use. A taker passes the update's own, so a node that would have given
            its players 300 seconds still does; a follower uses the manager's default.
        """
        if scope != 'server':
            self.log.warning(f"A {scope} window on {resource_id} is not implemented yet - nothing stepped down.")
            return 0
        names = {name for _kind, name, _state in await self.registry.dependents(resource_id)}
        manager = ServerMaintenanceManager(self.node, warn_times=warn_times, message=WINDOW_MESSAGE)
        servers = [server for server in manager.node_servers()
                   if server is not None and getattr(server, 'name', None) in names]
        if not servers:
            await self.registry.set_servers_up(resource_id, 0)
            await self.windows.ack(resource_id)
            return 0
        # flag=False deliberately: the start gate is what keeps a server from coming up during a window, and the
        # maintenance flag would be a second guard whose ownership has to be tracked across a reboot to be cleared
        # correctly. One guard, one owner.
        outcome = await manager.power_off(servers, flag=False, stop=True)
        # Everything below is measured over what this operation HANDLED — the servers that were in service when
        # the window opened. Mixing the sets up is how a window starts a server nobody asked to start:
        #   * a server that was already down before the window is NOT ours: it is neither recorded (restore()
        #     starts exactly the recorded names, so recording it is the very act that starts it) nor published
        #     as up;
        #   * "went down" is the POST-state test, so a server that refused to stop is never recorded as stepped
        #     down, and it stays visible in the published count — a hopeful 0 there is what would let an update
        #     run under a live server.
        went_down = [server for server in outcome.handled if not manager.in_service(server)]
        still_up = [server for server in outcome.handled if manager.in_service(server)]
        stopped = [str(server.name) for server in went_down if getattr(server, 'name', None)]
        await self.registry.set_dependents_state(resource_id, 'stepped_down', stopped)
        await self.registry.set_servers_up(resource_id, len(still_up))
        await self.windows.ack(resource_id)
        self.log.info(f"- Stepped down for {resource_id}: {len(went_down)} of {len(outcome.handled)} server(s) stopped.")
        if still_up:
            self.log.warning(f"- {len(still_up)} server(s) on {resource_id} are still up. The update "
                             f"cannot start while they are.")
        return len(went_down)

    async def restore(self, resource_id: str) -> int:
        """Bring back exactly the dependents this node marked as stepped down — never more.

        The list comes from the table rather than from memory: this node may well have rebooted since it stepped down,
        and whatever it did *not* stop must not be started here.
        """
        names = {name for _kind, name, _state in
                 await self.registry.dependents(resource_id, states=('stepped_down',))}
        manager = ServerMaintenanceManager(self.node, message=WINDOW_MESSAGE)
        servers = [server for server in manager.node_servers()
                   if server is not None and getattr(server, 'name', None) in names]
        outcome = await manager.power_on(servers, start=servers, skip_running=True)
        await self.registry.set_dependents_state(resource_id, 'in_service', names)
        # ... and the acknowledgement goes with the record: a restored node is no longer stepped down.
        await self.windows.unack(resource_id)
        await self.registry.set_servers_up(
            resource_id, sum(1 for server in manager.node_servers()
                             if server is not None and manager.in_service(server)))
        if servers:
            self.log.info(f"- {len(outcome.started)} server(s) restored after the window on {resource_id} closed.")
        return len(outcome.started)

    async def wait_for_clear(self, resource_id: str, timeout: float) -> int:
        """Wait until no live member still has a server running on the resource, and return what is left.

        A residue is *returned*, never silently waited out: the installer is not started, and the operator has to know
        whose servers are up. The number is the members' own published sum, so it needs no clock of its own — and it
        cannot see an unreachable member's servers, which is what the pre-flight probe is for.
        """
        deadline = time.monotonic() + timeout
        while True:
            residue = await self.registry.in_service_count(resource_id)
            if residue == 0 or time.monotonic() >= deadline:
                return residue
            await asyncio.sleep(POLL_INTERVAL)

    async def publish(self, resource_id: str) -> int:
        """Publish how many of this node's dependents on the resource are running right now.

        The number every window turns on, and only ever this node's own. Refreshed on every cycle rather than kept
        anywhere else: a count written only when a window opens would still read 0 for a node that has simply been
        running servers all along — and a taker would then update underneath them. Written unconditionally rather than
        diffed in memory, because one small UPDATE per resource per cycle is cheaper than a cache that can go stale
        against a restart.
        """
        manager = ServerMaintenanceManager(self.node, message=WINDOW_MESSAGE)
        names = {name for _kind, name, _state in await self.registry.dependents(resource_id)}
        running = sum(1 for server in manager.node_servers()
                      if server is not None and getattr(server, 'name', None) in names
                      and manager.in_service(server))
        await self.registry.set_servers_up(resource_id, running)
        return running

    async def follow(self, resource_id: str, scope: str = 'server') -> str:
        """One follower cycle for one resource: publish, read, decide, act. Returns what it did.

        The read and the decision are deliberately in one place — the same predicate decides whether this node steps
        down, comes back, or does nothing, and a second reading somewhere else is how those two get to disagree. What
        it *did* is recorded where it matters (the dependents table), never here, so it outlives this process.

        Publishing first is what makes the count a taker sums trustworthy: every node says how much of its own is
        running, every cycle, whether or not a window exists.
        """
        await self.publish(resource_id)
        window = await self.windows.state(resource_id)
        # "Have I already stepped down?" is asked of this node's own ACK for the window open now, never of the
        # stepped-down record being non-empty: that record is empty BOTH before a step-down and after one that had
        # nothing to stop (a node with 0 servers in service), so reading it as "not done yet" re-runs the whole
        # step-down every cycle for as long as the window is open. The ack is written when a step-down completes and
        # is cleared by every take(), so it answers for exactly the window it belongs to.
        action = follower_action(window, await self.windows.acked(resource_id))
        if action == 'step_down':
            await self.step_down(resource_id, scope)
        elif action == 'restore':
            await self.restore(resource_id)
        return action
