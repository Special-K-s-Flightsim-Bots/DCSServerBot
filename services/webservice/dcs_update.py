"""The console's CACHED DCS update signal — one background check per node, read by the render.

WHY THIS MODULE EXISTS. A node row states the installed DCS branch and version and whether a newer
build exists on that branch. Those facts come from the node's OWN API — ``get_dcs_branch_and_version()``
and ``is_dcs_update_available()`` — and each of them is a local file/HTTP check on the node and an RPC
on a remote one. They therefore must NEVER be issued from a page RENDER: every page view would call
every node, and one slow node would stall the page (``readmodels/nodes.py``: no RPC on render). The
answer is POLLED in the background and CACHED here; the render only ever reads the cache.

WHETHER A NODE HAS DCS AT ALL is a CONFIGURATION fact, never a node call: it is the presence of the
node's ``DCS:`` block in ``nodes.yaml``, read once per process by ``core.utils.validators.NodeData``
and carried to the render as ``readmodels.source.declared_dcs``. ``NodeProxy.installation`` raises on a
remote node, so asking the node is not an option — and must not be one.

THE CALLS ARE THE NODE'S OWN API, never its internals. Two per checked node, in this order:
``get_dcs_branch_and_version()`` for the installed branch/version, then
``is_dcs_update_available()`` for the newer version on that branch (``None`` when there is none). This
module never opens a file, never parses ``nodes.yaml`` itself and never reaches into a node's
attributes. A node whose configuration declares NO DCS block is SKIPPED WITH NO CALL AT ALL and cached
as ``has_dcs=False``, so a master-only node costs nothing.

WHAT IS POLLED, AND WHEN (the poller's four triggers):

* **service start** — the first tick sweeps every node the console knows (``tick`` treats "no sweep
  yet" as due);
* **a node coming online** — a node that was absent or ``None`` in the console's registry and is now a
  live object is a NEWCOMER, and only newcomers are checked on an ordinary tick;
* **every 6 hours** — a full sweep re-checks every node, so a node that stays up still learns about a
  release published after its last check;
* **immediately after a DCS update is accepted** — :func:`check_node` re-checks that ONE node once the
  action has succeeded, so the cache does not wait for the next tick.

THE FAILURE RULE, stated here because it is a choice, not an accident. A check that RAISES means
**UNKNOWN** (``available is None``), never "up to date" and never "update available". Logging is ONCE
PER TRANSITION — the first failure of a node logs a warning, and later failures stay quiet until a
success clears the node's failure latch. A failure KEEPS THE LAST KNOWN VALUE ONLY IF IT WAS ``True``,
so a transient error can never hide a real update (the most common shape here is a node restarting
THROUGH an update: its check blips, and the console must not forget the update that is in flight). A
last known ``False`` is dropped to UNKNOWN on failure, which is the safe direction: an unknown value
says "not checked" rather than claiming the node is up to date.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["SAMPLE_SECONDS", "SWEEP_SECONDS", "DcsCheck", "ABSENT", "UNKNOWN", "DcsChecks", "CHECKS",
           "DcsUpdatePoller", "console_nodes", "console_declared_dcs", "check_node", "entry",
           "available", "checked_at", "has_dcs", "snapshot", "reset", "record", "mark_absent"]

#: how often the transition sampler runs: at 30 s a tick is two dict lookups and, on the ordinary
#: case, NO call at all. Short enough that a node coming back after an update is noticed promptly.
SAMPLE_SECONDS = 30

#: how long a sweep's answer is trusted before EVERY node is re-checked — "every 6 hours": the
#: per-node answer is otherwise only refreshed when the node comes online.
SWEEP_SECONDS = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class DcsCheck:
    """ONE node's cached DCS facts.

    ``has_dcs`` is the CONFIGURATION fact (a ``DCS:`` block exists for this node). ``branch`` /
    ``version`` are the INSTALLED build, as the node last reported them; ``latest`` is the newest
    version on that branch (the newer one when an update exists, the installed one when the node
    answered that there is nothing newer, ``''`` while unknown).

    ``available`` is a THREE-valued fact: ``True`` (a newer build exists), ``False`` (the node
    answered, and it is current) or ``None`` (**UNKNOWN** — never checked, no DCS block, or the last
    check raised).

    ``checked_at`` is the time of the last check that PRODUCED this record (an aware UTC datetime), or
    ``None`` while nothing was produced. A stale answer is then visible as stale instead of looking
    authoritative.
    """

    has_dcs: bool = False
    branch: str = ""
    version: str = ""
    latest: str = ""
    available: bool | None = None
    checked_at: datetime | None = None


#: a node the configuration declares NO DCS block for: it has no installation to describe
ABSENT = DcsCheck()

#: a node that declares a DCS installation whose facts are not known (never checked, or the last
#: check raised)
UNKNOWN = DcsCheck(has_dcs=True)


class DcsChecks:
    """The process's cached DCS answers, keyed by node name — behind one lock.

    PROCESS state, deliberately, exactly like the console's upgrade cache: the poller writes it in the
    service and every render reads it, and a per-request object would be a second copy that drifts. The
    lock keeps a read from seeing a half-written mapping while the poller replaces an entry.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, DcsCheck] = {}
        #: nodes whose LAST check failed — the "log once per transition" latch (the failure rule in
        #: the module docstring). A success discards the name again.
        self._failed: set[str] = set()

    def reset(self) -> None:
        """Drop every cached answer and every failure latch (the tests' seam, and a master/agent
        switch that must not read the previous process's verdicts)."""
        with self._lock:
            self._entries.clear()
            self._failed.clear()

    def snapshot(self) -> dict[str, DcsCheck]:
        """A COPY of the cache, never the live mapping — a reader cannot mutate it."""
        with self._lock:
            return dict(self._entries)

    def entry(self, node_name: str) -> DcsCheck | None:
        """The cached record for *node_name*, or ``None`` when nothing has been checked yet."""
        with self._lock:
            return self._entries.get(str(node_name))

    def available(self, node_name: str) -> bool | None:
        """The cached availability: ``True``, ``False`` or ``None`` (UNKNOWN)."""
        record_ = self.entry(node_name)
        return None if record_ is None else record_.available

    def checked_at(self, node_name: str) -> datetime | None:
        """When *node_name*'s cached record was last produced, or ``None``."""
        record_ = self.entry(node_name)
        return None if record_ is None else record_.checked_at

    def has_dcs(self, node_name: str) -> bool:
        """Whether the cache says *node_name* has a DCS installation (``False`` when unrecorded)."""
        record_ = self.entry(node_name)
        return bool(record_ is not None and record_.has_dcs)

    def record(self, node_name: str, *, has_dcs: bool = True, branch: str = "", version: str = "",
               latest: str = "", available: bool | None = None,
               at: datetime | None = None) -> DcsCheck:
        """Store one answer for *node_name* (the success path's own write, and the tests' seeding).

        ``at`` defaults to now (UTC). A gauge — the poller calls this on a successful check; a test
        calls it to stand in for a check that has already run.
        """
        name = str(node_name).strip()
        stamp = at if at is not None else datetime.now(timezone.utc)
        value = DcsCheck(has_dcs=bool(has_dcs), branch=branch, version=version, latest=latest,
                         available=available, checked_at=stamp)
        with self._lock:
            self._entries[name] = value
        return value

    def mark_absent(self, node_name: str) -> DcsCheck:
        """Record that *node_name* declares NO DCS installation — no call was made, nothing to know.

        The failure latch is cleared: a node that declares no installation is not failing, so a later
        real failure must still be logged.
        """
        name = str(node_name).strip()
        with self._lock:
            self._failed.discard(name)
            self._entries[name] = ABSENT
        return ABSENT

    def _succeed(self, node_name: str, record_: DcsCheck) -> None:
        """A check answered: store the record and clear the node's failure latch."""
        with self._lock:
            self._failed.discard(node_name)
            self._entries[node_name] = record_

    def _fail(self, node_name: str, *, branch: str = "", version: str = "") -> bool | None:
        """A check raised: UNKNOWN, unless the last known value was ``True`` (see the module docstring).

        *branch* / *version* are the installed facts the check DID learn before the failure, if any.
        Returns the availability now cached for the node — ``True`` when a real update was kept,
        otherwise ``None``.
        """
        with self._lock:
            previous = self._entries.get(node_name)
            first = node_name not in self._failed
            self._failed.add(node_name)
        if first:
            # ONCE PER TRANSITION: the first failure of this node is loud, the rest are quiet until a
            # success clears the latch. A status page must not fill the log with one warning a minute
            # about a node that is simply down.
            log.warning("DCS update check for node '%s' failed; treating it as unknown.", node_name)
        if previous is not None and previous.available is True:
            # KEEP THE LAST KNOWN TRUE — a transient failure must not forget an update that may be
            # in flight. ``checked_at`` is deliberately NOT moved: the value is stale, and the row
            # must be able to say so.
            return True
        with self._lock:
            self._entries[node_name] = DcsCheck(has_dcs=True, branch=branch, version=version,
                                                latest="", available=None, checked_at=None)
        return None

    async def check(self, node, *, declared: bool = True) -> bool | None:
        """Run ONE check against *node* and cache the answer.

        *declared* is the CONFIGURATION fact (the node has a ``DCS:`` block). ``False`` means there is
        nothing to ask: the node is recorded as having no installation and **no call is made at all**.

        Returns the availability now cached for the node (``True``/``False``/``None``). A node with no
        readable name is left alone (no entry, so UNKNOWN).
        """
        name = _node_name(node)
        if not name:
            return None
        if not declared:
            self.mark_absent(name)
            return None
        try:
            branch, version = await _branch_and_version(node)
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._fail(name)
        try:
            newest = await _newer_version(node)
        except asyncio.CancelledError:
            raise
        except Exception:
            # the installed facts were read, the availability was not: keep what was learned
            return self._fail(name, branch=branch, version=version)
        available = bool(newest)
        self._succeed(name, DcsCheck(has_dcs=True, branch=branch, version=version,
                                     latest=newest if newest else version,
                                     available=available,
                                     checked_at=datetime.now(timezone.utc)))
        return available


