"""The tool list of one level, built from the endpoint rows.

A tool is a ROW: its name is the row's action qualname, its description the row's summary, and its
parameter list the action's own signature through ``core.action_surface.tool_spec_for``. Nothing is
declared twice, so a tool can never describe an action differently from the ``/api/v1`` route that
serves the same action.

Every handler carries a SYNTHETIC ``__signature__``: mcp 2.3.0 builds a tool's input schema with
``inspect.signature``, which honours it, so the schema is explicit — typed, by name — and never a
``**kwargs`` catch-all.

One ``MCPServer`` per level (see ``service.py``): a tool list is a PROMISE an agent plans against, so
a ``read`` client is not shown a lifecycle tool that would answer "no". The wrappers below re-check
the level anyway, because a crafted ``tools/call`` can name a tool the list never offered.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Mapping, Sequence

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from core.action_results import ActionResult
from core.action_surface import Risk, ToolSpec, tool_spec_for
from core.actions import ActionContext, call_action, read_action

from .server import McpPrincipal, current_principal

log = logging.getLogger(__name__)

__all__ = ["register_tools", "visible"]

#: What a tool answers when no authenticated identity reached it. Fail closed: nothing runs.
NO_IDENTITY = "no authenticated identity on this call"

#: How an action's outbound name reads in a refusal ("'start_server' is not offered to ...").
_NOTHING_TO_RUN = "nothing was run"


def visible(row: Any, level: str) -> bool:
    """Whether a row belongs to *level*'s tool list.

    ``row.mcp is False`` excludes a row from MCP at every level (console/REST only in slice 1);
    otherwise the level decides through :data:`services.mcpservice.server.VISIBLE_RISKS`.
    """
    if not row.mcp:
        return False
    return McpPrincipal(scheme="key", label="-", level=level).sees(row.risk)


def _context(service: Any, principal: McpPrincipal) -> ActionContext:
    """The action context of one call, with the caller's own audit actor.

    The verified key is passed to ``ActionContext.from_service`` as ``actor``, so the trail names
    the CLIENT (``[mcp:key/<label>]``) and not the transport, and the sanitiser is the one core
    applies to every actor token.
    """
    return ActionContext.from_service(service, actor=principal.actor)


def _body(result: ActionResult) -> dict[str, Any]:
    """The wire body of a successful result: the shared envelope, ``{status, message, **data}``."""
    return result.to_dict()


def _wrap(spec: ToolSpec, fn: Callable, *, service: Any) -> Callable:
    """One tool handler for *spec*, dispatching to *fn* through the action seam.

    Reads go through ``read_action`` (no in-flight guard, no audit burden) and everything else
    through ``call_action`` (guard, typed refusals, the missing-audit warning). A refusal becomes an
    ``isError`` tool answer carrying the action's own sentence — the model reads one sentence on both
    transports.
    """
    is_read = spec.risk is Risk.READ
    dispatch = read_action if is_read else call_action

    async def call(**params: Any) -> dict[str, Any]:
        principal = current_principal()
        if principal is None:
            raise ToolError(NO_IDENTITY)
        if not principal.sees(spec.risk):
            raise ToolError(f"this key is '{principal.level}'; '{spec.name}' is not offered to it "
                            f"and {_NOTHING_TO_RUN}.")
        result = await dispatch(spec.name, _context(service, principal), **params)
        if not result.success:
            raise ToolError(result.message or f"'{spec.name}' failed.")
        return _body(result)

    call.__name__ = call.__qualname__ = spec.name
    call.__doc__ = spec.description
    call.__signature__ = inspect.Signature(parameters=list(spec.parameters))  # type: ignore[attr-defined]
    return call


def register_tools(server: MCPServer, rows: Sequence[Any], *, level: str, service: Any,
                   registry: Mapping[str, Callable] | None = None) -> int:
    """Add every row *level* sees to *server* and answer how many were added.

    A row whose action is absent from this process is skipped with a WARNING: its tool could only
    answer a refusal, and a tool list is a promise.
    """
    actions = registry if registry is not None else _default_registry()
    added = 0
    for row in rows:
        if not visible(row, level):
            continue
        fn = actions.get(row.action)
        if fn is None:
            log.warning("MCP: the row %s names '%s', which is not registered in this process - the "
                        "tool is not offered at level '%s'.", row, row.action, level)
            continue
        spec = tool_spec_for(row, fn)
        server.add_tool(_wrap(spec, fn, service=service),
                        name=spec.name, description=spec.description)
        added += 1
    return added


def _default_registry() -> Mapping[str, Callable]:
    """The action registry of this process, imported on demand (``core`` needs the bot stack)."""
    from core.actions import action_registry

    return action_registry()
