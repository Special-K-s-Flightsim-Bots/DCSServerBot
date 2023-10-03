import asyncio
import logging
import os
import psycopg2
import shutil
import uvicorn

from contextlib import closing
from core import DCSServerBot, Plugin
from datetime import datetime
from fastapi import FastAPI, APIRouter, Form
from typing import Optional
from uvicorn import Config

app: Optional[FastAPI] = None


class RestAPI(Plugin):

    def __init__(self, bot: DCSServerBot, app: FastAPI):
        super().__init__(bot)
        self.router = APIRouter()
        self.router.add_api_route("/topkills", self.topkills, methods=["GET"])
        self.router.add_api_route("/topkdr", self.topkdr, methods=["GET"])
        self.router.add_api_route("/getuser", self.getuser, methods=["POST"])
        self.router.add_api_route("/missilepk", self.missilepk, methods=["POST"])
        self.router.add_api_route("/stats", self.stats, methods=["POST"])
        self.app = app
        cfg = self.locals['configs'][0]
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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid
                    AND hop_on > NOW() - interval '1 month' 
                    GROUP BY 1 ORDER BY 2 DESC LIMIT 10
                """)
                return cursor.fetchall()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def topkdr(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("""
                    SELECT p.name AS "fullNickname", SUM(pvp) AS "AAkills", SUM(deaths) AS "deaths", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp)/SUM(deaths::DECIMAL) END AS "AAKDR" 
                    FROM statistics s, players p 
                    WHERE s.player_ucid = p.ucid 
                    AND hop_on > NOW() - interval '1 month' 
                    GROUP BY 1 ORDER BY 4 DESC LIMIT 10
                """)
                return cursor.fetchall()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def getuser(self, nick: str = Form(default=None)):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("""
                    SELECT name AS \"nick\", last_seen AS \"date\" FROM players WHERE name ILIKE %s
                """, ('%' + nick + '%', ))
                return cursor.fetchall()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def missilepk(self, nick: str = Form(default=None), date: str = Form(default=None)):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("""
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
                    "missilePK": dict([(row['weapon'], row['pk']) for row in cursor.fetchall()])
                }
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def stats(self, nick: str = Form(default=None), date: str = Form(default=None)):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)) as cursor:
                cursor.execute("SELECT ucid FROM players WHERE name = %s AND last_seen = %s",
                               (nick, datetime.fromisoformat(date)))
                if cursor.rowcount == 0:
                    return {}
                ucid = cursor.fetchone()['ucid']
                cursor.execute("""
                    SELECT overall.deaths, overall.aakills, 
                           ROUND(CASE WHEN overall.deaths = 0 THEN overall.aakills ELSE overall.aakills/overall.deaths::DECIMAL END, 2) AS "aakdr", 
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
                data = cursor.fetchone()
                cursor.execute("""
                    SELECT slot AS "module", SUM(pvp) AS "kills" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid, ))
                data['killsByModule'] = cursor.fetchall()
                cursor.execute("""
                    SELECT slot AS "module", 
                           CASE WHEN SUM(deaths) = 0 THEN SUM(pvp) ELSE SUM(pvp) / SUM(deaths::DECIMAL) END AS "kdr" 
                    FROM statistics 
                    WHERE player_ucid = %s 
                    GROUP BY 1 HAVING SUM(pvp) > 1 
                    ORDER BY 2 DESC
                """, (ucid, ))
                data['kdrByModule'] = cursor.fetchall()
                return data
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


async def setup(bot: DCSServerBot):
    global app

    if bot.config.getboolean('BOT', 'MASTER'):
        if not os.path.exists('config/restapi.json'):
            bot.log.info('No restapi.json found, copying the sample.')
            shutil.copyfile('config/samples/restapi.json', 'config/restapi.json')
        app = FastAPI()
        restapi = RestAPI(bot, app)
        await bot.add_cog(restapi)
        app.include_router(restapi.router)
