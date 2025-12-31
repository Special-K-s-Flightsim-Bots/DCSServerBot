import asyncio
import os
import psycopg
import random
import re

from core import Plugin, DEFAULT_TAG, Side, DataObjectFactory, utils, Status, ServiceRegistry, PluginInstallationError, \
    Server, async_cache
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from fastapi import FastAPI, APIRouter, Form, Query, HTTPException, Depends
from fastapi.security import APIKeyHeader
from plugins.creditsystem.squadron import Squadron
from plugins.userstats.filter import StatisticsFilter, PeriodFilter
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from services.servicebus import ServiceBus
from services.webservice import WebService
from typing import Any, Literal, cast

from .models import (TopKill, ServerInfo, SquadronInfo, Trueskill, Highscore, UserEntry, WeaponPK, PlayerStats,
                     CampaignCredits, TrapEntry, SquadronCampaignCredit, LinkMeResponse, ServerStats, PlayerInfo,
                     PlayerSquadron, LeaderBoard, ModuleStats, PlayerEntry, WeatherInfo, ServerAttendanceStats)
from ..srs.commands import SRS

app: FastAPI | None = None


# Bit field constants
BIT_USER_LINKED = 1
BIT_LINK_IN_PROGRESS = 2
BIT_FORCE_OPERATION = 4


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        if not os.path.exists(os.path.join(self.node.config_dir, 'services', 'webservice.yaml')):
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not configured")

        self.web_service: WebService | None = None
        self.app: FastAPI | None = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.refresh_views.add_exception_type(psycopg.DatabaseError)
        self.refresh_views.start()
        asyncio.create_task(self.init_webservice())

    async def cog_unload(self) -> None:
        self.refresh_views.cancel()
        await super().cog_unload()

    async def init_webservice(self):
        # give the webservice 10 seconds to launch on master switches
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
            return

    def register_routes(self):
        prefix = self.locals.get(DEFAULT_TAG, {}).get('prefix', '')
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

        router = APIRouter(prefix=prefix, dependencies=dependencies)
        router.add_api_route(
            "/serverstats", self.serverstats,
            methods = ["GET"],
            response_model = ServerStats,
            description = "List the statistics of a whole group",
            summary = "Server Statistics",
            tags = ["Info"]
        )
        router.add_api_route(
            "/server_attendance", self.server_attendance,
            methods = ["GET"],
            response_model = ServerAttendanceStats,
            description = "Get detailed server attendance statistics",
            summary = "Server Attendance Statistics", 
            tags = ["Info"]
        )
        router.add_api_route(
            "/servers", self.servers,
            methods = ["GET"],
            response_model = list[ServerInfo],
            description = "List all servers, the active mission (if any) and the active extensions",
            summary = "Server list",
            tags = ["Info"]
        )
        router.add_api_route(
            "/squadrons", self.squadrons,
            methods = ["GET"],
            response_model = list[SquadronInfo],
            description = "List all squadrons and their roles",
            summary = "Squadron list",
            tags = ["Info"]
        )
        router.add_api_route(
            "/squadron_members", self.squadron_members,
            methods = ["POST"],
            response_model = list[UserEntry],
            description = "List squadron members",
            summary = "Squadron Members",
            tags = ["Info"]
        )
        router.add_api_route(
            "/getuser", self.getuser,
            methods = ["POST"],
            response_model = list[UserEntry],
            description = "Get users by name",
            summary = "User list",
            tags = ["Info"]
        )
        router.add_api_route(
            "/linkme", self.linkme,
            methods=["POST"],
            response_model=LinkMeResponse,
            description="Link your Discord account to your DCS account",
            summary="Link Discord to DCS",
            tags=["Info"]
        )
        router.add_api_route(
            "/leaderboard", self.leaderboard,
            methods = ["GET"],
            response_model = LeaderBoard,
            description = "Get leaderbord information",
            summary = "Leaderboard",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/topkills", self.topkills,
            methods = ["GET"],
            response_model = list[TopKill],
            description = "Get top kills statistics for players",
            summary = "Top Kills",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/topkdr", self.topkdr,
            methods = ["GET"],
            response_model = list[TopKill],
            description = "Get top KDR statistics for players",
            summary = "Top KDR",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/trueskill", self.trueskill,
            methods = ["GET"],
            response_model = list[Trueskill],
            description = "Get TrueSkill:tm: statistics for players",
            summary = "TrueSkill:tm:",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/weaponpk", self.weaponpk,
            methods = ["POST"],
            response_model = list[WeaponPK],
            description = "Get PK statistics for all weapons of a specific players",
            summary = "Weapon PK",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/stats", self.stats,
            methods = ["POST"],
            response_model = PlayerStats,
            description = "Get player statistics",
            summary = "Player Statistics",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/modulestats", self.modulestats,
            methods = ["POST"],
            response_model = list[ModuleStats],
            description = "Get module statistics",
            summary = "Module Statistics",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/current_server", self.current_server,
            methods = ["GET"],
            response_model = str | None,
            description = "Server name a player is flying on",
            summary = "Current Server",
            tags = ["Info"]
        )
        router.add_api_route(
            "/player_info", self.player_info,
            methods = ["POST"],
            response_model = PlayerInfo,
            description = "Get player information",
            summary = "Player Information",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/highscore", self.highscore,
            methods = ["GET"],
            response_model = Highscore,
            description = "Get highscore statistics for players",
            summary = "Highscore",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/traps", self.traps,
            methods = ["POST"],
            response_model = list[TrapEntry],
            description = "Get traps for players",
            summary = "Carrier Traps",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/credits", self.credits,
            methods = ["POST"],
            response_model = CampaignCredits,
            description = "Get campaign credits for players",
            summary = "Campaign Credits",
            tags = ["Credits"]
        )
        router.add_api_route(
            "/squadron_credits", self.squadron_credits,
            methods = ["POST"],
            response_model = SquadronCampaignCredit,
            description = "Squadron campaign credits",
            summary = "Squadron Credits",
            tags = ["Credits"]
        )
        router.add_api_route(
            "/player_squadrons", self.player_squadrons,
            methods = ["POST"],
            response_model = list[PlayerSquadron],
            description = "List of player squadrons",
            summary = "Player Squadrons",
            tags = ["Info"]
        )
        self.app.include_router(router)

    def get_endpoint_config(self, endpoint: str):
        return self.get_config().get('endpoints', {}).get(endpoint, {})

    @async_cache
    async def get_ucid(self, nick: str, date: str | datetime | None = None) -> str:
        if date and isinstance(date, str):
            try:
                date = datetime.fromisoformat(date)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format")
            where = "AND DATE_TRUNC('second', last_seen) = DATE_TRUNC('second', %(date)s)"
        else:
            where = ""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    SELECT ucid
                    FROM players
                    WHERE name = %(nick)s
                    {where}
                    ORDER BY last_seen DESC
                """, {"nick": nick, "date": date})
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Player {nick} not found"
                    )
                return row['ucid']

    async def serverstats(self, server_name: str = Query(default=None)):
        self.log.debug(f'Calling /serverstats with server_name = {server_name}')
        
        # Resolve server name and get server object
        resolved_server_name, server = self.get_resolved_server(server_name)
        
        serverstats = {}
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # For mv_serverstats, we can directly filter by server_name
                if resolved_server_name:
                    where_clause = "WHERE server_name = %(server_name)s"
                    params = {"server_name": resolved_server_name}
                else:
                    where_clause = ""
                    params = {}
                    
                await cursor.execute(f"""
                    SELECT SUM("totalPlayers") AS "totalPlayers", SUM("totalPlaytime") AS "totalPlaytime",
                           SUM("avgPlaytime") AS "avgPlaytime",
                           SUM("totalSorties") AS "totalSorties", SUM("totalKills") AS "totalKills",
                           SUM("totalDeaths") AS "totalDeaths", SUM("totalPvPKills") AS "totalPvPKills",
                           SUM("totalPvPDeaths") AS "totalPvPDeaths" 
                    FROM mv_serverstats
                    {where_clause}
                """, params)
                serverstats = await cursor.fetchone()

                # Get active players count
                if server:
                    serverstats['activePlayers'] = len(server.get_active_players())
                elif resolved_server_name:
                    serverstats['activePlayers'] = 0  # Server not found but name was provided
                else:
                    # Global stats - sum all servers
                    active = sum(len(s.get_active_players()) for s in self.bot.servers.values())
                    serverstats['activePlayers'] = active

                # Get daily players trend
                if resolved_server_name:
                    join_sql = "JOIN missions m ON s.mission_id = m.id"
                    where_sql = "WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days' AND m.server_name = %(server_name)s"
                else:
                    join_sql = ""
                    where_sql = "WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days'"
                    
                await cursor.execute(f"""
                    WITH date_series AS (
                        SELECT generate_series(
                            DATE_TRUNC('day', (now() AT TIME ZONE 'utc') - interval '7 days'),
                            DATE_TRUNC('day', now() AT TIME ZONE 'utc'),
                            interval '1 day'
                        ) AS date
                    ),
                    player_counts AS (
                        SELECT DATE_TRUNC('day', s.hop_on) AS date, COUNT(DISTINCT player_ucid) as player_count
                        FROM statistics s 
                        {join_sql}
                        {where_sql}
                        GROUP BY 1
                    )
                    SELECT ds.date, COALESCE(pc.player_count, 0) as player_count
                    FROM date_series ds
                    LEFT JOIN player_counts pc ON ds.date = pc.date
                    ORDER BY ds.date
                """, {"server_name": resolved_server_name})
                serverstats['daily_players'] = await cursor.fetchall()
        return ServerStats.model_validate(serverstats)

    async def server_attendance(self, server_name: str = Query(default=None)):
        """Get detailed server attendance statistics using monitoring infrastructure patterns"""
        self.log.debug(f'Calling /server_attendance with server_name = {server_name}')
        
        # Resolve server name alias to actual name
        resolved_server_name = self.resolve_server_name(server_name)
        
        # Get current players count
        current_players = 0
        if resolved_server_name:
            server = self.bot.servers.get(resolved_server_name)
            current_players = len(server.get_active_players()) if server else 0
        else:
            for server in self.bot.servers.values():
                current_players += len(server.get_active_players())

        # Use resolved name for database queries
        where_clause = f"AND m.server_name = '{resolved_server_name}'" if resolved_server_name else ""
        
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                # Get basic statistics for different periods using monitoring plugin pattern
                periods = {
                    '24h': "s.hop_on > (now() AT TIME ZONE 'utc') - interval '24 hours'",
                    '7d': "s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days'",
                    '30d': "s.hop_on > (now() AT TIME ZONE 'utc') - interval '30 days'"
                }
                
                stats = {"current_players": current_players}
                
                for period_key, time_filter in periods.items():
                    # Use same SQL structure as ServerUsage in monitoring plugin
                    sql = f"""
                        SELECT 
                            COUNT(DISTINCT s.player_ucid) AS unique_players,
                            COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS total_playtime_hours,
                            COUNT(DISTINCT p.discord_id) AS discord_members
                        FROM statistics s
                        JOIN missions m ON m.id = s.mission_id 
                        LEFT JOIN players p ON s.player_ucid = p.ucid
                        WHERE s.hop_off IS NOT NULL
                        AND {time_filter}
                        {where_clause}
                    """
                    await cursor.execute(sql)
                    row = await cursor.fetchone()
                    
                    if row:
                        stats[f"unique_players_{period_key}"] = int(row['unique_players'] or 0)
                        stats[f"total_playtime_hours_{period_key}"] = float(row['total_playtime_hours'] or 0.0)
                        stats[f"discord_members_{period_key}"] = int(row['discord_members'] or 0)

                # Get daily trend for last 7 days (simpler version)
                await cursor.execute(f"""
                    WITH date_series AS (
                        SELECT generate_series(
                            DATE_TRUNC('day', (now() AT TIME ZONE 'utc') - interval '7 days'),
                            DATE_TRUNC('day', now() AT TIME ZONE 'utc'),
                            interval '1 day'
                        ) AS date
                    ),
                    daily_counts AS (
                        SELECT 
                            DATE_TRUNC('day', s.hop_on) as date,
                            COUNT(DISTINCT s.player_ucid) as unique_players
                        FROM statistics s 
                        JOIN missions m ON m.id = s.mission_id
                        WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days'
                        {where_clause}
                        GROUP BY 1
                    )
                    SELECT ds.date, COALESCE(dc.unique_players, 0) as unique_players
                    FROM date_series ds
                    LEFT JOIN daily_counts dc ON ds.date = dc.date
                    ORDER BY ds.date
                """)
                
                daily_data = await cursor.fetchall()
                stats["daily_trend"] = [
                    {
                        "date": row['date'].strftime("%Y-%m-%d"),
                        "unique_players": int(row['unique_players'])
                    }
                    for row in daily_data
                ]

                # Add top theatres (from TopTheatresPerServer)
                await cursor.execute(f"""
                    SELECT m.mission_theatre, 
                           COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS playtime_hours 
                    FROM missions m, statistics s
                    WHERE m.id = s.mission_id
                    AND s.hop_off IS NOT NULL
                    {where_clause}
                    GROUP BY 1
                    ORDER BY 2 DESC
                    LIMIT 5
                """)
                theatres_data = await cursor.fetchall()
                stats["top_theatres"] = [
                    {
                        "theatre": row['mission_theatre'],
                        "playtime_hours": int(row['playtime_hours'])
                    }
                    for row in theatres_data
                ]

                # Add top missions (from TopMissionPerServer)  
                await cursor.execute(f"""
                    SELECT trim(regexp_replace(m.mission_name, '{self.bot.filter['mission_name']}', ' ', 'g')) AS mission_name, 
                           ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600) AS playtime_hours 
                    FROM missions m, statistics s 
                    WHERE m.id = s.mission_id 
                    AND s.hop_off IS NOT NULL
                    {where_clause}
                    GROUP BY 1
                    ORDER BY 2 DESC
                    LIMIT 3
                """)
                missions_data = await cursor.fetchall()
                stats["top_missions"] = [
                    {
                        "mission_name": row['mission_name'],
                        "playtime_hours": int(row['playtime_hours'])
                    }
                    for row in missions_data
                ]

                # Add top modules (from TopModulesPerServer)
                await cursor.execute(f"""
                    SELECT s.slot, COUNT(s.slot) AS total_uses, 
                           COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600),0) AS playtime_hours, 
                           COUNT(DISTINCT s.player_ucid) AS unique_players 
                    FROM missions m, statistics s 
                    WHERE m.id = s.mission_id
                    AND s.hop_off IS NOT NULL
                    {where_clause}
                    GROUP BY s.slot 
                    ORDER BY 3 DESC 
                    LIMIT 10
                """)
                modules_data = await cursor.fetchall()
                stats["top_modules"] = [
                    {
                        "module": row['slot'],
                        "playtime_hours": int(row['playtime_hours']),
                        "unique_players": int(row['unique_players']),
                        "total_uses": int(row['total_uses'])
                    }
                    for row in modules_data
                ]

                # Add additional server metrics from mv_serverstats
                if resolved_server_name:
                    mv_where_clause = "WHERE server_name = %(server_name)s"
                    mv_params = {"server_name": resolved_server_name}
                else:
                    mv_where_clause = ""
                    mv_params = {}
                    
                await cursor.execute(f"""
                    SELECT SUM("totalSorties") AS total_sorties,
                           SUM("totalKills") AS total_kills,
                           SUM("totalDeaths") AS total_deaths,
                           SUM("totalPvPKills") AS total_pvp_kills,
                           SUM("totalPvPDeaths") AS total_pvp_deaths
                    FROM mv_serverstats
                    {mv_where_clause}
                """, mv_params)
                mv_row = await cursor.fetchone()
                
                if mv_row:
                    stats["total_sorties"] = int(mv_row['total_sorties'] or 0)
                    stats["total_kills"] = int(mv_row['total_kills'] or 0)
                    stats["total_deaths"] = int(mv_row['total_deaths'] or 0)
                    stats["total_pvp_kills"] = int(mv_row['total_pvp_kills'] or 0)
                    stats["total_pvp_deaths"] = int(mv_row['total_pvp_deaths'] or 0)

        return ServerAttendanceStats.model_validate(stats)

    def resolve_server_name(self, server_name: str | None) -> str | None:
        """Resolve server alias (instance name) to actual DCS server name"""
        if not server_name:
            return None
        
        # Check if it's already a full DCS server name
        if server_name in self.bot.servers:
            return server_name
            
        # Check if it's an instance name (alias) - find by instance name
        for instance_name, instance in self.bot.bus.node.instances.items():
            if instance_name == server_name and instance.server:
                return instance.server.name  # return full DCS name
        
        # Return original if not found
        return server_name

    def get_resolved_server(self, server_name: str | None) -> tuple[str | None, Server | None]:
        """
        Resolve server name and get server object.
        Returns (resolved_name, server_object)
        """
        resolved_name = self.resolve_server_name(server_name)
        server = self.bot.servers.get(resolved_name) if resolved_name else None
        return resolved_name, server

    async def get_weather_info(self, server: Server) -> WeatherInfo | None:
        """Get current weather information from DCS server"""
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            return None
            
        # Check if we have weather data from the current mission
        if not server.current_mission:
            return None
            
        if not hasattr(server.current_mission, 'weather'):
            return None
            
        if not server.current_mission.weather:
            return None
            
        try:
            weather_data = server.current_mission.weather
            
            # Extract wind data (use ground level wind by default)
            wind_data = weather_data.get('wind', {}).get('atGround', {})
            
            # Extract clouds data (it's directly in weather_data, not separate)
            clouds_data = weather_data.get('clouds', {})
            
            # Map DCS weather data to our model using actual structure
            return WeatherInfo(
                temperature=weather_data.get('season', {}).get('temperature'),
                wind_speed=wind_data.get('speed'),
                wind_direction=wind_data.get('dir'),
                pressure=weather_data.get('qnh'),  # QNH pressure in mmHg
                visibility=weather_data.get('visibility', {}).get('distance'),  # Extract distance from visibility dict
                clouds_base=clouds_data.get('base'),
                clouds_density=clouds_data.get('density'),
                precipitation=clouds_data.get('iprecptns'),  # Precipitation is in clouds data
                fog_enabled=weather_data.get('enable_fog', False),
                fog_visibility=weather_data.get('fog', {}).get('visibility') if weather_data.get('fog', {}).get('visibility') else None,
                dust_enabled=weather_data.get('enable_dust', False),
                dust_visibility=weather_data.get('dust_density') if weather_data.get('enable_dust') else None
            )
        except Exception as ex:
            self.log.warning(f"Failed to get weather info for server {server.name}: {ex}")
            return None

    async def get_srs_channels(self, server_name: str, nick: str) -> list[int]:
        srs: SRS | None = cast(SRS, self.bot.cogs.get('SRS'))
        if not srs:
            return []
        player = srs.eventlistener.srs_users.get(server_name, {}).get(nick)
        if player:
            return player.get('radios', [])
        else:
            return []

    async def servers(self, server_name: str | None = Query(default=None)) -> list[ServerInfo]:
        self.log.debug('Calling /servers')

        def filter_servers(servers: list[Server]):
            config = self.get_endpoint_config('servers')
            for s in servers:
                dirty = False
                for f in config.get('filter', []):
                    if re.match(f, s.name):
                        dirty = True
                        break
                if not dirty:
                    yield s

        servers = []
        for server in filter_servers([s for s in self.bot.servers.values() if not server_name or s.name == server_name]):
            data: dict[str, Any] = {
                'name': server.name,
                'status': server.status.value,
                'address': f"{server.node.public_ip}:{server.settings.get('port', 10308)}",
                'password': server.settings.get('password', ''),
                'restart_time': server.restart_time,
            }
            if server.current_mission:
                mission = data['mission'] = {}
                mission['name'] = server.current_mission.name
                uptime = mission['uptime'] = int(server.current_mission.mission_time)
                if isinstance(server.current_mission.date, datetime):
                    date = server.current_mission.date.timestamp()
                    real_time = date + server.current_mission.start_time + uptime
                    mission['date_time'] = str(datetime.fromtimestamp(real_time))
                else:
                    mission['date_time'] = '{} {}'.format(server.current_mission.date,
                                                          timedelta(seconds=server.current_mission.start_time + uptime))

                mission['theatre'] = server.current_mission.map
                blue = len(server.get_active_players(side=Side.BLUE))
                red = len(server.get_active_players(side=Side.RED))
                if server.current_mission.num_slots_blue:
                    mission['blue_slots'] = server.current_mission.num_slots_blue
                    mission['blue_slots_used'] = blue
                if server.current_mission.num_slots_red:
                    mission['red_slots'] = server.current_mission.num_slots_red
                    mission['red_slots_used'] = red
                if server.restart_time and not server.maintenance:
                    mission['restart_time'] = int(server.restart_time.timestamp())

            # add extensions
            if server.status in [Status.RUNNING, Status.PAUSED]:
                data['extensions'] = await server.render_extensions()
            else:
                data['extensions'] = []

            # add current players
            data['players'] = [PlayerEntry.model_validate({
                "nick": player.name,
                "side": player.side.name,
                "unit_type": player.unit_type if player.unit_type != '?' else "",
                "callsign": player.unit_callsign,
                "radios": await self.get_srs_channels(server.name, player.name)
            }) for player in server.players.values()]

            # add weather information
            config = self.get_endpoint_config('servers')
            include_weather = config.get('include_weather', True)
            if include_weather:
                data['weather'] = await self.get_weather_info(server)

            # validate the data against the schema and return it
            servers.append(ServerInfo.model_validate(data))

        return servers

    async def squadrons(self, limit: int = Query(default=None), offset: int = Query(default=0)):
        self.log.debug(f'Calling /squadrons with limit={limit}, offset={offset}')
        if limit:
            sql_part = f"LIMIT {limit} OFFSET {offset}"
        else:
            sql_part = ""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                squadrons: list[SquadronInfo] = []
                async for row in await cursor.execute(f"""
                    SELECT * FROM squadrons 
                    ORDER BY name
                    {sql_part}
                """):
                    members = await self.squadron_members(row['name'])
                    squadrons.append(SquadronInfo.model_validate({
                        "name": row['name'],
                        "description": row['description'],
                        "image_url": row['image_url'],
                        "locked": row['locked'],
                        "role": self.bot.get_role(row['role']).name if row['role'] else None,
                        "members": members
                    }))
        return squadrons

    async def leaderboard(self, what: str, order: Literal['asc', 'desc'] = 'desc', query: str | None = None,
                          limit: int | None = 10, offset: int | None = 0, server_name: str | None = None):
        columns = {
            "kills": 3,
            "kills_pvp": 4,
            "deaths": 5,
            "kdr": 6,
            "deaths_pvp": 7,
            "kdr_pvp": 8,
            "playtime": 9,
            "credits": 10
        }
        try:
            order_column = columns[what]
        except KeyError:
            raise HTTPException(status_code=400, detail="Invalid ordering column supplied")

        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if resolved_server_name:
            where = "AND s.server_name = %(server_name)s"
        else:
            where = ""

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    WITH result_with_count AS (
                        SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date", SUM(s.kills) AS "kills", 
                        SUM(s.pvp) AS "kills_pvp",
                        SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) AS "deaths", 
                        CASE WHEN SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) = 0 
                             THEN SUM(s.kills) 
                             ELSE ROUND(SUM(s.kills::DECIMAL) / SUM((s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground)::DECIMAL), 2) 
                        END AS "kdr",
                        SUM(s.deaths_pvp) AS "deaths_pvp",
                        CASE WHEN SUM(s.deaths_pvp) = 0 
                             THEN SUM(s.pvp) ELSE ROUND(SUM(s.pvp::DECIMAL) / SUM(s.deaths_pvp::DECIMAL), 2) 
                        END AS "kdr_pvp",
                        SUM(playtime)::BIGINT AS playtime,
                        MAX(COALESCE(c.points, 0)) AS "credits",
                        COUNT(s.usage) as total_count
                        FROM mv_statistics s 
                        JOIN players p ON s.player_ucid = p.ucid 
                        LEFT OUTER JOIN credits c ON c.player_ucid = s.player_ucid
                        LEFT OUTER JOIN campaigns ca ON ca.id = c.campaign_id AND NOW() AT TIME ZONE 'utc' BETWEEN ca.start AND COALESCE(ca.stop, NOW() AT TIME ZONE 'utc')
                        WHERE 1=1
                        {where}
                        GROUP BY 1, 2 
                        ORDER BY {order_column} {order} 
                        LIMIT %(limit)s
                        OFFSET %(offset)s
                    )
                    SELECT ROW_NUMBER() OVER (ORDER BY {order_column} {order}) as row_num, * 
                    FROM result_with_count
                """, {"server_name": resolved_server_name, "query": f"%{query}%", "limit": limit, "offset": offset})
                rows = await cursor.fetchall()
                if not rows:
                    return {
                        'items': [],
                        'total_count': 0,
                        'offset': 0
                    }

                # get and remove total count
                total_count = rows[0]['total_count']
                for row in rows:
                    del row['total_count']

                return LeaderBoard.model_validate({
                    'items': [row for row in rows if not query or query.casefold() in row['nick'].casefold()],
                    'total_count': total_count,
                    'offset': offset
                })

    async def topkills(self, limit: int = Query(default=10), offset: int = Query(default=0),
                       server_name: str = Query(default=None)):
        self.log.debug(f'Calling /topkills with limit={limit}, server_name={server_name}')
        return (await self.leaderboard('kills', 'desc', None, limit, offset, server_name)).items

    async def topkdr(self, limit: int = Query(default=10), offset: int = Query(default=0),
                     server_name: str = Query(default=None)):
        self.log.debug(f'Calling /topkdr with limit={limit}, server_bane={server_name}')
        return (await self.leaderboard('kdr', 'desc', None, limit, offset, server_name)).items

    async def trueskill(self, limit: int = Query(default=10), offset: int = Query(default=0),
                        server_name: str = Query(default=None)):
        self.log.debug(f'Calling /trueskill with limit={limit}, server_name={server_name}')
        
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if resolved_server_name:
            where = "AND s.server_name = %(server_name)s"
        else:
            where = ""
        
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(pvp) AS "kills_pvp", SUM(deaths_pvp) AS "deaths_pvp", t.skill_mu AS "TrueSkill" 
                    FROM mv_statistics s JOIN players p ON s.player_ucid = p.ucid
                    JOIN trueskill t ON t.player_ucid = p.ucid
                    WHERE 1=1
                    {where}
                    GROUP BY 1, 2, 5 ORDER BY 5 DESC 
                    LIMIT {limit} OFFSET {offset}
                """, {"server_name": resolved_server_name})
                return [Trueskill.model_validate(result) for result in await cursor.fetchall()]

    async def highscore(self, server_name: str = Query(default=None), period: str = Query(default='all'),
                        limit: int = Query(default=10)):
        self.log.debug(f'Calling /highscore with server_name="{server_name}", period="{period}", limit={limit}')
        highscore = {}
        flt = StatisticsFilter.detect(self.bot, period) or PeriodFilter(period)
        
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                sql = """
                      SELECT p.name AS nick, DATE_TRUNC('second', p.last_seen) AS "date",
                             ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::BIGINT AS playtime
                      FROM statistics s,
                           players p,
                           missions m
                      WHERE p.ucid = s.player_ucid
                        AND s.mission_id = m.id
                      """
                if resolved_server_name:
                    sql += "AND m.server_name = %(server_name)s"
                sql += ' AND ' + flt.filter(self.bot)
                sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'
                await cursor.execute(sql, {"server_name": resolved_server_name})
                highscore['playtime'] = await cursor.fetchall()

                sql_parts = {
                    'Air Targets': 'SUM(s.kills_planes+s.kills_helicopters)',
                    'Ships': 'SUM(s.kills_ships)',
                    'Air Defence': 'SUM(s.kills_sams)',
                    'Ground Targets': 'SUM(s.kills_ground)',
                    'KD-Ratio': 'CASE WHEN SUM(deaths_planes + deaths_helicopters + deaths_ships + deaths_sams + '
                                'deaths_ground) = 0 THEN SUM(s.kills) ELSE SUM(s.kills::DECIMAL)/SUM((deaths_planes + '
                                'deaths_helicopters + deaths_ships + deaths_sams + deaths_ground)::DECIMAL) END',
                    'PvP-KD-Ratio': 'CASE WHEN SUM(s.deaths_pvp) = 0 THEN SUM(s.pvp) ELSE SUM(s.pvp::DECIMAL)/SUM('
                                    's.deaths_pvp::DECIMAL) END',
                    'Most Efficient Killers': 'SUM(s.kills::DECIMAL) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))::DECIMAL / 3600.0)',
                    'Most Wasteful Pilots': 'SUM(s.crashes::DECIMAL) / (SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on)))::DECIMAL / 3600.0)'
                }

                for kill_type in sql_parts.keys():
                    sql = f"""
                        SELECT p.name AS nick, DATE_TRUNC('second', p.last_seen) AS "date",
                               {sql_parts[kill_type]} AS value 
                        FROM players p, statistics s, missions m 
                        WHERE s.player_ucid = p.ucid AND s.mission_id = m.id
                    """
                    if resolved_server_name:
                        sql += "AND m.server_name = %(server_name)s"
                    sql += ' AND ' + flt.filter(self.bot)
                    # only flighttimes of over an hour count for most efficient / wasteful
                    if not (flt.period and (flt.period in ['day', 'today', 'yesterday'] or flt.period.startswith(
                            'mission_id:'))) and kill_type in ['Most Efficient Killers', 'Most Wasteful Pilots']:
                        sql += f" AND EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)) >= 3600"
                    sql += f' GROUP BY 1, 2 HAVING {sql_parts[kill_type]} > 0'
                    sql += f' ORDER BY 3 DESC LIMIT {limit}'

                    await cursor.execute(sql, {"server_name": resolved_server_name})
                    highscore[kill_type] = await cursor.fetchall()

        return Highscore.model_validate(highscore)

    async def getuser(self, nick: str = Form(...)):
        self.log.debug(f'Calling /getuser with nick="{nick}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT 
                        name AS "nick", 
                        DATE_TRUNC('second', last_seen) AS "date" 
                    FROM players 
                    WHERE name ILIKE %s 
                    ORDER BY 2 DESC
                """, ('%' + nick + '%',))
                return [UserEntry.model_validate({
                    "nick": result["nick"],
                    "date": result["date"],
                    "current_server": await self.current_server(result["nick"], result["date"])
                }) for result in await cursor.fetchall()]

    async def weaponpk(self, nick: str = Form(...), date: str | None = Form(None),
                       server_name: str | None = Form(None)):
        self.log.debug(f'Calling /weaponpk with nick="{nick}", date="{date}", server_name="{server_name}"')
        ucid = await self.get_ucid(nick, date)
        
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if resolved_server_name:
            join = "JOIN missions m ON ms.mission_id = m.id"
            where = "WHERE init_id = %(ucid)s AND weapon IS NOT NULL AND m.server_name = %(server_name)s"
        else:
            join = ""
            where = "WHERE init_id = %(ucid)s AND weapon IS NOT NULL"
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    SELECT x.weapon, x.shots, x.hits, 
                           ROUND(CASE WHEN x.shots = 0 THEN 0 ELSE x.hits / x.shots::DECIMAL END, 2) AS "pk"
                    FROM (
                        SELECT weapon, SUM(CASE WHEN event='S_EVENT_SHOT' THEN 1 ELSE 0 END) AS "shots", 
                               SUM(CASE WHEN event='S_EVENT_HIT' THEN 1 ELSE 0 END) AS "hits" 
                        FROM missionstats ms
                        {join}
                        {where}
                        GROUP BY weapon
                    ) x
                    ORDER BY 2 DESC
                """, {"ucid": ucid, "server_name": resolved_server_name})
                return [WeaponPK.model_validate(result) for result in await cursor.fetchall()]

    async def stats(self, nick: str = Form(...), date: str | None = Form(None),
                    server_name: str | None = Form(None), last_session: bool | None = Form(False)):
        self.log.debug(f'Calling /stats with nick="{nick}", date="{date}", server_name="{server_name}", '
                       f'last_session="{last_session}"')

        ucid = await self.get_ucid(nick, date)
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if last_session:
            if resolved_server_name:
                where = "AND m.server_name = %(server_name)s"
            else:
                where = ""
            query = f"""
                SELECT COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on)))), 0)::BIGINT AS playtime, 
                       COALESCE(SUM(s.kills), 0) as "kills", 
                       COALESCE(SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground), 0) AS "deaths", 
                       COALESCE(SUM(s.pvp), 0) AS "kills_pvp", 
                       COALESCE(SUM(s.deaths_pvp), 0) AS "deaths_pvp",
                       COALESCE(SUM(s.kills_sams), 0) AS "kills_sams",
                       COALESCE(SUM(s.kills_ships), 0) AS "kills_ships",
                       COALESCE(SUM(s.kills_ground), 0) AS "kills_ground",
                       COALESCE(SUM(s.kills_planes), 0) AS "kills_planes",
                       COALESCE(SUM(s.kills_helicopters), 0) AS "kills_helicopters",
                       COALESCE(SUM(s.deaths_sams), 0) AS "deaths_sams",
                       COALESCE(SUM(s.deaths_ships), 0) AS "deaths_ships",
                       COALESCE(SUM(s.deaths_ground), 0) AS "deaths_ground",
                       COALESCE(SUM(s.deaths_planes), 0) AS "deaths_planes",
                       COALESCE(SUM(s.deaths_helicopters), 0) AS "deaths_helicopters",
                       COALESCE(SUM(s.takeoffs), 0) AS "takeoffs", 
                       COALESCE(SUM(s.landings), 0) AS "landings", 
                       COALESCE(SUM(s.ejections), 0) AS "ejections",
                       COALESCE(SUM(s.crashes), 0) AS "crashes", 
                       COALESCE(SUM(s.teamkills), 0) AS "teamkills"
                FROM statistics s JOIN missions m ON s.mission_id = m.id
                WHERE s.player_ucid = %(ucid)s
                {where}
            """
            if resolved_server_name:
                inner_query = f"AND m2.server_name = %(server_name)s"
            else:
                inner_query = ""
            query += f"""
                AND (s.player_ucid, s.mission_id) = (
                    SELECT player_ucid, max(mission_id) 
                    FROM statistics s1 JOIN missions m2 ON s1.mission_id = m2.id 
                    {inner_query}
                    WHERE player_ucid = %(ucid)s 
                    GROUP BY 1
                )
            """
        else:
            if resolved_server_name:
                where = "AND s.server_name = %(server_name)s"
            else:
                where = ""

            query = f"""
                SELECT COALESCE(SUM(playtime), 0)::BIGINT AS playtime, 
                       COALESCE(SUM(s.kills), 0) as "kills", 
                       COALESCE(SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground), 0) AS "deaths", 
                       COALESCE(SUM(s.pvp), 0) AS "kills_pvp", 
                       COALESCE(SUM(s.deaths_pvp), 0) AS "deaths_pvp",
                       COALESCE(SUM(s.kills_sams), 0) AS "kills_sams",
                       COALESCE(SUM(s.kills_ships), 0) AS "kills_ships",
                       COALESCE(SUM(s.kills_ground), 0) AS "kills_ground",
                       COALESCE(SUM(s.kills_planes), 0) AS "kills_planes",
                       COALESCE(SUM(s.kills_helicopters), 0) AS "kills_helicopters",
                       COALESCE(SUM(s.deaths_sams), 0) AS "deaths_sams",
                       COALESCE(SUM(s.deaths_ships), 0) AS "deaths_ships",
                       COALESCE(SUM(s.deaths_ground), 0) AS "deaths_ground",
                       COALESCE(SUM(s.deaths_planes), 0) AS "deaths_planes",
                       COALESCE(SUM(s.deaths_helicopters), 0) AS "deaths_helicopters",
                       COALESCE(SUM(s.takeoffs), 0) AS "takeoffs", 
                       COALESCE(SUM(s.landings), 0) AS "landings", 
                       COALESCE(SUM(s.ejections), 0) AS "ejections",
                       COALESCE(SUM(s.crashes), 0) AS "crashes", 
                       COALESCE(SUM(s.teamkills), 0) AS "teamkills"
                FROM mv_statistics s
                WHERE s.player_ucid = %(ucid)s
                {where}
            """

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"ucid": ucid, "server_name": resolved_server_name})
                data = await cursor.fetchone()
                if data:
                    data['kdr'] = round(data['kills'] / data['deaths'] if data['deaths'] > 0 else data['kills'], 2)
                    data['kdr_pvp'] = round(data['kills_pvp'] / data['deaths_pvp'] if data['deaths_pvp'] > 0 else data['kills_pvp'], 2)

                await cursor.execute("""
                                     SELECT slot AS "module", SUM(kills) AS "kills"
                                     FROM mv_statistics
                                     WHERE player_ucid = %s
                                     GROUP BY 1
                                     HAVING SUM(kills) > 1
                                     ORDER BY 2 DESC
                                     """, (ucid,))
                data['killsByModule'] = await cursor.fetchall()
                await cursor.execute("""
                                     SELECT slot AS "module",
                                            CASE
                                                WHEN SUM(deaths) = 0 THEN SUM(kills)
                                                ELSE ROUND(SUM(kills) / SUM(deaths::DECIMAL), 2) END AS "kdr"
                                     FROM mv_statistics
                                     WHERE player_ucid = %s
                                     GROUP BY 1
                                     HAVING SUM(kills) > 1
                                     ORDER BY 2 DESC
                                     """, (ucid,))
                data['kdrByModule'] = await cursor.fetchall()

        return PlayerStats.model_validate(data)

    async def modulestats(self, nick: str = Form(...), date: str | None = Form(None),
                          server_name: str | None = Form(None)):
        self.log.debug(f'Calling /modulestats with nick="{nick}", date="{date}", server_name="{server_name}"')

        ucid = await self.get_ucid(nick, date)
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if resolved_server_name:
            where = "AND s.server_name = %(server_name)s"
        else:
            where = ""
        query = f"""
            SELECT s.slot AS "module", 
                   SUM(s.kills) AS "kills",
                   SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) AS "deaths",
                   CASE WHEN SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) = 0 
                        THEN SUM(s.kills) ELSE SUM(s.kills)::DECIMAL / SUM((s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground)::DECIMAL) END AS "kdr" 
            FROM mv_statistics s
            WHERE s.player_ucid = %(ucid)s 
            {where}
            GROUP BY 1 HAVING SUM(s.kills) > 0 
            ORDER BY 2 DESC
        """
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, {"ucid": ucid, "server_name": resolved_server_name})
                return [ModuleStats.model_validate(result) for result in await cursor.fetchall()]

    async def current_server(self, nick: str = Query(...), date: str | None = Query(None)) -> str | None:
        ucid = await self.get_ucid(nick, date)
        bus = ServiceRegistry.get(ServiceBus)
        for server in bus.servers.values():
            if server.get_player(ucid=ucid, active=True) is not None:
                return server.name
        return None

    async def player_info(self, nick: str = Form(...), date: str | None = Form(None),
                          server_name: str | None = Form(None)):
        self.log.debug(f'Calling /player_info with nick="{nick}", date="{date}", server_name="{server_name}"')
        player_info: dict[str, Any] = {
            'current_server': await self.current_server(nick, date),
            'overall': dict(await self.stats(nick, date, server_name, last_session=False)),
            'last_session': dict(await self.stats(nick, date, server_name, last_session=True)),
            'module_stats': await self.modulestats(nick, date, server_name)
        }
        # add credits
        try:
            player_info['credits'] = await self.credits(nick, date, None)
        except HTTPException:
            player_info['credits'] = None
        # add squadrons
        player_info['squadrons'] = await self.player_squadrons(nick, date)
        return PlayerInfo.model_validate(player_info)

    async def player_squadrons(self, nick: str = Form(...), date: str | None = Form(None)):
        self.log.debug(f'Calling /player_squadrons with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                await cursor.execute("""
                    SELECT name, image_url
                    FROM squadrons s JOIN squadron_members sm ON sm.squadron_id = s.id
                    WHERE sm.player_ucid = %s
                """, (ucid, ))
                return [PlayerSquadron.model_validate(result) for result in await cursor.fetchall()]

    async def credits(self, nick: str = Form(...), date: str | None = Form(None),
                      campaign: str | None = Form(default=None)):
        self.log.debug(f'Calling /credits with nick="{nick}", date="{date}", campaign="{campaign}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                # if a campaign was passed, use that, else use the current running campaign
                if campaign:
                    where = "WHERE c.name = %(campaign)s"
                else:
                    where = "WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc')"
                await cursor.execute(f"""
                    SELECT c.id, c.name, b.badge_name AS rank, b.badge_url AS badge, 
                           COALESCE(SUM(s.points), 0) AS credits 
                    FROM campaigns c 
                    LEFT OUTER JOIN credits s ON (c.id = s.campaign_id AND s.player_ucid = %(ucid)s) 
                    LEFT OUTER JOIN players_badges b ON (c.id = b.campaign_id AND b.player_ucid = %(ucid)s)
                    {where}
                    GROUP BY 1, 2, 3, 4
                """, {"ucid": ucid, "campaign": campaign})
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No credits found for player {nick}"
                    )
                return CampaignCredits.model_validate(row)

    async def traps(self, nick: str = Form(None), date: str | None = Form(None),
                    limit: int | None = Form(10), offset: int | None = Form(0),
                    server_name: str | None = Form(None)):
        self.log.debug(f'Calling /traps with nick="{nick}", date="{date}", server_name="{server_name}"')
        
        # Use centralized server resolution
        resolved_server_name, _ = self.get_resolved_server(server_name)
        
        if resolved_server_name:
            join = "JOIN missions m ON t.mission_id = m.id"
            where = "WHERE t.player_ucid = %(ucid)s AND m.server_name = %(server_name)s"
        else:
            join = ""
            where = "WHERE t.player_ucid = %(ucid)s"
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                await cursor.execute(f"""
                    SELECT t.unit_type, t.grade, t.comment, t.place, t.trapcase, t.wire, t.night, t.points, t.time
                    FROM traps t
                    {join}
                    {where}
                    ORDER BY time DESC 
                    LIMIT {limit} OFFSET {offset}
                """, {"ucid": ucid, "server_name": resolved_server_name})
                return [TrapEntry.model_validate(result) for result in await cursor.fetchall()]

    async def squadron_members(self, name: str = Form(...)):
        self.log.debug(f'Calling /squadron_members with name="{name}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date"  
                    FROM players p JOIN squadron_members sm ON sm.player_ucid = p.ucid
                                   JOIN squadrons s ON sm.squadron_id = s.id
                    WHERE s.name = %s
                """, (name, ))
                return [UserEntry.model_validate({
                    "nick": result["nick"],
                    "date": result["date"],
                    "current_server": await self.current_server(result["nick"], result["date"])
                }) for result in await cursor.fetchall()]

    async def squadron_credits(self, name: str = Form(...), campaign: str = Form(default=None)):
        self.log.debug(f'Calling /squadron_credits with name="{name}"')
        # if a campaign was passed, use that, else use the current running campaign
        if campaign:
            where = "WHERE c.name = %(campaign)s"
        else:
            where = "WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc')"
        async with self.apool.connection() as conn:
            cursor = await conn.execute(f"""
                SELECT c.id, c.name
                FROM campaigns c 
                LEFT OUTER JOIN squadron_credits s ON c.id = s.campaign_id
                JOIN squadrons s2 ON s.squadron_id = s2.id
                {where} 
                AND s2.name = %(name)s
            """, {"name": name, "campaign": campaign})
            row = await cursor.fetchone()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Squadron {name} does not have any credits"
                )
            squadron = utils.get_squadron(node=self.node, name=name)
            squadron_obj = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                                   campaign_id=row[0])
            return SquadronCampaignCredit.model_validate({"campaign": row[1], "credits": squadron_obj.points})

    async def linkme(self,
                     discord_id: str = Form(..., description="Discord user ID (snowflake)",
                                            examples=["123456789012345678"]),
                     force: bool = Form(False, description="Force the operation")):

        async def create_token() -> str:
            while True:
                try:
                    token = str(random.randint(1000, 9999))
                    cursor.execute("""
                        INSERT INTO players (ucid, discord_id, last_seen)
                        VALUES (%s, %s, NOW() AT TIME ZONE 'utc')
                    """, (token, discord_id))
                    return token
                except psycopg.errors.UniqueViolation:
                    pass

        self.log.debug(f'Calling /link with discord_id="{discord_id}", force="{force}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:

                # Check if discord_id exists
                await cursor.execute("SELECT ucid, last_seen FROM players WHERE discord_id = %s", (discord_id,))
                result = await cursor.fetchone()

                rc = 0
                token = None
                now = datetime.now(tz=timezone.utc)

                if result:
                    ucid, last_seen = result

                    if len(ucid) == 4:  # UCID is stored as a 4-character string
                        # Linking already in progress
                        token = ucid
                        rc |= BIT_LINK_IN_PROGRESS
                        if force:
                            rc |= BIT_FORCE_OPERATION
                            cursor.execute("""
                                UPDATE players 
                                SET last_seen = (NOW() AT TIME ZONE 'utc') 
                                WHERE discord_id = %s
                            """, (discord_id, ))
                            expiry_timestamp = (now + timedelta(hours=48)).isoformat()
                        else:
                            expiry_timestamp = (last_seen + timedelta(hours=48)).isoformat()
                    else:
                        # User already linked
                        rc |= BIT_USER_LINKED
                        if force:
                            rc |= BIT_FORCE_OPERATION
                            token = await create_token()
                            expiry_timestamp = (now + timedelta(hours=48)).isoformat()
                        else:
                            expiry_timestamp = None
                else:
                    token = await create_token()
                    expiry_timestamp = (datetime.now() + timedelta(hours=48)).isoformat()
                    # Set bit_field for a new user
                    rc = 0  # Default bit_field for new user
                    if force:
                        rc |= BIT_FORCE_OPERATION  # Set force operation flag

        return LinkMeResponse.model_validate({
            "token": token,
            "timestamp": expiry_timestamp,
            "rc": rc
        })

    @tasks.loop(hours=1)
    async def refresh_views(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_serverstats;
                """)

    @refresh_views.before_loop
    async def before_refresh_views(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    await bot.add_cog(RestAPI(bot))
