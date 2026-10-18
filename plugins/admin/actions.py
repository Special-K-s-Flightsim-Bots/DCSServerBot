"""
Action functions for NODE operations.

TWO FAMILIES:

* the NODE LIFECYCLE trio — **restart**, **shut down** and **upgrade** — acts on the node's own
  PROCESS (``Node.restart()`` / ``shutdown()`` / ``upgrade()``); every server on the node goes down
  with it, and an "offline" node is reached by the machine, never by a browser;
* the POWER pair — **offline** and **online** — acts on the SERVERS the node carries and touches no
  service at all. ``offline`` takes the in-service servers out of service: it shuts them down (through
  the engine's popup chain) and, unless the caller opts out, marks them ``maintenance``. ``online``
  reverts EXACTLY what ``offline`` did — clearing only the flags that operation set and starting only
  the servers it stopped. The node's services, including the webservice serving the console's request,
  keep running, which is why these two CAN be called from a browser;
* the SERVER CREATION pair — **add** and **delete** — acts on ONE instance and the server it carries,
  through the node's own instance operations (``Node.add_instance`` / ``Node.delete_instance``), so a
  running install can create and remove a server without a restart. Everything these two write —
  the ``nodes.yaml`` block, ``servers.yaml``, the database rows — is written by the bot method they
  call (``plugins/admin/commands.py``'s ``/node add_instance`` and ``/node delete_instance``).

The SEMANTICS do not live here: they are the engine's (``core/data/maintenance.py``,
:class:`~core.data.maintenance.ServerMaintenanceManager`), as is the record of what an ``offline`` did
(``NodePowerRecord``). This module resolves the target, drives the two halves and words the report.

Each operation lives here in the SAME shape as every other console write (``plugins/mission/actions.py``
is the model): a ``@action`` function takes typed parameters plus an
:class:`~core.actions.ActionContext`, resolves its target through the seam, performs the operation and
returns a typed result. The transport wraps the result; the console renders ``result.message`` in its
one-shot notice.

Each one mirrors an existing command so a word never means two things:

* :func:`restart_node` / :func:`shutdown_node` / :func:`upgrade_node` -> the ``Node`` method
  ``/node restart`` / ``/node shutdown`` / ``/node upgrade`` (``plugins/admin/commands.py``) reaches:
  ``NodeImpl.restart()`` is ``shutdown(RESTART)`` (the launcher brings it back), ``shutdown()`` does
  not come back on its own, and ``upgrade()`` checks for an update and shuts down with ``rc=UPDATE``;
* :func:`take_node_offline` / :func:`bring_node_online` -> ``/node offline`` / ``/node online`` (the
  power pair above).

THE TRAIL is written HERE, once, whatever the transport, including for a refusal. The audit's actor is
the caller's: ``[web:backend/subject]`` for the console, a display name for Discord (``core/actions``).

NO RPC ON THE RENDER PATH: the console cannot know whether an upgrade is pending while rendering and
must not ask (``Node.upgrade_pending()`` is an async git/HTTP check on the local node and an RPC on a
remote one), so the Upgrade control is OFFERED unconditionally. The check runs when the ACTION runs and
its verdict is the typed refusal this module returns verbatim.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime
from typing import Any

from core.action_results import NodeControlResult
from core.actions import audit_action, action
from core.data.maintenance import (PowerOffOutcome, ServerMaintenanceManager, clear_power_record,
                                   power_record, process_started_at, record_power_off)

log = logging.getLogger(__name__)

__all__ = ["NODE_OPERATIONS", "NODE_MAINTENANCE_OPERATIONS",
           "restart_node", "shutdown_node", "upgrade_node", "take_node_offline",
           "bring_node_online", "add_server", "delete_server"]

#: method name -> the two wordings ONE node operation needs: how it reads in a success sentence and
#: how it reads in a failure sentence. Declared here rather than inline so the three operations cannot
#: drift into three sentences.
#:
#: The UPGRADE's own refusal ("There is no upgrade available for node 'X'.") is not here: it mirrors
#: ``/node upgrade`` (``plugins/admin/commands.py``) word for word and is produced by the pre-check in
#: :func:`_lifecycle`, next to the code that runs it.
NODE_OPERATIONS: tuple[str, ...] = ("restart", "shutdown", "upgrade")

#: the POWER pair's own names, in the words the Discord arms use (``/node offline`` /
#: ``/node online``). Kept next to :data:`NODE_OPERATIONS` so the two families are one list each.
NODE_MAINTENANCE_OPERATIONS: tuple[str, ...] = ("offline", "online")


_VERBS: dict[str, tuple[str, str]] = {
    "restart": ("restarted", "restart"),
    "shutdown": ("shut down", "shut down"),
    "upgrade": ("upgraded", "upgrade"),
}


async def _lifecycle(ctx: Any, node_name: str, method: str) -> NodeControlResult:
    """The shared body of the three node operations — ONE implementation, so they refuse alike.

    ``method`` is the ``Node`` method name (``restart`` / ``shutdown`` / ``upgrade``). The lookup
    goes through :meth:`core.actions.ActionContext.resolve_node`, i.e. the caller's own view of the
    cluster, so a node the caller cannot see is simply not found — there is no lookup that begins
    anywhere else. ``upgrade`` additionally runs ``upgrade_pending()`` first, the check ``/node
    upgrade`` makes; the other two have no precondition.
    """
    done, verb = _VERBS[method]
    node = ctx.resolve_node(node_name)
    if node is None:
        # ONE refusal for both "no such name" and "the cluster knows it and cannot reach it": the
        # node page already renders the heartbeat verdict, so the two are not a secret from anybody.
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Node '{node_name}' is offline or unknown.", node_name=node_name))

    call = getattr(node, method, None)
    if not callable(call):
        # a transport whose node object does not expose the method (an older agent, a double): a
        # typed refusal, never an AttributeError and never a fake success.
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Node '{node_name}' cannot be {verb}ed in this installation.",
            node_name=node_name))

    if method == "upgrade":
        # THE UPGRADE'S OWN GUARD, mirroring ``/node upgrade`` (``plugins/admin/commands.py``), which
        # checks ``upgrade_pending()`` first and refuses with "There is no upgrade available for node
        # X". Without it ``Node.upgrade()`` would be called with nothing to do: it logs "No update
        # found" and returns, so the console would report a success that never happened.
        #
        # This check is NOT the render path's signal — a page must not call it (``readmodels/nodes``),
        # which is why Upgrade is still OFFERED unconditionally. It runs when the action runs, which is
        # exactly where ``/node upgrade`` runs it too.
        checker: Any = getattr(node, "upgrade_pending", None)
        if callable(checker):
            try:
                pending = bool(await checker())
            except Exception as ex:
                log.exception("upgrade_node: the update check failed for node '%s'", node_name)
                return await _audited(ctx, NodeControlResult(
                    success=False,
                    message=f"Could not check node '{node_name}' for an upgrade: {ex}",
                    node_name=node_name))
            if not pending:
                return await _audited(ctx, NodeControlResult(
                    success=False,
                    message=f"There is no upgrade available for node '{node_name}'.",
                    node_name=node_name))
        # A MAJOR upgrade moves the bot's version, and a DCS server only talks to a bot whose hook
        # version it carries: DCS has to be down on that node while it updates, or the servers come
        # back speaking the old hook. Refused HERE, where every transport lands, so the console cannot
        # skip it; ``/node upgrade`` asks its own question and takes the node offline first, which is
        # why it calls this action with nothing left in service. A node object that cannot answer —
        # an agent older than this check, caught mid-upgrade — skips the guard rather than blocking
        # every upgrade in the cluster.
        major: Any = getattr(node, "upgrade_is_major", None)
        if callable(major):
            try:
                is_major = bool(await major())
            except Exception as ex:
                log.exception("upgrade_node: the major-version check failed for node '%s'", node_name)
                return await _audited(ctx, NodeControlResult(
                    success=False,
                    message=f"Could not check node '{node_name}' for an upgrade: {ex}",
                    node_name=node_name))
            if is_major:
                canonical = str(getattr(node, "name", "") or node_name)
                running = [server for server in _node_servers(ctx, canonical)
                           if ServerMaintenanceManager.in_service(server)]
                if running:
                    return await _audited(ctx, NodeControlResult(
                        success=False,
                        message=(f"Node '{canonical}': {len(running)} server(s) are in service and "
                                 f"this is a major upgrade — DCS has to be down while the bot "
                                 f"updates. Take the node offline first."),
                        node_name=canonical))

    try:
        await call()
        result = NodeControlResult(success=True, message=f"Node '{node_name}' {done}.",
                                   node_name=node_name)
    except (TimeoutError, asyncio.TimeoutError):
        result = NodeControlResult(
            success=False, message=f"Timeout while trying to {verb} node '{node_name}'.",
            node_name=node_name)
    except Exception as ex:
        # Every operation's check is done ABOVE; by here a raise is a real failure (the RPC dropped,
        # the node rejected the call), so it says so rather than inventing a refusal.
        log.exception("%s_node failed", method)
        result = NodeControlResult(success=False,
                                   message=f"Failed to {verb} node: {ex}", node_name=node_name)
    return await _audited(ctx, result)


async def _audited(ctx: Any, result: NodeControlResult) -> NodeControlResult:
    """Write the trail for *result* and return it.

    ONE place, so every exit of every node operation — the success, each typed refusal (offline/unknown,
    nothing to upgrade, the check itself failing) and the failure — leaves exactly one entry. The entry
    names the node in its own message; the audit row carries no ``server`` and the target is spelled,
    which makes a node event greppable in the trail.
    """
    await audit_action(ctx, result)
    return result


@action
async def restart_node(ctx: Any, node_name: str) -> NodeControlResult:
    """Restart the bot process on ONE node — the same operation ``/node restart`` performs.

    ``NodeImpl.restart()`` is ``shutdown(RESTART)``: the node's process ends and the launcher on that
    machine brings it back. Every server on the node goes down with it and does not come back on its
    own, which is why the console's control confirms first.
    """
    return await _lifecycle(ctx, node_name, "restart")


@action
async def shutdown_node(ctx: Any, node_name: str) -> NodeControlResult:
    """Shut the bot process on ONE node down — the same operation ``/node shutdown`` performs.

    Nothing brings the node back: starting it again is an OS-level job on that machine (the
    launcher, a service, a terminal), and no browser can do it. The console's copy says so.
    """
    return await _lifecycle(ctx, node_name, "shutdown")


@action
async def upgrade_node(ctx: Any, node_name: str) -> NodeControlResult:
    """Update DCSServerBot on ONE node and restart it — the same operation ``/node upgrade`` performs.

    ``NodeImpl.upgrade()`` checks for an update itself, sets the cluster's ``update_pending`` flag,
    launches ``update.py`` and shuts the node down with ``rc=UPDATE`` — but a node with NOTHING to
    upgrade logs "No update found" and returns, reporting no success. So this action runs the SAME
    ``upgrade_pending()`` check ``/node upgrade`` runs (``plugins/admin/commands.py``:965) and refuses
    with the same sentence when it is false, rather than reporting an upgrade that never happened.

    A MAJOR upgrade is refused while any of the node's servers is in service: a DCS server only talks
    to a bot whose hook version it carries, so DCS has to be down while the bot updates. ``/node
    upgrade`` asks its own question and takes the node offline first; the console gets the refusal,
    which is what tells an operator to do the same.

    The console still OFFERS Upgrade unconditionally: that check is an async git/HTTP call on the node
    (an RPC for a remote one), and a page render must not issue one (``readmodels/nodes``).
    """
    return await _lifecycle(ctx, node_name, "upgrade")


# ── the NODE POWER pair ────────────────────────────────────────────────────
# ``/node offline`` / ``/node online`` act on the SERVERS a node carries and never on a service.
# ``offline`` shuts the in-service servers down (through the engine's popup chain) and flags the ones
# that were not already flagged; ``online`` reverts exactly the record that operation left. Nothing
# stops a node service, so the webservice serving the console's request keeps running — which is what
# makes this pair callable from a browser at all. The flag bookkeeping and the in-service test live in
# the engine (``core/data/maintenance.py``); this module carries no copy of them.


def _node_servers(ctx: Any, node_name: str) -> list:
    """Every server the CALLER's view puts on *node_name*, in a stable (name) order.

    Read from ``ctx.servers`` — the caller's own view, the mapping every action resolves against — so
    a node operation can never touch a server the caller could not see. Sorted by NAME so the
    staggered start's order (and therefore a test's assertion) is deterministic rather than a
    dict-insertion artefact.
    """
    found = [server for server in (getattr(ctx, "servers", None) or {}).values()
             if str(getattr(getattr(server, "node", None), "name", "") or "") == node_name]
    found.sort(key=lambda server: str(getattr(server, "name", "")))
    return found


def _server_names(servers) -> list[str]:
    """The names of *servers*, in order, skipping an object with no readable name."""
    return [str(server.name) for server in servers if getattr(server, "name", "")]


def _resolve_recorded(servers: list, names) -> tuple[list, list[str]]:
    """``(servers, missing names)`` for the recorded *names* among the node's *servers*.

    The record holds NAMES; the online half resolves them against the caller's current view. A name
    that no longer resolves — renamed, migrated or removed from the config — is returned in the second
    element and REPORTED, never raised. Matching folds case, like every other name in this seam.
    """
    index = {}
    for server in servers:
        name = str(getattr(server, "name", "") or "")
        if name:
            index[name.casefold()] = server
    found: list = []
    missing: list[str] = []
    for name in names:
        server = index.get(str(name).casefold())
        if server is not None:
            found.append(server)
        else:
            missing.append(name)
    return found, missing


async def _power_off(ctx: Any, node_name: str, *, maintenance: bool) -> NodeControlResult:
    """Take the in-service SERVERS of ONE node out of service — ``offline``'s one implementation.

    The semantics are the engine's (:class:`~core.data.maintenance.ServerMaintenanceManager`): a
    server that is not *in service* is skipped, a server that already carries the maintenance flag is
    recorded and left alone, and the rest are flagged (only when *maintenance* is set) and shut down
    through the engine's popup chain. What the operation DID is written to the engine's power-off
    record, which is what makes the matching :func:`bring_node_online` able to revert exactly it.

    A second ``offline`` on the same node UNIONS into that record rather than replacing it, so a
    server started by hand in between does not fall out of it.
    """
    node = ctx.resolve_node(node_name)
    if node is None:
        # The same ONE wording as the trio's: "no such name" and "the cluster knows it and cannot
        # reach it" are one answer, and the node page already renders the heartbeat verdict.
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Node '{node_name}' is offline or unknown.", node_name=node_name))
    canonical = str(getattr(node, "name", "") or node_name)
    servers = _node_servers(ctx, canonical)
    outcome = await ServerMaintenanceManager(node).power_off(servers, flag=maintenance, stop=True)
    record_power_off(canonical, when=datetime.now(), actor=ctx.audit_actor or "",
                     stopped=_server_names(outcome.stopped),
                     flagged=_server_names(outcome.flagged),
                     already_flagged=_server_names(outcome.already_flagged))
    message = _offline_message(canonical, servers, outcome, maintenance=maintenance)
    return await _audited(ctx, NodeControlResult(success=not outcome.failed, message=message,
                                                 node_name=canonical))


def _offline_message(node_name: str, servers: list, outcome: PowerOffOutcome, *,
                     maintenance: bool) -> str:
    """The power-off report: what was flagged, what was stopped, and what was left alone.

    Never a bare success count — an operator reading the notice must be able to tell a server that
    was already in maintenance (its flag untouched) from one that was already stopped, and a server
    that could not be handled. The ``already stopped`` figure is the servers the engine SKIPPED
    because their process was not in service (``SHUTDOWN``/``UNREGISTERED``).
    """
    skipped = max(0, len(servers) - len(outcome.handled))
    if maintenance:
        parts = [f"{len(outcome.flagged)} server(s) marked as maintenance and stopped"]
    else:
        parts = [f"{len(outcome.stopped)} server(s) stopped"]
    if outcome.already_flagged:
        parts.append(f"{len(outcome.already_flagged)} already in maintenance, left as it was")
    if skipped:
        parts.append(f"{skipped} already stopped")
    if not maintenance:
        parts.append("no maintenance flag was set")
    message = f"Node '{node_name}': " + ", ".join(parts) + "."
    if outcome.failed:
        # Never a bare success over a node where a server did not move: keep what DID happen and add
        # what did not, and report a failure so the console's banner says so.
        message = f"{message} {len(outcome.failed)} server(s) could not be marked as maintenance."
    return message


async def _power_on(ctx: Any, node_name: str, *, clear_all: bool = False) -> NodeControlResult:
    """Bring the SERVERS of ONE node back into service — ``online``'s one implementation.

    It CLEARS the maintenance flags and nothing else: the START is the scheduler's job, and this
    operation does not take it. With a power-off RECORD (the ordinary case) it reverts EXACTLY that
    record — it clears the flags the operation set and leaves the servers it stopped for the scheduler
    to pick up (naming how many are already up, and how many still carry a flag the record does not
    own). It clears NO flag it did not set.

    With NO record (this process started since the ``offline`` — a bot restart, a failover, a crash)
    it falls back to the RULE: release every server of the node that is down AND not in maintenance,
    clear NO flag, and say in the message that the record is gone, since when. The degradation is
    visible and never clears a flag nobody asked it to clear.

    *clear_all* is the FORCED clear: every maintenance flag on the node goes — hand-set ones included
    — and every server that is down is released for the scheduler. It is the ONLY path that clears a
    flag this process has no record of, which is what a restart between the halves leaves behind, and
    it is opt-in for that reason: the caller has said out loud that the flags on this node are not to
    be trusted.
    """
    node = ctx.resolve_node(node_name)
    if node is None:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Node '{node_name}' is offline or unknown.", node_name=node_name))
    canonical = str(getattr(node, "name", "") or node_name)
    servers = _node_servers(ctx, canonical)
    manager = ServerMaintenanceManager(node)

    def released(servers_to_start: list) -> list:
        """The servers the scheduler will actually pick up: down, and carrying no flag."""
        return [server for server in servers_to_start
                if not getattr(server, "maintenance", False) and not manager.in_service(server)]

    if clear_all:
        outcome = await manager.power_on(servers, clear=servers, start=())
        clear_power_record(canonical)
        will_start = released(servers)
        parts = [f"{len(outcome.cleared)} maintenance flag(s) cleared (forced)",
                 f"{len(will_start)} server(s) left for the scheduler to start"]
        if len(will_start) < len(servers):
            parts.append(f"{len(servers) - len(will_start)} left alone (already running)")
        message = f"Node '{canonical}': " + ", ".join(parts) + "."
        return await _audited(ctx, NodeControlResult(success=True, message=message,
                                                     node_name=canonical))
    record = power_record(canonical)
    if record is None:
        outcome = await manager.power_on(servers, clear=(), start=())
        started_at = process_started_at().strftime("%H:%M")
        message = (f"Node '{canonical}': no power-off is recorded for this node since the bot "
                   f"started at {started_at}; {len(outcome.cleared)} maintenance flag(s) cleared; "
                   f"{len(released(servers))} server(s) that were down and not in maintenance are "
                   f"left for the scheduler to start.")
    else:
        to_clear, missing_clear = _resolve_recorded(servers, record.flagged)
        to_start, missing_start = _resolve_recorded(servers, record.stopped)
        outcome = await manager.power_on(servers, clear=to_clear, start=())
        clear_power_record(canonical)
        missing = list(dict.fromkeys(missing_clear + missing_start))
        already_up = [server for server in to_start if manager.in_service(server)]
        still_flagged = [server for server in to_start if getattr(server, "maintenance", False)]
        left_alone = [server for server in to_start
                      if server in already_up or server in still_flagged]
        released_to_scheduler = [server for server in to_start if server not in left_alone]
        parts = [f"{len(outcome.cleared)} maintenance flag(s) cleared",
                 f"{len(released_to_scheduler)} server(s) left for the scheduler to start"]
        if left_alone:
            parts.append(f"{len(left_alone)} left alone (already running or still in maintenance)")
        if missing:
            parts.append(f"{len(missing)} recorded server(s) no longer exist on this node")
        message = f"Node '{canonical}': " + ", ".join(parts) + "."
    return await _audited(ctx, NodeControlResult(success=True, message=message, node_name=canonical))


@action
async def take_node_offline(ctx: Any, node_name: str, maintenance: bool = True) -> NodeControlResult:
    """Take the SERVERS of ONE node out of service — the same operation ``/node offline`` performs.

    Every server of the node whose process may be up is SHUT DOWN through the engine's popup chain
    (so the players are warned and disconnected, not silently dropped) and, when *maintenance* is
    truthy — **the default** — also marked ``maintenance`` so the scheduler cannot bring it back
    while the node is meant to be offline. A server that was ALREADY in maintenance keeps its flag
    and is only stopped; a server that is already stopped is left alone. *maintenance* off stops the
    servers without touching any flag (leave the flags to the admin).

    The operation and what it did are recorded (``core/data/maintenance.py``), so the matching
    :func:`bring_node_online` reverts exactly it. The node itself is not touched: its services,
    including the console that served this request, keep running.
    """
    return await _power_off(ctx, node_name, maintenance=bool(maintenance))


@action
async def bring_node_online(ctx: Any, node_name: str, clear_all: bool = False) -> NodeControlResult:
    """Bring the SERVERS of ONE node back into service — the same operation ``/node online`` performs.

    It reverts exactly the record the matching :func:`take_node_offline` left: it clears ONLY the
    maintenance flags that operation set (a flag set by hand is never cleared) and leaves the servers
    that operation stopped for the SCHEDULER to start — a server that is already up is reported, a
    server still in maintenance is not released. The start is the scheduler's job; this operation does
    not take it, so the two never race over the same server.

    With no record (the bot restarted in between) it releases every server of the node that is down
    and NOT in maintenance, clears NO flag, and the message says exactly that.

    *clear_all* — ``/node online <node> maintenance:false``, and the console's equivalent — is the
    FORCED clear for the case the record cannot cover: every maintenance flag on the node is cleared,
    hand-set ones included, and every server that is down is released for the scheduler. It is the
    only way to clear the flags an ``offline`` left before a restart wiped the record, and it is
    opt-in because those flags are then taken on trust. The node's services are untouched either way.
    """
    return await _power_on(ctx, node_name, clear_all=bool(clear_all))


# ── the SERVER CREATION pair ───────────────────────────────────────────────
#
# TWO operations, one instance each: **add** puts a server where there is none (an existing instance
# that carries none, or a fresh instance), **delete** removes the server TOGETHER with the instance it
# lives in — the two halves of ``/node add_instance`` and ``/node delete_instance``.
#
# The node method owns the writes (``nodes.yaml``, the directory, the database rows); this module
# resolves the target, calls it and words the report, exactly as the four operations above do.
# Binding a server to an instance that ALREADY exists is ``Node.bind_server`` on that instance's own
# node — ``add_instance`` mints a NEW instance and would overwrite an existing one's block, so it
# cannot serve an instance that is already there.

#: The word an agent answers an RPC method it does not have with — the fallback sentence is worded
#: for a node whose bot still predates ``Node.bind_server``.
MISSING_METHOD_MARKER = "non-existing function"


def _node_has_no_bind_server(ex: Exception) -> bool:
    """Whether *ex* is the RPC layer's answer to a method the node does not have."""
    return MISSING_METHOD_MARKER in str(ex)


