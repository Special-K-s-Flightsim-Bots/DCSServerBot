import asyncio
import discord
from contextlib import closing, suppress
from copy import deepcopy
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener, utils, Player, Server, Channel, \
    PluginInstallationError
from discord.ext import tasks, commands
from psycopg.rows import dict_row
from typing import Type, Union, Optional
from .listener import PunishmentEventListener


class PunishmentAgent(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.json file found!", plugin=self.plugin_name)
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
        admin_channel = self.bot.get_channel(server.get_channel(Channel.ADMIN))
        if punishment['action'] == 'ban':
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute(
                        'INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
                        (player.ucid, self.plugin_name, reason)
                    )
            # ban them on all servers on this node
            for s in self.bot.servers.values():
                s.sendtoDCS({
                    "command": "ban",
                    "ucid": player.ucid,
                    "reason": reason
                })
            if player.member:
                message = "Member {} banned by {} for {}.".format(utils.escape_string(player.member.display_name),
                                                                  utils.escape_string(self.bot.member.name), reason)
                await admin_channel.send(message)
                await self.bot.audit(message)
                with suppress(Exception):
                    guild = self.bot.guilds[0]
                    channel = await player.member.create_dm()
                    await channel.send("You have been banned from the DCS servers on {} for {}.\n"
                                       "To check your current penalty points, use the {}penalty "
                                       "command.".format(utils.escape_string(guild.name), reason,
                                                         self.bot.config['BOT']['COMMAND_PREFIX']))
            else:
                message = f"Player {player.display_name} (ucid={player.ucid}) banned by {self.bot.member.name} " \
                          f"for {reason}."
                await admin_channel.send(message)
                await self.bot.audit(message)

        if punishment['action'] == 'kick' and player.active:
            server.kick(player, reason)
            await admin_channel.send(f"Player {player.display_name} (ucid={player.ucid}) kicked by "
                                     f"{self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'move_to_spec':
            server.move_to_spectators(player)
            player.sendChatMessage(f"You've been kicked back to spectators because of: {reason}.")
            await admin_channel.send(f"Player {player.display_name} (ucid={player.ucid}) moved to "
                                     f"spectators by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'credits' and type(player).__name__ == 'CreditPlayer':
            old_points = player.points
            player.points -= punishment['penalty']
            player.audit('punishment', old_points, f"Punished for {reason}")
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!\n"
                                   f"Your current credit points are: {player.points}")
            await admin_channel.send(f"Player {player.display_name} (ucid={player.ucid}) punished "
                                     f"with credits by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'warn':
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!")
            
        elif punishment['action'] == 'message':
            player.sendUserMessage(f"{player.name}, check your fire: {reason}!")  

    # TODO: change to pubsub
    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with closing(conn.cursor(row_factory=dict_row)) as cursor:
                        for server_name, server in self.bot.servers.items():
                            for row in cursor.execute('SELECT * FROM pu_events_sdw WHERE server_name = %s',
                                                      (server_name, )).fetchall():
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
                                            self.log.warning(
                                                f"No penalty or reason configured for event {row['event']}.")
                                            reason = row['event']
                                        await self.punish(server, player, punishment, reason)
                                        if player.active:
                                            player.sendChatMessage(
                                                f"Your current punishment points are: {row['points']}")
                                        break
                                cursor.execute('DELETE FROM pu_events_sdw WHERE id = %s', (row['id'], ))

    @check_punishments.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        # we need the CreditSystem to be loaded before processing punishments
        while 'CreditSystemMaster' not in self.bot.cogs and 'CreditSystemAgent' not in self.bot.cogs:
            await asyncio.sleep(1)


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
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE pu_events SET server_name = %s WHERE server_name = %s', (new_name, old_name))
                conn.execute('UPDATE pu_events_sdw SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Punishment ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid,))
        elif days > 0:
            conn.execute(f"DELETE FROM pu_events WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Punishment pruned.')

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
            with self.pool.connection() as conn:
                with conn.transaction():
                    with closing(conn.cursor(row_factory=dict_row)) as cursor:
                        for d in self.decay_config:
                            cursor.execute("""
                                UPDATE pu_events SET points = ROUND(points * %s, 2), decay_run = %s 
                                WHERE time < (NOW() - interval '%s days') AND decay_run < %s
                            """, (d['weight'], d['days'], d['days'], d['days']))
                        if self.unban_config:
                            for row in cursor.execute("""
                                SELECT ucid FROM bans b, (
                                    SELECT init_id, SUM(points) AS points 
                                    FROM pu_events 
                                    GROUP BY init_id
                                ) p 
                                WHERE b.ucid = p.init_id AND b.banned_by = %s 
                                AND p.points <= %s
                            """, (self.plugin_name, self.unban_config)).fetchall():
                                for server_name, server in self.bot.servers.items():
                                    server.sendtoDCS({
                                        "command": "unban",
                                        "ucid": row['ucid']
                                    })
                                cursor.execute('DELETE FROM bans WHERE ucid = %s', (row['ucid'], ))
                                banned = cursor.execute('SELECT discord_id, name FROM players WHERE ucid = %s',
                                                        (row['ucid'],)).fetchone()
                                await self.bot.audit(f"Player {banned['name']} (ucid={row['ucid']}) unbanned by "
                                                     f"{self.bot.member.name} due to decay.")
                                if banned['discord_id'] != -1:
                                    with suppress(Exception):
                                        guild = self.bot.guilds[0]
                                        member = await guild.fetch_member(banned['discord_id'])
                                        channel = await member.create_dm()
                                        await channel.send(
                                            f"You have been auto-unbanned from the DCS servers on {guild.name}.\n"
                                            f"Please behave according to the rules to not risk another ban.")

    @commands.command(description='Set punishment to 0 for a user', usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def forgive(self, ctx, user: Union[discord.Member, str]):
        if isinstance(user, str) and len(user) != 32:
            await ctx.send(f'Usage: {ctx.prefix}forgive <@member|ucid>')
            return

        if await utils.yn_question(ctx, 'This will delete all the punishment points for this user.\n'
                                        'Are you sure (Y/N)?') is True:
            with self.pool.connection() as conn:
                with conn.transaction():
                    with closing(conn.cursor()) as cursor:
                        if isinstance(user, discord.Member):
                            ucids = [
                                row[0] for row in cursor.execute('SELECT ucid FROM players WHERE discord_id = %s',
                                                                 (user.id,)).fetchall()
                            ]
                        else:
                            ucids = [user]
                        for ucid in ucids:
                            cursor.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid, ))
                            cursor.execute('DELETE FROM pu_events_sdw WHERE init_id = %s', (ucid, ))
                            cursor.execute("DELETE FROM bans WHERE ucid = %s AND banned_by = %s",
                                           (self.plugin_name, ucid))
                            for server_name, server in self.bot.servers.items():
                                server.sendtoDCS({
                                    "command": "unban",
                                    "ucid": ucid
                                })
                    await ctx.send('All punishment points deleted and player unbanned (if they were banned by the bot '
                                   'before).')

    @commands.command(description='Displays your current penalty points', usage='[member|ucid]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def penalty(self, ctx: commands.Context, member: Optional[Union[discord.Member, str]]):
        if member:
            if not utils.check_roles(['DCS Admin'], ctx.message.author):
                await ctx.send('You need the DCS Admin role to use this command.')
                return
            if isinstance(member, str):
                if len(member) != 32:
                    await ctx.send(f'Usage: {ctx.prefix}penalty [@member] / [ucid]')
                    return
                ucid = member
                member = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = self.bot.get_ucid_by_member(member)
                if not ucid:
                    await ctx.send(
                        "Member {} is not linked to any DCS user.".format(utils.escape_string(member.display_name)))
                    return
        else:
            member = ctx.message.author
            ucid = self.bot.get_ucid_by_member(ctx.message.author)
            if not ucid:
                await ctx.send(f"Use {ctx.prefix}linkme to link your account.")
                return
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute("SELECT event, points, time FROM pu_events WHERE init_id = %s ORDER BY time DESC",
                               (ucid, ))
                if cursor.rowcount == 0:
                    await ctx.send('{} has no penalty points.'.format(member.display_name
                                                                      if isinstance(member, discord.Member)
                                                                      else member))
                    return
                embed = discord.Embed(
                    title="Penalty Points for {}".format(member.display_name
                                                         if isinstance(member, discord.Member)
                                                         else member),
                    color=discord.Color.blue())
                times = events = points = ''
                total = 0.0
                for row in cursor.fetchall():
                    times += f"{row['time']:%m/%d %H:%M}\n"
                    events += ' '.join(row['event'].split('_')).title() + '\n'
                    points += f"{row['points']:.2f}\n"
                    total += row['points']
                embed.description = f"Total penalty points: {total:.2f}"
                embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
                embed.add_field(name='Time', value=times)
                embed.add_field(name='Event', value=events)
                embed.add_field(name='Points', value=points)
                embed.set_footer(text='Points decay over time, you might see different results on different days.')
                if cursor.execute("SELECT COUNT(*) FROM bans b WHERE b.ucid = %s", (ucid, )).fetchone()[0] > 0:
                    unban = self.read_unban_config()
                    if unban:
                        embed.set_footer(text=f"You are currently banned.\nAutomatic unban will happen, if your "
                                              f"points decayed below {unban}.")
                    else:
                        embed.set_footer(text=f"You are currently banned.\n"
                                              f"Please contact an admin if you want to get unbanned.")
                timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
                await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(PunishmentMaster(bot, PunishmentEventListener))
    else:
        await bot.add_cog(PunishmentAgent(bot, PunishmentEventListener))
