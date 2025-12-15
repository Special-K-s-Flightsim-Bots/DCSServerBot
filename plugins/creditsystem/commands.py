import discord

from datetime import timezone
from discord import app_commands, SelectOption
from discord.ext import tasks
from core import utils, Plugin, PluginRequiredError, Group, get_translation, PersistentReport
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import cast

from .listener import CreditSystemListener
from .player import CreditPlayer

_ = get_translation(__name__.split('.')[1])


class CreditSystem(Plugin[CreditSystemListener]):

    async def cog_load(self) -> None:
        await super().cog_load()
        config = self.get_config()
        if config.get('leaderboard'):
            self.update_leaderboard.start()

    async def cog_unload(self) -> None:
        config = self.get_config()
        if config.get('leaderboard'):
            self.update_leaderboard.cancel()
        await super().cog_unload()

    async def get_credits(self, ucid: str) -> list[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT c.id, c.name, COALESCE(SUM(s.points), 0) AS credits 
                    FROM campaigns c LEFT OUTER JOIN credits s ON (c.id = s.campaign_id AND s.player_ucid = %s) 
                    WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
                    GROUP BY 1, 2
                """, (ucid,))
                return await cursor.fetchall()

    async def get_credits_log(self, ucid: str) -> list[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT s.event, s.old_points, s.new_points, remark, time 
                    FROM credits_log s, campaigns c 
                    WHERE s.player_ucid = %s AND s.campaign_id = c.id 
                    AND (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
                    ORDER BY s.time DESC LIMIT 10
                """, (ucid, ))
                return await cursor.fetchall()

    # New command group "/credits"
    credits = Group(name="credits", description=_("Commands to manage player credits"))

    @credits.command(description=_('Shows your current credits'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(member="user")
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
        if member:
            if member != interaction.user and not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_('You need the DCS Admin role to use this command!'),
                                                        ephemeral=True)
                return
            if isinstance(member, str):
                ucid = member
                member = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = await self.bot.get_ucid_by_member(member)
                if not ucid:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        _("Member {} is not linked to any DCS user!").format(utils.escape_string(member.display_name)),
                        ephemeral=True)
                    return
        else:
            member = interaction.user
            ucid = await self.bot.get_ucid_by_member(member)
            if not ucid:
                _mission = self.bot.cogs['Mission']
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(_("Use {} to link your account.").format(
                    (await utils.get_command(self.bot, name=_mission.linkme.name)).mention
                ), ephemeral=True)
                return
        data = await self.get_credits(ucid)
        name = member.display_name if isinstance(member, discord.Member) else member
        if not data:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('{} has no campaign credits.').format(name), ephemeral=True)
            return
        embed = discord.Embed(title=_("Campaign Credits for {}").format(name), color=discord.Color.blue())
        campaigns = points = ''
        for row in data:
            campaigns += row['name'] + '\n'
            points += f"{row['credits']}\n"
        embed.add_field(name=_('Campaign'), value=campaigns)
        embed.add_field(name=_('Credits'), value=points)
        embed.add_field(name='_ _', value='_ _')
        data = await self.get_credits_log(ucid)
        if len(data):
            embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
            times = events = deltas = ''
            for row in data:
                points = row['new_points'] - row['old_points']
                if points == 0:
                    continue
                times += f"<t:{int(row['time'].replace(tzinfo=timezone.utc).timestamp())}:R>\n"
                events += row['event'].title() + '\n'
                deltas += f"{points}\n"
            embed.add_field(name=_('Time'), value=times)
            embed.add_field(name=_('Event'), value=events)
            embed.add_field(name=_('Credits'), value=deltas)
            embed.set_footer(text=_('Log shows the last 10 events only.'))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    async def _admin_donate(self, interaction: discord.Interaction, to: discord.Member, donation: int):
        ephemeral = utils.get_ephemeral(interaction)
        receiver = await self.bot.get_ucid_by_member(to)
        if not receiver:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('{} needs to properly link their DCS account to receive donations.').format(
                    utils.escape_string(to.display_name)), ephemeral=ephemeral)
            return
        data = await self.get_credits(receiver)
        if not data:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('It seems like there is no campaign running on your server(s).'),
                                                    ephemeral=ephemeral)
            return
        if len(data) > 1:
            n = await utils.selection(interaction, title=_("Campaign Credits"),
                                      options=[
                                          SelectOption(
                                              label=f"{x['name']} (credits={x['credits']})",
                                              value=str(idx),
                                              default=(idx == 0)
                                          )
                                          for idx, x in enumerate(data)
                                      ])
        else:
            n = 0
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        p_receiver: CreditPlayer | None = None
        for server in self.bot.servers.values():
            p_receiver = cast(CreditPlayer, server.get_player(ucid=receiver))
            if p_receiver:
                break
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if not p_receiver:
                    cursor = await conn.execute("""
                        SELECT COALESCE(SUM(points), 0) 
                        FROM credits 
                        WHERE campaign_id = %s AND player_ucid = %s
                    """, (data[n]['id'], receiver))
                    old_points_receiver = (await cursor.fetchone())[0]
                else:
                    old_points_receiver = p_receiver.points
                if 'max_points' in self.get_config() and \
                        (old_points_receiver + donation) > int(self.get_config()['max_points']):
                    await interaction.followup.send(
                        _('Member {} would overrun the configured maximum points with this donation. Aborted.').format(
                            utils.escape_string(to.display_name)), ephemeral=True
                    )
                    return
                if p_receiver:
                    # make sure we do not donate to a squadron
                    squadron = p_receiver.squadron
                    p_receiver.squadron = None
                    p_receiver.points += donation
                    p_receiver.squadron = squadron
                    p_receiver.audit('donation', old_points_receiver,
                                     _('Donation from member {}').format(interaction.user.display_name))
                else:
                    await conn.execute("""
                        INSERT INTO credits (campaign_id, player_ucid, points) 
                        VALUES (%s, %s, %s) 
                        ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                        SET points = credits.points + EXCLUDED.points
                    """, (data[n]['id'], receiver, donation))
                    cursor = await conn.execute("""
                        SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s
                    """, (data[n]['id'], receiver))
                    new_points_receiver = (await cursor.fetchone())[0]
                    await conn.execute("""
                        INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (data[n]['id'], 'donation', receiver, old_points_receiver, new_points_receiver,
                          _('Credit points change by Admin {}').format(interaction.user.display_name)))
            if donation > 0:
                try:
                    await (await to.create_dm()).send(
                        _('You just received {} credit points from an Admin.').format(donation))
                except discord.Forbidden:
                    await interaction.followup.send(
                        to.mention + _(', you just received {} credit points from an Admin.').format(donation))
            else:
                try:
                    await (await to.create_dm()).send(
                        _('Your credits were decreased by {} credit points by an Admin.').format(donation))
                except discord.Forbidden:
                    await interaction.followup.send(
                        to.mention + _(', your credits were decreased by {} credit points by an Admin.').format(
                            donation))
            await interaction.followup.send(
                _('Donated {credits} points to {name}.').format(credits=donation, name=to.display_name),
                ephemeral=ephemeral)

    @credits.command(description=_('Donate credits to another member'))
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def donate(self, interaction: discord.Interaction, to: discord.Member, donation: int):
        if utils.check_roles(set(self.bot.roles['Admin'] + self.bot.roles['DCS Admin']), interaction.user):
            await self._admin_donate(interaction, to, donation)
            return
        if interaction.user == to:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You can't donate to yourself."), ephemeral=True)
            return
        if donation < 1:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Your donation has to be > 0."), ephemeral=True)
            return
        receiver = await self.bot.get_ucid_by_member(to)
        if not receiver:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _('{} needs to properly link their DCS account to receive donations.').format(utils.escape_string(
                    to.display_name)), ephemeral=True)
            return
        donor = await self.bot.get_ucid_by_member(interaction.user)
        if not donor:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('You need to properly link your DCS account to give donations!'),
                                                    ephemeral=True)
            return
        data = await self.get_credits(donor)
        if not data:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("You don't have any credit points to donate."), ephemeral=True)
            return
        elif len(data) > 1:
            n = await utils.selection(interaction, title=_("Campaign Credits"),
                                      options=[
                                          SelectOption(label=f"{x['name']} (credits={x['credits']})", value=str(idx))
                                          for idx, x in enumerate(data)
                                      ])
        else:
            n = 0
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        if data[n]['credits'] < donation:
            await interaction.followup.send(
                _("You can't donate {donation} credit points, you only have {total}!").format(donation=donation,
                                                                                              total=data[n]['credits']),
                ephemeral=True)
            return
        # now see, if one of the parties is an active player already...
        p_donor: CreditPlayer | None = None
        for server in self.bot.servers.values():
            p_donor = cast(CreditPlayer, server.get_player(ucid=donor))
            if p_donor:
                break
        p_receiver: CreditPlayer | None = None
        for server in self.bot.servers.values():
            p_receiver = cast(CreditPlayer, server.get_player(ucid=receiver))
            if p_receiver:
                break
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if not p_receiver:
                    cursor = await conn.execute("""
                        SELECT COALESCE(SUM(points), 0) FROM credits WHERE campaign_id = %s AND player_ucid = %s
                    """, (data[n]['id'], receiver))
                    old_points_receiver = (await cursor.fetchone())[0]
                else:
                    old_points_receiver = p_receiver.points
                if 'max_points' in self.get_config() and \
                        (old_points_receiver + donation) > int(self.get_config()['max_points']):
                    await interaction.followup.send(
                        _('Member {} would overrun the configured maximum points with this donation. Aborted.').format(
                            utils.escape_string(to.display_name)), ephemeral=True)
                    return
                if p_donor:
                    squadron = p_donor.squadron
                    p_donor.squadron = None
                    p_donor.points -= donation
                    p_donor.squadron = squadron
                    p_donor.audit('donation', data[n]['credits'], _('Donation to member {}').format(to.display_name))
                else:
                    await conn.execute("""
                        UPDATE credits SET points = points - %s WHERE campaign_id = %s AND player_ucid = %s
                    """, (donation, data[n]['id'], donor))
                    cursor = await conn.execute("""
                        SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s
                    """, (data[n]['id'], donor))
                    new_points_donor = (await cursor.fetchone())[0]
                    await conn.execute("""
                        INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (data[n]['id'], 'donation', donor, data[n]['credits'], new_points_donor,
                          _('Donation to member {}').format(to.display_name)))
                if p_receiver:
                    # make sure we do not donate to a squadron
                    squadron = p_receiver.squadron
                    p_receiver.squadron = None
                    p_receiver.points += donation
                    p_receiver.squadron = squadron
                    p_receiver.audit('donation', old_points_receiver,
                                     _('Donation from member {}').format(interaction.user.display_name))
                else:
                    await conn.execute("""
                        INSERT INTO credits (campaign_id, player_ucid, points) 
                        VALUES (%s, %s, %s) 
                        ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                        SET points = credits.points + EXCLUDED.points
                    """, (data[n]['id'], receiver, donation))
                    cursor = await conn.execute("""
                        SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s
                    """, (data[n]['id'], receiver))
                    new_points_receiver = (await cursor.fetchone())[0]
                    await conn.execute("""
                        INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (data[n]['id'], 'donation', receiver, old_points_receiver, new_points_receiver,
                          _('Donation from member {}').format(interaction.user.display_name)))
            try:
                await (await to.create_dm()).send(
                    _('You just received {donation} credit points from {member}!').format(
                        donation=donation, member=utils.escape_string(interaction.user.display_name)))
            except discord.Forbidden:
                await interaction.followup.send(
                    to.mention + _(', you just received {donation} credit points from {member}!').format(
                        donation=donation, member=utils.escape_string(interaction.user.display_name)))

    @credits.command(description=_('Reset credits for a player or a whole campaign'))
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def reset(self, interaction: discord.Interaction,
                    user: app_commands.Transform[discord.Member | str, utils.UserTransformer] | None = None):
        if user:
            if isinstance(user, discord.Member):
                ucid = await self.bot.get_ucid_by_member(user)
                name = user.display_name
            else:
                ucid = user
                _user = await self.bot.get_member_or_name_by_ucid(ucid)
                if isinstance(user, discord.Member):
                    name = _user.display_name
                else:
                    name = _user
        else:
            ucid = None

        ephemeral = utils.get_ephemeral(interaction)
        sql = "UPDATE credits SET points = 0 WHERE campaign_id = %(campaign_id)s"
        if user:
            message = _("I'm going to delete the campaign credits of user\n"
                        "{} for the running campaign.").format(name)
            sql += " AND player_ucid = %(ucid)s"
        else:
            message = _("I'm going to delete all campaign credits for the running campaign.")
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send(_("Aborted."))
            return

        campaign_id, campaign_name = utils.get_running_campaign(self.node)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute(sql, {
                    "campaign_id": campaign_id,
                    "ucid": ucid
                })
        await interaction.followup.send(_('Campaign credits have been deleted.'), ephemeral=ephemeral)

    @tasks.loop(minutes=5)
    async def update_leaderboard(self):
        config = self.get_config().get('leaderboard', {})
        report = PersistentReport(self.bot, self.plugin_name, "leaderboard.json",
                                  embed_name="credits_leaderboard",
                                  channel_id=config['channel'])
        await report.render(limit=config.get('limit', 10))

    @update_leaderboard.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(CreditSystem(bot, CreditSystemListener))
