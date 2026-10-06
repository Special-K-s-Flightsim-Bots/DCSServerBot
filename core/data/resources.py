"""
The federation registry: which resources exist, who uses them, and what has to stop when one is taken down.

A **resource** is anything several clusters can share on one machine — a DCS installation today — and its name is
derived, never stored (see :mod:`core.utils.resources`). This module is the cluster-database side of it: the
registration of this node's own installations, and the readings the coordination protocol makes.

The tables are not created here: they belong to the cluster schema (``sql/cluster.sql``, and ``migrate_3_19`` for
databases that already exist). A registry that created its own tables would give one install two schemas with two
histories.

Nothing here decides anything either. Taking a resource down is the window protocol, deliberately not in this module —
what lives here is the state it reads.
"""
from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Iterable

from psycopg import sql

from core.utils.resources import ResourceIdentity

if TYPE_CHECKING:
    # the pools live on NodeImpl's cpool, not on the abstract Node; the import is typing-only so this module can be
    # imported BY nodeimpl without a cycle
    from core.data.impl.nodeimpl import NodeImpl

log = logging.getLogger(__name__)

#: What a resource type may stop, declared **by type**: no installation can be configured to take the wrong thing
#: down, and the value travels with every window row. Only ``dcs_installation`` is implemented so far; the rest are
#: typed from day one so that adding one is an addition rather than a redesign.
SCOPES = {
    'dcs_installation': 'server',
    'srs_installation': 'service',
    'tacview_installation': 'service',
    'bot_installation': 'service'
}


def scope_of(resource_type: str) -> str:
    """The scope a resource type may require."""
    try:
        return SCOPES[resource_type]
    except KeyError:
        raise ValueError(
            f"Unknown resource type '{resource_type}'. Add it to SCOPES together with the scope it may stop — "
            f"a resource with no declared scope must not reach a maintenance window."
        )


