import asyncio
import logging
import os
import psycopg
import random
import shutil
import uvicorn

from core import Plugin, DEFAULT_TAG, Side, DataObjectFactory, utils
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, APIRouter, Form
from psycopg.rows import dict_row

from plugins.creditsystem.squadron import Squadron
from services.bot import DCSServerBot
from typing import Optional, Any
from uvicorn import Config

app: Optional[FastAPI] = None


# Bit field constants
BIT_USER_LINKED = 1
BIT_LINK_IN_PROGRESS = 2
BIT_FORCE_OPERATION = 4


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        global app

        super().__init__(bot)
        cfg = self.locals[DEFAULT_TAG]
        prefix = cfg.get('prefix', '')
        self.router = APIRouter()
        self.router.add_api_route(prefix + "/servers", self.servers, methods=["GET"])
        self.router.add_api_route(prefix + "/squadrons", self.squadrons, methods=["GET"])
        self.router.add_api_route(prefix + "/topkills", self.topkills, methods=["GET"])
        self.router.add_api_route(prefix + "/topkdr", self.topkdr, methods=["GET"])
        self.router.add_api_route(prefix + "/trueskill", self.trueskill, methods=["GET"])
        self.router.add_api_route(prefix + "/getuser", self.getuser, methods=["POST"])
        self.router.add_api_route(prefix + "/missilepk", self.missilepk, methods=["POST"])
        self.router.add_api_route(prefix + "/stats", self.stats, methods=["POST"])
        self.router.add_api_route(prefix + "/credits", self.credits, methods=["POST"])
        self.router.add_api_route(prefix + "/squadron_members", self.squadron_members, methods=["POST"])
        self.router.add_api_route(prefix + "/squadron_credits", self.squadron_credits, methods=["POST"])
        self.router.add_api_route(prefix + "/linkme", self.linkme, methods=["POST"])
        self.app = app
        self.config = Config(app=self.app, host=cfg['listen'], port=cfg['port'], log_level=logging.ERROR,
                             use_colors=False)
        self.server: uvicorn.Server = uvicorn.Server(config=self.config)
        self.task = None

    async def cog_load(self) -> None:
        await super().cog_load()
        self.task = asyncio.create_task(self.server.serve())

    async def cog_unload(self):
        self.server.should_exit = True
        await self.task
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            self.log.info('No restapi.yaml found, copying the sample.')
            shutil.copyfile('samples/plugins/restapi.yaml',
                            os.path.join(self.node.config_dir, 'plugins', 'restapi.yaml'))
            config = super().read_locals()
        return config

    async def servers(self):
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
            servers.append(data)
        return servers

    async def squadrons(self):
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

    async def topkills(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 10
                """)
                return await cursor.fetchall()

    async def topkdr(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1 ORDER BY 4 DESC LIMIT 10
                """)
                return await cursor.fetchall()

    async def trueskill(self):
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT 
                        p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                        t.skill_mu AS "TrueSkill" 
                    FROM statistics s, players p, trueskill t 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > (now() AT TIME ZONE 'utc') - interval '1 month' 
                    GROUP BY 1,4 ORDER BY 4 DESC LIMIT 10
                """)
                return await cursor.fetchall()

    async def getuser(self, nick: str = Form(default=None)):
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
                return await cursor.fetchall()

    async def missilepk(self, nick: str = Form(default=None), date: str = Form(default=None)):
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
                return data

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
                return await cursor.fetchone()

    async def squadron_members(self, name: str = Form(default=None)):
        self.log.debug(f'Calling /squadron_members with name="{name}"')
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT p.name, DATE_TRUNC('second', p.last_seen) AS "date"  
                    FROM players p JOIN squadron_members sm ON sm.player_ucid = p.ucid
                                   JOIN squadrons s ON sm.squadron_id = s.id
                    WHERE s.name = %s
                """, (name, ))
                return await cursor.fetchall()

    async def squadron_credits(self, name: str = Form(default=None)):
        self.log.debug(f'Calling /squadron_members with name="{name}"')
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
                    # Set bit_field for new user
                    rc = 0  # Default bit_field for new user
                    if force:
                        rc |= BIT_FORCE_OPERATION  # Set force operation flag

        return {
            "token": token,
            "timestamp": expiry_timestamp,
            "rc": rc
        }


async def setup(bot: DCSServerBot):
    global app

    app = FastAPI(docs_url=None, redoc_url=None)
    restapi = RestAPI(bot)
    await bot.add_cog(restapi)
    app.include_router(restapi.router)