def _node_name(node) -> str:
    """The node's own name, trimmed — ``''`` when there is none to key the cache by."""
    value = getattr(node, "name", "")
    return str(value).strip() if value is not None else ""


def _text(value) -> str:
    """A trimmed string, or ``''`` — never ``None`` and never ``'None'``."""
    return "" if value is None else str(value).strip()


async def _branch_and_version(node) -> tuple[str, str]:
    """The node's installed DCS branch and version — through its own API, one call."""
    method: Any = getattr(node, "get_dcs_branch_and_version")
    branch, version = await method()
    return _text(branch), _text(version)


async def _newer_version(node) -> str:
    """The newer version on the installed branch, or ``''`` when the node reports none.

    ``is_dcs_update_available()`` answers the newer VERSION STRING when there is one and ``None`` when
    the node is current (or could not reach the publisher) — its own contract, unchanged here.
    """
    method: Any = getattr(node, "is_dcs_update_available")
    return _text(await method())


#: THE process-wide cache. The service writes it through :class:`DcsUpdatePoller`; every render reads
#: it, and a test resets it (the suite's autouse fixture, alongside the console's other stores).
CHECKS = DcsChecks()


def reset() -> None:
    CHECKS.reset()


def snapshot() -> dict[str, DcsCheck]:
    return CHECKS.snapshot()


