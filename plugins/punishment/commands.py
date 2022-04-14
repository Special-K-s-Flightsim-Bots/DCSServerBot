import discord
import psycopg2
from contextlib import closing, suppress
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener, utils
from discord.ext import tasks, commands
from typing import Type, Union
from .listener import PunishmentEventListener


class PunishmentAgent(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_punishments.start()

    def cog_unload(self):
        self.check_punishments.cancel()
        super().cog_unload()

    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    for server_name, server in self.globals.items():
                        cursor.execute('SELECT * FROM pu_events_sdw WHERE server_name = %s', (server_name, ))
                        for row in cursor.fetchall():
                            # we are not initialized correctly yet
                            if self.plugin not in self.globals[server_name]:
                                return
                            config = self.globals[server_name][self.plugin]
                            player = utils.get_player(self, server_name, ucid=row['init_id'])
                            if 'punishments' in config:
                                for punishment in config['punishments']:
                                    if row['points'] < punishment['points']:
                                        continue
                                    reason = None
                                    for penalty in config['penalties']:
                                        if penalty['event'] == row['event']:
                                            reason = penalty['reason'] if 'reason' in penalty else row['event']
                                            break
                                    if not reason:
                                        self.log.warning(f"No penalty configured for event {row['event']}.")
                                        reason = row['event']
                                    if punishment['action'] == 'ban':
                                        cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, '
                                                       '%s, %s) ON CONFLICT DO NOTHING',
                                                       (row['init_id'], self.plugin, reason))
                                        # ban them on all servers on this node
                                        for s in self.globals.values():
                                            self.bot.sendtoDCS(s, {
                                                "command": "ban",
                                                "ucid": row['init_id'],
                                                "reason": reason
                                            })
                                        cursor.execute('SELECT discord_id, name FROM players WHERE ucid = %s AND ucid '
                                                       'NOT IN (SELECT ucid FROM bans)', (row['init_id'], ))
                                        if cursor.rowcount == 1:
                                            banned = cursor.fetchone()
                                            await self.bot.audit(f"Player {banned['name']}(ucid={row['init_id']}) banned by {self.bot.member.name} for {reason}.")
                                            with suppress(Exception):
                                                guild = self.bot.guilds[0]
                                                member = await guild.fetch_member(banned['discord_id'])
                                                channel = await member.create_dm()
                                                await channel.send(f"You have been banned from the DCS servers on {guild.name} for {reason}.\n"
                                                                   f"To retrieve your current points, check out the {self.config['BOT']['COMMAND_PREFIX']}penalty command.")
                                    # all other punishments only happen if the player is still in the server
                                    elif player:
                                        if punishment['action'] == 'kick':
                                            self.bot.sendtoDCS(server, {
                                                "command": "kick",
                                                "ucid": row['init_id'],
                                                "reason": reason
                                            })
                                        elif punishment['action'] == 'move_to_spec':
                                            self.bot.sendtoDCS(server, {
                                                "command": "force_player_slot",
                                                "playerID": player['id']
                                            })
                                            self.bot.sendtoDCS(server, {
                                                "command": "sendChatMessage",
                                                "to": player['id'],
                                                "message": f"You've been kicked back to spectators because of: "
                                                           f"{reason}.\nYour current punishment points are: "
                                                           f"{row['points']}"
                                            })
                                        elif punishment['action'] == 'warn':
                                            message = f"{player['name']}, you have been punished for: {reason}!\n" \
                                                      f"Your current punishment points are: {row['points']}"
                                            if 'group_id' in player:
                                                self.bot.sendtoDCS(server, {
                                                    "command": "sendPopupMessage",
                                                    "to": player['group_id'],
                                                    "message": message,
                                                    "time": self.config['BOT']['MESSAGE_TIMEOUT']
                                                })
                                            else:
                                                self.bot.sendtoDCS(server, {
                                                    "command": "sendChatMessage",
                                                    "to": player['id'],
                                                    "message": message
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


class PunishmentMaster(PunishmentAgent):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.decay_config = self.read_decay_config()
        self.unban_config = self.read_unban_config()
        self.decay.start()

    def cog_unload(self):
        self.decay.cancel()
        super().cog_unload()

    def rename(self, old_name: str, new_name: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE pu_events SET server_name = %s WHERE server_name = %s', (new_name, old_name))
                cursor.execute('UPDATE pu_events_sdw SET server_name = %s WHERE server_name = %s', (new_name, old_name))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

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
                        cursor.execute('UPDATE pu_events SET points = ROUND(points * %s, 2), decay_run = %s WHERE '
                                       'time < (NOW() - interval \'%s days\') AND decay_run < %s',
                                       (d['weight'], d['days'], d['days'], d['days']))
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
                            cursor.execute('SELECT discord_id, name FROM players WHERE ucid = %s', (row['init_id'],))
                            banned = cursor.fetchone()
                            await self.bot.audit(
                                f"Player {banned['name']}(ucid={row['init_id']}) unbanned by {self.bot.member.name} due to decay.")
                            with suppress(Exception):
                                guild = self.bot.guilds[0]
                                member = await guild.fetch_member(banned['discord_id'])
                                channel = await member.create_dm()
                                await channel.send(
                                    f"You have been auto-unbanned from the DCS servers on {guild.name}.\n"
                                    f"Please behave according to the rules to not risk another ban.")
                        conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    @commands.command(description='Set punishment to 0 for a user', usage='<member / ucid>')
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
                        cursor.execute('DELETE FROM pu_events_sdw WHERE init_id = %s', (ucid, ))
                        cursor.execute(f"DELETE FROM bans WHERE ucid = %s AND banned_by = '{self.plugin}'", (ucid,))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    @commands.command(description='Displays your current penalty points')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def penalty(self, ctx):
        await ctx.message.delete()
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("SELECT p.name, COALESCE(SUM(e.points), 0) FROM pu_events e, players p WHERE e.init_id "
                               "= p.ucid AND p.discord_id = %s GROUP BY p.name ORDER BY 2 DESC",
                               (ctx.message.author.id, ))
                if cursor.rowcount == 0:
                    await ctx.send('You currently have 0 penalty points.')
                    return
                embed = discord.Embed(title='Penalty Points', color=discord.Color.blue())
                embed.description = 'You currently have these penalty points:'
                names = points = ''
                for row in cursor.fetchall():
                    names += row[0] + '\n'
                    points += f"{row[1]:.2f}\n"
                embed.add_field(name='DCS Name', value=names)
                embed.add_field(name='Points', value=points)
                cursor.execute("SELECT COUNT(*) FROM bans b, players p WHERE p.discord_id = %s AND b.ucid = p.ucid",
                               (ctx.message.author.id, ))
                if cursor.fetchone()[0] > 0:
                    unban = self.read_unban_config()
                    if unban:
                        embed.set_footer(text=f"You are currently banned.\nAutomatic unban will happen, if your "
                                              f"points decayed below {unban}.")
                timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
                await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(PunishmentMaster(bot, PunishmentEventListener))
    else:
        bot.add_cog(PunishmentAgent(bot, PunishmentEventListener))
