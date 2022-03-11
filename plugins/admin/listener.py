# listener.py
import asyncio
import platform
import psycopg2
from contextlib import closing
from core import utils, EventListener, Status


class AdminEventListener(EventListener):

    def updateBans(self, data=None):
        banlist = []
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT ucid FROM bans')
                banlist = [dict(row) for row in cursor.fetchall()]
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
        if data is not None:
            servers = [self.globals[data['server_name']]]
        else:
            servers = self.globals.values()
        for server in servers:
            for ban in banlist:
                self.bot.sendtoDCS(server, {"command": "ban", "ucid": ban['ucid'], "channel": server['status_channel']})

    async def registerDCSServer(self, data):
        # upload the current bans to the server
        self.updateBans(data)

    async def ban(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                               (data['ucid'], 'DCSServerBot', data['reason']))
                for server in self.globals.values():
                    self.bot.sendtoDCS(server, {
                        "command": "ban",
                        "ucid": data['ucid'],
                        "reason": data['reason']
                    })
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
