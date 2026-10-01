"""Where the read models get their state: an in-process source, or an honest empty one.

THE RULE THIS MODULE EXISTS FOR. The page must render when there is no bot or no live state at
all (early start, after a master/agent takeover — ``BotService.stop()`` sets ``bot = None``), and
it must never make a Discord call while rendering. So there is exactly one seam:

* :class:`LiveSource` reads objects that are ALREADY in this process — the ``ServiceBus``' server
  registry, every node's instance registry (the node's own plus each ``all_nodes`` entry), and the
  log file on disk. Nothing is awaited, nothing is RPC'd to another node, no Discord API is touched;
* :class:`EmptySource` is what every failure path degrades to, carrying the REASON so the page can
  say "live state is not available yet" instead of showing a zero that reads as a measurement.

Duck-typed on purpose. The source hands the read models plain objects and the models read the
attributes they need with ``getattr``; a test therefore drives the whole package with
``SimpleNamespace`` stand-ins and needs no ``core`` object of its own — which is what makes "the read
models are pure" a checked property (acceptance 4) rather than a claim. Purity here means the INPUTS
are duck-typed and nothing is awaited, NOT that the package is core-free: it reaches ``core`` at
import time through ``..scope`` (see the package docstring).

The log file is the bot's own rotating log (``run.py:85``: ``logs/dcssb-<node>.log``, relative to
the process' working directory, format ``%(asctime)s.%(msecs)03d %(levelname)s\\t%(message)s``).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .access import attr, text
from .instances import bound_server_name
from .model import NO_LIVE_REASON, SourceStatus
from ..scope import server_managed_by

__all__ = ["LOG_DIR", "LOG_NAME_TEMPLATE", "log_path_for", "declared_node_instances", "Source",
           "EmptySource", "LiveSource", "ScopedSource", "scoped_source", "console_source",
           "resolve_source"]

log = logging.getLogger(__name__)

#: where ``run.py`` puts the rotating logs (relative to the bot's working directory)
LOG_DIR = "logs"
LOG_NAME_TEMPLATE = "dcssb-{node}.log"


def log_path_for(node_name: str | None, *, base: str | Path = LOG_DIR) -> Path | None:
    """The bot's log file for *node_name*, or ``None`` when there is no name to build one from."""
    if not node_name:
        return None
    return Path(base) / LOG_NAME_TEMPLATE.format(node=node_name)


def declared_node_instances() -> dict[str, tuple[str, ...]]:
    """The per-node instance names DECLARED in ``nodes.yaml`` for EVERY node in the cluster.

    THE IN-PROCESS ACCESSOR: ``core.utils.validators.get_node_data()`` is a ``NodeData``
    singleton that parses ``nodes.yaml`` ONCE per process (``validators.py:82-98``) and exposes
    ``.instances`` as ``{node_name: [instance_name, ...]}`` (``validators.py:88-92, 111-113``) — the
    same loaded configuration the bot's own validators read, so this is the master's node config and
    NOT a second reading of the file. It costs **no node call** (nothing is RPC'd to any node) and
    **no per-render YAML read** (the file is opened when the singleton is first built, not here).

    Guarded like every optional read in this package: a process with no readable ``nodes.yaml``
    (the test suite, an early start) gets ``{}`` rather than an exception, and a node that declares
    no ``instances:`` block is simply absent — the truthful "declares none" case.

    REFRESH: read ONCE at source build; the singleton is not refreshed, so a ``nodes.yaml`` edit
    while the bot runs is seen on the next bot restart, not on the next render — the same freshness
    the rest of the source has (the ``all_nodes`` NAMES come from the same startup read).
    """
    try:
        from core.utils.validators import get_node_data
        mapping = get_node_data().instances or {}
    except Exception:  # noqa: BLE001 - a missing/unreadable config must not break a status page
        return {}
    return {text(name): tuple(text(instance) for instance in (names or ()))
            for name, names in mapping.items()}



class Source:
    """What the read models need. A duck-typed contract, not a base class.

    Attributes
    ----------
    status:
        :class:`~services.webservice.readmodels.model.SourceStatus` — whether this is live state
        and, when it is not, the sentence to render.
    nodes:
        ``{name: node-or-None}``. ``None`` is a node the cluster knows about but has not resolved
        yet; it renders as offline rather than disappearing.
    declared_instances:
        ``{node name: (instance name, ...)}`` — the instances each node DECLARES in ``nodes.yaml``,
        read once at source build from the process's own loaded node configuration
        (:func:`declared_node_instances`). Used ONLY as the instance COUNT for a node that is
        offline/``None``; it is NOT merged into :attr:`instances`, so an offline node's declared
        instances never become Instances-table rows.
    instances, servers:
        the collections to read (iterables of objects).
    log_path:
        :class:`pathlib.Path` to the bot log, or ``None``.
    now:
        an aware timestamp, injected so a read model can be tested without patching ``datetime``.
    """

    status: SourceStatus
    nodes: dict
    declared_instances: dict
    instances: tuple
    servers: tuple
    log_path: Path | None
    now: datetime


