import psycopg2
from contextlib import closing
from core import EventListener, Plugin


class ServerStatsListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.fps = {}

    async def perfmon(self, data):
        self.fps[data['server_name']] = data['fps']

    async def rename(self, data):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE serverstats SET server_name = %s WHERE server_name = %s',
                               (data['newname'], data['server_name']))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
