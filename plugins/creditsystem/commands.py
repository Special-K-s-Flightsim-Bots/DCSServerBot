import discord
from contextlib import closing
from discord import app_commands, SelectOption
from discord.app_commands import Range
from core import utils, Plugin, PluginRequiredError, Group
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, cast, Union

from .listener import CreditSystemListener
from .player import CreditPlayer


class CreditSystem(Plugin):

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Creditsystem ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM credits WHERE player_ucid = %s', (ucid,))
                conn.execute('DELETE FROM credits_log WHERE player_ucid = %s', (ucid,))
        self.log.debug('Creditsystem pruned.')

    def get_credits(self, ucid: str) -> list[dict]:
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return list(cursor.execute("""
                    SELECT c.id, c.name, COALESCE(SUM(s.points), 0) AS credits 
                    FROM campaigns c LEFT OUTER JOIN credits s ON (c.id = s.campaign_id AND s.player_ucid = %s) 
                    WHERE NOW() BETWEEN c.start AND COALESCE(c.stop, NOW()) 
                    GROUP BY 1, 2
                """, (ucid, )).fetchall())

    def get_credits_log(self, ucid: str) -> list[dict]:
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return list(cursor.execute("""
                    SELECT s.event, s.old_points, s.new_points, remark, time 
                    FROM credits_log s, campaigns c 
                    WHERE s.player_ucid = %s AND s.campaign_id = c.id 
                    AND NOW() BETWEEN c.start AND COALESCE(c.stop, NOW()) 
                    ORDER BY s.time DESC LIMIT 10
                """, (ucid, )).fetchall())

    # New command group "/credits"
    credits = Group(name="credits", description="Commands to manage player credits")

    @credits.command(description='Displays your current credits')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(member="user")
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer] = None):
        if member:
            if not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
                await interaction.response.send_message('You need the DCS Admin role to use this command.',
                                                        ephemeral=True)
                return
            if isinstance(member, str):
                ucid = member
                member = self.bot.get_member_by_ucid(ucid) or ucid
            else:
                ucid = self.bot.get_ucid_by_member(member)
                if not ucid:
                    await interaction.response.send_message(f"Member {utils.escape_string(member.display_name)} is "
                                                            f"not linked to any DCS user.", ephemeral=True)
                    return
        else:
            member = interaction.user
            ucid = self.bot.get_ucid_by_member(member)
            if not ucid:
                await interaction.response.send_message(f"Use `/linkme` to link your account.", ephemeral=True)
                return
        data = self.get_credits(ucid)
        if not data:
            await interaction.response.send_message(f'{utils.escape_string(member.display_name)} has no campaign '
                                                    f'credits.', ephemeral=True)
            return
        embed = discord.Embed(
            title="Campaign Credits for {}".format(utils.escape_string(member.display_name)
                                                   if isinstance(member, discord.Member) else member),
            color=discord.Color.blue())
        campaigns = points = ''
        for row in data:
            campaigns += row['name'] + '\n'
            points += f"{row['credits']}\n"
        embed.add_field(name='Campaign', value=campaigns)
        embed.add_field(name='Points', value=points)
        embed.add_field(name='_ _', value='_ _')
        data = self.get_credits_log(ucid)
        if len(data):
            embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
            times = events = deltas = ''
            for row in data:
                points = row['new_points'] - row['old_points']
                if points == 0:
                    continue
                times += f"{row['time']:%m/%d %H:%M}\n"
                events += row['event'].title() + '\n'
                deltas += f"{points}\n"
            embed.add_field(name='Time', value=times)
            embed.add_field(name='Event', value=events)
            embed.add_field(name='Points', value=deltas)
            embed.set_footer(text='Log will show the last 10 events only.')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _admin_donate(self, interaction: discord.Interaction, to: discord.Member, donation: int):
        receiver = self.bot.get_ucid_by_member(to)
        if not receiver:
            await interaction.response.send_message(f'{utils.escape_string(to.display_name)} needs to properly link '
                                                    f'their DCS account to receive donations.', ephemeral=True)
            return
        data = self.get_credits(receiver)
        if not data:
            await interaction.response.send_message('It seems like there is no campaign running on your server(s).',
                                                    ephemeral=True)
            return
        if len(data) > 1:
            n = await utils.selection(interaction, title="Campaign Credits",
                                      options=[
                                          SelectOption(label=f"{x['name']} (credits={x['credits']})", value=str(idx))
                                          for idx, x in enumerate(data)
                                      ])
        else:
            n = 0
        p_receiver: Optional[CreditPlayer] = None
        for server in self.bot.servers.values():
            p_receiver = cast(CreditPlayer, server.get_player(ucid=receiver))
            if p_receiver:
                break
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    if not p_receiver:
                        old_points_receiver = cursor.execute("""
                            SELECT COALESCE(SUM(points), 0) 
                            FROM credits 
                            WHERE campaign_id = %s AND player_ucid = %s
                        """, (data[n]['id'], receiver)).fetchone()[0]
                    else:
                        old_points_receiver = p_receiver.points
                    if 'max_points' in self.get_config() and \
                            (old_points_receiver + donation) > self.get_config()['max_points']:
                        await interaction.response.send_message(
                            f'Member {utils.escape_string(to.display_name)} would overrun the configured maximum '
                            f'points with this donation. Aborted.')
                        return
                    if p_receiver:
                        p_receiver.points += donation
                        p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                          f'{interaction.user.display_name}')
                    else:
                        cursor.execute("""
                            INSERT INTO credits (campaign_id, player_ucid, points) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                            SET points = credits.points + EXCLUDED.points
                        """, (data[n]['id'], receiver, donation))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n][0], receiver))
                        new_points_receiver = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n]['id'], 'donation', receiver, old_points_receiver, new_points_receiver,
                              f'Credit points change by Admin {interaction.user.display_name}'))
            if donation > 0:
                await interaction.response.send_message(
                    to.mention + f' you just received {donation} credit points from an Admin.')
            else:
                await interaction.response.send_message(
                    to.mention + f' your credits were decreased by {donation} credit points by an Admin.')

    @credits.command(description='Donate credits to another member')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def donate(self, interaction: discord.Interaction, to: discord.Member, donation: Range[int, 1]):
        if interaction.user == to:
            await interaction.response.send_message("You can't donate to yourself.", ephemeral=True)
            return
        if utils.check_roles(set(self.bot.roles['Admin'] + self.bot.roles['DCS Admin']), interaction.user):
            await self._admin_donate(interaction, to, donation)
            return
        receiver = self.bot.get_ucid_by_member(to)
        if not receiver:
            await interaction.response.send_message(f'{utils.escape_string(to.display_name)} needs to properly link '
                                                    f'their DCS account to receive donations.', ephemeral=True)
            return
        donor = self.bot.get_ucid_by_member(interaction.user)
        if not donor:
            await interaction.response.send_message(f'You need to properly link your DCS account to give donations!',
                                                    ephemeral=True)
            return
        data = self.get_credits(donor)
        if not data:
            await interaction.response.send_message(f"You don't have any credit points to donate.", ephemeral=True)
            return
        elif len(data) > 1:
            n = await utils.selection(interaction, title="Campaign Credits",
                                      options=[
                                          SelectOption(label=f"{x['name']} (credits={x['credits']})", value=str(idx))
                                          for idx, x in enumerate(data)
                                      ])
        else:
            n = 0
        if data[n]['credits'] < donation:
            await interaction.response.send_message(f"You can't donate {donation} credit points, as you only "
                                                    f"have {data[n]['credits']} in total!", ephemeral=True)
            return
        # now see, if one of the parties is an active player already...
        p_donor: Optional[CreditPlayer] = None
        for server in self.bot.servers.values():
            p_donor = cast(CreditPlayer, server.get_player(ucid=donor))
            if p_donor:
                break
        p_receiver: Optional[CreditPlayer] = None
        for server in self.bot.servers.values():
            p_receiver = cast(CreditPlayer, server.get_player(ucid=receiver))
            if p_receiver:
                break
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    if not p_receiver:
                        cursor.execute("""
                            SELECT COALESCE(SUM(points), 0) FROM credits WHERE campaign_id = %s AND player_ucid = %s
                        """, (data[n]['id'], receiver))
                        old_points_receiver = cursor.fetchone()[0]
                    else:
                        old_points_receiver = p_receiver.points
                    if 'max_points' in self.get_config() and \
                            (old_points_receiver + donation) > self.get_config()['max_points']:
                        await interaction.response.send_message(
                            f'Member {utils.escape_string(to.display_name)} would overrun the configured maximum '
                            f'points with this donation. Aborted.', ephemeral=True)
                        return
                    if p_donor:
                        p_donor.points -= donation
                        p_donor.audit('donation', data[n]['credits'], f'Donation to member {to.display_name}')
                    else:
                        cursor.execute("""
                            UPDATE credits SET points = points - %s WHERE campaign_id = %s AND player_ucid = %s
                        """, (donation, data[n]['id'], donor))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n]['id'], donor))
                        new_points_donor = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n]['id'], 'donation', donor, data[n]['credits'], new_points_donor,
                              f'Donation to member {to.display_name}'))
                    if p_receiver:
                        p_receiver.points += donation
                        p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                          f'{interaction.user.display_name}')
                    else:
                        cursor.execute("""
                            INSERT INTO credits (campaign_id, player_ucid, points) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                            SET points = credits.points + EXCLUDED.points
                        """, (data[n]['id'], receiver, donation))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n]['id'], receiver))
                        new_points_receiver = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n]['id'], 'donation', receiver, old_points_receiver, new_points_receiver,
                              f'Donation from member {interaction.user.display_name}'))
            await interaction.response.send_message(
                to.mention + f' you just received {donation} credit points from '
                             f'{utils.escape_string(interaction.user.display_name)}!', ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(CreditSystem(bot, CreditSystemListener))
