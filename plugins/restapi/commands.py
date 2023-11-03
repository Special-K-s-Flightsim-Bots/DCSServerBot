import asyncio
import logging
import os
import shutil
import uvicorn

from contextlib import closing
from core import Plugin, DEFAULT_TAG
from datetime import datetime
from fastapi import FastAPI, APIRouter, Form
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional
from uvicorn import Config

app: Optional[FastAPI] = None


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot):
        global app

        super().__init__(bot)
        self.router = APIRouter()
        self.router.add_api_route("/topkills", self.topkills, methods=["GET"])
        self.router.add_api_route("/topkdr", self.topkdr, methods=["GET"])
        self.router.add_api_route("/trueskill", self.trueskill, methods=["GET"])
        self.router.add_api_route("/getuser", self.getuser, methods=["POST"])
        self.router.add_api_route("/missilepk", self.missilepk, methods=["POST"])
        self.router.add_api_route("/stats", self.stats, methods=["POST"])
        self.app = app
        cfg = self.locals[DEFAULT_TAG]
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

    def topkills(self):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > NOW() - interval '1 month' 
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 10
                """).fetchall()

    def topkdr(self):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > NOW() - interval '1 month' 
                    GROUP BY 1 ORDER BY 4 DESC LIMIT 10
                """).fetchall()

    def trueskill(self):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return cursor.execute("""
                    SELECT 
                        p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                        p.skill_mu AS "TrueSkill" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > NOW() - interval '1 month' 
                    GROUP BY 1 ORDER BY 4 DESC LIMIT 10
                """).fetchall()

    def getuser(self, nick: str = Form(default=None)):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return cursor.execute("""
                    SELECT name AS \"nick\", last_seen AS \"date\" FROM players WHERE name ILIKE %s ORDER BY 2 DESC
                """, ('%' + nick + '%', )).fetchall()

    def missilepk(self, nick: str = Form(default=None), date: str = Form(default=None)):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return {
                    "missilePK": dict([(row['weapon'], row['pk']) for row in cursor.execute("""
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
                    """, (nick, datetime.fromisoformat(date))).fetchall()])
                }

    def stats(self, nick: str = Form(default=None), date: str = Form(default=None)):
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                row = cursor.execute("SELECT ucid FROM players WHERE name = %s AND last_seen = %s",
                                     (nick, datetime.fromisoformat(date))).fetchone()
                if row:
                    ucid = row['ucid']
                else:
                    return {}
                data = cursor.execute("""
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
                """, (ucid, ucid)).fetchone()
                data['killsByModule'] = cursor.execute("""
                    SELECT slot AS "module", SUM(pvp) AS "kills" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid, )).fetchall()
                data['kdrByModule'] = cursor.execute("""
                    SELECT slot AS "module", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp) / SUM(deaths::DECIMAL) END AS "kdr" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid, )).fetchall()
                return data


async def setup(bot: DCSServerBot):
    global app

    if not os.path.exists('config/plugins/restapi.yaml'):
        bot.log.info('No restapi.yaml found, copying the sample.')
        shutil.copyfile('config/samples/plugins/restapi.yaml', 'config/plugins/restapi.yaml')
    app = FastAPI()
    restapi = RestAPI(bot)
    await bot.add_cog(restapi)
    app.include_router(restapi.router)