def _instance_target(instance: str, node_name: str) -> tuple[str, str]:
    """``(node, instance)`` for a picker value — ``<node>/<instance>``, or the named node's own.

    The instance picker states the OWNING node with the instance because the same instance name can
    exist on two nodes; a value that carries no separator is an instance of *node_name*. The split is
    on the LAST separator: an instance name is a Saved Games folder name, so it carries none itself.
    """
    value = str(instance or "").strip()
    node, _, name = value.rpartition("/")
    return (node, name) if node else (node_name, value)


def _service_bus() -> Any:
    """The process's own ``ServiceBus``, or ``None`` when there is none.

    Deliberately NOT ``ctx.bus``: the console hands an action a per-request, SCOPED VIEW of the
    cluster (``services/webservice/pages/actions._Cluster``), which is a read surface with no
    ``send_init`` — and a bound server is a PROCESS fact. The real registry is the one
    ``NodeImpl.add_instance`` writes, so this is the same place a Discord `/node add_instance` lands.
    """
    try:
        from core.services.registry import ServiceRegistry
        from services.servicebus import ServiceBus

        return ServiceRegistry.get(ServiceBus)
    except Exception:
        return None


async def _drop_blank_server(server: Any, bus: Any) -> None:
    """Undo a binding that could not be completed: unbind the blank server and forget it.

    ``ServerImpl.unlink`` is the decline Discord's own view offers (its Dismiss leaves the instance
    behind, unbound); the bus entry is dropped here as well, because a declined blank server left in
    the registry is a server row no instance owns.
    """
    with suppress(Exception):
        await server.unlink()
    if getattr(bus, "servers", None) is not None and bus.servers.get(server.name) is server:
        bus.servers.pop(server.name, None)


