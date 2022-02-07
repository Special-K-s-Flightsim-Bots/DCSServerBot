import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener
from discord.ext import tasks
from typing import Type
from .listener import PunishmentEventListener


class Punishment(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.decay_config = self.read_decay_config()
        self.unban_config = self.read_unban_config()
        self.decay.start()

    def cog_unload(self):
        self.decay.cancel()
        super().cog_unload()

    def read_decay_config(self):
        if 'configs' in self.locals:
            for element in self.locals['configs']:
                if 'decay' in element:
                    return element['decay']
        return None

    def read_unban_config(self):
        if 'configs' in self.locals:
            for element in self.locals['configs']:
                if 'unban' in element:
                    return element['unban']
        return None

    @tasks.loop(hours=12.0)
    async def decay(self):
        if self.decay_config:
            self.log.debug('Punishment - Running decay.')
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    for d in self.decay_config:
                        cursor.execute('UPDATE pu_events SET points = points * %s, decay_run = %s WHERE time < (NOW() '
                                       '- interval \'%s days\') AND decay_run < %s', (d['weight'], d['days'],
                                                                                      d['days'], d['days']))
                    if self.unban_config:
                        cursor.execute(f"SELECT ucid FROM bans b, (SELECT init_id, SUM(points) AS points FROM "
                                       f"pu_events GROUP BY init_id) p WHERE b.ucid = p.init_id AND "
                                       f"b.banned_by = '{self.plugin}' AND p.points <= %s", (self.unban_config, ))
                        for row in cursor.fetchall():
                            for server_name, server in self.globals.items():
                                self.bot.sendtoDCS(server, {
                                    "command": "unban",
                                    "ucid": row[0]
                                })
                            cursor.execute('DELETE FROM bans WHERE ucid = %s', (row[0], ))
                            await self.bot.audit(f'Unbanned ucid {row[0]} due to punishment points decay.')
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(Punishment(bot, PunishmentEventListener))
    else:
        bot.add_cog(Plugin(bot, PunishmentEventListener))
