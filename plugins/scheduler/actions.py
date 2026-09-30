"""The MAINTENANCE FLAG's action pair — set / clear it for ONE server (card ``t_ceb27d2c`` W5b).

The flag (``servers.maintenance``) is the SCHEDULER's concept, and it is a different thing from a
server's power: it is a persisted, cluster-wide switch that keeps a server OUT OF SERVICE. The
scheduler will not start a flagged server, and setting the flag aborts a restart the scheduler was
already waiting to perform. A server can be flagged while it is running and unflagged while it is
stopped — a flag is not a power state (``MAINTENANCE.md`` §1).

ONE implementation, every transport: ``/scheduler maintenance`` / ``/scheduler clear``
(``plugins/scheduler/commands.py``) and the console's server-row pair (``services/webservice/pages/
actions.py``) all reach :func:`set_maintenance` / :func:`clear_maintenance`, so the console and
Discord cannot drift into two meanings for one flag (``MAINTENANCE.md`` §4.3, §6, §7). The Discord
commands keep the ONE thing an action cannot carry — the ``yn_question`` that warns about an
aborted pending restart — and delegate the change itself to here.

The trail is written HERE, once, whatever the transport (design §5.3 D1), including for a refusal:
one entry per attempt is what an operator wants after an incident.
"""
from __future__ import annotations

import logging
from typing import Any

from core.action_results import ServerControlResult
from core.actions import audit_action, action

log = logging.getLogger(__name__)

__all__ = ["set_maintenance", "clear_maintenance"]

#: The attributes a set flag ABORTS, because setting the flag is what aborts them. Exactly the four
#: ``/scheduler maintenance`` clears today (``plugins/scheduler/commands.py``): a pending restart,
#: the server-empty trigger and the two on-mission-end triggers. Named here once so the action and
#: the command cannot disagree about what "setting the flag" means.
ABORTED_BY_MAINTENANCE: tuple[str, ...] = ("restart_pending", "on_empty", "on_mission_end",
                                           "on_coalition_win")


def _label(server: Any, fallback: str) -> str:
    """The name a message uses: the object's own, never anything the caller sent."""
    return str(getattr(server, "display_name", None) or getattr(server, "name", "") or fallback)


def _abort_pending_restart(server: Any) -> None:
    """Abort everything waiting to restart *server* — the four triggers the flag overrides.

    Read tolerantly, because the four are not one type: ``restart_pending`` is a bool while the other
    three are the event sets (``on_empty`` / ``on_mission_end`` / ``on_coalition_win``). A container
    is ``clear()``-ed; anything else is set to ``False``. An object that carries only some of them —
    or none at all — must not cost the flag: this is housekeeping around the ONE write that matters.
    """
    for attribute in ABORTED_BY_MAINTENANCE:
        value = getattr(server, attribute, None)
        try:
            emptier = getattr(value, "clear", None)
            if callable(emptier):
                emptier()
            elif value is not None:
                setattr(server, attribute, False)
        except Exception:                     # pragma: no cover - a double that refuses the write
            log.exception("Could not clear '%s' on server '%s' while setting the maintenance flag.",
                          attribute, getattr(server, "name", "?"))


async def _flag(ctx: Any, server_name: str, *, maintenance: bool) -> ServerControlResult:
    """The shared body of the pair — ONE implementation, because they differ by one boolean.

    A server that is ALREADY in the requested state is a TYPED REFUSAL, never a silent re-write: the
    console's control is only offered in the state that applies (``WriteAction.when_maintenance``),
    so a second attempt means a stale page or another transport, and both deserve to be told the
    flag did not move rather than a success they cannot see. This mirrors the two Discord commands'
    own \"is already in maintenance mode\" / \"is not in maintenance mode\" answers, word for word.
    """
    server = ctx.resolve_server(server_name)
    if server is None:
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Server '{server_name}' not found.", server_name=server_name))
    name = _label(server, server_name)
    if bool(getattr(server, "maintenance", False)) is maintenance:
        message = (f"Server \"{name}\" is already in maintenance mode." if maintenance
                   else f"Server \"{name}\" is not in maintenance mode.")
        return await _audited(ctx, ServerControlResult(
            success=False, message=message, server_name=server_name), server)
    try:
        server.maintenance = maintenance
    except Exception as ex:
        # Never an AttributeError out of a transport: a typed failure, and the flag did not move.
        log.exception("maintenance flag: could not write it on server '%s'", name)
        return await _audited(ctx, ServerControlResult(
            success=False, message=f"Failed to change the maintenance flag on server '{name}': {ex}",
            server_name=server_name), server)
    if maintenance:
        _abort_pending_restart(server)
        message = f"Maintenance mode set for server \"{name}\"."
    else:
        message = f"Maintenance mode cleared for server \"{name}\"."
    return await _audited(ctx, ServerControlResult(
        success=True, message=message, server_name=server_name), server)


async def _audited(ctx: Any, result: ServerControlResult,
                   server: Any = None) -> ServerControlResult:
    """Write the trail for *result* and return it (design §5.3 D1).

    ONE place, so every exit of both halves — the change, the already-in-that-state refusal and the
    not-found refusal — leaves exactly one entry, and none of them can forget the one mechanism.
    """
    await audit_action(ctx, result, server=server)
    return result


@action
async def set_maintenance(ctx: Any, server_name: str) -> ServerControlResult:
    """Set the maintenance flag on ONE server — the same operation ``/scheduler maintenance`` performs.

    The server is taken out of service: the scheduler will not start it, and any restart it was
    already queued for is ABORTED (``restart_pending`` / ``on_empty`` / ``on_mission_end`` /
    ``on_coalition_win`` all cleared, exactly as the command does). The server's process is NOT
    touched — a flagged server keeps running until somebody stops it, which is why this control
    lives on the server row and not with the node's power pair.
    """
    return await _flag(ctx, server_name, maintenance=True)


@action
async def clear_maintenance(ctx: Any, server_name: str) -> ServerControlResult:
    """Clear the maintenance flag on ONE server — the same operation ``/scheduler clear`` performs.

    The server is back in service: it can be started again and the scheduler may start it. Nothing
    is started here — ending maintenance is a state change, not a power operation.
    """
    return await _flag(ctx, server_name, maintenance=False)
