"""The MCP service: one tool list per key level, mounted on the master's web application.

The surface is a MOUNT (``/mcp``), not a route: the application-level capability gate is a route
dependency, so a mount is the one surface that answers without it — which is why
``permissions.EXPECTED_MOUNTS`` declares this path and the mount audit stays quiet. The gate that
replaces it is ``services.mcpservice.server._Bearer``: verify the bearer token, pick the level's
application, stamp the principal for the duration of the request.

A configured-but-unusable transport is a STARTUP refusal (``McpConfigError``), never a mount that
answers 421 to every request: the service registry logs ``Service MCP NOT started: <reason>`` and
keeps the rest of the process running.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer

from core.services.base import Service
from core.services.registry import ServiceRegistry
from core.plugin_manager import PluginManager
from services.bot import BotService
from services.servicebus import ServiceBus
from services.webservice.service import WebService

from .server import (
    McpConfigError,
    McpKey,
    McpVerifier,
    LEVELS,
    find_mount,
    keys_from_config,
    mount,
    streamable_app,
    transport_from_config,
    unmount,
)
from .tools import register_tools

if TYPE_CHECKING:
    from core import NodeImpl

log = logging.getLogger(__name__)

#: What a client is told the surface is for. Kept short: it is read by a model, once per session.
INSTRUCTIONS = (
    "DCS server control for this DCSServerBot installation. Read the fleet and a server's state "
    "with the read tools, then start, stop or restart a server. Every call is audited under the "
    "key it was made with."
)


@ServiceRegistry.register(master_only=True, depends_on=[ServiceBus, BotService, WebService])
class MCPService(Service):
    """Serves the endpoint rows as MCP tools over ``POST /mcp`` on the web application."""

    def __init__(self, node: "NodeImpl"):
        super().__init__(node)
        #: the level → ``MCPServer`` map, one instance per level (a tool list is per session)
        self._servers: dict[str, MCPServer] = {}
        #: the per-level session managers' lifetimes; a mounted app's own lifespan never runs
        self._exit_stack: AsyncExitStack | None = None
        #: the mount this service appended, so a stop removes exactly it
        self._mount: Any = None
        #: the application it was appended to
        self._app: Any = None
        #: how many tools each served level offers
        self._counts: dict[str, int] = {}

    async def start(self):
        await super().start()
        config_file = self._config_file()
        if not config_file.exists():
            self.log.info("MCP: no config file at %s - nothing is mounted. Create it with a "
                          "'DEFAULT:' block to serve /mcp.", config_file)
            return
        config = self.get_config() or {}
        if not config.get("enabled", False):
            self.log.info("MCP: not enabled in services/mcpservice.yaml - nothing is mounted.")
            return
        keys = keys_from_config(config)
        transport = transport_from_config(config)
        app = self._web_app()
        rows = self._rows()
        await self._build(keys, transport, rows, app)
        self.log.info("MCP: mounted on %s with %d key(s) - tool lists: %s.", "/mcp", len(keys),
                      ", ".join(f"{level}={count}" for level, count in self._counts.items()))

    # ── wiring ──────────────────────────────────────────────────────────────

    def _config_file(self) -> Path:
        """The file ``Service.read_locals`` loads: ``config/services/mcpservice.yaml``."""
        return Path(self.node.config_dir) / "services" / f"{self.name.lower()}.yaml"

    def _web_app(self) -> FastAPI:
        """The master's web application — the only place this surface can be a mount of."""
        web = ServiceRegistry.get(WebService)
        app = getattr(web, "app", None)
        if not isinstance(app, FastAPI):
            raise McpConfigError(
                "MCP is enabled but this node serves no web application: the surface is a mount on "
                "the master's FastAPI app (services/webservice.yaml), so an install with no "
                "webservice, or with it not started, cannot expose /mcp. Disable the service "
                "(enabled: false) or start the webservice.")
        return app

    def _rows(self) -> tuple[Any, ...]:
        """The endpoint rows of this installation, discovered and validated.

        The action registry is filled where the PLUGINS load (``PluginManager``); the call here is
        the fallback for a process without that load, and it is idempotent.
        """
        from core.actions import action_registry, discover_actions, kind_of
        from core.action_surface import discover_endpoints

        plugin_names = PluginManager(self.node).plugins
        discover_actions(plugin_names)
        rows = discover_endpoints(plugin_names, registry=action_registry(), kind_of=kind_of)
        if not rows:
            self.log.warning("MCP: no endpoint row is registered in this process - the mount will "
                             "serve an empty tool list.")
        return rows

    async def _build(self, keys: tuple[McpKey, ...], transport: Any, rows: tuple[Any, ...],
                     app: FastAPI) -> None:
        """Build one server per configured level, start its session manager, mount the gate."""
        from core.actions import action_registry

        registry = action_registry()
        apps: dict[str, Any] = {}
        self._counts = {}
        stack = AsyncExitStack()
        try:
            for level in LEVELS:
                if not any(key.level == level for key in keys):
                    continue
                server = MCPServer("dcsserverbot", instructions=INSTRUCTIONS)
                count = register_tools(server, rows, level=level, service=self, registry=registry)
                if not count:
                    self.log.warning("MCP: level '%s' has keys but no tool to offer - the level is "
                                     "not served.", level)
                    continue
                self._servers[level] = server
                self._counts[level] = count
                apps[level] = streamable_app(server, transport)
                # a mounted app's lifespan never runs, so the session manager is entered here
                await stack.enter_async_context(server.session_manager.run())
            if not apps:
                raise McpConfigError("MCP is enabled and configured but serves no level: no tool "
                                     "is visible to any configured key.")
            self._mount = mount(app, apps, McpVerifier(keys))
        except BaseException:
            # an abort must close what was already entered: a session manager left running has no
            # owner and keeps answering, and the stack is only published on success
            self._servers.clear()
            self._counts = {}
            self._mount = None
            await stack.aclose()
            raise
        self._exit_stack = stack
        self._app = app

    # ── lifecycle ───────────────────────────────────────────────────────────

    async def stop(self):
        if self._mount is not None:
            # the mount is removed from whatever app currently carries it: the service may be
            # stopped after the web application was rebuilt, and a stale object would answer 404
            for candidate in (getattr(ServiceRegistry.get(WebService), "app", None), self._app):
                if candidate is not None:
                    unmount(candidate, self._mount)
            self._mount = None
            self.log.info("MCP: unmounted /mcp.")
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            finally:
                self._exit_stack = None
        self._servers.clear()
        self._counts = {}
        await super().stop()

    # ── introspection (tests and the web console) ───────────────────────────

    @property
    def tool_counts(self) -> dict[str, int]:
        """How many tools each served level offers."""
        return dict(self._counts)

    def mounted_on(self, app: FastAPI) -> bool:
        """Whether this service's mount is currently installed on *app*."""
        return self._mount is not None and find_mount(app, "/mcp") is self._mount
