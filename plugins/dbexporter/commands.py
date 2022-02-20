import json
import os
import psycopg2
from contextlib import closing
from core import Plugin, DCSServerBot, TEventListener, utils
from discord.ext import tasks, commands
from os import path
from typing import Type, List


class DBExporter(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not path.exists('./export'):
            os.makedirs('./export')
        if 'config' in self.locals and 'autoexport' in self.locals['config'] and \
                self.locals['config']['autoexport'] == True:
            self.schedule.start()

    def cog_unload(self):
        if 'config' in self.locals and 'autoexport' in self.locals['config'] and \
                self.locals['configs']['autoexport'] == True:
            self.schedule.cancel()
        super().cog_unload()

    def do_export(self, tablefilter: List[str]):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND "
                               "table_name not in ('pu_events_sdw', 'servers', 'message_persistence')")
                for table in (x[0] for x in cursor.fetchall() if x[0] not in tablefilter):
                    cursor.execute(f'SELECT ROW_TO_JSON(t) FROM (SELECT * FROM {table}) t')
                    if cursor.rowcount > 0:
                        with open(f'export/{table}.json', 'w') as file:
                            file.writelines([json.dumps(x[0]) + '\n' for x in cursor.fetchall()])
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Exports the database to the servers /export directory.')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def export(self, ctx):
        self.do_export([])
        await ctx.send('Database dumped to ./export')

    @tasks.loop(hours=1.0)
    async def schedule(self):
        self.do_export(self.locals['config']['tablefilter'] if ('config' in self.locals and
                                                               'tablefilter' in self.locals['config']) else [])


def setup(bot: DCSServerBot):
    bot.add_cog(DBExporter(bot))