class ResourceRegistry:
    """One per node: registers the resources this node uses, and reads the shared state about them."""

    def __init__(self, node: "NodeImpl"):
        self.node = node
        self.log = node.log

    @property
    def heartbeat(self) -> int:
        """The staleness rule the cluster already fences nodes with — this node's own ``cluster.heartbeat`` setting,
        which is also what ``get_ready_nodes()`` applies to *peers*, so the registry does not invent a second number.
        """
        return self.node.locals.get('cluster', {}).get('heartbeat', 30)

    def _pool(self):
        """The cluster pool.

        Registration runs after the pools are opened, so a ``None`` pool here is a wiring error rather than a state to
        work around — and saying so is cheaper than a confusing failure four frames later.
        """
        if self.node.cpool is None:
            raise RuntimeError("The cluster pool is not initialized: resource registration runs after init_db().")
        return self.node.cpool

    async def register(self, identity: ResourceIdentity, *, dependents: Iterable[tuple[str, str]] = (),
                       servers_up: int = 0) -> None:
        """Declare this node's use of *identity*, and what it runs on it.

        Idempotent by construction, and automatic rather than opt-in: the user who installs a second bot on one PC has
        no idea they just created a dependency, so nothing may depend on them configuring it.

        :param dependents: ``(kind, name)`` pairs that must stop when the resource goes down — for a DCS installation
            every server using it.
        :param servers_up: how many of them this node currently has running.
        """
        async with self._pool().connection() as conn:
            # ON CONFLICT DO NOTHING keeps the FIRST declarer as the resource's owner: it carries no veto, it is
            # there so a human reading the row can see who brought the resource in.
            await conn.execute("""
                INSERT INTO resources (id, type, scope, owner_guild, path)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (identity.id, identity.resource_type, scope_of(identity.resource_type),
                  self.node.guild_id, identity.path))
            await conn.execute("""
                INSERT INTO resource_members (resource_id, guild_id, node, servers_up)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (resource_id, guild_id, node) DO UPDATE
                SET servers_up = EXCLUDED.servers_up, changed = (now() AT TIME ZONE 'utc')
            """, (identity.id, self.node.guild_id, self.node.name, servers_up))
            for kind, name in dependents:
                await conn.execute("""
                    INSERT INTO resource_dependents (resource_id, guild_id, node, kind, name)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (resource_id, guild_id, node, kind, name) DO NOTHING
                """, (identity.id, self.node.guild_id, self.node.name, kind, name))

    async def members(self, resource_id: str) -> list[tuple[int, str, int, bool]]:
        """Every member of *resource_id* as ``(guild_id, node, servers_up, live)``.

        A member whose node row is gone reads as not live rather than raising: a node unregistered while still in the
        table must not break the reading the protocol depends on.
        """
        async with self._pool().connection() as conn:
            query = sql.SQL("""
                SELECT m.guild_id, m.node, m.servers_up,
                       coalesce(n.last_seen > (NOW() AT TIME ZONE 'UTC' - interval {grace}), FALSE) AS live
                FROM resource_members m
                LEFT JOIN nodes n ON n.guild_id = m.guild_id AND n.node = m.node
                WHERE m.resource_id = %s
                ORDER BY m.guild_id, m.node
            """).format(grace=sql.Literal(f"{self.heartbeat} seconds"))
            cursor = await conn.execute(query, (resource_id,))
            return [(row[0], row[1], row[2], row[3]) async for row in cursor]

    async def in_service_count(self, resource_id: str) -> int:
        """How many servers the **live** members of *resource_id* still have running.

        This is the number the whole protocol turns on: the taker may run an installer only when it is zero. It is
        derived from the members' own published counts, never kept as a counter of its own — a stored total desyncs
        the moment one process dies, and then never returns to zero. Liveness is the cluster's own staleness rule, so
        a dead member drops out without anybody having to notice it died.
        """
        async with self._pool().connection() as conn:
            query = sql.SQL("""
                SELECT coalesce(sum(m.servers_up), 0)
                FROM resource_members m
                JOIN nodes n ON n.guild_id = m.guild_id AND n.node = m.node
                WHERE m.resource_id = %s
                  AND n.last_seen > (NOW() AT TIME ZONE 'UTC' - interval {grace})
            """).format(grace=sql.Literal(f"{self.heartbeat} seconds"))
            cursor = await conn.execute(query, (resource_id,))
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def set_servers_up(self, resource_id: str, servers_up: int) -> None:
        """Publish this node's own count of running servers on *resource_id*.

        Only ever its own number: that is what makes the total trustworthy, and what a counter maintained by a third
        party could not be.
        """
        async with self._pool().connection() as conn:
            await conn.execute("""
                UPDATE resource_members
                SET servers_up = %s, changed = (now() AT TIME ZONE 'utc')
                WHERE resource_id = %s AND guild_id = %s AND node = %s
            """, (servers_up, resource_id, self.node.guild_id, self.node.name))

    async def dependents(self, resource_id: str, *, kinds: Iterable[str] = ('server',),
                         states: Iterable[str] = ('in_service', 'stepped_down')) -> list[tuple[str, str, str]]:
        """This node's own dependents of *resource_id* as ``(kind, name, state)``.

        Always scoped to this node: a node acts on what is its own, and nothing else.
        """
        async with self._pool().connection() as conn:
            cursor = await conn.execute("""
                SELECT kind, name, state FROM resource_dependents
                WHERE resource_id = %s AND guild_id = %s AND node = %s
                  AND kind = ANY(%s) AND state = ANY(%s)
                ORDER BY kind, name
            """, (resource_id, self.node.guild_id, self.node.name, list(kinds), list(states)))
            return [(row[0], row[1], row[2]) async for row in cursor]

    async def set_dependents_state(self, resource_id: str, state: str, names: Iterable[str]) -> None:
        """Mark this node's dependents of *resource_id* as *state*.

        This is the durable half of a step-down, and it has to be durable: a window is most likely to be open while
        this node is *rebooting* (that is when an installation is updated), so an in-memory record of what it powered
        off would be gone exactly when the restore needs it.
        """
        names = list(names)
        if not names:
            return
        async with self._pool().connection() as conn:
            await conn.execute("""
                UPDATE resource_dependents SET state = %s
                WHERE resource_id = %s AND guild_id = %s AND node = %s AND name = ANY(%s)
            """, (state, resource_id, self.node.guild_id, self.node.name, names))
