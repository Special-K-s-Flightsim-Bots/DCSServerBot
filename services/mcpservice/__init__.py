"""The MCP service: endpoint rows served as MCP tools over ``POST /mcp``.

``MCPService`` is registered by :mod:`services.mcpservice.service`. This module imports nothing
from the bot stack: the service discovery walk imports every module of the package, so an
``__init__`` that imported ``service`` would make the package's importability depend on the bot
stack, and a registration side effect here would be the only thing registering the service — the
failure mode ``tests/test_service_discovery_registration.py`` pins.

* :mod:`services.mcpservice.server`   — config, caller identity, the mount;
* :mod:`services.mcpservice.tools`    — the tool list of one level, built from the rows;
* :mod:`services.mcpservice.service`  — the service: one server per level, mounted and unmounted.
"""
import logging

# The SDK's transport logs an INFO teardown line per client connection, carrying a ``None`` session
# id; its WARNINGs (mount and session-manager faults) stay visible at this level.
logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)
