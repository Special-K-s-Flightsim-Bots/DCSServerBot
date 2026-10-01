"""The console's CACHED upgrade signal — one background check per node, read by the render.

WHY THIS MODULE EXISTS. The Upgrade control on a node row is offered ONLY while the
node's OWN API says an update is pending. That answer comes from ``Node.upgrade_pending()``
(``core/data/node.py``), which is an async git/HTTP check on the local node and an RPC on a remote
one — so it must never be issued from a page RENDER: every page view would call every node, and one
slow node would stall the page (``readmodels/nodes.py``: no RPC on render). The answer is therefore
POLLED in the background and CACHED here; the render only ever reads the cache.

THE CALL IS THE NODE'S OWN API, never its internals. This module calls ``upgrade_pending()`` and
nothing else: it never reaches ``_upgrade_pending_git`` / ``_upgrade_pending_non_git`` and never
issues a git/HTTP request of its own — whether an update is pending is the node's business, and the
whole point of ``upgrade_pending()`` is that it answers that question in ONE call, locally
(``NodeImpl``) or remotely (``NodeProxy``, ``{"method": "upgrade_pending"}``).

WHAT IS POLLED, AND WHEN (the poller's four triggers):

* **service start** — the first tick sweeps every node the console knows (``UpgradePoller.tick``
  treats "no sweep yet" as due);
* **a node coming online** — a node that was absent or ``None`` in the console's registry and is
  now a live object is a NEWCOMER, and only newcomers are checked on an ordinary tick;
* **every 6 hours** — a full sweep re-checks every node, so a node that stays up still learns about
  a release published after its last check;
* **immediately after an Upgrade is accepted** — the node route re-checks that ONE node once the
  action has succeeded (``pages/actions``), so the cache does not wait for the next tick.

THE FAILURE RULE (item 4 of the card), stated here because it is a choice, not an accident. A check
that RAISES means **UNKNOWN**, never "pending": the control is then not offered. Logging is ONCE PER
TRANSITION — the first failure of a node logs a warning, and later failures stay quiet until a
success clears the node's failure latch. A failure KEEPS THE LAST KNOWN VALUE ONLY IF IT WAS True,
so a transient error can never hide a real pending upgrade (the most common shape here is a node
restarting THROUGH an upgrade: its check blips, and the console must not withdraw the very control
that is in flight). A last known ``False`` is dropped to UNKNOWN on failure, which is the safe
direction: an unknown value withholds the control rather than offering it wrongly.
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

__all__ = ["SAMPLE_SECONDS", "SWEEP_SECONDS", "NOTE_NO_CHECK", "Check", "UpgradeChecks", "CHECKS",
           "UpgradePoller", "console_nodes", "check_node", "pending", "checked_at", "entry",
           "snapshot", "reset", "record"]

#: how often the transition sampler runs. Short enough that a node coming back after an upgrade is
#: noticed promptly (the vanish path, item 3), long enough that it is not a polling flood: at 30 s
#: a tick is two dict lookups and, on the ordinary case, NO call at all.
SAMPLE_SECONDS = 30

#: how long a sweep's answer is trusted before EVERY node is re-checked. This is the card's
#: "every 6 hours": the per-node answer is otherwise only refreshed when the node comes online.
SWEEP_SECONDS = 6 * 60 * 60

#: the honest statement a row carries while the cached value is UNKNOWN (never checked, or the last
#: check failed). The card's own words.
NOTE_NO_CHECK = "no update check yet"


@dataclass(frozen=True, slots=True)
class Check:
    """ONE cached answer: whether an upgrade is pending, and when it was last checked.

    ``pending`` is a THREE-valued fact: ``True`` (an update is available), ``False`` (the node
    answered, and there is nothing to upgrade) or ``None`` (**UNKNOWN** — never checked, or the last
    check raised). The render treats ``True`` as "offer the control", everything else as "do not".

    ``checked_at`` is the time of the last check that PRODUCED this value (an aware UTC datetime),
    or ``None`` while the value is unknown. The dialog shows it, so a stale ``True`` is visible as
    stale rather than looking authoritative.
    """

    pending: bool | None
    checked_at: datetime | None


UNKNOWN = Check(pending=None, checked_at=None)


class UpgradeChecks:
    """The process's cached upgrade answers, keyed by node name — behind one lock.

    PROCESS state, deliberately, exactly like the console's other process stores (the seam's
    in-flight set, ``pages/actions``' expectation store): the poller writes it in the service and
    every render reads it, and a per-request object would be a second copy that drifts. The lock
    keeps a read from seeing a half-written mapping while the poller replaces an entry.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Check] = {}
        #: nodes whose LAST check failed — the "log once per transition" latch (the failure rule in
        #: the module docstring). A success discards the name again.
        self._failed: set[str] = set()

    def reset(self) -> None:
        """Drop every cached answer and every failure latch (the tests' seam, and a master/agent
        switch that must not read the previous process's verdicts)."""
        with self._lock:
            self._entries.clear()
            self._failed.clear()

    def snapshot(self) -> dict[str, Check]:
        """A COPY of the cache, never the live mapping — a reader cannot mutate it."""
        with self._lock:
            return dict(self._entries)

    def entry(self, node_name: str) -> Check | None:
        """The cached record for *node_name*, or ``None`` when nothing has been checked yet."""
        with self._lock:
            return self._entries.get(str(node_name))

    def pending(self, node_name: str) -> bool | None:
        """The cached pending value: ``True``, ``False`` or ``None`` (UNKNOWN)."""
        record_ = self.entry(node_name)
        return None if record_ is None else record_.pending

    def checked_at(self, node_name: str) -> datetime | None:
        """When *node_name*'s cached value was last produced, or ``None``."""
        record_ = self.entry(node_name)
        return None if record_ is None else record_.checked_at

    def record(self, node_name: str, pending: bool | None, *,
               at: datetime | None = None) -> Check:
        """Store one answer for *node_name* (the success path's own write, and the tests' seeding).

        ``at`` defaults to now (UTC). A gauge — the poller calls this on a successful check; the
        tests call it to stand in for a check that has already run.
        """
        name = str(node_name).strip()
        stamp = at if at is not None else datetime.now(timezone.utc)
        value = Check(pending=pending, checked_at=stamp)
        with self._lock:
            self._entries[name] = value
        return value

    def _succeed(self, node_name: str, pending: bool) -> None:
        """A check answered: store the value and clear the node's failure latch."""
        with self._lock:
            self._failed.discard(node_name)
        self.record(node_name, bool(pending))

    def _fail(self, node_name: str) -> bool | None:
        """A check raised: UNKNOWN, unless the last known value was True (see the module docstring).

        Returns the value now cached for the node — ``True`` when a real pending upgrade was kept,
        otherwise ``None``.
        """
        with self._lock:
            previous = self._entries.get(node_name)
            first = node_name not in self._failed
            self._failed.add(node_name)
        if first:
            # ONCE PER TRANSITION: the first failure of this node is loud, the rest are quiet until
            # a success clears the latch. A status page must not fill the log with one warning a
            # minute about a node that is simply down.
            log.warning("Upgrade check for node '%s' failed; treating it as unknown.", node_name)
        if previous is not None and previous.pending is True:
            # KEEP THE LAST KNOWN TRUE — a transient failure must not withdraw the Upgrade control
            # while an upgrade it is about may be in flight. ``checked_at`` is deliberately NOT
            # moved: the value is stale, and the dialog must be able to say so.
            return True
        with self._lock:
            self._entries[node_name] = UNKNOWN
        return None

    async def check(self, node) -> bool | None:
        """Run ONE ``upgrade_pending()`` call against *node* and cache the answer.

        Returns the pending value now cached for the node (``True``/``False``/``None``). A node
        with no readable name or no ``upgrade_pending`` method is left alone (no entry, so UNKNOWN):
        the render then withholds the control rather than offering one the action would refuse.
        """
        name = _node_name(node)
        if not name:
            return None
        checker: Any = getattr(node, "upgrade_pending", None)
        if not callable(checker):
            return None
        try:
            pending = bool(await checker())
        except asyncio.CancelledError:
            raise
        except Exception:
            return self._fail(name)
        self._succeed(name, pending)
        return pending


