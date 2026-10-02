"""
The maintenance ENGINE, and the one home of the power-off RECORD.

:class:`ServerMaintenanceManager` is the ONE place that knows what a server's ``maintenance`` flag
means to a power operation: which servers are *in service* at all, which were already flagged before
the operation ran (and must never be unflagged by it), which the operation flagged itself (and may
therefore unflag), and how a stop is announced to the players
(:meth:`~ServerMaintenanceManager.shutdown_with_warning` — the popup chain, never a silent
``shutdown()``).

It is an async context manager for the in-process call sites (``nodeimpl.dcs_update`` / ``dcs_repair``
/ ``handle_module``, the four extensions, the monitoring service), whose ``power_off`` and ``power_on``
are also usable ON THEIR OWN — a node power-off is driven as two requests minutes or hours apart
(``/node offline`` then ``/node online``), and ``__aenter__`` / ``__aexit__`` simply delegate to them.
The in-service test and the flag bookkeeping live here, in ONE copy, so callers cannot disagree about
what an operation may touch.

The power-off RECORD is a small module-level registry keyed by node name: the memory the console pair
needs between its two requests — what the operation stopped, what it flagged, and what was already
flagged before it ran. See :class:`NodePowerRecord` for why it is in memory and not persisted.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from core import utils
from core.data.node import Node
from core.data.server import Server
from core.data.const import Status, Coalition
from core.utils.helper import format_time

log = logging.getLogger(__name__)


# ── the power-off record ──────────────────────────────────────────────────────
#
# Deliberately NOT an attribute on ``NodeImpl`` / ``NodeProxy``: both are dataclasses, and an
# undeclared attribute is a landmine for ``__eq__``/``__repr__``, for pickling across the service bus
# and for every transport that carries a node object. A module-level registry keyed by NAME touches no
# object and is visible only through its accessors.
#
# Deliberately NOT persisted: the record is meaningful only while the node is "offline", between the
# two halves of one maintenance window, and a row in a table could be acted on weeks later. The
# consequence, stated plainly: the record is lost on a bot restart, a master failover or a crash
# between the halves. ``/node online`` still works then, but only by the rule — start every server that
# is down and NOT flagged, clear NO flag — and says so in its message.


@dataclass
class NodePowerRecord:
    """What ONE node power-off did, so the matching ``online`` can revert EXACTLY that.

    ``stopped``          server NAMES this operation shut down (started again by ``online``).
    ``flagged``          server NAMES whose ``maintenance`` flag THIS operation set (cleared again
                         by ``online``).
    ``already_flagged``  server NAMES that already carried the flag when the operation ran; the flag
                         is NOT the operation's and is never touched by it — neither set nor cleared.
    """

    node: str
    when: datetime
    actor: str
    stopped: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)
    already_flagged: list[str] = field(default_factory=list)


#: ``{node name: NodePowerRecord}``. Module-level on purpose — see the block comment above.
_power_records: dict[str, NodePowerRecord] = {}

#: When THIS process started. Read by no operation; it is the HONEST FLOOR of the no-record message
#: (``/node online`` after a restart can say *since when* it has no memory — a bare "no record" would
#: read as "the bot never recorded anything", which is not the same claim).
_PROCESS_STARTED_AT = datetime.now()


def process_started_at() -> datetime:
    """When this bot process started — the floor an unrecorded ``online`` names in its message."""
    return _PROCESS_STARTED_AT


def power_record(node_name: str) -> NodePowerRecord | None:
    """The power-off record for *node_name*, or ``None`` when this process holds none.

    ``None`` is the ordinary answer after a bot restart, and the online half has a dedicated,
    documented rule for it — never a silent "nothing to do".
    """
    return _power_records.get(str(node_name))


def record_power_off(node_name: str, *, when: datetime, actor: str,
                     stopped=(), flagged=(), already_flagged=()) -> NodePowerRecord:
    """Create or UNION the power-off record for *node_name*, and return it.

    A SECOND ``offline`` on the same node unions into the record rather than replacing it, so a server
    started by hand between the two halves does not fall out of it. A name is added only to a list that
    does not already carry it and never MOVED between lists: a server this process flagged stays in
    ``flagged`` even when a later pass sees it already flagged, because ``online`` must still know the
    flag is the operation's to clear.
    """
    node_name = str(node_name)
    record = _power_records.get(node_name)
    if record is None:
        record = NodePowerRecord(node=node_name, when=when, actor=actor)
        _power_records[node_name] = record
    else:
        # the newest pass owns the "when"/"actor" of a record that already exists
        record.when = when
        record.actor = actor
    # UNION, list by list, with ONE cross-list rule: a name in ``flagged`` is OURS to clear and must
    # never also sit in ``already_flagged`` (which the online half reads as "not yours"). A name this
    # pass found already flagged does not move a name this process flagged earlier.
    _union(record.stopped, stopped)
    for name in flagged:
        if name not in record.flagged:
            record.flagged.append(name)
        if name in record.already_flagged:
            record.already_flagged.remove(name)
    for name in already_flagged:
        if name in record.flagged or name in record.already_flagged:
            continue
        record.already_flagged.append(name)
    return record


def clear_power_record(node_name: str) -> NodePowerRecord | None:
    """Forget the power-off record for *node_name* and return it (``None`` if there was none).

    Called by ``online`` once it has reverted the record: an operation that has been answered must not
    be answerable a second time.
    """
    return _power_records.pop(str(node_name), None)


def reset_power_records() -> None:
    """Forget EVERY record. For a fresh process and for tests — nothing else calls it."""
    _power_records.clear()


def _union(target: list[str], names) -> None:
    """Append each of *names* to *target* once — never a duplicate, never a re-order."""
    for name in names:
        if name not in target:
            target.append(name)


# ── the engine ────────────────────────────────────────────────────────────────

@dataclass
class PowerOffOutcome:
    """What :meth:`ServerMaintenanceManager.power_off` did, in the servers it was handed.

    ``handled``          every server that was IN SERVICE and therefore part of the operation;
    ``stopped``          the servers it shut down (a subset of ``handled`` when ``stop`` is on);
    ``flagged``          the servers whose flag IT set (never a previously-flagged one);
    ``already_flagged``  the servers that already carried the flag (their flag is untouched);
    ``failed``           servers whose flag could not be written (counted, never fatal).
    """

    handled: list[Server]
    stopped: list[Server]
    flagged: list[Server]
    already_flagged: list[Server]
    failed: list[Server]


@dataclass
class PowerOnOutcome:
    """What :meth:`ServerMaintenanceManager.power_on` did.

    ``cleared``  the servers whose flag it cleared (only ever ones the operation had set);
    ``started``  the servers it (re)started;
    ``skipped``  servers asked for but left alone: still up / booting, or (with ``skip_flagged``) a
                 flag this operation does not own.
    """

    cleared: list[Server]
    started: list[Server]
    skipped: list[Server]


class ServerMaintenanceManager:
    """Take a node's servers out of service (and back) — the ONE implementation of that semantics.

    As a context manager:

        async with ServerMaintenanceManager(node, message="… in {}!"):
            …                       # servers flagged and stopped; flag cleared and start on exit

    As two independent halves (the node power pair, one request each):

        off = await mgr.power_off(servers, stop=True)      # flags + stops, for the record
        on = await mgr.power_on(servers, clear=…, start=…, skip_running=True)

    ``shutdown`` is the context-manager default of the STOP half of ``power_off`` (overridable per
    call with ``stop=``); it is NOT the flag half, which is ``flag=``.
    """

    def __init__(self, node: Node, *, warn_times: list[int] = None, message: str = None,
                 shutdown: bool = True):
        self.node: Node = node
        self.warn_times: list[int] = warn_times or [120, 60, 10]
        self.message: str = message or "Server is going down for maintenance in {}"
        self.shutdown: bool = shutdown
        #: the servers a context-manager power-off HANDLED — the ones ``__aexit__`` brings back
        self.to_start: list[Server] = []
        #: the subset of the above that was ALREADY flagged — its flag is not this operation's
        self.in_maintenance: list[Server] = []

    # -- the in-service test: ONE definition for the whole engine ------------------------------

    @staticmethod
    def in_service(server) -> bool:
        """Whether *server*'s process MAY be up — i.e. whether a power operation may be about it.

        ``Status.SHUTDOWN`` and ``Status.UNREGISTERED`` are both "not in service". ``UNREGISTERED``
        matters even though it is the INITIAL status of a server that has never reported: it is not
        ``SHUTDOWN``, so the old test flagged such a server and called ``shutdown()`` on it. Everything
        else, ``SHUTTING_DOWN`` included, counts as in service — the process is still up.
        """
        return getattr(server, "status", None) not in (Status.SHUTDOWN, Status.UNREGISTERED)

    # -- helpers --------------------------------------------------------------------------------

    def _log(self):
        """The node's own logger when it carries one, else this module's."""
        return getattr(self.node, "log", None) or log

    def node_servers(self) -> list:
        """The servers of THIS node, from its own ``instances`` (the context-manager source).

        Entries may include ``None`` (an instance that carries no server object); the halves skip
        those. The node power pair does NOT read this — an action resolves its servers from the
        caller's own view, so a node the caller cannot see can never be acted on.
        """
        return [instance.server for instance in self.node.instances.values()]

    async def shutdown_with_warning(self, server: Server) -> None:
        """Announce *server*'s shutdown to the players, then shut it down — the popup chain.

        Every warn time in ``self.warn_times`` is sent as an in-game popup to every coalition while
        the countdown runs; a server with no players is shut down immediately. This is the ONE stop
        a power operation performs — a bare ``server.shutdown()`` disconnects the players with no
        notice, which is the second, quieter defect the console's blanket stop had.
        """
        if server.is_populated():
            shutdown_in = max(self.warn_times) if len(self.warn_times) else 0
            while shutdown_in > 0:
                for warn_time in self.warn_times:
                    if warn_time == shutdown_in:
                        await server.sendPopupMessage(Coalition.ALL, self.message.format(format_time(warn_time)))
                await asyncio.sleep(1)
                shutdown_in -= 1
        await server.shutdown()

    # -- the two halves -------------------------------------------------------------------------

    async def power_off(self, servers, *, flag: bool = True, stop: bool | None = None) -> PowerOffOutcome:
        """Take *servers* out of service: flag them (when *flag*) and stop them (when *stop*).

        * ``servers`` — the candidates. A server that is NOT in service (and ``None``) is skipped
          entirely: there is nothing to take out of service.
        * ``flag`` — set ``maintenance`` on the servers that do not already carry it. A server that
          ALREADY carries the flag is recorded in ``already_flagged`` and its flag is NOT touched —
          that is the whole point: a power operation never writes a flag it did not set, so a hand-set
          maintenance survives the cycle. The console's ``maintenance`` option is this flag (default
          on: without it a scheduled start brings the servers back while the node is "offline").
        * ``stop`` — shut the in-service servers down through
          :meth:`~ServerMaintenanceManager.shutdown_with_warning` (the popup chain). Defaults to the
          manager's ``shutdown`` flag, which is what keeps the context-manager call sites' behaviour
          unchanged; the node power pair passes ``stop=True`` (the stop is what that control IS).

        Never raises for one server: a flag write that fails is counted in ``failed`` and the stop is
        still attempted, which is what the per-node report is built from.
        """
        if stop is None:
            stop = self.shutdown
        handled: list[Server] = []
        stopped: list[Server] = []
        flagged: list[Server] = []
        already: list[Server] = []
        failed: list[Server] = []
        tasks = []
        for server in servers:
            if not server:
                continue
            # ALREADY-FLAGGED is collected node-WIDE, not only for in-service servers: the record's
            # ``already_flagged`` tells the online half "this flag is not yours to clear", and a
            # flagged server that was already stopped is exactly such a flag.
            was_flagged = bool(getattr(server, "maintenance", False))
            if was_flagged:
                already.append(server)
            if not self.in_service(server):
                continue
            handled.append(server)
            if flag and not was_flagged:
                if _try_set_maintenance(server, True):
                    flagged.append(server)
                else:
                    failed.append(server)
            if stop:
                stopped.append(server)
                tasks.append(asyncio.create_task(self.shutdown_with_warning(server)))
        if tasks:
            await utils.run_parallel_nofail(*tasks)
        return PowerOffOutcome(handled=handled, stopped=stopped, flagged=flagged,
                               already_flagged=already, failed=failed)

    async def power_on(self, servers, *, clear=(), start=(), skip_running: bool = False,
                       skip_flagged: bool = True, stagger: float | None = None) -> PowerOnOutcome:
        """Bring *servers* back into service: clear the flags THIS operation set, start what it stopped.

        * ``clear`` — the servers whose ``maintenance`` flag this operation set, now cleared. A
          server that is NOT in this collection keeps its flag, whatever it is: a hand-set flag (an
          ``already_flagged`` server) is never cleared by a power cycle.
        * ``start`` — the servers this operation stopped, started again. With ``skip_running`` a
          server that is already up or booting at this moment is NOT started a second time; it is
          returned in ``skipped`` and reported. ``skip_running`` is for the SPLIT operation (whose
          second half may arrive long after an admin started a server by hand); the in-process
          context manager keeps it ``False`` — it has just stopped these servers itself, and for an
          agent node the master may still read ``SHUTTING_DOWN`` over the RPC.
        * ``skip_flagged`` — never start a server that still carries ``maintenance`` (a flag this
          operation does not own keeps its server out of service). On by default, which is the split
          operation's rule. The in-process CONTEXT MANAGER passes ``False`` deliberately: it restarts
          everything it stopped, so a server that was RUNNING and already flagged before an update is
          restored to running afterwards — the pre-operation state every call site relies on.
        * ``stagger`` — when set, the starts are spaced by that many seconds (the node power pair's
          rule, so a whole node does not load DCS at once); the method returns as soon as they are
          SCHEDULED, and the report says "starting", not "started". When ``None`` the starts are
          awaited (the context manager's own behaviour).

        ``start`` is deliberately independent of ``clear``: a server the operation stopped but did
        NOT flag (the console's flag option is off) is started again and no flag is involved.
        """
        cleared: list[Server] = []
        for server in clear:
            if getattr(server, "maintenance", False) and _try_set_maintenance(server, False):
                cleared.append(server)
        skipped: list[Server] = []
        to_start: list[Server] = []
        for server in start:
            if not server:
                continue
            if skip_flagged and getattr(server, "maintenance", False):
                skipped.append(server)
            elif skip_running and self.in_service(server):
                skipped.append(server)
            else:
                to_start.append(server)
        if stagger:
            loop = asyncio.get_running_loop()
            for index, server in enumerate(to_start):
                loop.call_later(index * stagger, self._start_scheduled, server)
        elif to_start:
            results = await asyncio.gather(*(server.startup() for server in to_start),
                                           return_exceptions=True)
            for server, outcome in zip(to_start, results):
                if isinstance(outcome, Exception):
                    self._log().error(
                        f'Timeout while starting {server.display_name}, please check it manually!')
        return PowerOnOutcome(cleared=cleared, started=list(to_start), skipped=skipped)

    def _start_scheduled(self, server: Server) -> None:
        """Start *server* as a task, reporting a failure instead of raising into the loop."""
        asyncio.create_task(self._start_quietly(server))

    async def _start_quietly(self, server: Server) -> None:
        try:
            await server.startup()
        except (TimeoutError, asyncio.TimeoutError):
            self._log().warning(f"Timeout while starting {server.display_name}.")
        except Exception:
            self._log().exception(f"Starting {server.display_name} failed.")

    # -- the context manager delegates to the two halves ----------------------------------------

    async def __aenter__(self):
        outcome = await self.power_off(self.node_servers())
        # the context manager's own memory, unchanged in meaning: what it handled, and which of them
        # were already flagged (their flag is not its to clear in __aexit__)
        self.to_start = list(outcome.handled)
        self.in_maintenance = list(outcome.already_flagged)

    async def __aexit__(self, exc_type, exc_value, traceback):
        clear = [server for server in self.to_start if server not in self.in_maintenance]
        # ``skip_flagged=False``: this half restarts everything it stopped, INCLUDING a server that
        # was already flagged (and running) before the operation — restoring the pre-operation state
        # is the context manager's contract. The split operation's ``online`` does the opposite (see
        # ``power_on``); the difference is deliberate and the only one between the two paths.
        await self.power_on(self.to_start, clear=clear,
                            start=self.to_start if self.shutdown else [],
                            skip_flagged=False)


def _try_set_maintenance(server, maintenance: bool) -> bool:
    """Write ``server.maintenance``, reporting whether it took. Never raises.

    A server whose attribute cannot be written must not abort the other servers of a power operation:
    a node operation is per NODE, so one broken object costs one server and is COUNTED into the
    outcome rather than turning the whole operation into a failure.
    """
    try:
        server.maintenance = maintenance
        return True
    except Exception:
        log.exception("Could not set maintenance=%s on server '%s'", maintenance,
                      getattr(server, "name", "?"))
        return False
