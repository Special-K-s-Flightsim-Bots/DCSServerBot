"""Instances: EVERY node's instance registry, and which server occupies one.

The rows are built from ``source.instances``, which ``resolve_source`` fills from every node in the
cluster (the master's ``all_nodes``), so an instance on a remote node renders as well as the
webservice node's own. Each row names its OWN node (``Instance.node.name``): instance names are
unique per NODE, not per cluster, so two nodes may carry the same name and the row must say where.

``Instance.dcs_port`` / ``Instance.webgui_port`` are properties that log warnings and raise
``FatalException`` for an out-of-range port, so they are read through :func:`access.safe`: a
mis-configured instance renders with a blank port, it does not take the dashboard down.

An instance with no server renders as such (``server_name`` is ``None``) — an idle instance is a
normal state here, not an empty row.
"""
from __future__ import annotations

from .access import attr, port_number, safe, text
from .model import NO_INSTANCES_MESSAGE, InstanceView

__all__ = ["instances", "instance_views", "bound_server_name"]

#: the empty-state copy this module owns
EMPTY_MESSAGE = NO_INSTANCES_MESSAGE


def _pairs(entries):
    """Yield ``(name, instance)`` pairs from a ``{name: instance}`` mapping OR a plain sequence.

    ``NodeImpl.instances`` is a dict today, but a caller can hand the read models a list (a test
    stub, or a future node wrapper that exposes a list), and being tolerant here is what keeps the
    template's contract unchanged: it renders records, not the shape they arrived in.
    """
    if entries is None:
        return
    if hasattr(entries, "items"):
        yield from entries.items()
        return
    for instance in entries:
        yield text(attr(instance, "name", None)), instance


def bound_server_name(instance) -> str:
    """The NAME of the server an instance currently holds, or ``''`` when it holds none.

    ONE place: this is the mapping the row renders (``server_name``) and the one the scoped source
    wrapper filters on ("an instance with no server is OMITTED for a scoped viewer", spec §10.3), so
    a row and its scope decision can never disagree about which server an instance belongs to.
    """
    server = attr(instance, "server", None)
    return text(attr(server, "name", None)) if server is not None else ""


def instance_views(entries, node_name: str = "") -> list[InstanceView]:
    """Build the instance rows from the ``{name: instance}`` mapping (or a sequence).

    ``node_name`` is a DEFAULT for rows whose instance carries no ``node`` (a test stub, a future
    wrapper); a LIVE instance always names its own node (``Instance.node.name``), and that wins —
    instance names are unique per NODE, not per cluster, so the page must say which node each row
    belongs to and cannot fall back to the source's own node for a remote one.
    """
    out: list[InstanceView] = []
    for name, instance in _pairs(entries):
        bound = bound_server_name(instance)
        out.append(InstanceView(
            name=text(attr(instance, "name", None)) or text(name),
            node_name=text(attr(attr(instance, "node", None), "name", None)) or text(node_name),
            server_name=bound or None,
            dcs_port=safe(lambda: port_number(attr(instance, "dcs_port", None)), None),
            webgui_port=safe(lambda: port_number(attr(instance, "webgui_port", None)), None),
        ))
    out.sort(key=lambda view: view.name)
    return out


def instances(source) -> list[InstanceView]:
    """The instance rows for *source* (empty when the source is not live).

    The rows span EVERY node in the source (``source.instances`` is cluster-wide, built by
    ``resolve_source``), and each row is attributed to ITS OWN node from the instance itself — never
    to the source's node, which is the webservice's own and would mislabel a remote node's rows.
    """
    return instance_views(getattr(source, "instances", None))