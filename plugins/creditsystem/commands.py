import discord
from contextlib import closing
from copy import deepcopy
from discord import app_commands
from discord.app_commands import Range
from core import utils, Plugin, PluginRequiredError, Server
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, cast, Union

from .listener import CreditSystemListener
from .player import CreditPlayer


class CreditSystem(Plugin):

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
                    merged = {}
                    if 'initial_points' in specific:
                        merged['initial_points'] = specific['initial_points']
                    elif 'initial_points' in default:
                        merged['initial_points'] = default['initial_points']
                    if 'max_points' in specific:
                        merged['max_points'] = specific['max_points']
                    elif 'max_points' in default:
                        merged['max_points'] = default['max_points']
                    if 'points_per_kill' in default and 'points_per_kill' not in specific:
                        merged['points_per_kill'] = default['points_per_kill']
                    elif 'points_per_kill' not in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = specific['points_per_kill']
                    elif 'points_per_kill' in default and 'points_per_kill' in specific:
                        merged['points_per_kill'] = default['points_per_kill'] + specific['points_per_kill']
                    if 'achievements' in specific:
                        merged['achievements'] = specific['achievements']
                    elif 'achievements' in default:
                        merged['achievements'] = default['achievements']
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
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
    credits = app_commands.Group(name="credits", description="Commands to manage player credits")

    @credits.command(description='Displays your current credits')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(member="user")
    @app_commands.autocomplete(member=utils.all_users_autocomplete)
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer] = None):
        if member:
            if not utils.check_roles(['DCS Admin'], interaction.user):
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
                await interaction.response.send_message(f"Use /linkme to link your account.", ephemeral=True)
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
            campaigns += row[1] + '\n'
            points += f"{row[2]}\n"
        embed.add_field(name='Campaign', value=campaigns)
        embed.add_field(name='Points', value=points)
        embed.add_field(name='_ _', value='_ _')
        data = self.get_credits_log(ucid)
        if len(data):
            embed.add_field(name='▬' * 10 + ' Log ' + '▬' * 10, value='_ _', inline=False)
            times = events = deltas = ''
            for row in data:
                times += f"{row['time']:%m/%d %H:%M}\n"
                events += row['event'].title() + '\n'
                deltas += f"{row['new_points'] - row['old_points']}\n"
            embed.add_field(name='Time', value=times)
            embed.add_field(name='Event', value=events)
            embed.add_field(name='Points', value=deltas)
            embed.set_footer(text='Log will show the last 10 events only.')
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @staticmethod
    def format_credits(data, marker, marker_emoji):
        embed = discord.Embed(title='Campaign Credits', color=discord.Color.blue())
        ids = campaigns = points = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            campaigns += f"{data[i][1]}\n"
            points += f"{data[i][2]}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Campaign', value=campaigns)
        embed.add_field(name='Credits', value=points)
        embed.set_footer(text='Press a number to donate from these credits.')
        return embed

    async def admin_donate(self, ctx, to: discord.Member, donation: int):
        receiver = self.bot.get_ucid_by_member(to)
        if not receiver:
            await ctx.send('{} needs to properly link their DCS account to receive '
                           'donations.'.format(utils.escape_string(to.display_name)))
            return
        data = self.get_credits(receiver)
        if not data:
            await ctx.send('It seems like there is no campaign running on your server(s).')
            return
        if len(data) > 1:
            n = await utils.selection_list(self.bot, ctx, data, self.format_credits)
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
                        """, (data[n][0], receiver)).fetchone()[0]
                    else:
                        old_points_receiver = p_receiver.points
                    if 'max_points' in self.locals['configs'][0] and \
                            (old_points_receiver + donation) > self.locals['configs'][0]['max_points']:
                        await ctx.send('Member {} would overrun the configured maximum points with this donation. '
                                       'Aborted.'.format(utils.escape_string(to.display_name)))
                        return
                    if p_receiver:
                        p_receiver.points += donation
                        p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                          f'{ctx.message.author.display_name}')
                    else:
                        cursor.execute("""
                            INSERT INTO credits (campaign_id, player_ucid, points) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                            SET points = credits.points + EXCLUDED.points
                        """, (data[n][0], receiver, donation))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n][0], receiver))
                        new_points_receiver = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n][0], 'donation', receiver, old_points_receiver, new_points_receiver,
                              f'Credit points change by Admin {ctx.message.author.display_name}'))
            if donation > 0:
                await ctx.send(to.mention + f' you just received {donation} credit points from an Admin.')
            else:
                await ctx.send(to.mention + f' your credits were decreased by {donation} credit points by an Admin.')

    @credits.command(description='Donate credits to another member')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def donate(self, interaction: discord.Interaction, to: discord.Member, donation: Range[int, 1]):
        if interaction.user == to:
            await interaction.response.send_message("You can't donate to yourself.", ephemeral=True)
            return
        if utils.check_roles(['Admin', 'DCS Admin'], interaction.user):
            await self.admin_donate(interaction, to, donation)
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
            n = await utils.selection_list(self.bot, interaction, data, self.format_credits)
        else:
            n = 0
        if data[n][2] < donation:
            await interaction.response.send_message(f"You can't donate {donation} credit points, as you only "
                                                    f"have {data[n][2]} in total!", ephemeral=True)
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
                        """, (data[n][0], receiver))
                        old_points_receiver = cursor.fetchone()[0]
                    else:
                        old_points_receiver = p_receiver.points
                    if 'max_points' in self.locals['configs'][0] and \
                            (old_points_receiver + donation) > self.locals['configs'][0]['max_points']:
                        await interaction.response.send_message(
                            f'Member {utils.escape_string(to.display_name)} would overrun the configured maximum '
                            f'points with this donation. Aborted.', ephemeral=True)
                        return
                    if p_donor:
                        p_donor.points -= donation
                        p_donor.audit('donation', data[n][2], f'Donation to member {to.display_name}')
                    else:
                        cursor.execute("""
                            UPDATE credits SET points = points - %s WHERE campaign_id = %s AND player_ucid = %s
                        """, (donation, data[n][0], donor))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n][0], donor))
                        new_points_donor = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n][0], 'donation', donor, data[n][2], new_points_donor,
                              f'Donation to member {to.display_name}'))
                    if p_receiver:
                        p_receiver.points += donation
                        p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                          f'{ctx.message.author.display_name}')
                    else:
                        cursor.execute("""
                            INSERT INTO credits (campaign_id, player_ucid, points) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (campaign_id, player_ucid) DO UPDATE 
                            SET points = credits.points + EXCLUDED.points
                        """, (data[n][0], receiver, donation))
                        cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                       (data[n][0], receiver))
                        new_points_receiver = cursor.fetchone()[0]
                        cursor.execute("""
                            INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, remark) 
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (data[n][0], 'donation', receiver, old_points_receiver, new_points_receiver,
                              f'Donation from member {ctx.message.author.display_name}'))
            await interaction.response.send_message(
                to.mention + f' you just received {donation} credit points from '
                             f'{utils.escape_string(interaction.user.display_name)}!', ephemeral=True)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(CreditSystem(bot, CreditSystemListener))