class EmptySource(Source):
    """No live state. Every read model's empty message comes from here."""

    def __init__(self, reason: str = NO_LIVE_REASON, *, node_name: str | None = None,
                 master: bool = False, log_path: Path | None = None):
        self.status = SourceStatus(live=False, reason=reason, node_name=node_name, master=master)
        self.nodes: dict = {}
        self.declared_instances: dict = {}
        self.instances: tuple = ()
        self.servers: tuple = ()
        self.log_path = log_path
        self.now = datetime.now(timezone.utc)


class LiveSource(Source):
    """State read from the running process. No await, no RPC, no Discord."""

    def __init__(self, *, servers=(), instances=(), nodes=None, node=None,
                 declared_instances=None, log_path: Path | None = None):
        node_name = getattr(node, "name", None)
        self.status = SourceStatus(live=True, reason="", node_name=node_name,
                                   master=bool(getattr(node, "master", False)))
        self.nodes = dict(nodes or {})
        self.declared_instances = {text(name): tuple(names or ())
                                   for name, names in dict(declared_instances or {}).items()}
        self.instances = tuple(instances or ())
        self.servers = tuple(servers or ())
        self.log_path = log_path if log_path is not None else log_path_for(node_name)
        self.now = datetime.now(timezone.utc)


# ------------------------------------------------------------------------------ the scope wrap
#
# THE ONE PLACE THE HOSTER VIEW IS APPLIED (spec §10.3). Every page, every standalone list and the
# live stream read their state through ``pages.dashboard.request_source``, which wraps what
# ``resolve_source`` returned in a :class:`ScopedSource`; the read models can therefore only ever see
# the rows that survived the scope, and every derived figure (the pills, the tab badges, the banner,
# the counts, a streamed fragment) follows BY CONSTRUCTION rather than by a second edit at each
# render site. A page that read the source itself would escape the scope silently, which is why the
# seam is pinned by a test (``tests/test_webui_scope_view.py``).

class ScopedSource(Source):
    """A :class:`Source` restricted to the servers ONE request may see (``managed_by``, spec §10.3).

    What is filtered, and why each one:

    * ``servers`` — kept iff :meth:`services.webservice.scope.Scope.allows` the server's
      ``managed_by``. A server that declares none is visible to every caller who may read the
      console (spec §10.1), which is what :func:`holds_scope` already means;
    * ``instances`` — kept iff BOUND TO A SURVIVING SERVER. An idle instance (one holding no server)
      is cluster infrastructure, not "their server", and its ports are not part of the hoster view:
      it is OMITTED for a scoped viewer;
    * ``nodes`` — kept iff it CARRIES a surviving server. That is the decision the spec records for
      the one case a scoped user shares a node with others: a node their servers run on is in their
      view even when it is offline (its downtime is their problem), while a node carrying none of
      their servers is not theirs to see (an offline node they do not manage is not their problem).
      The read model derives a node's server/player/instance counters from the server views it is
      handed, so the counters are scoped with no change to that model;
    * ``log_path`` / ``status`` — NOT scoped: the bot log is Admin-only and a scoped viewer is never
      an Admin (spec §9.3), and the status describes the deployment the viewer is signed in to. The
      status stays LIVE, so the page still says live state is available; the "empty because of the
      scope" case carries its own sentence instead (``scoped_empty``).

    ``scoped_empty`` is True when the scope is not unrestricted AND no server survived: the view is
    empty BECAUSE OF THE SCOPE, and the page must say so in its own words rather than claim that no
    servers are registered (or that live state is missing). It is computed HERE, where the fact is
    known, and read by the empty-state copy.
    """

    def __init__(self, source, scope):
        self._source = source
        self.scope = scope
        self.servers = tuple(server for server in (getattr(source, "servers", ()) or ())
                             if scope.allows(server_managed_by(server)))
        served = {text(attr(server, "name", None)) for server in self.servers}
        served.discard("")
        self.instances = tuple(instance for instance in (getattr(source, "instances", ()) or ())
                               if bound_server_name(instance) in served)
        node_names = {text(attr(attr(server, "node", None), "name", None))
                      for server in self.servers}
        node_names.discard("")
        self.nodes = {name: node for name, node in (getattr(source, "nodes", None) or {}).items()
                      if text(name) in node_names}
        # The declared per-node instance names follow the SAME node filter as ``nodes``:
        # a scoped viewer sees a node only when it carries one of their surviving servers, so a
        # node's declared count must not survive for a node whose row did not.
        self.declared_instances = {
            name: names
            for name, names in (getattr(source, "declared_instances", None) or {}).items()
            if text(name) in node_names}
        self.status = getattr(source, "status", None)
        self.log_path = getattr(source, "log_path", None)
        self.now = getattr(source, "now", None)
        self.scoped_empty = (not getattr(scope, "unrestricted", False)
                             and not self.servers
                             and bool(getattr(self.status, "live", False)))

    def __repr__(self) -> str:  # pragma: no cover - a debugging aid
        return (f"ScopedSource(servers={len(self.servers)}, scope={self.scope!r}, "
                f"scoped_empty={self.scoped_empty})")