async def _name_server(server: Any, name: str, channels: dict[str, int]) -> None:
    """Give *server* its NAME — the ``servers.yaml`` key — and its channels.

    The two writes Discord's ``ServerConfigView`` Save performs: ``Server.rename`` refuses a reserved,
    empty or colliding name itself (``RenameError``), so a name another node already owns is refused
    by the bot's own guard and never overwrites it.
    """
    await server.rename(name, update_settings=True)
    if channels:
        await server.update_channels(channels)


@action
async def add_server(ctx: Any, node_name: str, name: str, instance: str = "",
                     configure: bool = True, status: int | None = None,
                     chat: int | None = None,
                     admin: int | None = None) -> NodeControlResult:
    """Create a server on ONE node — either ON an instance that already exists, or on a new one.

    TWO PATHS, one parameter each:

    * *instance* names an instance that exists and carries NO server (exactly what declining
      Discord's "configure a server for this instance?" leaves behind): ``Node.bind_server`` runs on
      that instance's OWN node and binds a blank server to its EXISTING ``bot_port``, then names it.
      A taken instance is REFUSED, never replaced. The value states the owning node with the instance
      (``<node>/<instance>``), so the instance is bound where it lives rather than where this process
      runs; a value carrying no separator is an instance of *node_name*;
    * no *instance*: ``Node.add_instance(name)`` runs on *node_name* — it mints the instance's ports,
      creates its Saved Games directory and writes its ``nodes.yaml`` block — and the server it
      creates is named here. With *configure* off that instance is left blank, unbound, which is what
      Discord's own Dismiss does.

    *name* is the server's name (the ``servers.yaml`` key) and, on the new-instance path, the
    instance's name as well: the two are the same word in ``/node add_instance``'s flow, and a
    separate instance name is a decision nobody needs. *status* / *chat* / *admin* are the Discord
    channel ids of the same names, each optional and each written through the bot's own
    ``Server.update_channels``, which MERGES onto the file (a channel this write does not name keeps
    what the file holds).

    The trail is written once, for every outcome, including each refusal.
    """
    node = ctx.resolve_node(node_name)
    if node is None:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Node '{node_name}' is offline or unknown.", node_name=node_name))
    wanted = str(name or "").strip()
    chosen = str(instance or "").strip()
    canonical = str(getattr(node, "name", "") or node_name)
    channels = {key: int(value) for key, value in
                (("status", status), ("chat", chat), ("admin", admin)) if value is not None}

    if chosen:
        if not configure:
            return await _audited(ctx, NodeControlResult(
                success=False,
                message=(f"Attaching a server to instance '{chosen}' IS binding it — the "
                         f"'configure a server' choice only applies to a new instance."),
                node_name=canonical))
        if not wanted:
            return await _audited(ctx, NodeControlResult(
                success=False, message="A server name is required.", node_name=canonical))
        owner_name, instance_name = _instance_target(chosen, canonical)
        owner = ctx.resolve_node(owner_name)
        if owner is None:
            return await _audited(ctx, NodeControlResult(
                success=False, message=f"Node '{owner_name}' is offline or unknown.",
                node_name=owner_name))
        owner_canonical = str(getattr(owner, "name", "") or owner_name)
        try:
            # the node that OWNS the instance binds it: its home, its serverSettings.lua and its DCS
            # process are there, and only that node can hand the new server its bot port
            await owner.bind_server(instance_name, wanted, channels)
        except Exception as ex:
            if _node_has_no_bind_server(ex):
                return await _audited(ctx, NodeControlResult(
                    success=False,
                    message=(f"Instance '{instance_name}' is on node '{owner_canonical}', whose bot "
                             f"does not support binding a server to an existing instance; update "
                             f"that node and try again."),
                    node_name=owner_canonical))
            return await _audited(ctx, NodeControlResult(
                success=False,
                message=(f"Could not bind server '{wanted}' to instance '{instance_name}' on node "
                         f"'{owner_canonical}': {ex}"),
                node_name=owner_canonical))
        return await _audited(ctx, NodeControlResult(
            success=True,
            message=(f"Server '{wanted}' was bound to instance '{instance_name}' on node "
                     f"'{owner_canonical}'."),
            node_name=owner_canonical))

    # ── a NEW instance ────────────────────────────────────────────────────
    if not wanted:
        return await _audited(ctx, NodeControlResult(
            success=False, message="A name is required for a new instance.", node_name=canonical))
    try:
        new_instance = await node.add_instance(wanted)
    except Exception as ex:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Instance '{wanted}' could not be added to node '{canonical}': {ex}",
            node_name=canonical))
    if not new_instance:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Instance '{wanted}' could not be added to node '{canonical}'.",
            node_name=canonical))
    server = getattr(new_instance, "server", None)
    if not configure:
        # Discord's own "no": the instance exists, no server is bound to it
        if server is not None:
            await _drop_blank_server(server, _service_bus())
        return await _audited(ctx, NodeControlResult(
            success=True,
            message=(f"Instance '{wanted}' was created on node '{canonical}' with no server bound "
                     f"to it."),
            node_name=canonical))
    if server is None:
        return await _audited(ctx, NodeControlResult(
            success=False,
            message=(f"Instance '{wanted}' was created on node '{canonical}' but carries no server "
                     f"to name."),
            node_name=canonical))
    try:
        await _name_server(server, wanted, channels)
    except Exception as ex:
        await _drop_blank_server(server, _service_bus())
        return await _audited(ctx, NodeControlResult(
            success=False,
            message=(f"Instance '{wanted}' was created on node '{canonical}', but the server could "
                     f"not be named: {ex}"),
            node_name=canonical))
    return await _audited(ctx, NodeControlResult(
        success=True,
        message=(f"Server '{wanted}' was created on node '{canonical}' "
                 f"(instance '{wanted}', port {new_instance.bot_port})."),
        node_name=canonical))


