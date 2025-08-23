import psycopg
import random
import re

from core import Plugin, DEFAULT_TAG, Side, DataObjectFactory, utils, Status, ServiceRegistry, PluginInstallationError, \
    Server
from datetime import datetime, timedelta, timezone
from discord.ext import tasks
from fastapi import FastAPI, APIRouter, Form, Query, HTTPException
from plugins.creditsystem.squadron import Squadron
from plugins.userstats.filter import StatisticsFilter, PeriodFilter
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from services.webservice import WebService
from typing import Optional, Any, Union, Literal

from .models import (TopKill, ServerInfo, SquadronInfo, Trueskill, Highscore, UserEntry, WeaponPK, PlayerStats,
                     CampaignCredits, TrapEntry, SquadronCampaignCredit, LinkMeResponse, ServerStats, PlayerInfo,
                     PlayerSquadron, LeaderBoard)

app: Optional[FastAPI] = None


# Bit field constants
BIT_USER_LINKED = 1
BIT_LINK_IN_PROGRESS = 2
BIT_FORCE_OPERATION = 4


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.web_service = ServiceRegistry.get(WebService)
        if not self.web_service.is_running():
            raise PluginInstallationError(plugin=self.plugin_name, reason="WebService is not running")

        self.app = self.web_service.app
        self.register_routes()

    def register_routes(self):
        prefix = self.locals.get(DEFAULT_TAG, {}).get('prefix', '')
        router = APIRouter(prefix=prefix)
        router.add_api_route(
            "/serverstats", self.serverstats,
            methods = ["GET"],
            response_model = ServerStats,
            description = "List the statistics of a whole group",
            summary = "Server Statistics",
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

    async def cog_load(self) -> None:
        await super().cog_load()
        self.refresh_views.start()

    async def cog_unload(self) -> None:
        self.refresh_views.cancel()
        await super().cog_unload()

    def get_endpoint_config(self, endpoint: str):
        return self.get_config().get('endpoints', {}).get(endpoint, {})

    async def get_ucid(self, nick: str, date: Optional[Union[str, datetime]] = None) -> str:
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
        serverstats = {}
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                if server_name:
                    where = "WHERE server_name = %(server_name)s"
                else:
                    where = ""
                await cursor.execute(f"""
                    SELECT SUM("totalPlayers") AS "totalPlayers", SUM("totalPlaytime") AS "totalPlaytime",
                           SUM("avgPlaytime") AS "avgPlaytime", SUM("activePlayers") AS "activePlayers",
                           SUM("totalSorties") AS "totalSorties", SUM("totalKills") AS "totalKills",
                           SUM("totalDeaths") AS "totalDeaths", SUM("totalPvPKills") AS "totalPvPKills",
                           SUM("totalPvPDeaths") AS "totalPvPDeaths" 
                    FROM mv_serverstats
                    {where}
                """, {"server_name": server_name})
                serverstats = await cursor.fetchone()

                if server_name:
                    join = f"JOIN missions m ON s.mission_id = m.id AND m.server_name = %(server_name)s"
                else:
                    join = ""
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
                        {join}
                        WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days'
                        GROUP BY 1
                    )
                    SELECT ds.date, COALESCE(pc.player_count, 0) as player_count
                    FROM date_series ds
                    LEFT JOIN player_counts pc ON ds.date = pc.date
                    ORDER BY ds.date
                """, {"server_name": server_name})
                serverstats['daily_players'] = await cursor.fetchall()
        return ServerStats.model_validate(serverstats)

    async def servers(self):
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
        for server in filter_servers(list(self.bot.servers.values())):
            data: dict[str, Any] = {
                'name': server.name,
                'status': server.status.value,
                'address': f"{server.node.public_ip}:{server.settings.get('port', 10308)}",
                'password': server.settings.get('password', '')
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

    async def leaderboard(self, what: str, order: Literal['asc', 'desc'] = 'desc', query: Optional[str] = None,
                          limit: Optional[int] = 10, offset: Optional[int] = 0, server_name: Optional[str] = None):
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

        if server_name:
            join = "JOIN missions m ON s.mission_id = m.id AND m.server_name = %(server_name)s"
        else:
            join = ""

        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    WITH result_with_count AS (
                        SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date", SUM(s.kills) AS "kills", 
                        SUM(s.pvp) AS "kills_pvp",
                        SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) AS "deaths", 
                        CASE WHEN SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) = 0 
                             THEN SUM(s.kills) 
                             ELSE SUM(s.kills::DECIMAL) / SUM((s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground)::DECIMAL) 
                        END AS "kdr",
                        SUM(s.deaths_pvp) AS "deaths_pvp",
                        CASE WHEN SUM(s.deaths_pvp) = 0 
                             THEN SUM(s.pvp) ELSE SUM(s.pvp::DECIMAL) / SUM(s.deaths_pvp::DECIMAL) 
                        END AS "kdr_pvp",
                        ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS playtime,
                        MAX(COALESCE(c.points, 0)) AS "credits",
                        COUNT(*) OVER() as total_count
                        FROM statistics s 
                        JOIN players p ON s.player_ucid = p.ucid 
                        {join}
                        LEFT OUTER JOIN credits c ON c.player_ucid = s.player_ucid
                        LEFT OUTER JOIN campaigns ca ON ca.id = c.campaign_id AND NOW() AT TIME ZONE 'utc' BETWEEN ca.start AND COALESCE(ca.stop, NOW() AT TIME ZONE 'utc')
                        GROUP BY 1, 2 
                        ORDER BY {order_column} {order} 
                        LIMIT %(limit)s
                        OFFSET %(offset)s
                    )
                    SELECT ROW_NUMBER() OVER (ORDER BY {order_column} {order}) as row_num, * 
                    FROM result_with_count
                """, {"server_name": server_name, "query": f"%{query}%", "limit": limit, "offset": offset})
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
        if server_name:
            join = "JOIN missions m ON s.mission_id = m.id AND m.server_name = %(server_name)s"
        else:
            join = ""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(f"""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(pvp) AS "kills_pvp", SUM(deaths_pvp) AS "deaths_pvp", t.skill_mu AS "TrueSkill" 
                    FROM statistics s JOIN players p ON s.player_ucid = p.ucid
                    JOIN trueskill t ON t.player_ucid = p.ucid
                    {join}
                    WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1, 2, 5 ORDER BY 5 DESC 
                    LIMIT {limit} OFFSET {offset}
                """, {"server_name": server_name})
                return [Trueskill.model_validate(result) for result in await cursor.fetchall()]

    async def highscore(self, server_name: str = Query(default=None), period: str = Query(default='all'),
                        limit: int = Query(default=10)):
        self.log.debug(f'Calling /highscore with server_name="{server_name}", period="{period}", limit={limit}')
        highscore = {}
        flt = StatisticsFilter.detect(self.bot, period) or PeriodFilter(period)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                sql = """
                      SELECT p.name AS nick, DATE_TRUNC('second', p.last_seen) AS "date",
                             ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS playtime
                      FROM statistics s,
                           players p,
                           missions m
                      WHERE p.ucid = s.player_ucid
                        AND s.mission_id = m.id
                      """
                if server_name:
                    sql += "AND m.server_name = %(server_name)s"
                sql += ' AND ' + flt.filter(self.bot)
                sql += f' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}'
                await cursor.execute(sql, {"server_name": server_name})
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
                    if server_name:
                        sql += "AND m.server_name = %(server_name)s"
                    sql += ' AND ' + flt.filter(self.bot)
                    sql += f' GROUP BY 1, 2 HAVING {sql_parts[kill_type]} > 0'
                    if kill_type in ['Most Efficient Killers', 'Most Wasteful Pilots']:
                        sql += f" AND SUM(EXTRACT(EPOCH FROM (COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))) > 1800"
                    sql += f' ORDER BY 3 DESC LIMIT {limit}'

                    await cursor.execute(sql, {"server_name": server_name})
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
                return [UserEntry.model_validate(result) for result in await cursor.fetchall()]

    async def weaponpk(self, nick: str = Form(...), date: Optional[str] = Form(None),
                       server_name: Optional[str] = Form(None)):
        self.log.debug(f'Calling /weaponpk with nick="{nick}", date="{date}", server_name="{server_name}"')
        ucid = await self.get_ucid(nick, date)
        if server_name:
            join = "JOIN missions m ON ms.mission_id = m.id AND m.server_name = %(server_name)s"
        else:
            join = ""
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
                        WHERE init_id = %(ucid)s AND weapon IS NOT NULL
                        GROUP BY weapon
                    ) x
                    ORDER BY 2 DESC
                """, {"ucid": ucid, "server_name": server_name})
                return [WeaponPK.model_validate(result) for result in await cursor.fetchall()]

    async def stats(self, nick: str = Form(...), date: Optional[str] = Form(None),
                    server_name: Optional[str] = Form(None)):
        self.log.debug(f'Calling /stats with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                if server_name:
                    join = "JOIN missions m ON s.mission_id = m.id AND m.server_name = %(server_name)s"
                else:
                    join = ""
                await cursor.execute(f"""
                    SELECT COALESCE(overall.playtime, 0) AS playtime, 
                           COALESCE(overall.kills, 0) as kills, 
                           COALESCE(overall.deaths, 0) AS deaths, 
                           COALESCE(overall.kills_pvp, 0) AS kills_pvp, 
                           COALESCE(overall.deaths_pvp, 0) AS deaths_pvp,
                           COALESCE(overall.kills_ground, 0) AS kills_ground,
                           COALESCE(overall.kills_planes, 0) AS kills_planes,
                           COALESCE(overall.kills_helicopters, 0) AS kills_helicopters,
                           COALESCE(overall.kills_ships, 0) AS kills_ships,
                           COALESCE(overall.kills_sams, 0) AS kills_sams, 
                           COALESCE(overall.deaths_ground, 0) AS deaths_ground,
                           COALESCE(overall.deaths_planes, 0) AS deaths_planes,
                           COALESCE(overall.deaths_helicopters, 0) AS deaths_helicopters,
                           COALESCE(overall.deaths_ships, 0) AS deaths_ships,
                           COALESCE(overall.deaths_sams, 0) AS deaths_sams, 
                           COALESCE(overall.deaths_ground, 0) AS deaths_ground,
                           COALESCE(overall.takeoffs, 0) AS takeoffs, 
                           COALESCE(overall.landings, 0) AS landings, 
                           COALESCE(overall.ejections, 0) AS ejections, 
                           COALESCE(overall.crashes, 0) AS crashes, 
                           COALESCE(overall.teamkills, 0) AS teamkills, 
                           COALESCE(ROUND(CASE WHEN overall.deaths = 0 
                                      THEN overall.kills 
                                      ELSE overall.kills/overall.deaths::DECIMAL END, 2), 0) AS "kdr", 
                           COALESCE(ROUND(CASE WHEN overall.deaths_pvp = 0 
                                      THEN overall.kills_pvp 
                                      ELSE overall.kills/overall.deaths_pvp::DECIMAL END, 2), 0) AS "kdr_pvp", 
                           COALESCE(lastsession.kills, 0) AS "lastSessionKills", COALESCE(lastsession.deaths, 0) AS "lastSessionDeaths"
                    FROM (
                        SELECT ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS playtime, 
                               SUM(s.kills) as "kills", 
                               SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) AS "deaths", 
                               SUM(s.pvp) AS "kills_pvp", 
                               SUM(s.deaths_pvp) AS "deaths_pvp",
                               SUM(s.kills_sams) AS "kills_sams",
                               SUM(s.kills_ships) AS "kills_ships",
                               SUM(s.kills_ground) AS "kills_ground",
                               SUM(s.kills_planes) AS "kills_planes",
                               SUM(s.kills_helicopters) AS "kills_helicopters",
                               SUM(s.deaths_sams) AS "deaths_sams",
                               SUM(s.deaths_ships) AS "deaths_ships",
                               SUM(s.deaths_ground) AS "deaths_ground",
                               SUM(s.deaths_planes) AS "deaths_planes",
                               SUM(s.deaths_helicopters) AS "deaths_helicopters",
                               SUM(s.takeoffs) AS "takeoffs", 
                               SUM(s.landings) AS "landings", 
                               SUM(s.ejections) AS "ejections",
                               SUM(s.crashes) AS "crashes", 
                               SUM(s.teamkills) AS "teamkills"
                        FROM statistics s
                        {join}
                        WHERE s.player_ucid = %(ucid)s
                    ) overall, (
                        SELECT SUM(kills) AS "kills", SUM(deaths) AS "deaths"
                        FROM statistics
                        WHERE (player_ucid, mission_id) = (
                            SELECT player_ucid, max(mission_id) FROM statistics WHERE player_ucid = %(ucid)s GROUP BY 1
                        )
                    ) lastsession
                """, {"ucid": ucid, "server_name": server_name})
                data = await cursor.fetchone()
                await cursor.execute(f"""
                    SELECT s.slot AS "module", 
                           SUM(s.kills) AS "kills",
                           SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) AS "deaths",
                           CASE WHEN SUM(s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground) = 0 
                                THEN SUM(s.kills) ELSE SUM(s.kills)::DECIMAL / SUM((s.deaths_planes + s.deaths_helicopters + s.deaths_ships + s.deaths_sams + s.deaths_ground)::DECIMAL) END AS "kdr" 
                    FROM statistics s
                    {join}
                    WHERE player_ucid = %(ucid)s 
                    GROUP BY 1 HAVING SUM(kills) > 1 
                    ORDER BY 2 DESC
                """, {"ucid": ucid, "server_name": server_name})
                data['module_stats'] = await cursor.fetchall()
                return PlayerStats.model_validate(data)

    async def player_info(self, nick: str = Form(...), date: Optional[str] = Form(None),
                          server_name: Optional[str] = Form(None)):
        self.log.debug(f'Calling /player_info with nick="{nick}", date="{date}", server_name="{server_name}"')
        player_info = dict(await self.stats(nick, date, server_name))
        # add credits
        try:
            player_info['credits'] = await self.credits(nick, date, None)
        except HTTPException:
            player_info['credits'] = None
        # add squadrons
        player_info['squadrons'] = await self.player_squadrons(nick, date)
        return PlayerInfo.model_validate(player_info)

    async def player_squadrons(self, nick: str = Form(...), date: Optional[str] = Form(None)):
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

    async def credits(self, nick: str = Form(...), date: Optional[str] = Form(None),
                      campaign: Optional[str] = Form(default=None)):
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
                    SELECT c.id, c.name, COALESCE(SUM(s.points), 0) AS credits 
                    FROM campaigns c LEFT OUTER JOIN credits s 
                    ON (c.id = s.campaign_id AND s.player_ucid = %(ucid)s) 
                    {where}
                    GROUP BY 1, 2
                """, {"ucid": ucid, "campaign": campaign})
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"No credits found for player {nick}"
                    )
                return CampaignCredits.model_validate(row)

    async def traps(self, nick: str = Form(None), date: Optional[str] = Form(None),
                    limit: Optional[int] = Form(10), offset: Optional[int] = Form(0),
                    server_name: Optional[str] = Form(None)):
        self.log.debug(f'Calling /traps with nick="{nick}", date="{date}", server_name="{server_name}"')
        if server_name:
            join = "JOIN missions m ON t.mission_id = m.id AND m.server_name = %(server_name)s"
        else:
            join = ""
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                await cursor.execute(f"""
                    SELECT t.unit_type, t.grade, t.comment, t.place, t.trapcase, t.wire, t.night, t.points, t.time
                    FROM traps t
                    {join}
                    WHERE t.player_ucid = %(ucid)s
                    ORDER BY time DESC 
                    LIMIT {limit} OFFSET {offset}
                """, {"ucid": ucid, "server_name": server_name})
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
                return [UserEntry.model_validate(result) for result in await cursor.fetchall()]

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
                     discord_id: str = Form(..., description="Discord user ID (snowflake)", example="123456789012345678"),
                     force: bool = Form(False, description="Force the operation", example=True)):

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
                    REFRESH MATERIALIZED VIEW mv_serverstats;
                """)


async def setup(bot: DCSServerBot):
    await bot.add_cog(RestAPI(bot))