def entry(node_name: str) -> DcsCheck | None:
    return CHECKS.entry(node_name)


def available(node_name: str) -> bool | None:
    return CHECKS.available(node_name)


def checked_at(node_name: str) -> datetime | None:
    return CHECKS.checked_at(node_name)


def has_dcs(node_name: str) -> bool:
    return CHECKS.has_dcs(node_name)


def record(node_name: str, **kwargs) -> DcsCheck:
    return CHECKS.record(node_name, **kwargs)


def mark_absent(node_name: str) -> DcsCheck:
    return CHECKS.mark_absent(node_name)


async def check_node(node) -> bool | None:
    """Check ONE node now — the "immediately after a DCS update is accepted" trigger's entry point.

    Also the seam a page route uses for that trigger, so the route never reaches into the store's
    internals and the poller's own rules (the failure handling above) apply to it unchanged. Whether
    the node declares a DCS installation is answered by the CONFIGURATION (``console_declared_dcs``),
    exactly as on a sweep.
    """
    name = _node_name(node)
    return await CHECKS.check(node, declared=bool(name) and name in console_declared_dcs())


def console_nodes() -> dict:
    """``{name: node-or-None}`` as the console sees the cluster right now — the poller's work list.

    Read through the SAME source the pages render from (:func:`readmodels.console_source`), so the
    poller and the rows can never disagree about which nodes exist. Nothing is awaited: the live
    source reads the process's own registry. Imported inside the function because ``readmodels``
    reaches ``core`` at import time through ``..scope`` and this module stays dependency-light for
    the suite's conftest.
    """
    from . import readmodels

    source = readmodels.console_source()
    nodes = getattr(source, "nodes", None)
    return dict(nodes) if isinstance(nodes, dict) else {}


