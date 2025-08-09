import psycopg
import random

from core import Plugin, DEFAULT_TAG, Side, DataObjectFactory, utils, Status, ServiceRegistry
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, APIRouter, Form, Query
from plugins.creditsystem.squadron import Squadron
from plugins.userstats.filter import StatisticsFilter, PeriodFilter
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Optional, Any

from services.webservice import WebService
from .models import (TopKill, ServerInfo, SquadronInfo, TopKDR, Trueskill, Highscore, UserEntry, MissilePK, PlayerStats,
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
            description = "Get AA top kills statistics for players",
            summary = "AA-Top Kills",
            tags = ["Statistics"]
        )
        router.add_api_route(
            "/topkdr", self.topkdr,
            methods = ["GET"],
            response_model = list[TopKDR],
            description = "Get AA top KDR statistics for players",
            summary = "AA-Top KDR",
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
            "/missilepk", self.missilepk,
            methods = ["POST"],
            response_model = list[MissilePK],
            description = "Get missile PK statistics for players",
            summary = "Missile PK",
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
            response_model = list[SquadronCampaignCredit],
            description = "List squadron campaign credits",
            summary = "Squadron Credits",
            tags = ["Credits"]
        )
        self.app.include_router(router)

    async def serverstats(self):
        self.log.debug('Calling /serverstats')
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
                           SUM(s.pvp) AS "totalAAKills",
                           SUM(s.deaths_pvp) AS "totalAADeaths"
                    FROM players p JOIN statistics s 
                    ON p.ucid = s.player_ucid
                """)
                return ServerStats.model_validate(await cursor.fetchone())

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
                    SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                    CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {limit}
                """.format(limit=limit if limit else 10))
                return [TopKill.model_validate(result) for result in await cursor.fetchall()]

    async def topkdr(self, limit: int = Query(default=10)):
        self.log.debug(f'Calling /topkdr with limit={limit}')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                    CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1, 2 ORDER BY 5 DESC LIMIT {limit}
                """.format(limit=limit if limit else 10))
                return [TopKDR.model_validate(result) for result in await cursor.fetchall()]

    async def trueskill(self, limit: int = Query(default=10)):
        self.log.debug(f'Calling /trueskill with limit={limit}')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date",
                    SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", t.skill_mu AS "TrueSkill" 
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

        return Highscore.model_validate(highscore, by_alias=True)

    async def getuser(self, nick: str = Form(default=None)):
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

    async def missilepk(self, nick: str = Form(default=None), date: str = Form(default=None)):
        self.log.debug(f'Calling /missilepk with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT weapon, shots, hits, 
                           ROUND(CASE WHEN shots = 0 THEN 0 ELSE hits/shots::DECIMAL END, 2) AS "pk"
                    FROM (
                        SELECT weapon, SUM(CASE WHEN event='S_EVENT_SHOT' THEN 1 ELSE 0 END) AS "shots", 
                               SUM(CASE WHEN event='S_EVENT_HIT' THEN 1 ELSE 0 END) AS "hits" 
                        FROM missionstats 
                        WHERE init_id = (SELECT ucid FROM players WHERE name = %s AND last_seen = %s)
                        AND weapon IS NOT NULL
                        GROUP BY weapon
                    ) x
                    ORDER BY 4 DESC
                """, (nick, datetime.fromisoformat(date)))
                return {
                    "missilePK": dict([(row['weapon'], row['pk']) async for row in cursor])
                }

    async def stats(self, nick: str = Form(default=None), date: str = Form(default=None)):
        self.log.debug(f'Calling /stats with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT ucid
                    FROM players
                    WHERE name = %s
                      AND DATE_TRUNC('second', last_seen) = DATE_TRUNC('second', %s)
                """, (nick, datetime.fromisoformat(date)))
                row = await cursor.fetchone()
                if row:
                    ucid = row['ucid']
                    self.log.debug(f'Found UCID: {ucid}')
                else:
                    self.log.debug("No UCID found.")
                    return {}
                await cursor.execute("""
                    SELECT overall.kills, overall.deaths, overall.aakills, overall.takeoffs, overall.landings, 
                           overall.ejections, overall.crashes, overall.teamkills, 
                           ROUND(CASE WHEN overall.deaths = 0 
                                      THEN overall.aakills 
                                      ELSE overall.aakills/overall.deaths::DECIMAL END, 2) AS "aakdr", 
                           lastsession.kills AS "lastSessionKills", lastsession.deaths AS "lastSessionDeaths"
                    FROM (
                        SELECT SUM(kills) as "kills", SUM(deaths) AS "deaths", SUM(pvp) AS "aakills", 
                               SUM(takeoffs) AS "takeoffs", SUM(landings) AS "landings", SUM(ejections) AS "ejections",
                               SUM(crashes) AS "crashes", SUM(teamkills) AS "teamkills"
                        FROM statistics
                        WHERE player_ucid = %s
                    ) overall, (
                        SELECT SUM(pvp) AS "kills", SUM(deaths) AS "deaths"
                        FROM statistics
                        WHERE (player_ucid, mission_id) = (
                            SELECT player_ucid, max(mission_id) FROM statistics WHERE player_ucid = %s GROUP BY 1
                        )
                    ) lastsession
                """, (ucid, ucid))
                data = await cursor.fetchone()
                await cursor.execute("""
                    SELECT slot AS "module", SUM(pvp) AS "kills" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid,))
                data['killsByModule'] = await cursor.fetchall()
                await cursor.execute("""
                    SELECT slot AS "module", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp) / SUM(deaths::DECIMAL) END AS "kdr" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid,))
                data['kdrByModule'] = await cursor.fetchall()
                return PlayerStats.model_validate(data)

    async def credits(self, nick: str = Form(default=None), date: str = Form(default=None)):
        self.log.debug(f'Calling /credits with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT ucid
                    FROM players
                    WHERE name = %s
                      AND DATE_TRUNC('second', last_seen) = DATE_TRUNC('second', %s)
                """, (nick, datetime.fromisoformat(date)))
                row = await cursor.fetchone()
                if row:
                    ucid = row['ucid']
                    self.log.debug(f'Found UCID: {ucid}')
                else:
                    self.log.debug("No UCID found.")
                    return {}
                await cursor.execute("""
                    SELECT c.id, c.name, COALESCE(SUM(s.points), 0) AS credits 
                    FROM campaigns c LEFT OUTER JOIN credits s ON (c.id = s.campaign_id AND s.player_ucid = %s) 
                    WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
                    GROUP BY 1, 2
                """, (ucid, ))
                return CampaignCredits.model_validate(await cursor.fetchone())

    async def traps(self, nick: str = Form(default=None), date: str = Form(default=None),
                    limit: int = Form(default=10)):
        self.log.debug(f'Calling /traps with nick="{nick}", date="{date}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT ucid
                    FROM players
                    WHERE name = %s
                      AND DATE_TRUNC('second', last_seen) = DATE_TRUNC('second', %s)
                """, (nick, datetime.fromisoformat(date)))
                row = await cursor.fetchone()
                if row:
                    ucid = row['ucid']
                    self.log.debug(f'Found UCID: {ucid}')
                else:
                    self.log.debug("No UCID found.")
                    return {}
                await cursor.execute(f"""
                    SELECT unit_type, grade, comment, place, trapcase, wire, night, points, time
                    FROM traps
                    WHERE player_ucid = %s
                    ORDER BY time DESC LIMIT {limit}
                """, (ucid, ))
                return [TrapEntry.model_validate(result) for result in await cursor.fetchall()]

    async def squadron_members(self, name: str = Form(default=None)):
        self.log.debug(f'Calling /squadron_members with name="{name}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "nick", DATE_TRUNC('second', p.last_seen) AS "date"  
                    FROM players p JOIN squadron_members sm ON sm.player_ucid = p.ucid
                                   JOIN squadrons s ON sm.squadron_id = s.id
                    WHERE s.name = %s
                """, (name, ))
                return await cursor.fetchall()

    async def squadron_credits(self, name: str = Form(default=None)):
        self.log.debug(f'Calling /squadron_credits with name="{name}"')
        ret = []
        async with self.apool.connection() as conn:
            async for row in await conn.execute("""
                    SELECT c.id, c.name
                    FROM campaigns c 
                    LEFT OUTER JOIN squadron_credits s ON c.id = s.campaign_id
                    JOIN squadrons s2 ON s.squadron_id = s2.id
                    WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
                    AND s2.name = %s
                """, (name, )):
                squadron = utils.get_squadron(node=self.node, name=name)
                squadron_obj = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                                       campaign_id=row[0])
                ret.append({"campaign": row[1], "credits": squadron_obj.points})
        return ret

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