@action
async def delete_server(ctx: Any, server_name: str, remove_files: bool = False) -> NodeControlResult:
    """Remove ONE server TOGETHER with the instance it lives in — ``/node delete_instance``.

    The server is stopped first (``Server.shutdown(force=True)``, the call ``/node delete_instance``
    makes) whenever its process may be up, then the node's own ``delete_instance`` removes the
    instance: its ``nodes.yaml`` block, its database rows, its registration — and, only with
    *remove_files*, its Saved Games directory. That directory is the ONE thing a mistake cannot be
    undone from, which is why it is a choice of its own and never implied by removing the server.

    THE NODE IS DERIVED, never posted: it is the server's own ``node``, so a request cannot point the
    removal at another machine's instance, and a remote server's removal goes over the RPC its node
    proxy owns — the same call ``/node delete_instance`` makes from Discord.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Server '{server_name}' not found.", node_name=""))
    name = str(getattr(server, "name", "") or server_name)
    node = getattr(server, "node", None)
    canonical_node = str(getattr(node, "name", "") or "")
    if node is None or not canonical_node:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Server '{name}' has no node to be removed from.",
            node_name=canonical_node))
    instance = getattr(server, "instance", None)
    if instance is None:
        return await _audited(ctx, NodeControlResult(
            success=False, message=f"Server '{name}' is not bound to an instance.",
            node_name=canonical_node))
    instance_name = str(getattr(instance, "name", "") or "")
    try:
        if ServerMaintenanceManager.in_service(server):
            # The engine's OWN test for "this server's process may be up" — a LOADING or
            # SHUTTING_DOWN server is stopped as well, not only the states Discord's command lists.
            # Removing an instance under a live DCS process is how a directory ends up in use.
            await server.shutdown(force=True)
        await node.delete_instance(instance, bool(remove_files))
    except PermissionError:
        return await _audited(ctx, NodeControlResult(
            success=False,
            message=(f"Server '{name}' was stopped, but instance '{instance_name}' could not be "
                     f"removed, because its directory is in use."),
            node_name=canonical_node))
    except Exception as ex:
        return await _audited(ctx, NodeControlResult(
            success=False,
            message=f"Could not remove server '{name}' from node '{canonical_node}': {ex}",
            node_name=canonical_node))
    extra = " and its Saved Games directory" if remove_files else ""
    return await _audited(ctx, NodeControlResult(
        success=True,
        message=(f"Server '{name}' and instance '{instance_name}' were removed from node "
                 f"'{canonical_node}'{extra}."),
        node_name=canonical_node))
