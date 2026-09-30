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

CARD B2 — AN OFFLINE NODE'S INSTANCE COUNT IS THE DECLARED ONE. An offline node has no live
registry to read (``all_nodes[name] is None``), so its instance FIGURE used to fall to ``0`` and,
worse, the row then read *"No instances on this node"* — a wrong count and a misleading empty on
the very row a reader uses to reason about a dead node. The count now comes from the ``instances:``
block that node declares in ``nodes.yaml`` (``source.declared_instances``, built once at source
build from the process's loaded config — see ``source.declared_node_instances``), which is exactly
the truth the config holds. A node that genuinely declares none still reads 0, with the note.

THE RULES FOR WHAT THE OFFLINE ROW STILL DOES NOT KNOW (stated, not left to the template):

* the Instances COUNT is the declared total — we know those instances EXIST, not who occupies them;
* the "occupied by" question does not arise HERE, and that is deliberate: card B2 adds NO rows to
  the Instances table for an offline node (a declared name is a COUNT, never merged into
  ``source.instances``), so there is no per-instance "occupied by" cell for such a node to fill and
  none is guessed. Surfacing an offline node's instances as ROWS — with an unknown binding — is the
  separate residual tracked for §3.1 (an instance is not visible until its node reports it), not
  this card;
* the Servers/Players figures stay ``0`` for an offline node (no live server view exists to count;
  we do not invent servers from declared instances) and the ONLINE/OFFLINE tag is unchanged;
* a LIVE node's row is untouched: its count is its own registry's length, as before.
"""
from __future__ import annotations

from .access import attr, safe, text
from .model import NO_NODES_MESSAGE, NodeView

__all__ = ["nodes", "node_views"]

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_NODES_MESSAGE


def node_views(entries, servers, declared=None) -> list[NodeView]:
    """Build the node rows from a ``{name: node-or-None}`` mapping and the server views.

    *declared* is the per-node declared instance-name mapping (``source.declared_instances``): it
    backs the count ONLY for a node that is ``None`` (offline). A LIVE node's count is read from its
    own registry, unchanged by this card.
    """
    declared = declared or {}
    by_node: dict[str, list] = {}
    for server in servers:
        by_node.setdefault(server.node_name, []).append(server)

    out: list[NodeView] = []
    for name, node in (entries or {}).items():
        key = text(name)
        attached = by_node.get(key, [])
        if node is not None:
            instances = safe(lambda: len(attr(node, "instances", {}) or {}), 0)
        else:
            # offline: what the node DECLARES it carries (card B2), never a live node call
            instances = len(tuple(declared.get(key) or ()))
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
    return node_views(getattr(source, "nodes", None), _server_views(source),
                      getattr(source, "declared_instances", None))


def _server_views(source) -> list:
    """The server views for the counters, built through the servers read model.

    Imported inside the function: ``servers.py`` imports this module for nothing, but keeping the
    call local means the two modules can never become an import cycle if either grows a shared
    helper.
    """
    from .servers import servers as server_views
    return safe(lambda: server_views(source), []) or []