def console_declared_dcs() -> dict:
    """``{node: DCS block}`` for the nodes that DECLARE a DCS installation — read from the source.

    The same in-process map the render reads (``source.declared_dcs``, built once from the process's
    loaded ``nodes.yaml``): this module never opens or parses the file itself. An empty mapping (a
    process with no readable config) makes every node "declares none", which is the truthful answer
    when the configuration cannot be read.
    """
    from . import readmodels

    source = readmodels.console_source()
    declared = getattr(source, "declared_dcs", None)
    return dict(declared) if isinstance(declared, dict) else {}


class DcsUpdatePoller:
    """The background sampler: decides WHICH nodes a tick checks, and nothing else.

    It owns one piece of state of its own — which nodes it saw ONLINE at the last tick — and the time
    of the last full sweep. Everything else (the answers, the failure latch) lives in :data:`CHECKS`,
    so a test can drive the poller and the render against one shared cache.

    ``tick`` is deliberately pure of I/O scheduling: it takes the node mapping and the DECLARED DCS
    map, works out the targets (newcomers + a due full sweep) and awaits one check per target. The
    service's loop is the only caller that sleeps; a test can call ``tick`` directly with a hand-built
    mapping and no sleeping at all.
    """

    def __init__(self, checks: DcsChecks | None = None, *, sample_seconds: int = SAMPLE_SECONDS,
                 sweep_seconds: int = SWEEP_SECONDS, clock=time.monotonic) -> None:
        self.checks = checks if checks is not None else CHECKS
        self.sample_seconds = sample_seconds
        self.sweep_seconds = sweep_seconds
        self._clock = clock
        self._online: set[str] = set()
        self._last_sweep: float | None = None

    async def tick(self, nodes, declared=None) -> tuple[str, ...]:
        """One round: check every NEWCOMER, and every node when a full sweep is due.

        ``nodes`` is ``{name: node-or-None}`` (the console's own mapping; ``None`` is a node the
        cluster knows and cannot reach). ``declared`` is ``{name: DCS block}`` — the configuration
        map; when it is ``None`` it is read from the console source
        (:func:`console_declared_dcs`).

        Returns the names a check was ISSUED for, so a test can assert the triggers without watching
        the cache. A target whose node declares no DCS block is recorded as ``has_dcs=False`` with NO
        call and is not part of the returned tuple.

        THE FIRST TICK IS A FULL SWEEP (``_last_sweep is None``), which is the "service start"
        trigger; a node that appears afterwards is a newcomer; and ``sweep_seconds`` after the last
        sweep, every node is re-checked. A node that leaves the console's view is simply no longer a
        target — its cached answer is kept, because the row it belongs to is gone with it.
        """
        mapping = dict(nodes or {})
        declarations = console_declared_dcs() if declared is None else dict(declared or {})
        online = {name for name, node in mapping.items() if node is not None}
        newcomers = online - self._online
        self._online = online
        due = self._last_sweep is None or (self._clock() - self._last_sweep) >= self.sweep_seconds
        targets = set(newcomers)
        if due:
            targets |= online
            self._last_sweep = self._clock()
        checked: list[str] = []
        for name in sorted(targets):
            if name not in declarations:
                self.checks.mark_absent(name)
                continue
            await self.checks.check(mapping[name], declared=True)
            checked.append(name)
        return tuple(checked)
