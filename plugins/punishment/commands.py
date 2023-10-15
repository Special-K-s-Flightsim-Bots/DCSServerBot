import asyncio
import discord
import psycopg

from contextlib import closing, suppress
from core import Plugin, PluginRequiredError, TEventListener, utils, Player, Server, PluginInstallationError, \
    command, DEFAULT_TAG
from datetime import timezone
from discord import app_commands
from discord.ext import tasks
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Type, Union, cast, Optional

from .listener import PunishmentEventListener
from ..creditsystem.player import CreditPlayer


class Punishment(Plugin):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)
        self.check_punishments.add_exception_type(psycopg.DatabaseError)
        self.check_punishments.start()
        self.decay_config = self.locals.get(DEFAULT_TAG, {}).get('decay')
        self.decay.add_exception_type(psycopg.DatabaseError)
        self.decay.start()

    async def cog_unload(self):
        self.decay.cancel()
        self.check_punishments.cancel()
        await super().cog_unload()

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE pu_events SET server_name = %s WHERE server_name = %s', (new_name, old_name))
        conn.execute('UPDATE pu_events_sdw SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Punishment ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid,))
        elif days > -1:
            conn.execute(f"DELETE FROM pu_events WHERE time < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Punishment pruned.')

    async def punish(self, server: Server, ucid: str, punishment: dict, reason: str, points: Optional[float] = None):
        player: Player = server.get_player(ucid=ucid, active=True)
        member = self.bot.get_member_by_ucid(ucid)
        admin_channel = self.bot.get_admin_channel(server)
        if punishment['action'] == 'ban':
            self.bus.ban(ucid, self.plugin_name, reason, punishment.get('days', 3))
            if member:
                message = "Member {} banned by {} for {}.".format(utils.escape_string(member.display_name),
                                                                  utils.escape_string(self.bot.member.name), reason)
                await admin_channel.send(message)
                await self.bot.audit(message)
                with suppress(Exception):
                    guild = self.bot.guilds[0]
                    channel = await member.create_dm()
                    await channel.send("You have been banned from the DCS servers on {} for {} for the amount of {} "
                                       "days.\n".format(utils.escape_string(guild.name), reason,
                                                        punishment.get('days', 3)))
            elif player:
                message = f"Player {player.display_name} (ucid={player.ucid}) banned by {self.bot.member.name} " \
                          f"for {reason}."
                await admin_channel.send(message)
                await self.bot.audit(message)
            else:
                message = f"Player with ucid {ucid} banned by {self.bot.member.name} for {reason}."
                await admin_channel.send(message)
                await self.bot.audit(message)

        # everything after that point can only be executed if players are active
        if not player:
            return

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
            player: CreditPlayer = cast(CreditPlayer, player)
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
        if points:
            player.sendChatMessage(f"Your current punishment points are: {points}")

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

    @check_punishments.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        # we need the CreditSystem to be loaded before processing punishments
        while 'CreditSystem' not in self.bot.cogs:
            await asyncio.sleep(1)

    @tasks.loop(hours=12.0)
    async def decay(self):
        if self.decay_config:
            self.log.debug('Punishment - Running decay ...')
            with self.pool.connection() as conn:
                with conn.transaction():
                    for d in self.decay_config:
                        conn.execute("""
                            UPDATE pu_events SET points = ROUND((points * %s)::numeric, 2), decay_run = %s 
                            WHERE time < (NOW() - interval '%s days') AND decay_run < %s
                        """, (d['weight'], d['days'], d['days'], d['days']))
                        conn.execute("DELETE FROM pu_events WHERE points = 0.0")

    @command(description='Delete all punishment points for a given user')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def forgive(self, interaction: discord.Interaction,
                      user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        if await utils.yn_question(interaction,
                                   'This will delete all the punishment points for this user and unban them '
                                   'if they were banned.\nAre you sure (Y/N)?'):
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
                                server.send_to_dcs({
                                    "command": "unban",
                                    "ucid": ucid
                                })
                    await interaction.followup.send('All punishment points deleted and player unbanned '
                                                    '(if they were banned by the bot before).')

    @command(description='Displays your current penalty points')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def penalty(self, interaction: discord.Interaction,
                      user: app_commands.Transform[Union[str, discord.Member], utils.UserTransformer]):
        if user:
            if not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
                await interaction.response.send_message('You need the DCS Admin role to use this command.',
                                                        ephemeral=True)
                return
            if isinstance(user, str):
                ucid = user
                user = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = self.bot.get_ucid_by_member(user)
                if not ucid:
                    await interaction.response.send_message(
                        f"Member {utils.escape_string(user.display_name)} is not linked to any DCS user.",
                        ephemeral=True)
                    return
        else:
            user = interaction.user
            ucid = self.bot.get_ucid_by_member(user)
            if not ucid:
                await interaction.response.send_message(f"Use `/linkme` to link your account first.", ephemeral=True)
                return
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute("SELECT event, points, time FROM pu_events WHERE init_id = %s ORDER BY time DESC",
                               (ucid, ))
                if cursor.rowcount == 0:
                    await interaction.response.send_message('User has no penalty points.')
                    return
                embed = discord.Embed(
                    title="Penalty Points for {}".format(user.display_name
                                                         if isinstance(user, discord.Member)
                                                         else user),
                    color=discord.Color.blue())
                times = events = points = ''
                total = 0.0
                for row in cursor.fetchall():
                    times += f"{row['time']:%m/%d %H:%M}\n"
                    events += ' '.join(row['event'].split('_')).title() + '\n'
                    points += f"{row['points']:.2f}\n"
                    total += float(row['points'])
                embed.description = f"Total penalty points: {total:.2f}"
                embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
                embed.add_field(name='Time (UTC)', value=times)
                embed.add_field(name='Event', value=events)
                embed.add_field(name='Points', value=points)
                embed.set_footer(text='Points decay over time, you might see different results on different days.')
                embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
                # check bans
                ban = self.bus.is_banned(ucid)
                if ban:
                    if ban['banned_until'].year == 9999:
                        until = 'never'
                    else:
                        until = f"<t:{int(ban['banned_until'].astimezone(timezone.utc).timestamp())}:f>"
                    embed.add_field(name="Ban expires", value=until)
                    embed.add_field(name="Reason", value=ban['reason'])
                    embed.add_field(name='_ _', value='_ _')
                    embed.set_footer(text=f"You are currently banned.\n"
                                          f"Please contact an admin if you want to get unbanned.")
                await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Punishment(bot, PunishmentEventListener))
