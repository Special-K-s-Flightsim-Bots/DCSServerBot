"""The one writer for the cluster's ``audit`` table.

An audit entry has two independent halves: the record and the announcement. The Discord bot posts
it to its audit channel AND writes the row; an install without Discord writes the row only. Both
halves therefore live behind their own seam, and the row never depends on a Discord object — it
needs the connection pool and a node name, nothing else.
"""
from __future__ import annotations

from typing import Any


async def record_audit(apool: Any, *, node: str, event: str,
                       server_name: str | None = None,
                       actor: str | None = None,
                       discord_id: int | None = None,
                       ucid: str | None = None) -> None:
    """Append ONE entry to the ``audit`` table of the cluster *apool* belongs to.

    ``event`` is the sentence the operator reads. ``actor`` is the identity as the caller names it:
    a Discord member already travels in ``discord_id`` and a linked player in ``ucid``, but a local
    web user has neither, so its label (``[web:local/<name>]``) is kept here. ``node`` names the
    node the entry belongs to, which is what makes the table a per-cluster ledger rather than a
    per-process log.
    """
    async with apool.connection() as conn:
        await conn.execute("""
            INSERT INTO audit (node, event, server_name, actor, discord_id, ucid)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (node, event, server_name, actor, discord_id, ucid))
