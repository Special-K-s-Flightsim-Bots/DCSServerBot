import discord
import psycopg2
import string
from contextlib import closing
from copy import deepcopy
from core import utils, DCSServerBot, Plugin, PluginRequiredError, Server
from discord.ext import commands
from typing import Optional, cast, Union
from .listener import CreditSystemListener
from .player import CreditPlayer


class CreditSystemAgent(Plugin):

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
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None


class CreditSystemMaster(CreditSystemAgent):

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Creditsystem ...')
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM credits WHERE player_ucid = %s', (ucid,))
                    cursor.execute('DELETE FROM credits_log WHERE player_ucid = %s', (ucid,))
        self.log.debug('Creditsystem pruned.')

    def get_credits(self, ucid: str) -> list[dict]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(f"SELECT c.id, c.name, COALESCE(SUM(s.points), 0) AS credits FROM credits s, campaigns "
                               f"c WHERE s.player_ucid = %s AND s.campaign_id = c.id AND NOW() BETWEEN c.start AND "
                               f"COALESCE(c.stop, NOW()) GROUP BY 1, 2",
                               (ucid, ))
                return list(cursor.fetchall())
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    def get_credits_log(self, ucid: str) -> list[dict]:
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT s.event, s.old_points, s.new_points, remark, time FROM credits_log s, '
                               'campaigns c WHERE s.player_ucid = %s AND s.campaign_id = c.id AND NOW() BETWEEN '
                               'c.start AND COALESCE(c.stop, NOW()) ORDER BY s.time DESC LIMIT 10', (ucid, ))
                return list(cursor.fetchall())
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Displays your current credits')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def credits(self, ctx, member: Optional[Union[discord.Member, str]]):
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
        data = self.get_credits(ucid)
        await ctx.message.delete()
        if len(data) == 0:
            await ctx.send(f'{member.display_name} has no campaign credits.')
            return
        embed = discord.Embed(
            title="Campaign Credits for {}".format(member.display_name if isinstance(member, discord.Member) else member),
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
                events += string.capwords(row['event']) + '\n'
                deltas += f"{row['new_points'] - row['old_points']}\n"
            embed.add_field(name='Time', value=times)
            embed.add_field(name='Event', value=events)
            embed.add_field(name='Points', value=deltas)
            embed.set_footer(text='Log will show the last 10 events only.')
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        await ctx.send(embed=embed, delete_after=timeout if timeout > 0 else None)

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
            await ctx.send(f'{to.display_name} needs to properly link their DCS account to receive donations.')
            return
        data = self.get_credits(receiver)
        if len(data) > 1:
            n = await utils.selection_list(self, ctx, data, self.format_credits)
        else:
            n = 0
        p_receiver: Optional[CreditPlayer] = None
        for server in self.bot.servers.values():
            p_receiver = cast(CreditPlayer, server.get_player(ucid=receiver))
            if p_receiver:
                break
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if not p_receiver:
                    cursor.execute('SELECT COALESCE(SUM(points), 0) FROM credits WHERE campaign_id = %s AND '
                                   'player_ucid = %s', (data[n][0], receiver))
                    old_points_receiver = cursor.fetchone()[0]
                else:
                    old_points_receiver = p_receiver.points
                if 'max_points' in self.locals['configs'][0] and \
                        (old_points_receiver + donation) > self.locals['configs'][0]['max_points']:
                    await ctx.send(f'Member {to.display_name} would overrun the configured maximum points with '
                                   f'this donation. Aborted.')
                    return
                if p_receiver:
                    p_receiver.points += donation
                    p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                      f'{ctx.message.author.display_name}')
                else:
                    cursor.execute('INSERT INTO credits (campaign_id, player_ucid, points) VALUES (%s, %s, '
                                   '%s) ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = credits.points + '
                                   'EXCLUDED.points', (data[n][0], receiver, donation))
                    cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                   (data[n][0], receiver))
                    new_points_receiver = cursor.fetchone()[0]
                    cursor.execute('INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, '
                                   'remark) VALUES (%s, %s, %s, %s, %s, %s)', (data[n][0], 'donation', receiver,
                                                                               old_points_receiver, new_points_receiver,
                                                                               f'Credit points change by Admin '
                                                                               f'{ctx.message.author.display_name}'))
            conn.commit()
            if donation > 0:
                await ctx.send(to.mention + f' you just received {donation} credit points from an Admin.')
            else:
                await ctx.send(to.mention + f' your credits were decreased by {donation} credit points by an Admin.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Donate credits to another member', usage='<member> <credits>')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def donate(self, ctx, to: discord.Member, donation: int):
        if ctx.message.author.id == to.id:
            await ctx.send("You can't donate to yourself.")
            return
        if utils.check_roles(['Admin', 'DCS Admin'], ctx.message.author):
            await self.admin_donate(ctx, to, donation)
            return
        if donation <= 0:
            await ctx.send("Donation has to be a positive value.")
            return
        receiver = self.bot.get_ucid_by_member(to)
        if not receiver:
            await ctx.send(f'{to.display_name} needs to properly link their DCS account to receive donations.')
            return
        donor = self.bot.get_ucid_by_member(ctx.message.author)
        if not donor:
            await ctx.send(f'You need to properly link your DCS account to give donations!')
            return
        data = self.get_credits(donor)
        if not len(data):
            await ctx.send(f"You can't donate credit points, as you don't have any.")
            return
        elif len(data) > 1:
            n = await utils.selection_list(self, ctx, data, self.format_credits)
        else:
            n = 0
        if data[n][2] < donation:
            await ctx.send(f"You can't donate {donation} credit points, as you only have {data[n][2]} in total!")
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
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if not p_receiver:
                    cursor.execute('SELECT COALESCE(SUM(points), 0) FROM credits WHERE campaign_id = %s AND '
                                   'player_ucid = %s', (data[n][0], receiver))
                    old_points_receiver = cursor.fetchone()[0]
                else:
                    old_points_receiver = p_receiver.points
                if 'max_points' in self.locals['configs'][0] and \
                        (old_points_receiver + donation) > self.locals['configs'][0]['max_points']:
                    await ctx.send(f'Member {to.display_name} would overrun the configured maximum points with '
                                   f'this donation. Aborted.')
                    return
                if p_donor:
                    p_donor.points -= donation
                    p_donor.audit('donation', data[n][2], f'Donation to member {to.display_name}')
                else:
                    cursor.execute('UPDATE credits SET points = points - %s WHERE campaign_id = %s AND player_ucid = %s',
                                   (donation, data[n][0], donor))
                    cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                   (data[n][0], donor))
                    new_points_donor = cursor.fetchone()[0]
                    cursor.execute('INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, '
                                   'remark) VALUES (%s, %s, %s, %s, %s, %s)', (data[n][0], 'donation', donor,
                                                                               data[n][2], new_points_donor,
                                                                               f'Donation to member {to.display_name}'))
                if p_receiver:
                    p_receiver.points += donation
                    p_receiver.audit('donation', old_points_receiver, f'Donation from member '
                                                                      f'{ctx.message.author.display_name}')
                else:
                    cursor.execute('INSERT INTO credits (campaign_id, player_ucid, points) VALUES (%s, %s, '
                                   '%s) ON CONFLICT (campaign_id, player_ucid) DO UPDATE SET points = credits.points + '
                                   'EXCLUDED.points', (data[n][0], receiver, donation))
                    cursor.execute('SELECT points FROM credits WHERE campaign_id = %s AND player_ucid = %s',
                                   (data[n][0], receiver))
                    new_points_receiver = cursor.fetchone()[0]
                    cursor.execute('INSERT INTO credits_log (campaign_id, event, player_ucid, old_points, new_points, '
                                   'remark) VALUES (%s, %s, %s, %s, %s, %s)', (data[n][0], 'donation', receiver,
                                                                               old_points_receiver, new_points_receiver,
                                                                               f'Donation from member '
                                                                               f'{ctx.message.author.display_name}'))
            conn.commit()
            await ctx.send(to.mention + f' you just received {donation} credit points from '
                                        f'{ctx.message.author.display_name}!')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Displays your current player profile')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def profile(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        config: dict = self.locals['configs'][0]
        if not member:
            member = ctx.message.author
        embed = discord.Embed(title="User Campaign Profile", colour=discord.Color.blue())
        embed.set_thumbnail(url=member.avatar.url)
        if 'achievements' in config:
            for achievement in config['achievements']:
                if utils.check_roles([achievement['role']], member):
                    embed.add_field(name='Rank', value=achievement['role'])
                    break
            else:
                embed.add_field(name='Rank', value='n/a')
        ucid = self.bot.get_ucid_by_member(member, True)
        if ucid:
            playtime = self.eventlistener._get_flighttime(ucid)
            embed.add_field(name='Playtime', value=utils.format_time(playtime - playtime % 60))
            embed.add_field(name='_ _', value='_ _', inline=True)
            data = self.get_credits(ucid)
            campaigns = points = ''
            for row in data:
                campaigns += row[1] + '\n'
                points += f"{row[2]}\n"
            embed.add_field(name='Campaign', value=campaigns)
            embed.add_field(name='Points', value=points)
            embed.add_field(name='_ _', value='_ _')
        await ctx.send(embed=embed)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(CreditSystemMaster(bot, CreditSystemListener))
    else:
        await bot.add_cog(CreditSystemAgent(bot, CreditSystemListener))
