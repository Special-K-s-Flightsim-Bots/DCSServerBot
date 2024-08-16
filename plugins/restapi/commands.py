import asyncio
import logging
import os
import shutil
import uvicorn

from core import Plugin, DEFAULT_TAG
from datetime import datetime
from fastapi import FastAPI, APIRouter, Form
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Optional
from uvicorn import Config

app: Optional[FastAPI] = None


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        global app

        super().__init__(bot)
        cfg = self.locals[DEFAULT_TAG]
        prefix = cfg.get('prefix', '')
        self.router = APIRouter()
        self.router.add_api_route(prefix + "/topkills", self.topkills, methods=["GET"])
        self.router.add_api_route(prefix + "/topkdr", self.topkdr, methods=["GET"])
        self.router.add_api_route(prefix + "/trueskill", self.trueskill, methods=["GET"])
        self.router.add_api_route(prefix + "/getuser", self.getuser, methods=["POST"])
        self.router.add_api_route(prefix + "/missilepk", self.missilepk, methods=["POST"])
        self.router.add_api_route(prefix + "/stats", self.stats, methods=["POST"])
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
                    SELECT name AS \"nick\", last_seen AS \"date\" FROM players WHERE name ILIKE %s ORDER BY 2 DESC
                """, ('%' + nick + '%', ))
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
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT ucid FROM players WHERE name = %s AND last_seen = %s",
                                     (nick, datetime.fromisoformat(date)))
                row = await cursor.fetchone()
                if row:
                    ucid = row['ucid']
                else:
                    return {}
                await cursor.execute("""
                    SELECT overall.deaths, overall.aakills, 
                           ROUND(CASE WHEN overall.deaths = 0 
                                      THEN overall.aakills 
                                      ELSE overall.aakills/overall.deaths::DECIMAL END, 2) AS "aakdr", 
                           lastsession.kills AS "lastSessionKills", lastsession.deaths AS "lastSessionDeaths"
                    FROM (
                        SELECT SUM(deaths) AS "deaths", SUM(pvp) AS "aakills"
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


async def setup(bot: DCSServerBot):
    global app

    app = FastAPI()
    restapi = RestAPI(bot)
    await bot.add_cog(restapi)
    app.include_router(restapi.router)
