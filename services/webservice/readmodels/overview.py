"""One call that builds everything the dashboard renders.

The page reads this and nothing else, so every figure on it has exactly one source: a property of
:class:`~services.webservice.readmodels.model.Overview`, whose inputs are the pure read models in
this package. That is what makes "no hand-typed figures in the template" checkable — the template
cannot reach a number that is not on this record.

The status/servers/nodes/instances/players/log list is built unconditionally, so a source that is
not live yields empty collections AND their messages rather than a short-circuit that skips a
section (a section left out of the render is indistinguishable from a section whose read failed).
"""
from __future__ import annotations

from .access import attr
from .instances import instances
from .logtail import DEFAULT_LEVEL, DEFAULT_LINES, log_tail
from .model import NO_LIVE_REASON, Overview, SourceStatus
from .nodes import nodes
from .players import players_online
from .servers import servers

__all__ = ["overview"]


def overview(source, *, lines: int = DEFAULT_LINES, level: str = DEFAULT_LEVEL) -> Overview:
    """The dashboard's whole data set, from one (possibly empty) source."""
    status = attr(source, "status", None)
    if not isinstance(status, SourceStatus):
        status = SourceStatus(live=False, reason=NO_LIVE_REASON)
    return Overview(
        status=status,
        nodes=tuple(nodes(source)),
        instances=tuple(instances(source)),
        servers=tuple(servers(source)),
        players=tuple(players_online(source)),
        log=log_tail(source, lines, level),
        scoped_empty=bool(getattr(source, "scoped_empty", False)),
    )