def scoped_source(source, scope) -> Source:
    """*source* restricted to *scope* — the ONE wrapper the request source is built with.

    An UNRESTRICTED scope (``Admin``, break-glass, a local account that declares none) returns the
    source UNCHANGED, and that is deliberate on two counts: there is nothing to filter, and an idle
    instance is part of the Admin's view (the spec omits it for a *scoped* viewer only).
    """
    if getattr(scope, "unrestricted", False):
        return source
    return ScopedSource(source, scope)


def console_source(state=None) -> Source:
    """The source the console reads RIGHT NOW — THE one place that decides where data comes from.

    ``state`` is the application state, and the seam it may carry
    (``app.state.webui_source_provider``) is what lets a test — or an installation that wants the
    console pinned to a specific node — hand the pages a source without a running bot. With no
    provider it is :func:`resolve_source`, which itself degrades to an ``EmptySource``.

    It lives HERE, between the pages and the identity layer, because two different questions are
    asked of the same object and they must be asked of the SAME one: the pages read it (through
    ``pages.dashboard.request_source``, which then applies the per-request scope) and the identity
    layer reads its ``servers`` to answer whether an identity is a MANAGER of one (the console's
    access rule). Two spellings of "where the console's data comes from" would be exactly the kind
    of drift this package is built to make impossible — the seam is resolved once, here.
    """
    provider = getattr(state, "webui_source_provider", None)
    return provider() if callable(provider) else resolve_source()


def resolve_source(node=None, *, bot=None) -> Source:
    """The source for the process right now. Never raises, never returns ``None``.

    Tried in order:

    1. the ``ServiceBus`` (the in-process registry the master owns) for the servers, and its
       ``node`` for the node/instance registries — this is the live path and it works even while
       ``BotService.bot`` is ``None`` (early start, after a takeover);
    2. ``bot.servers`` when a bot object was handed in (the same mapping, via the bot);
    3. :class:`EmptySource` with the reason, for every failure — including the import of the
       registry failing, which is what a process with no ``ServiceBus`` yet looks like.

    The ``core`` import of the LIVE registry lives INSIDE the function on purpose: it is only
    meaningful once the process has a registry, and a module-level import would bind it before the
    bot is up (and would make the ``EmptySource`` fallback unreachable). It is NOT what keeps this
    module dependency-light — the module reaches ``core`` at import time through ``..scope`` (see
    the package docstring).
    """
    problem: str | None = None
    bus = None
    try:
        from core import ServiceRegistry  # noqa: F401  (import is the point)
        from services.servicebus import ServiceBus
        bus = ServiceRegistry.get(ServiceBus)
    except Exception as ex:  # ImportError, or the service registry being empty early on
        problem = str(ex) or ex.__class__.__name__
        log.debug("Web UI read models: no ServiceBus available (%s).", problem)

    servers: tuple = ()
    if bus is not None:
        servers = tuple((getattr(bus, "servers", None) or {}).values())
    elif bot is not None:
        servers = tuple((getattr(bot, "servers", None) or {}).values())

    if bus is None and bot is None:
        return EmptySource(NO_LIVE_REASON)
    if bus is not None and node is None:
        node = getattr(bus, "node", None)
    if node is None and bot is not None:
        node = getattr(bot, "node", None)

    if node is None:
        # servers but no node registry: still useful (the servers carry their own node name)
        return LiveSource(servers=servers, nodes={}, log_path=None)

    nodes = dict(getattr(node, "all_nodes", None) or {})
    nodes.setdefault(getattr(node, "name", ""), node)
    # Every node the cluster knows about, not just the one this webservice runs on: a remote node's
    # registry is already cached here (``NodeProxy.instances``, populated from the master's
    # ``nodes.yaml`` and each remote server's init), so reading all of them needs no RPC. An entry
    # that is ``None`` is a node the cluster knows about but has not resolved — skipped, so it
    # neither raises nor contributes rows (its ``NodeView`` still renders offline in the nodes list).
    instances = tuple(
        instance
        for member in nodes.values()
        if member is not None
        for instance in (getattr(member, "instances", None) or {}).values()
    )
    # The instances each node DECLARES in ``nodes.yaml``, read ONCE here (source build) so
    # an offline node's row can report the count it really has without a node call and without a
    # per-render YAML read. It is kept BESIDE ``instances``, never merged into it: a declared
    # instance of an offline node becomes a count, not an Instances-table row.
    declared = declared_node_instances()
    return LiveSource(servers=servers, instances=instances, nodes=nodes, node=node,
                      declared_instances=declared)