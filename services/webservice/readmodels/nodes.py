"""Nodes: the cluster registry the running process holds (``node.all_nodes``), plus the SERVERS
each of them currently carries.

Why ``all_nodes`` rather than the ``nodes`` table: the dashboard is a live-state page and the
question a reader asks is "is that node up right now". ``all_nodes`` is the master's in-process
view (name -> node object, or ``None`` for a node the cluster knows about but has not resolved),
and it is populated by the heartbeat that already decides whether a node is alive. Nothing is
awaited here and no RPC is issued: a status page must not send traffic to the node it is
describing.

A node entry that is ``None`` renders as offline with its counters at 0 rather than vanishing —
"the cluster knows this node and cannot reach it" is the interesting case, not a reason to hide a
row.
"""
from __future__ import annotations

from .access import attr, safe, text
from .model import NO_NODES_MESSAGE, NodeView

__all__ = ["nodes", "node_views"]

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_NODES_MESSAGE


def node_views(entries, servers) -> list[NodeView]:
    """Build the node rows from a ``{name: node-or-None}`` mapping and the server views."""
    by_node: dict[str, list] = {}
    for server in servers:
        by_node.setdefault(server.node_name, []).append(server)

    out: list[NodeView] = []
    for name, node in (entries or {}).items():
        key = text(name)
        attached = by_node.get(key, [])
        instances = safe(lambda: len(attr(node, "instances", {}) or {}), 0) if node is not None else 0
        out.append(NodeView(
            name=key,
            master=bool(attr(node, "master", False)),
            online=node is not None,
            instances=instances or 0,
            servers=len(attached),
            players=sum(server.active_players for server in attached),
        ))
    out.sort(key=lambda view: (not view.master, view.name))
    return out


def nodes(source) -> list[NodeView]:
    """The node rows for *source* (empty when the source is not live)."""
    return node_views(getattr(source, "nodes", None), _server_views(source))


def _server_views(source) -> list:
    """The server views for the counters, built through the servers read model.

    Imported inside the function: ``servers.py`` imports this module for nothing, but keeping the
    call local means the two modules can never become an import cycle if either grows a shared
    helper.
    """
    from .servers import servers as server_views
    return safe(lambda: server_views(source), []) or []