import asyncio
import json
import os
import re

from collections import deque
from core import Plugin, DEFAULT_TAG, Status, ServiceRegistry, PluginInstallationError, Server, Coalition
from datetime import datetime, timezone
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Query, Body
from fastapi.security import APIKeyHeader
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from services.webservice import WebService
from typing import Any

from .models import (
    BotStatus, HealthCheck, PluginInfo, LogsResponse, LogEntry,
    ServerStatus, MissionInfo, PlayerInfo, ChatMessage, ChatResponse,
    LogisticsTaskCreate, LogisticsTaskResponse
)


def seconds_to_time_str(seconds: int | float | None) -> str:
    """Convert seconds to HH:MM:SS format."""
    if seconds is None:
        return "Unknown"
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class MCPAPI(Plugin):
    """
    MCP (Model Context Protocol) REST API plugin.

    Provides endpoints for bot control, DCS server interaction, and monitoring
    to enable AI assistants and external tools to interact with DCSServerBot.
    """

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'webservice.yaml')):
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not configured")

        self.web_service: WebService | None = None
        self.app: FastAPI | None = None
        self.router: APIRouter | None = None
        self._start_time = datetime.now(timezone.utc)

    async def cog_load(self) -> None:
        await super().cog_load()
        asyncio.create_task(self.init_webservice())

    async def cog_unload(self) -> None:
        if self.app and self.router:
            for route in self.router.routes:
                if route in self.app.routes:
                    self.app.routes.remove(route)
        await super().cog_unload()

    async def init_webservice(self):
        for i in range(0, 10):
            self.web_service = ServiceRegistry.get(WebService)
            if self.web_service and self.web_service.is_running():
                break
            await asyncio.sleep(1)
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not running, aborted.")
            return

        self.log.debug(f"   - {self.__cog_name__}: WebService is running")
        self.app = self.web_service.app
        if self.app:
            self.register_routes()
        else:
            self.log.error(f"  - {self.__cog_name__}: WebService is not available, aborted.")

    def register_routes(self):
        prefix = self.locals.get(DEFAULT_TAG, {}).get('prefix', '/mcp')
        if prefix and not prefix.startswith('/'):
            prefix = '/' + prefix
        api_key = self.locals.get(DEFAULT_TAG, {}).get('api_key')

        if api_key:
            api_key_header = APIKeyHeader(name="X-API-Key")

            def get_api_key(api_key_in_header: str = Depends(api_key_header)):
                if api_key_in_header != str(api_key):
                    raise HTTPException(status_code=403, detail="Invalid API Key")

            dependencies = [Depends(get_api_key)]
        else:
            dependencies = None

        self.router = APIRouter(prefix=prefix, dependencies=dependencies)

        # Bot Control Routes
        self.router.add_api_route(
            "/bot/status", self.bot_status,
            methods=["GET"],
            response_model=BotStatus,
            description="Get bot status including version, uptime, and loaded plugins.",
            summary="Bot Status",
            tags=["Bot Control"]
        )
        self.router.add_api_route(
            "/bot/health", self.bot_health,
            methods=["GET"],
            response_model=HealthCheck,
            description="Health check endpoint for monitoring.",
            summary="Health Check",
            tags=["Bot Control"]
        )
        self.router.add_api_route(
            "/bot/logs", self.bot_logs,
            methods=["GET"],
            response_model=LogsResponse,
            description="Get recent log entries from the bot.",
            summary="Bot Logs",
            tags=["Bot Control"]
        )
        self.router.add_api_route(
            "/bot/plugins", self.bot_plugins,
            methods=["GET"],
            response_model=list[PluginInfo],
            description="List all loaded plugins with their versions.",
            summary="List Plugins",
            tags=["Bot Control"]
        )

        # DCS Server Routes
        self.router.add_api_route(
            "/servers/{server_name}/status", self.server_status,
            methods=["GET"],
            response_model=ServerStatus,
            description="Get detailed status of a DCS server including mission and players.",
            summary="Server Status",
            tags=["DCS Server"]
        )
        self.router.add_api_route(
            "/servers/{server_name}/players", self.server_players,
            methods=["GET"],
            response_model=list[PlayerInfo],
            description="List all connected players on a DCS server.",
            summary="Server Players",
            tags=["DCS Server"]
        )
        self.router.add_api_route(
            "/servers/{server_name}/mission", self.server_mission,
            methods=["GET"],
            response_model=MissionInfo,
            description="Get current mission information from a DCS server.",
            summary="Mission Info",
            tags=["DCS Server"]
        )
        self.router.add_api_route(
            "/servers/{server_name}/chat", self.server_chat,
            methods=["POST"],
            response_model=ChatResponse,
            description="Send a chat message to the DCS server.",
            summary="Send Chat",
            tags=["DCS Server"]
        )

        # Logistics Routes
        self.router.add_api_route(
            "/servers/{server_name}/logistics/task", self.create_logistics_task,
            methods=["POST"],
            response_model=LogisticsTaskResponse,
            description="Create a new logistics task and post it to Discord.",
            summary="Create Logistics Task",
            tags=["Logistics"]
        )

        self.app.include_router(self.router)
        self.log.info(f"  - {self.__cog_name__}: Registered MCP API routes at {prefix}")

    def _resolve_server(self, server_name: str) -> Server:
        """Resolve server by name or instance name."""
        server = self.bot.servers.get(server_name)
        if not server:
            for s in self.bot.servers.values():
                if s.instance.name == server_name or s.name.lower() == server_name.lower():
                    server = s
                    break
        if not server:
            raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
        return server

    # Bot Control Endpoints

    async def bot_status(self) -> BotStatus:
        """Get bot status information."""
        plugins = []
        for cog_name in self.bot.cogs:
            cog = self.bot.cogs[cog_name]
            if hasattr(cog, 'plugin_version'):
                plugins.append(PluginInfo(
                    name=cog_name.lower(),
                    version=cog.plugin_version
                ))

        uptime = (datetime.now(timezone.utc) - self._start_time).total_seconds()

        return BotStatus(
            running=True,
            uptime_seconds=uptime,
            version=self.node.bot_version,
            sub_version=str(self.node.sub_version),
            node_name=self.node.name,
            is_master=self.node.master,
            plugins=plugins
        )

    async def bot_health(self) -> HealthCheck:
        """Health check endpoint."""
        # Check database connection
        db_status = "connected"
        try:
            async with self.apool.connection() as conn:
                await conn.execute("SELECT 1")
        except Exception:
            db_status = "disconnected"

        # Count servers
        servers_online = sum(1 for s in self.bot.servers.values()
                           if s.status in [Status.RUNNING, Status.PAUSED])
        servers_total = len(self.bot.servers)

        # Determine overall health
        if db_status == "disconnected":
            status = "unhealthy"
        elif servers_online == 0 and servers_total > 0:
            status = "degraded"
        else:
            status = "healthy"

        return HealthCheck(
            status=status,
            database=db_status,
            servers_online=servers_online,
            servers_total=servers_total
        )

    async def bot_logs(
        self,
        lines: int = Query(default=100, ge=1, le=1000, description="Number of log lines to return"),
        level: str = Query(default=None, description="Filter by log level (DEBUG, INFO, WARNING, ERROR)")
    ) -> LogsResponse:
        """Get recent log entries."""
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
        log_file = os.path.join(log_dir, 'dcssb-server.log')

        if not os.path.exists(log_file):
            # Try the rotated log file
            log_file = os.path.join(log_dir, 'dcssb-server.log.1')

        if not os.path.exists(log_file):
            return LogsResponse(entries=[], total_lines=0)

        entries = deque(maxlen=lines)
        total_lines = 0

        # Parse log format: 2026-01-23 19:08:51.643 DEBUG	message
        log_pattern = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(\w+)\t(.+)')

        try:
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    total_lines += 1
                    line = line.rstrip()
                    if not line:
                        continue

                    match = log_pattern.match(line)
                    if match:
                        timestamp, log_level, message = match.groups()
                        if level and log_level.upper() != level.upper():
                            continue
                        entries.append(LogEntry(
                            timestamp=timestamp,
                            level=log_level,
                            message=message
                        ))
                    elif entries:
                        # Continuation of previous log entry
                        entries[-1].message += '\n' + line
        except Exception as e:
            self.log.error(f"Error reading log file: {e}")
            return LogsResponse(entries=[], total_lines=0)

        return LogsResponse(entries=list(entries), total_lines=total_lines)

    async def bot_plugins(self) -> list[PluginInfo]:
        """List all loaded plugins."""
        plugins = []
        for cog_name in sorted(self.bot.cogs):
            cog = self.bot.cogs[cog_name]
            version = getattr(cog, 'plugin_version', 'unknown')
            plugins.append(PluginInfo(name=cog_name.lower(), version=version))
        return plugins

    # DCS Server Endpoints

    async def server_status(self, server_name: str) -> ServerStatus:
        """Get detailed status of a DCS server."""
        server = self._resolve_server(server_name)

        mission_info = None
        if server.current_mission and server.status in [Status.RUNNING, Status.PAUSED]:
            mission = server.current_mission
            mission_info = MissionInfo(
                name=mission.name,
                filename=mission.filename,
                theatre=mission.map or "Unknown",
                start_time=seconds_to_time_str(mission.start_time),
                real_time=int(mission.real_time) if mission.real_time else 0,
                pause=server.status == Status.PAUSED
            )

        players = []
        for player in server.players.values():
            if player.active:
                players.append(PlayerInfo(
                    id=player.id,
                    name=player.name,
                    ucid=player.ucid,
                    side=player.side.name.lower() if player.side else "spectator",
                    slot=player.slot if hasattr(player, 'slot') else None,
                    unit_type=player.unit_type if hasattr(player, 'unit_type') else None
                ))

        return ServerStatus(
            name=server.name,
            status=server.status.name.lower(),
            num_players=len(players),
            max_players=server.settings.get('maxPlayers', 0) if server.settings else 0,
            mission=mission_info,
            players=players
        )

    async def server_players(self, server_name: str) -> list[PlayerInfo]:
        """List all connected players on a DCS server."""
        server = self._resolve_server(server_name)

        players = []
        for player in server.players.values():
            if player.active:
                players.append(PlayerInfo(
                    id=player.id,
                    name=player.name,
                    ucid=player.ucid,
                    side=player.side.name.lower() if player.side else "spectator",
                    slot=player.slot if hasattr(player, 'slot') else None,
                    unit_type=player.unit_type if hasattr(player, 'unit_type') else None
                ))

        return players

    async def server_mission(self, server_name: str) -> MissionInfo:
        """Get current mission information."""
        server = self._resolve_server(server_name)

        if not server.current_mission:
            raise HTTPException(status_code=404, detail="No mission currently loaded")

        if server.status not in [Status.RUNNING, Status.PAUSED]:
            raise HTTPException(status_code=400, detail=f"Server is not running (status: {server.status.name})")

        mission = server.current_mission
        return MissionInfo(
            name=mission.name,
            filename=mission.filename,
            theatre=mission.map or "Unknown",
            start_time=seconds_to_time_str(mission.start_time),
            real_time=int(mission.real_time) if mission.real_time else 0,
            pause=server.status == Status.PAUSED
        )

    async def server_chat(
        self,
        server_name: str,
        chat: ChatMessage = Body(...)
    ) -> ChatResponse:
        """Send a chat message to the DCS server."""
        server = self._resolve_server(server_name)

        if server.status not in [Status.RUNNING, Status.PAUSED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot send chat to server that is not running (status: {server.status.name})"
            )

        try:
            await server.sendChatMessage(Coalition.ALL, chat.message, chat.sender)
            return ChatResponse(
                success=True,
                message=f"Message sent to {server.name}"
            )
        except Exception as e:
            self.log.error(f"Failed to send chat message: {e}")
            return ChatResponse(
                success=False,
                message=f"Failed to send message: {str(e)}"
            )

    # Logistics Endpoints

    async def create_logistics_task(
        self,
        server_name: str,
        task: LogisticsTaskCreate = Body(...)
    ) -> LogisticsTaskResponse:
        """Create a new logistics task and post it to Discord."""
        server = self._resolve_server(server_name)

        if server.status not in [Status.RUNNING, Status.PAUSED]:
            raise HTTPException(
                status_code=400,
                detail=f"Server is not running (status: {server.status.name})"
            )

        # Get the logistics plugin to access its event listener
        logistics_cog = self.bot.cogs.get('Logistics')
        if not logistics_cog:
            raise HTTPException(status_code=503, detail="Logistics plugin is not loaded")

        # Validate source and destination against mission airbases
        if not server.current_mission or not server.current_mission.airbases:
            raise HTTPException(status_code=400, detail="No mission loaded or no airbases available")

        source_airbase = None
        dest_airbase = None
        for ab in server.current_mission.airbases:
            if ab['name'].lower() == task.source.lower():
                source_airbase = ab
            if ab['name'].lower() == task.destination.lower():
                dest_airbase = ab

        if not source_airbase:
            raise HTTPException(status_code=400, detail=f"Source airbase '{task.source}' not found in mission")
        if not dest_airbase:
            raise HTTPException(status_code=400, detail=f"Destination airbase '{task.destination}' not found in mission")

        coalition_id = 1 if task.coalition == 'red' else 2
        source_position = json.dumps(source_airbase.get('position')) if source_airbase.get('position') else None
        dest_position = json.dumps(dest_airbase.get('position')) if dest_airbase.get('position') else None

        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    now = datetime.now(timezone.utc)
                    cursor = await conn.execute("""
                        INSERT INTO logistics_tasks
                        (server_name, created_by_ucid, status, priority, cargo_type,
                         source_name, source_position, destination_name, destination_position,
                         coalition, deadline, approved_by, approved_at, created_at, updated_at)
                        VALUES (%s, %s, 'approved', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        server.name,
                        None,  # No player UCID for API-created tasks
                        task.priority,
                        task.cargo,
                        source_airbase['name'],
                        source_position,
                        dest_airbase['name'],
                        dest_position,
                        coalition_id,
                        None,  # No deadline
                        'MCP_API',  # Approved by API
                        now,
                        now,
                        now
                    ))
                    row = await cursor.fetchone()
                    task_id = row[0]

                    # Record history
                    await conn.execute("""
                        INSERT INTO logistics_tasks_history (task_id, event, actor_discord_id, details)
                        VALUES (%s, 'created', %s, %s)
                    """, (task_id, None, '{"source": "mcp_api", "auto_approved": true}'))

            # Publish to Discord
            discord_posted = False
            try:
                task_data = {
                    'id': task_id,
                    'server_name': server.name,
                    'cargo_type': task.cargo,
                    'source_name': source_airbase['name'],
                    'destination_name': dest_airbase['name'],
                    'priority': task.priority,
                    'coalition': coalition_id,
                    'deadline': None,
                    'status': 'approved',
                    'created_at': now,
                    'discord_message_id': None
                }
                await logistics_cog.eventlistener.publish_logistics_task(task_data, 'approved')
                discord_posted = True
            except Exception as e:
                self.log.warning(f"Failed to post logistics task to Discord: {e}")

            return LogisticsTaskResponse(
                success=True,
                task_id=task_id,
                message=f"Logistics task #{task_id} created: {task.cargo} from {source_airbase['name']} to {dest_airbase['name']}",
                discord_posted=discord_posted
            )

        except Exception as e:
            self.log.error(f"Failed to create logistics task: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


async def setup(bot: DCSServerBot):
    await bot.add_cog(MCPAPI(bot))
