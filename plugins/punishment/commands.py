import discord
import psycopg2
import string
from contextlib import closing, suppress
from copy import deepcopy
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener, utils, Player, Server, Channel
from discord.ext import tasks, commands
from typing import Type, Union, Optional
from .listener import PunishmentEventListener


class PunishmentAgent(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_punishments.start()

    async def cog_unload(self):
        self.check_punishments.cancel()
        await super().cog_unload()

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = default
                    # specific settings will always overwrite default settings
                    for key, value in specific.items():
                        merged[key] = value
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    async def punish(self, server: Server, player: Player, punishment: dict, reason: str):
        if punishment['action'] == 'ban':
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, '
                                   '%s, %s) ON CONFLICT DO NOTHING',
                                   (player.ucid, self.plugin_name, reason))
                    conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)
            # ban them on all servers on this node
            for s in self.bot.servers.values():
                s.sendtoDCS({
                    "command": "ban",
                    "ucid": player.ucid,
                    "reason": reason
                })
            if player.member:
                message = f"Member {player.member.display_name} banned by {self.bot.member.name} for {reason}."
                await server.get_channel(Channel.ADMIN).send(message)
                await self.bot.audit(message)
                with suppress(Exception):
                    guild = self.bot.guilds[0]
                    channel = await player.member.create_dm()
                    await channel.send(f"You have been banned from the DCS servers on {guild.name} for {reason}.\n"
                                       f"To check your current penalty points, use the "
                                       f"{self.bot.config['BOT']['COMMAND_PREFIX']}penalty command.")
            else:
                message = f"Player {player.name}(ucid={player.ucid}) banned by {self.bot.member.name} for {reason}."
                await server.get_channel(Channel.ADMIN).send(message)
                await self.bot.audit(message)

        if punishment['action'] == 'kick' and player.active:
            server.kick(player, reason)
            await server.get_channel(Channel.ADMIN).send(f"Player {player.name}(ucid={player.ucid}) kicked by "
                                                         f"{self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'move_to_spec':
            server.move_to_spectators(player)
            player.sendChatMessage(f"You've been kicked back to spectators because of: {reason}.")
            await server.get_channel(Channel.ADMIN).send(f"Player {player.name}(ucid={player.ucid}) moved to "
                                                         f"spectators by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'credits' and type(player).__name__ == 'CreditPlayer':
            old_points = player.points
            player.points -= punishment['penalty']
            player.audit('punishment', old_points, f"Punished for {reason}")
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!\n"
                                   f"Your current credit points are: {player.points}")
            await server.get_channel(Channel.ADMIN).send(f"Player {player.name}(ucid={player.ucid}) punished with "
                                                         f"credits by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'warn':
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!")

    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    for server_name, server in self.bot.servers.items():
                        cursor.execute('SELECT * FROM pu_events_sdw WHERE server_name = %s', (server_name, ))
                        for row in cursor.fetchall():
                            config = self.get_config(server)
                            # we are not initialized correctly yet
                            if not config:
                                continue
                            player: Player = server.get_player(ucid=row['init_id'], active=True)
                            if not player:
                                continue
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
                                        self.log.warning(f"No penalty or reason configured for event {row['event']}.")
                                        reason = row['event']
                                    await self.punish(server, player, punishment, reason)
                                    if player.active:
                                        player.sendChatMessage(f"Your current punishment points are: {row['points']}")
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

    async def cog_unload(self):
        self.decay.cancel()
        await super().cog_unload()

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
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    for d in self.decay_config:
                        cursor.execute('UPDATE pu_events SET points = ROUND(points * %s, 2), decay_run = %s WHERE '
                                       'time < (NOW() - interval \'%s days\') AND decay_run < %s',
                                       (d['weight'], d['days'], d['days'], d['days']))
                    if self.unban_config:
                        cursor.execute(f"SELECT ucid FROM bans b, (SELECT init_id, SUM(points) AS points FROM "
                                       f"pu_events GROUP BY init_id) p WHERE b.ucid = p.init_id AND "
                                       f"b.banned_by = '{self.plugin_name}' AND p.points <= %s", (self.unban_config,))
                        for row in cursor.fetchall():
                            for server_name, server in self.bot.servers.items():
                                server.sendtoDCS({
                                    "command": "unban",
                                    "ucid": row['ucid']
                                })
                            cursor.execute('DELETE FROM bans WHERE ucid = %s', (row['ucid'], ))
                            cursor.execute('SELECT discord_id, name FROM players WHERE ucid = %s', (row['ucid'],))
                            banned = cursor.fetchone()
                            await self.bot.audit(f"Player {banned['name']}(ucid={row['ucid']}) unbanned by "
                                                 f"{self.bot.member.name} due to decay.")
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

    @commands.command(description='Set punishment to 0 for a user', usage='<member|ucid>')
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
                        cursor.execute(f"DELETE FROM bans WHERE ucid = %s AND banned_by = '{self.plugin_name}'",
                                       (ucid,))
                        for server_name, server in self.bot.servers.items():
                            server.sendtoDCS({
                                "command": "unban",
                                "ucid": ucid
                            })
                    conn.commit()
                    await ctx.send('All punishment points deleted and player unbanned (if they were banned by the bot '
                                   'before).')
            except (Exception, psycopg2.DatabaseError) as error:
                conn.rollback()
                self.log.exception(error)
            finally:
                self.pool.putconn(conn)

    @commands.command(description='Displays your current penalty points')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def penalty(self, ctx, member: Optional[Union[discord.Member, str]]):
        if member:
            if not utils.check_roles(['DCS Admin'], ctx.message.author):
                await ctx.send('You need the DCS Admin role to use this command.')
                return
            if isinstance(member, str):
                ucid = member
                member = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = self.bot.get_ucid_by_member(member)
                if not ucid:
                    await ctx.send(f"Member {member.display_name} is not linked to any DCS user.")
                    return
        else:
            member = ctx.message.author
            ucid = self.bot.get_ucid_by_member(ctx.message.author)
            if not ucid:
                await ctx.send(f"Use {ctx.prefix}linkme to link your account.")
                return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute("SELECT event, points, time FROM pu_events WHERE init_id = %s ORDER BY time DESC",
                               (ucid, ))
                if cursor.rowcount == 0:
                    await ctx.send(f'{member.display_name} has no penalty points.')
                    return
                embed = discord.Embed(
                    title="Penalty Points for {}".format(member.display_name if isinstance(member, discord.Member) else member),
                    color=discord.Color.blue())
                times = events = points = ''
                total = 0.0
                for row in cursor.fetchall():
                    times += f"{row['time']:%m/%d %H:%M}\n"
                    events += string.capwords(' '.join(row['event'].split('_'))) + '\n'
                    points += f"{row['points']:.2f}\n"
                    total += row['points']
                embed.description = f"Total penalty points: {total:.2f}"
                embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
                embed.add_field(name='Time', value=times)
                embed.add_field(name='Event', value=events)
                embed.add_field(name='Points', value=points)
                embed.set_footer(text='Points decay over time, you might see different results on different days.')
                cursor.execute("SELECT COUNT(*) FROM bans b WHERE b.ucid = %s", (ucid, ))
                if cursor.fetchone()[0] > 0:
                    unban = self.read_unban_config()
                    if unban:
                        embed.set_footer(text=f"You are currently banned.\nAutomatic unban will happen, if your "
                                              f"points decayed below {unban}.")
                    else:
                        embed.set_footer(text=f"You are currently banned.\n"
                                              f"Please contact an admin if you want to get unbanned.")
                timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
                await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(PunishmentMaster(bot, PunishmentEventListener))
    else:
        await bot.add_cog(PunishmentAgent(bot, PunishmentEventListener))
