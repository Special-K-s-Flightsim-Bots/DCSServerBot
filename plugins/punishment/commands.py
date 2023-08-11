import asyncio
import discord
import psycopg2

from contextlib import closing, suppress
from copy import deepcopy
from core import DCSServerBot, Plugin, PluginRequiredError, TEventListener, utils, Player, Server, Channel, \
    PluginInstallationError
from datetime import timezone, datetime, timedelta
from discord.ext import tasks, commands
from typing import Type, Union, Optional, cast

from .listener import PunishmentEventListener
from ..creditsystem.player import CreditPlayer


class PunishmentAgent(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.json file found!", plugin=self.plugin_name)
        self.check_punishments.start()

    async def cog_unload(self):
        self.check_punishments.cancel()
        await super().cog_unload()

    def migrate(self, version: str) -> None:
        if version in["1.4", "1.5"]:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                # migrate active bans from the punishment system and migrate them to the new method (fix days only)
                    cursor.execute("SELECT ucid, banned_at FROM bans WHERE banned_by = %s", (self.plugin_name, ))
                    for row in cursor.fetchall():
                        for server in self.bot.servers.values():
                            config = self.get_config(server)
                            now = datetime.now()
                            delta = now - row[1]
                            ban_days = next(x.get('days', 3) for x in config.get('punishments', {}) if x['action'] == 'ban')
                            if delta.days < ban_days:
                                cursor.execute("UPDATE bans SET banned_until = %s WHERE ucid = %s",
                                               (now + timedelta(ban_days - delta.days), row[0]))
                            else:
                                cursor.execute("DELETE FROM bans WHERE ucid = %s", (row[0]))
                conn.commit()
            except (Exception, psycopg2.DatabaseError) as error:
                self.log.exception(error)
                conn.rollback()
            finally:
                self.pool.putconn(conn)

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

    @commands.command(name='punish', description='Adds punishment points to a user', usage='<member|ucid> <points>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def _punish(self, ctx, user: Union[discord.Member, str], points: int):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return
        if isinstance(user, str):
            if not utils.is_ucid(user):
                await ctx.send(f'Usage: {ctx.prefix}punish <@member|ucid> <points>')
                return
            else:
                ucid = user
        else:
            ucid = self.bot.get_ucid_by_member(user)
            if not ucid:
                await ctx.send(f'Member {user.display_name} not linked.')
                return
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    INSERT INTO pu_events (init_id, server_name, event, points)
                    VALUES (%s, %s, %s, %s) 
                """, (ucid, server.name, 'admin', points))
            conn.commit()
            await ctx.send(f'User punished with {points} points.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    async def punish(self, server: Server, ucid: str, punishment: dict, reason: str, points: Optional[float] = None):
        player: Player = server.get_player(ucid=ucid, active=True)
        member = self.bot.get_member_by_ucid(ucid)
        if punishment['action'] == 'ban':
            until = datetime.now() + timedelta(days=punishment.get('days', 3))
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor()) as cursor:
                    cursor.execute("""
                        INSERT INTO bans (ucid, banned_by, reason, banned_until) 
                        VALUES (%s, %s, %s, %s) 
                        ON CONFLICT DO NOTHING
                    """, (ucid, self.plugin_name, reason, until))
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
                    "ucid": ucid,
                    "reason": reason,
                    "banned_until": until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M') + ' (UTC)'
                })
            if member:
                message = "Member {} banned by {} for {}.".format(utils.escape_string(member.display_name),
                                                                  utils.escape_string(self.bot.member.name), reason)
                await server.get_channel(Channel.ADMIN).send(message)
                await self.bot.audit(message)
                with suppress(Exception):
                    guild = self.bot.guilds[0]
                    channel = await member.create_dm()
                    await channel.send("You have been banned from the DCS servers on {} for {}.\n"
                                       "To check your current penalty status, use the {}penalty "
                                       "command.".format(utils.escape_string(guild.name), reason,
                                                         self.bot.config['BOT']['COMMAND_PREFIX']))
            elif player:
                message = f"Player {player.display_name} (ucid={player.ucid}) banned by {self.bot.member.name} " \
                          f"for {reason}."
                await server.get_channel(Channel.ADMIN).send(message)
                await self.bot.audit(message)
            else:
                message = f"Player with ucid {ucid} banned by {self.bot.member.name} for {reason}."
                await server.get_channel(Channel.ADMIN).send(message)
                await self.bot.audit(message)

        # everything after that point can only be executed if players are active
        if not player:
            return

        if punishment['action'] == 'kick' and player.active:
            server.kick(player, reason)
            await server.get_channel(Channel.ADMIN).send(f"Player {player.display_name} (ucid={player.ucid}) kicked by "
                                                         f"{self.bot.member.name} for {reason}.")
            return

        elif punishment['action'] == 'move_to_spec':
            server.move_to_spectators(player)
            player.sendChatMessage(f"You've been kicked back to spectators because of: {reason}.")
            await server.get_channel(Channel.ADMIN).send(f"Player {player.display_name} (ucid={player.ucid}) moved to "
                                                         f"spectators by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'credits' and type(player).__name__ == 'CreditPlayer':
            player: CreditPlayer = cast(CreditPlayer, player)
            old_points = player.points
            player.points -= punishment['penalty']
            player.audit('punishment', old_points, f"Punished for {reason}")
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!\n"
                                   f"Your current credit points are: {player.points}")
            await server.get_channel(Channel.ADMIN).send(f"Player {player.display_name} (ucid={player.ucid}) punished "
                                                         f"with credits by {self.bot.member.name} for {reason}.")

        elif punishment['action'] == 'warn':
            player.sendUserMessage(f"{player.name}, you have been punished for: {reason}!")
            
        elif punishment['action'] == 'message':
            player.sendUserMessage(f"{player.name}, check your fire: {reason}!")
        if points:
            player.sendChatMessage(f"Your current punishment points are: {points}")

    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    for server_name, server in self.bot.servers.items():
                        cursor.execute('SELECT * FROM pu_events_sdw WHERE server_name = %s', (server_name, ))
                        for row in cursor.fetchall():
                            try:
                                config = self.get_config(server)
                                # we are not initialized correctly yet
                                if not config:
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
                                        await self.punish(server, row['init_id'], punishment, reason, row['points'])
                                        break
                            finally:
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
        # we need the CreditSystem to be loaded before processing punishments
        while 'CreditSystemMaster' not in self.bot.cogs and 'CreditSystemAgent' not in self.bot.cogs:
            await asyncio.sleep(1)


class PunishmentMaster(PunishmentAgent):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.decay_config = self.read_decay_config()
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

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Punishment ...')
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid,))
            elif days > 0:
                cursor.execute(f"DELETE FROM pu_events WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Punishment pruned.')

    def read_decay_config(self):
        if 'configs' in self.locals:
            for element in self.locals['configs']:
                if 'decay' in element:
                    return element['decay']
        return None

    @tasks.loop(hours=12.0)
    async def decay(self):
        if self.decay_config:
            self.log.debug('Punishment - Running decay.')
            conn = self.pool.getconn()
            try:
                with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                    for d in self.decay_config:
                        cursor.execute("""
                            UPDATE pu_events 
                            SET points = ROUND((points * %s)::numeric, 2), decay_run = %s 
                            WHERE time < (timezone('utc', now()) - interval '%s days') AND decay_run < %s
                        """, (d['weight'], d['days'], d['days'], d['days']))
                        cursor.execute("DELETE FROM pu_events WHERE points = 0")
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
        if isinstance(user, str) and not utils.is_ucid(user):
            await ctx.send(f'Usage: {ctx.prefix}forgive <@member|ucid>')
            return

        if await utils.yn_question(ctx, 'This will delete all the punishment points for this user.\n'
                                        'Are you sure (Y/N)?') is True:
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

    @commands.command(description='Displays your current penalty points', usage='[member|ucid]')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def penalty(self, ctx: commands.Context, member: Optional[Union[discord.Member, str]]):
        if member:
            if not utils.check_roles(['DCS Admin'], ctx.message.author):
                await ctx.send('You need the DCS Admin role to use this command.')
                return
            if isinstance(member, str):
                if not utils.is_ucid(member):
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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute("SELECT event, points, time FROM pu_events WHERE init_id = %s ORDER BY time DESC",
                               (ucid, ))
                if cursor.rowcount == 0:
                    await ctx.send('{} has no penalty points.'.format(
                        member.display_name if isinstance(member, discord.Member) else member)
                    )
                    return
                embed = discord.Embed(
                    title="Penalty Points for {}".format(
                        member.display_name if isinstance(member, discord.Member) else member
                    ),
                    color=discord.Color.blue()
                )
                times = events = points = ''
                total = 0.0
                for row in cursor.fetchall():
                    times += f"{row['time']:%m/%d %H:%M}\n"
                    events += ' '.join(row['event'].split('_')).title() + '\n'
                    points += f"{row['points']:.2f}\n"
                    total += row['points']
                embed.description = f"Total penalty points: {total:.2f}"
                embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
                embed.add_field(name='Time (UTC)', value=times)
                embed.add_field(name='Event', value=events)
                embed.add_field(name='Points', value=points)
                embed.set_footer(text='Points decay over time, you might see different results on different days.')
                cursor.execute("SELECT reason, banned_until FROM bans b WHERE b.ucid = %s", (ucid, ))
                if cursor.rowcount > 0:
                    row = cursor.fetchone()
                    if row[1].year == 9999:
                        until = 'never'
                    else:
                        until = f"<t:{int(row[1].astimezone(timezone.utc).timestamp())}:f>"
                    embed.add_field(name="Ban expires", value=until)
                    embed.add_field(name="Reason", value=row[0])
                    embed.add_field(name='_ _', value='_ _')
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
