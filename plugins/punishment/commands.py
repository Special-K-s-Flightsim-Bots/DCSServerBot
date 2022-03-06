import discord
import psycopg2
from contextlib import closing
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener, utils
from discord.ext import tasks, commands
from typing import Type, Union
from .listener import PunishmentEventListener


class Punishment(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.decay_config = self.read_decay_config()
        self.unban_config = self.read_unban_config()
        self.decay.start()
        self.check_punishments.start()

    def cog_unload(self):
        self.check_punishments.cancel()
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

    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    cursor.execute('SELECT * FROM pu_events_sdw')
                    for row in cursor.fetchall():
                        # we are not initialized correctly yet
                        if self.plugin not in self.globals[row['server_name']]:
                            return
                        config = self.globals[row['server_name']][self.plugin]
                        server = self.globals[row['server_name']]
                        initiator = utils.get_player(self, row['server_name'], ucid=row['init_id'])
                        if 'punishments' in config:
                            for punishment in config['punishments']:
                                if row['points'] >= punishment['points']:
                                    for penalty in config['penalties']:
                                        if penalty['event'] == row['event']:
                                            reason = penalty['reason'] if 'reason' in penalty else row['event']
                                            break
                                    if punishment['action'] in ['kick', 'ban']:
                                        self.bot.sendtoDCS(server, {
                                            "command": punishment['action'],
                                            "ucid": initiator['ucid'],
                                            "reason": reason
                                        })
                                        if punishment['action'] == 'ban':
                                            cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, '
                                                           '%s, %s)', (row['init_id'], self.plugin, reason))
                                    elif punishment['action'] == 'move_to_spec':
                                        self.bot.sendtoDCS(server, {
                                            "command": "force_player_slot",
                                            "playerID": initiator['id']
                                        })
                                        self.eventlistener.sendChatMessage(server['server_name'], initiator['id'],
                                                                           f"You've been kicked back to spectators "
                                                                           f"because of: {reason}.\nYour current "
                                                                           f"punishment points are: {row['points']}")
                                    elif punishment['action'] == 'warn':
                                        self.bot.sendtoDCS(server, {
                                            "command": "sendPopupMessage",
                                            "to": initiator['group_id'],
                                            "message": f"{initiator['name']}, you have been punished for: {reason}!\n"
                                                       f"Your current punishment points are: {row['points']}",
                                            "time": self.config['BOT']['MESSAGE_TIMEOUT']
                                        })
                                    break
                        cursor.execute('DELETE FROM pu_events_sdw WHERE id = %s', (row['id'], ))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    @check_punishments.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=12.0)
    async def decay(self):
        if self.decay_config:
            self.log.debug('Punishment - Running decay.')
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    for d in self.decay_config:
                        cursor.execute('UPDATE pu_events SET points = ROUND(points * %s, 2), decay_run = %s WHERE '
                                       'time < (NOW() - interval \'%s days\') AND decay_run < %s', (d['weight'],
                                                                                                    d['days'],
                                                                                                    d['days'],
                                                                                                    d['days']))
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

    @commands.command(description='Clears the punishment points of a specific user', usage='<member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def forgive(self, ctx, user: Union[discord.Member, str]):
        if await utils.yn_question(self, ctx, 'This will delete all the punishment points for this user.\nAre you '
                                              'sure (Y/N)?') is True:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    if isinstance(user, discord.Member):
                        cursor.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id,))
                        ucids = [row[0] for row in cursor.fetchall()]
                    else:
                        ucids = [user]
                    for ucid in ucids:
                        cursor.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid, ))
                        cursor.execute(f"DELETE FROM bans WHERE ucid = %s AND banned_by = '{self.plugin}'", (ucid,))
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
