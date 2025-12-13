import asyncio
import discord
import psycopg

from contextlib import suppress
from core import (Plugin, PluginRequiredError, utils, Player, Server, PluginInstallationError, command, DEFAULT_TAG,
                  Report, get_translation)
from discord import app_commands
from discord.app_commands import Range
from discord.ext import tasks
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Type, cast

from .listener import PunishmentEventListener
from ..creditsystem.player import CreditPlayer

_ = get_translation(__name__.split('.')[1])


class Punishment(Plugin[PunishmentEventListener]):
    def __init__(self, bot: DCSServerBot, eventlistener: Type[PunishmentEventListener] = None):
        super().__init__(bot, eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)
        self.check_punishments.add_exception_type(psycopg.DatabaseError)
        self.check_punishments.add_exception_type(discord.DiscordException)
        self.check_punishments.add_exception_type(KeyError)
        self.check_punishments.start()
        self.decay_config = self.locals.get(DEFAULT_TAG, {}).get('decay')
        self.decay.add_exception_type(psycopg.DatabaseError)
        self.decay.start()

    async def cog_unload(self):
        self.decay.cancel()
        self.check_punishments.cancel()
        await super().cog_unload()

    async def punish(self, server: Server, ucid: str, punishment: dict, reason: str, points: float | None = None):
        player: Player = server.get_player(ucid=ucid)
        member = self.bot.get_member_by_ucid(ucid)
        admin_channel = self.bot.get_admin_channel(server)
        if punishment['action'] == 'ban':
            # we must not punish for reslots here
            self.eventlistener.pending_kill.pop(ucid, None)
            await self.bus.ban(ucid, self.plugin_name, reason, punishment.get('days', 3))
            if member:
                message = _("Member {member} banned by {banned_by} for {reason}.").format(
                    member=utils.escape_string(member.display_name),
                    banned_by=utils.escape_string(self.bot.member.name),
                    reason=reason)
                if admin_channel:
                    await admin_channel.send(message)
                await self.bot.audit(message)
                with suppress(Exception):
                    guild = self.bot.guilds[0]
                    channel = await member.create_dm()
                    await channel.send(_("You have been banned from the DCS servers on {guild} for {reason} for "
                                         "the amount of {days} days.").format(guild=utils.escape_string(guild.name),
                                                                              reason=reason,
                                                                              days=punishment.get('days', 3)))
            elif player:
                message = _("Player {player} (ucid={ucid}) banned by {banned_by} for {reason}.").format(
                    player=player.display_name, ucid=player.ucid, banned_by=self.bot.member.name, reason=reason)
                if admin_channel:
                    await admin_channel.send(message)
                await self.bot.audit(message)
            else:
                message = _("Player with ucid {ucid} banned by {banned_by} for {reason}.").format(
                    ucid=ucid, banned_by=self.bot.member.name, reason=reason)
                if admin_channel:
                    await admin_channel.send(message)
                await self.bot.audit(message)

        # everything after that point can only be executed if players are active
        if not player:
            return

        if punishment['action'] == 'kick' and player.active:
            # we must not punish for reslots here
            self.eventlistener.pending_kill.pop(ucid, None)
            await server.kick(player, reason)
            if admin_channel:
                await admin_channel.send(
                    _("Player {player} (ucid={ucid}) kicked by {kicked_by} for {reason}.").format(
                        player=player.display_name, ucid=player.ucid, kicked_by=self.bot.member.name, reason=reason))

        elif punishment['action'] == 'move_to_spec':
            # we must not punish for reslots here
            self.eventlistener.pending_kill.pop(ucid, None)
            await server.move_to_spectators(player)
            await player.sendUserMessage(_("You've been kicked back to spectators because of: {}.").format(reason))
            if admin_channel:
                await admin_channel.send(
                    _("Player {player} (ucid={ucid}) moved to spectators by {spec_by} for {reason}.").format(
                        player=player.display_name, ucid=player.ucid, spec_by=self.bot.member.name, reason=reason))

        elif punishment['action'] == 'credits' and type(player).__name__ == 'CreditPlayer':
            player: CreditPlayer = cast(CreditPlayer, player)
            old_points = player.points
            player.points -= punishment['penalty']
            player.audit('punishment', old_points, _("Punished for {}").format(reason))
            await player.sendUserMessage(
                _("{name}, you have been punished for: {reason}!\n"
                  "Your current credit points are: {points}").format(
                    name=player.name, reason=reason, points=player.points))
            if admin_channel:
                await admin_channel.send(
                    _("Player {player} (ucid={ucid}) punished with credits by {punished_by} for {reason}.").format(
                        player=player.display_name, ucid=player.ucid, punished_by=self.bot.member.name, reason=reason))

        elif punishment['action'] == 'warn':
            await player.sendUserMessage(_("{name}, you have been punished for: {reason}!").format(name=player.name,
                                                                                                   reason=reason))
            
        elif punishment['action'] == 'message':
            await player.sendUserMessage(_("{name}, check your fire: {reason}!").format(name=player.name,
                                                                                        reason=reason))
        if points:
            await player.sendUserMessage(_("{name}, you have {points} punishment points.").format(name=player.name,
                                                                                                  points=points))

    # TODO: change to pubsub
    @tasks.loop(minutes=1.0)
    async def check_punishments(self):
        async with self.eventlistener.lock:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        for server_name, server in self.bot.servers.items():
                            config = self.get_config(server)
                            # we are not initialized correctly yet
                            if not config:
                                continue
                            await cursor.execute("""
                                SELECT * FROM pu_events_sdw WHERE server_name = %s
                            """, (server_name,))
                            rows = await cursor.fetchall()
                            for row in rows:
                                try:
                                    for punishment in config.get('punishments', {}):
                                        if row['points'] < punishment['points']:
                                            continue
                                        reason = None
                                        for penalty in config.get('penalties', []):
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
                                    await cursor.execute('DELETE FROM pu_events_sdw WHERE id = %s', (row['id'], ))

    @check_punishments.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        # we need the CreditSystem to be loaded before processing punishments
        while 'CreditSystem' not in self.bot.cogs:
            await asyncio.sleep(1)

    @tasks.loop(hours=1.0)
    async def decay(self):
        if self.decay_config:
            self.log.debug('Punishment - Running decay ...')
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    for d in self.decay_config:
                        days = d['days']
                        await conn.execute(f"""
                            UPDATE pu_events SET points = ROUND((points * %s)::numeric, 2), decay_run = %s 
                            WHERE time < (timezone('utc', now()) - interval '{days} days') AND decay_run < %s
                        """, (d['weight'], days, days))
                        await conn.execute("DELETE FROM pu_events WHERE points = 0.0")

    @decay.before_loop
    async def before_decay(self):
        await self.bot.wait_until_ready()

    @command(name='punish', description=_('Adds punishment points to a user\n'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def _punish(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer],
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer],
                      points: int, reason: str | None = 'admin'):

        ephemeral = utils.get_ephemeral(interaction)
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("User {} is not linked.").format(user.display_name),
                                                        ephemeral=ephemeral)
                return
        elif user is not None:
            ucid = user
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("The UCID provided is invalid."), ephemeral=True)
            return

        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO pu_events (init_id, server_name, event, points)
                    VALUES (%s, %s, %s, %s) 
                """, (ucid, server.name, reason, points))

        if points >= 0:
            message = _('User punished with {} points.').format(points)
        else:
            message = _('The users punishment points have been reduced by {} points.').format(abs(points))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(message, ephemeral=ephemeral)
        await self.bot.audit(
            _("changed punishment points of user {ucid} by {points} points.").format(ucid=ucid, points=points),
            user=interaction.user
        )

    @command(description=_('Deletes a users punishment points'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def forgive(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer]):
        ephemeral = utils.get_ephemeral(interaction)

        if not user:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("The user provided is invalid."), ephemeral=True)
            return

        if await utils.yn_question(
                interaction,
                _("This will delete all the punishment points for this user and unban them if they were banned.\n"
                  "Are you sure?"), ephemeral=ephemeral):
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    if isinstance(user, discord.Member):
                        cursor = await conn.execute('SELECT ucid FROM players WHERE discord_id = %s', (user.id,))
                        ucids = [row[0] async for row in cursor]
                        if not ucids:
                            await interaction.followup.send(f"User {user.display_name} is not linked.",
                                                            ephemeral=True)
                    else:
                        ucids = [user]

                    for ucid in ucids:
                        await conn.execute('DELETE FROM pu_events WHERE init_id = %s', (ucid, ))
                        await conn.execute('DELETE FROM pu_events_sdw WHERE init_id = %s', (ucid, ))
                        await self.bus.unban(ucid)

            await interaction.followup.send(
                _("All punishment points deleted and player unbanned (if they were banned by the bot before)."),
                ephemeral=ephemeral)
            await self.bot.audit(_("forgave player {}").format(ucid), user=interaction.user)

    @command(description=_('Displays the current penalty points'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def penalty(self, interaction: discord.Interaction,
                      user: app_commands.Transform[str | discord.Member, utils.UserTransformer] | None):
        ephemeral = utils.get_ephemeral(interaction)
        if user and user != interaction.user:
            if not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _('You need the DCS Admin role to show penalty points for other users.'), ephemeral=True)
                return
            if isinstance(user, str):
                ucid = user
                user = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = await self.bot.get_ucid_by_member(user)
                if not ucid:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _("Member {} is not linked.").format(utils.escape_string(user.display_name)), ephemeral=True)
                    return
        else:
            user = interaction.user
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _("Use {} to link your Discord and DCS accounts first.").format(
                        (await utils.get_command(self.bot, name='linkme')).mention
                    ), ephemeral=True)
                return
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT event, points, time FROM pu_events WHERE init_id = %s ORDER BY time DESC",
                                     (ucid, ))
                if cursor.rowcount == 0:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('User has no penalty points.'), ephemeral=ephemeral)
                    return
                embed = discord.Embed(
                    title=_("Penalty Points for {}").format(
                        user.display_name if isinstance(user, discord.Member) else user),
                    color=discord.Color.blue())
                times = events = points = ''
                total = 0.0
                async for row in cursor:
                    times += f"{row['time']:%m-%d %H:%M}\n"
                    events += ' '.join(row['event'].split('_')).title() + '\n'
                    points += f"{row['points']:.2f}\n"
                    total += float(row['points'])

        embed.description = _("Total penalty points: {total:.2f}").format(total=total)
        embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
        embed.add_field(name=_('Time (UTC)'), value=times)
        embed.add_field(name=_('Event'), value=events)
        embed.add_field(name=_('Points'), value=points)
        embed.set_footer(text=_('Points decay over time, you might see different results on different days.'))
        # check bans
        ban = await self.bus.is_banned(ucid)
        if ban:
            if ban['banned_until'].year == 9999:
                until = _('never')
            else:
                until = ban['banned_until'].strftime('%y-%m-%d %H:%M')
            embed.add_field(name=_("Ban expires"), value=until)
            embed.add_field(name=_("Reason"), value=ban['reason'])
            embed.add_field(name='_ _', value='_ _')
            embed.set_footer(text=_("You are currently banned.\n"
                                    "Please contact a member of the server staff, if you want to get unbanned."))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)

    @command(description=_('Show last infractions of a user'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    async def infractions(self, interaction: discord.Interaction,
                          user: app_commands.Transform[discord.Member | str, utils.UserTransformer],
                          limit: Range[int, 3, 20] | None = 10):
        if not user:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("This user does not exist. Try {} to find them in the historic data.").format(
                    (await utils.get_command(self.bot, name='find')).mention
                ),
                ephemeral=True)
            return
        if isinstance(user, str):
            ucid = user
        else:
            ucid = await self.bot.get_ucid_by_member(user)
            if not ucid:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(
                    _("Member {} is not linked.").format(utils.escape_string(user.display_name)), ephemeral=True)
                return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, 'events.json')
        env = await report.render(ucid=ucid, limit=limit)
        await interaction.followup.send(embed=env.embed, ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Punishment(bot, PunishmentEventListener))
