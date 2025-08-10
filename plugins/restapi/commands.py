import psycopg
import random

from core import Plugin, DEFAULT_TAG, Side, DataObjectFactory, utils, Status, ServiceRegistry
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, APIRouter, Form, Query, HTTPException
from plugins.creditsystem.squadron import Squadron
from plugins.userstats.filter import StatisticsFilter, PeriodFilter
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from services.webservice import WebService
from typing import Optional, Any, Union

from .models import (TopKill, ServerInfo, SquadronInfo, TopKDR, Trueskill, Highscore, UserEntry, WeaponPK, PlayerStats,
                     CampaignCredits, TrapEntry, SquadronMember, SquadronCampaignCredit, LinkMeResponse, ServerStats)

app: Optional[FastAPI] = None


# Bit field constants
BIT_USER_LINKED = 1
BIT_LINK_IN_PROGRESS = 2
BIT_FORCE_OPERATION = 4


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.web_service = ServiceRegistry.get(WebService)
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
            response_model = list[SquadronMember],
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
            response_model = list[TopKDR],
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
        self.app.include_router(router)

    async def get_ucid(self, nick: str, date: Union[str, datetime]) -> str:
        if isinstance(date, str):
            date = datetime.fromisoformat(date)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT ucid
                    FROM players
                    WHERE name = %s
                      AND DATE_TRUNC('second', last_seen) = DATE_TRUNC('second', %s)
                """, (nick, date))
                row = await cursor.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Player {nick} not found with specified date"
                    )
                return row['ucid']

    async def serverstats(self):
        self.log.debug('Calling /serverstats')
        serverstats = {}
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT COUNT(p.ucid) AS "totalPlayers", 
                           ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS "totalPlaytime",
                           ROUND(AVG(EXTRACT(EPOCH FROM(COALESCE(s.hop_off, NOW() AT TIME ZONE 'UTC') - s.hop_on))))::INTEGER AS "avgPlaytime",
                           SUM(CASE WHEN s.hop_off IS NULL THEN 1 ELSE 0 END) AS "activePlayers",
                           SUM(s.takeoffs) AS "totalSorties",
                           SUM(s.kills) AS "totalKills",
                           SUM(s.deaths) AS "totalDeaths",
                           SUM(s.pvp) AS "totalPvPKills",
                           SUM(s.deaths_pvp) AS "totalPvPDeaths"
                    FROM players p JOIN statistics s 
                    ON p.ucid = s.player_ucid
                """)
                serverstats = await cursor.fetchone()
                await cursor.execute("""
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
                        WHERE s.hop_on > (now() AT TIME ZONE 'utc') - interval '7 days'
                        GROUP BY 1
                    )
                    SELECT ds.date, COALESCE(pc.player_count, 0) as player_count
                    FROM date_series ds
                    LEFT JOIN player_counts pc ON ds.date = pc.date
                    ORDER BY ds.date
                """)
                serverstats['daily_players'] = await cursor.fetchall()
        return ServerStats.model_validate(serverstats)

    async def servers(self):
        self.log.debug('Calling /servers')
        servers = []
        for server in self.bot.servers.values():
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

    async def squadrons(self):
        self.log.debug('Calling /squadrons')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                squadrons: list[dict] = []
                async for row in await cursor.execute("""
                    SELECT * FROM squadrons ORDER BY name
                """):
                    squadrons.append({
                        "name": row['name'],
                        "description": row['description'],
                        "image_url": row['image_url'],
                        "locked": row['locked'],
                        "role": self.bot.get_role(row['role']).name
                    })
        return squadrons

    async def topkills(self, limit: int = Query(default=10)):
        self.log.debug(f'Calling /topkills with limit={limit}')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(s.kills) AS "kills", SUM(s.deaths) AS "deaths",
                    CASE WHEN SUM(s.deaths) = 0 THEN SUM(s.kills) ELSE SUM(s.kills)/SUM(s.deaths::DECIMAL) END AS "kdr" 
                    FROM statistics s JOIN players p 
                    ON s.player_ucid = p.ucid 
                    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}
                """.format(limit=limit if limit else 10))
                return [TopKill.model_validate(result) for result in await cursor.fetchall()]

    async def topkdr(self, limit: int = Query(default=10)):
        self.log.debug(f'Calling /topkdr with limit={limit}')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(s.kills) AS "kills", SUM(s.deaths) AS "deaths", 
                    CASE WHEN SUM(s.deaths) = 0 THEN SUM(s.kills) ELSE SUM(s.kills)/SUM(s.deaths::DECIMAL) END AS "kdr" 
                    FROM statistics s JOIN players p 
                    ON s.player_ucid = p.ucid 
                    GROUP BY 1, 2 ORDER BY 5 DESC LIMIT {limit}
                """.format(limit=limit if limit else 10))
                return [TopKDR.model_validate(result) for result in await cursor.fetchall()]

    async def trueskill(self, limit: int = Query(default=10)):
        self.log.debug(f'Calling /trueskill with limit={limit}')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(pvp) AS "kills_pvp", SUM(deaths_pvp) AS "deaths_pvp", t.skill_mu AS "TrueSkill" 
                    FROM statistics s, players p, trueskill t 
                    WHERE s.player_ucid = p.ucid 
                    AND t.player_ucid = p.ucid
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1, 2, 5 ORDER BY 5 DESC LIMIT {limit}
                """.format(limit=limit if limit else 10))
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

    async def weaponpk(self, nick: str = Form(...), date: str = Form(...)):
        self.log.debug(f'Calling /weaponpk with nick="{nick}", date="{date}"')
        ucid = await self.get_ucid(nick, date)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT x.weapon, x.shots, x.hits, 
                           ROUND(CASE WHEN x.shots = 0 THEN 0 ELSE x.hits / x.shots::DECIMAL END, 2) AS "pk"
                    FROM (
                        SELECT weapon, SUM(CASE WHEN event='S_EVENT_SHOT' THEN 1 ELSE 0 END) AS "shots", 
                               SUM(CASE WHEN event='S_EVENT_HIT' THEN 1 ELSE 0 END) AS "hits" 
                        FROM missionstats 
                        WHERE init_id = %s AND weapon IS NOT NULL
                        GROUP BY weapon
                    ) x
                    ORDER BY 2 DESC
                """, (ucid, ))
                return [WeaponPK.model_validate(result) for result in await cursor.fetchall()]

    async def stats(self, nick: str = Form(...), date: str = Form(...)):
        self.log.debug(f'Calling /stats with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                await cursor.execute("""
                    SELECT overall.playtime, overall.kills, overall.deaths, overall.kills_pvp, overall.deaths_pvp, 
                           overall.takeoffs, overall.landings, overall.ejections, overall.crashes, overall.teamkills, 
                           ROUND(CASE WHEN overall.deaths = 0 
                                      THEN overall.kills 
                                      ELSE overall.kills/overall.deaths::DECIMAL END, 2) AS "kdr", 
                           ROUND(CASE WHEN overall.deaths_pvp = 0 
                                      THEN overall.kills_pvp 
                                      ELSE overall.kills/overall.deaths_pvp::DECIMAL END, 2) AS "kdr_pvp", 
                           lastsession.kills AS "lastSessionKills", lastsession.deaths AS "lastSessionDeaths"
                    FROM (
                        SELECT ROUND(SUM(EXTRACT(EPOCH FROM(COALESCE(hop_off, NOW() AT TIME ZONE 'UTC') - hop_on))))::INTEGER AS playtime, 
                               SUM(kills) as "kills", SUM(deaths) AS "deaths", 
                               SUM(pvp) AS "kills_pvp", SUM(deaths_pvp) AS "deaths_pvp",
                               SUM(takeoffs) AS "takeoffs", SUM(landings) AS "landings", SUM(ejections) AS "ejections",
                               SUM(crashes) AS "crashes", SUM(teamkills) AS "teamkills"
                        FROM statistics
                        WHERE player_ucid = %(ucid)s
                    ) overall, (
                        SELECT SUM(kills) AS "kills", SUM(deaths) AS "deaths"
                        FROM statistics
                        WHERE (player_ucid, mission_id) = (
                            SELECT player_ucid, max(mission_id) FROM statistics WHERE player_ucid = %(ucid)s GROUP BY 1
                        )
                    ) lastsession
                """, {"ucid": ucid})
                data = await cursor.fetchone()
                await cursor.execute("""
                    SELECT slot AS "module", SUM(kills) AS "kills" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(kills) > 1 
                    ORDER BY 2 DESC
                """, (ucid,))
                data['killsByModule'] = await cursor.fetchall()
                await cursor.execute("""
                    SELECT slot AS "module", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(kills) ELSE SUM(kills) / SUM(deaths::DECIMAL) END AS "kdr" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(kills) > 1 
                    ORDER BY 2 DESC
                """, (ucid,))
                data['kdrByModule'] = await cursor.fetchall()
                return PlayerStats.model_validate(data)

    async def credits(self, nick: str = Form(...), date: str = Form(...), campaign: str = Form(default=None)):
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
                return CampaignCredits.model_validate(await cursor.fetchone())

    async def traps(self, nick: str = Form(default=None), date: str = Form(default=None),
                    limit: int = Form(default=10)):
        self.log.debug(f'Calling /traps with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                ucid = await self.get_ucid(nick, date)
                await cursor.execute(f"""
                    SELECT unit_type, grade, comment, place, trapcase, wire, night, points, time
                    FROM traps
                    WHERE player_ucid = %s
                    ORDER BY time DESC LIMIT {limit}
                """, (ucid, ))
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
                return [SquadronMember.model_validate(result) for result in await cursor.fetchall()]

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
            if row:
                squadron = utils.get_squadron(node=self.node, name=name)
                squadron_obj = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                                       campaign_id=row[0])
                return SquadronCampaignCredit.model_validate({"campaign": row[1], "credits": squadron_obj.points})
            else:
                return {}

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


async def setup(bot: DCSServerBot):
    await bot.add_cog(RestAPI(bot))