def _node_name(node) -> str:
    """The node's own name, trimmed — ``''`` when there is none to key the cache by."""
    value = getattr(node, "name", "")
    return str(value).strip() if value is not None else ""


#: THE process-wide cache. The service writes it through :class:`UpgradePoller`; every render reads
#: it, and a test resets it (the suite's autouse fixture, alongside the console's other stores).
CHECKS = UpgradeChecks()


def reset() -> None:
    CHECKS.reset()


def snapshot() -> dict[str, Check]:
    return CHECKS.snapshot()


def entry(node_name: str) -> Check | None:
    return CHECKS.entry(node_name)


def pending(node_name: str) -> bool | None:
    return CHECKS.pending(node_name)


def checked_at(node_name: str) -> datetime | None:
    return CHECKS.checked_at(node_name)


def record(node_name: str, pending: bool | None, *, at: datetime | None = None) -> Check:
    return CHECKS.record(node_name, pending, at=at)


async def check_node(node) -> bool | None:
    """Check ONE node now — the "immediately after an Upgrade is accepted" trigger's entry point.

    Also the seam a page route uses for that trigger, so the route never reaches into the store's
    internals and the poller's own rules (the failure handling above) apply to it unchanged.
    """
    return await CHECKS.check(node)


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


class UpgradePoller:
    """The background sampler: decides WHICH nodes a tick checks, and nothing else.

    It owns one piece of state of its own — which nodes it saw ONLINE at the last tick — and the
    time of the last full sweep. Everything else (the answers, the failure latch) lives in
    :data:`CHECKS`, so a test can drive the poller and the render against one shared cache.

    ``tick`` is deliberately pure of I/O scheduling: it takes the node mapping, works out the
    targets (newcomers + a due full sweep) and awaits one check per target. The service's loop is
    the only caller that sleeps; a test can call ``tick`` directly with a hand-built mapping and no
    sleeping at all.
    """

    def __init__(self, checks: UpgradeChecks | None = None, *, sample_seconds: int = SAMPLE_SECONDS,
                 sweep_seconds: int = SWEEP_SECONDS, clock=time.monotonic) -> None:
        self.checks = checks if checks is not None else CHECKS
        self.sample_seconds = sample_seconds
        self.sweep_seconds = sweep_seconds
        self._clock = clock
        self._online: set[str] = set()
        self._last_sweep: float | None = None

    async def tick(self, nodes) -> tuple[str, ...]:
        """One round: check every NEWCOMER, and every node when a full sweep is due.

        ``nodes`` is ``{name: node-or-None}`` (the console's own mapping; ``None`` is a node the
        cluster knows and cannot reach). Returns the names actually checked, so a test can assert
        the triggers without watching the cache.

        THE FIRST TICK IS A FULL SWEEP (``_last_sweep is None``), which is the "service start"
        trigger; a node that appears afterwards is a newcomer; and ``sweep_seconds`` after the last
        sweep, every node is re-checked. A node that leaves the console's view is simply no longer a
        target — its cached answer is kept, because the row it belongs to is gone with it.
        """
        mapping = dict(nodes or {})
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
            await self.checks.check(mapping[name])
            checked.append(name)
        return tuple(checked)
