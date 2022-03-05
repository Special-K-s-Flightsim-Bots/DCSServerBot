import asyncio
import discord
import psycopg2
from contextlib import closing
from core import utils, DCSServerBot, Plugin, PluginRequiredError, PaginationReport
from core.const import Status
from datetime import datetime
from discord.ext import commands
from typing import Union, Optional
from .listener import UserStatisticsEventListener


class UserStatistics(Plugin):

    @commands.command(description='Shows player statistics', usage='[member] [period]', aliases=['stats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def statistics(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        num = len(params)
        if not member:
            member = ctx.message.author
            period = None
        elif isinstance(member, discord.Member):
            period = params[0] if num > 0 else None
        elif member in ['day', 'week', 'month', 'year']:
            period = member
            member = ctx.message.author
        else:
            i = 0
            name = member
            while i < num and params[i] not in ['day', 'week', 'month', 'year']:
                name += ' ' + params[i]
                i += 1
            member = utils.find_user(self, name)
            if not member:
                await ctx.send('No players found with that nickname.')
                return
            period = params[i] if i < num else None
        await ctx.message.delete()
        report = PaginationReport(self.bot, ctx, self.plugin, 'userstats.json')
        await report.render(member=member,
                            member_name=member.display_name if isinstance(member, discord.Member) else name,
                            period=period, server_name=None)

    @commands.command(description='Shows actual highscores', usage='[period]', aliases=['hs'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def highscore(self, ctx, period: Optional[str], server_name: Optional[str]):
        await ctx.message.delete()
        report = PaginationReport(self.bot, ctx, self.plugin, 'highscore.json')
        await report.render(period=period, server_name=server_name)

    @commands.command(description='Links a member to a DCS user', usage='<member> <ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (member.id, ucid))
                conn.commit()
                await ctx.send('Member {} linked to ucid {}'.format(member.display_name, ucid))
                await self.bot.audit(f'User {ctx.message.author.display_name} linked member {member.display_name} to ucid {ucid}.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Unlinks a member', usage='<member> / <ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unlink(self, ctx, member: Union[discord.Member, str]):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(member, discord.Member):
                    cursor.execute('UPDATE players SET discord_id = -1 WHERE discord_id = %s', (member.id, ))
                    await ctx.send('Member {} unlinked.'.format(member.display_name))
                    await self.bot.audit(
                        f'User {ctx.message.author.display_name} unlinked member {member.display_name}.')
                else:
                    cursor.execute('UPDATE players SET discord_id = -1 WHERE ucid = %s', (member, ))
                    await ctx.send('ucid {} unlinked.'.format(member))
                    await self.bot.audit(f'User {ctx.message.author.display_name} unlinked ucid {member}.')
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Resets the statistics of a specific server')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def reset(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            server_name = server['server_name']
            if server['status'] in [Status.STOPPED, Status.SHUTDOWN]:
                conn = self.pool.getconn()
                try:
                    if await utils.yn_question(self, ctx, 'I\'m going to **DELETE ALL STATISTICS**\nof server "{}".\n\nAre you sure?'.format(server_name)) is True:
                        with closing(conn.cursor()) as cursor:
                            cursor.execute(
                                'DELETE FROM statistics WHERE mission_id in (SELECT id FROM missions WHERE '
                                'server_name = %s)', (server_name, ))
                            cursor.execute('DELETE FROM missions WHERE server_name = %s', (server_name, ))
                            conn.commit()
                        await ctx.send('Statistics for server "{}" have been wiped.'.format(server_name))
                        await self.bot.audit(
                            f'User {ctx.message.author.display_name} reset the statistics for server "{server_name}".')
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
            else:
                await ctx.send('Please stop server "{}" before deleteing the statistics!'.format(server_name))

    @commands.command(description='Shows information about a specific player', usage='<@member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def info(self, ctx, member: Union[discord.Member, str], *params):
        sql = 'SELECT p.discord_id, p.ucid, p.last_seen, COALESCE(p.name, \'?\') AS NAME, ' \
              'COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS playtime, ' \
              'CASE WHEN p.ucid = b.ucid THEN 1 ELSE 0 END AS banned ' \
              'FROM players p ' \
              'LEFT OUTER JOIN statistics s ON (s.player_ucid = p.ucid) ' \
              'LEFT OUTER JOIN bans b ON (b.ucid = p.ucid) ' \
              'WHERE p.discord_id = '
        if isinstance(member, str):
            if len(params):
                member += ' ' + ' '.join(params)
                print(member)
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR p.name = '{member}' "
        else:
            sql += f"'{member.id}'"
        sql += ' GROUP BY p.ucid, b.ucid'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute(sql)
                rows = list(cursor.fetchall())
                if rows is not None and len(rows) > 0:
                    embed = discord.Embed(title='User Information', color=discord.Color.blue())
                    embed.description = f'Information about '
                    if rows[0]['discord_id'] != -1:
                        member = ctx.guild.get_member(rows[0]['discord_id'])
                    if isinstance(member, discord.Member):
                        embed.description += f'member **{member.display_name}**:'
                        embed.add_field(name='Discord ID:', value=member.id)
                    else:
                        embed.description += 'a non-member user:'
                    last_seen = datetime(1970, 1, 1)
                    banned = False
                    for row in rows:
                        if row['last_seen'] and row['last_seen'] > last_seen:
                            last_seen = row['last_seen']
                        if row['banned'] == 1:
                            banned = True
                    if last_seen != datetime(1970, 1, 1):
                        embed.add_field(name='Last seen:', value=last_seen.strftime("%m/%d/%Y, %H:%M:%S"))
                    if banned:
                        embed.add_field(name='Status', value='Banned')
                    embed.add_field(name='▬' * 32, value='_ _', inline=False)
                    ucids = []
                    names = []
                    playtimes = []
                    match = True
                    mismatched_members = []
                    for row in rows:
                        ucids.append(row['ucid'])
                        names.append(row['name'])
                        playtimes.append('{:.0f}'.format(row['playtime']))
                        # check if the match should be updated
                        matched_member = utils.match_user(self, dict(row), True)
                        if matched_member:
                            if isinstance(member, discord.Member):
                                if member.id != matched_member.id:
                                    mismatched_members.append({"ucid": row['ucid'], "member": matched_member})
                                    match = False
                            else:
                                mismatched_members.append({"ucid": row['ucid'], "member": matched_member})
                                match = False
                    embed.add_field(name='UCID', value='\n'.join(ucids))
                    embed.add_field(name='DCS Name', value='\n'.join(names))
                    embed.add_field(name='Playtime (h)', value='\n'.join(playtimes))
                    embed.add_field(name='▬' * 32, value='_ _', inline=False)
                    servers = []
                    dcs_names = []
                    for server_name in self.globals.keys():
                        if server_name in self.bot.player_data:
                            for i in range(0, len(ucids)):
                                if utils.get_player(self, server_name, ucid=ucids[i]) is not None:
                                    servers.append(server_name)
                                    dcs_names.append(names[i])
                                    break
                    if len(servers):
                        embed.add_field(name='Active on Server', value='\n'.join(servers))
                        embed.add_field(name='DCS Name', value='\n'.join(dcs_names))
                        embed.add_field(name='_ _', value='_ _')
                        embed.add_field(name='▬' * 32, value='_ _', inline=False)
                    footer = '🔀 unlink all ucids from this user\n' if isinstance(member, discord.Member) else ''
                    footer += f'🔄 relink ucid(s) to the correct user\n' if not match else ''
                    footer += '⏏️ kick this user from the current servers\n' if len(servers) > 0 else ''
                    footer += '✅ unban this user' if banned else '⛔ to ban this user (DCS only)'
                    embed.set_footer(text=footer)
                    message = await ctx.send(embed=embed)
                    if isinstance(member, discord.Member):
                        await message.add_reaction('🔀')
                    if not match:
                        await message.add_reaction('🔄')
                    if len(servers) > 0:
                        await message.add_reaction('⏏️')
                    await message.add_reaction('✅' if banned else '⛔')
                    react = await utils.wait_for_single_reaction(self, ctx, message)
                    if react.emoji == '🔀':
                        await self.unlink(ctx, member)
                        await self.bot.audit(f'User {ctx.message.author.display_name} has unlinked ' +
                                             (f' all ucids from user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member} from its member.'))
                    elif react.emoji == '🔄':
                        for match in mismatched_members:
                            cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (match['member'].id, match['ucid']))
                            await self.bot.audit(f"User {ctx.message.author.display_name} has relinked ucid {match['ucid']} to user {match['member'].display_name}")
                        await ctx.send('ucids have been relinked.')
                    elif react.emoji == '⏏️':
                        for server in self.globals.values():
                            for ucid in ucids:
                                self.bot.sendtoDCS(server, {"command": "kick", "ucid": ucid, "reason": "Kicked by admin."})
                        await ctx.send('User has been kicked.')
                        await self.bot.audit(f'User {ctx.message.author.display_name} has kicked ' +
                                             (f' user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member}'))
                    elif react.emoji == '⛔':
                        for ucid in ucids:
                            cursor.execute('INSERT INTO bans (ucid, banned_by, reason) VALUES (%s, %s, %s)',
                                           (ucid, ctx.message.author.display_name, 'n/a'))
                            for server in self.globals.values():
                                self.bot.sendtoDCS(server, {
                                    "command": "ban",
                                    "ucid": ucid,
                                    "reason": "Banned by admin."
                                })
                        await ctx.send('User has been banned.')
                        await self.bot.audit(f'User {ctx.message.author.display_name} has banned ' +
                                             (f' user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member}'))
                    elif react.emoji == '✅':
                        for ucid in ucids:
                            cursor.execute('DELETE FROM bans WHERE ucid = %s', (ucid, ))
                            for server in self.globals.values():
                                self.bot.sendtoDCS(server, {
                                    "command": "unban",
                                    "ucid": ucid
                                })
                        await ctx.send('User has been unbanned.')
                        await self.bot.audit(f'User {ctx.message.author.display_name} has unbanned ' +
                                             (f' user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member}'))
                    await message.delete()
                    conn.commit()
                else:
                    await ctx.send(f'No data found for user "{member if isinstance(member, str) else member.display_name}".')
        except asyncio.TimeoutError:
            await message.clear_reactions()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Checks the matching links of all members / ucids and displays potential violations')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def linkcheck(self, ctx):
        embed = discord.Embed(title='Link Violations', color=discord.Color.blue())
        embed.description = 'Displays possible link violations for users.'
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                cursor.execute('SELECT a.total, b.filled FROM (SELECT COUNT(*) AS total FROM players) a,  (SELECT '
                               'COUNT(*) AS filled FROM players WHERE name IS NOT NULL) b')
                row = cursor.fetchone()
                embed.add_field(name='Discord Members', value=str(len(self.bot.guilds[0].members)))
                embed.add_field(name='DCS Players', value=row[0])
                embed.add_field(name='.. with names', value=row[1])
                # check all unmatched players
                unmatched = []
                cursor.execute('SELECT ucid, name FROM players WHERE discord_id = -1 AND name IS NOT NULL')
                for row in cursor.fetchall():
                    matched_member = utils.match_user(self, dict(row), True)
                    if matched_member:
                        unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
                if len(unmatched):
                    embed.add_field(name='▬' * 32, value='These players could be linked:', inline=False)
                    left = right = ''
                    for match in unmatched:
                        left += f"{match['name']}({match['ucid']})\n"
                        right += f"{match['match'].display_name}\n"
                    embed.add_field(name='DCS Player', value=left)
                    embed.add_field(name='_ _', value='=>\n' * len(unmatched))
                    embed.add_field(name='Member', value=right)
                # check all matched members
                suspicious = []
                for member in self.bot.get_all_members():
                    cursor.execute('SELECT ucid, name FROM players WHERE discord_id = %s AND name IS NOT NULL', (member.id, ))
                    for row in cursor.fetchall():
                        matched_member = utils.match_user(self, dict(row), True)
                        if not matched_member:
                            suspicious.append(
                                {"name": row['name'], "ucid": row['ucid'], "mismatch": member})
                        elif matched_member.id != member.id:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member, "match": matched_member})
                if len(suspicious):
                    embed.add_field(name='▬' * 32, value='These members might be mislinked:', inline=False)
                    left = right = ''
                    for mismatch in suspicious:
                        left += f"{mismatch['name']}({mismatch['ucid']})\n"
                        right += f"{mismatch['mismatch'].display_name}\n"
                    embed.add_field(name='DCS Player', value=left)
                    embed.add_field(name='_ _', value='\u2260\n' * len(suspicious))
                    embed.add_field(name='Member', value=right)
                else:
                    embed.add_field(name='All members that have DCS names are linked correctly.', value='_ _', inline=False)
                footer = ''
                if len(unmatched) > 0:
                    footer += '🔄 link all unlinked players\n'
                if len(suspicious) > 0:
                    footer += '🔀 relink all mislinked members\n'
                if len(unmatched) > 0 and len(suspicious) > 0:
                    footer += '✅ all of the above'
                if len(footer):
                    embed.set_footer(text=footer)
                message = await ctx.send(embed=embed)
                if len(unmatched) > 0 or len(suspicious) > 0:
                    if len(unmatched) > 0:
                        await message.add_reaction('🔄')
                    if len(suspicious) > 0:
                        await message.add_reaction('🔀')
                    if len(unmatched) > 0 and len(suspicious) > 0:
                        await message.add_reaction('✅')
                    react = await utils.wait_for_single_reaction(self, ctx, message)
                    if react.emoji == '🔄' or react.emoji == '✅':
                        for match in unmatched:
                            cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (match['match'].id, match['ucid']))
                            await self.bot.audit(
                                f"User {ctx.message.author.display_name} linked user {match['match'].display_name} to ucid {match['ucid']}.")
                        await ctx.send('All unlinked players are linked.')
                    if react.emoji == '🔀' or react.emoji == '✅':
                        for mismatch in suspicious:
                            cursor.execute('UPDATE players SET discord_id = %s WHERE ucid = %s', (mismatch['match'].id if 'match' in mismatch else -1, mismatch['ucid']))
                        await self.bot.audit(
                            f"User {ctx.message.author.display_name} linked user {mismatch['match'].display_name} to ucid {mismatch['ucid']}.")
                        await ctx.send('All incorrectly linked members have been relinked.')
                    conn.commit()
                    await message.delete()
        except asyncio.TimeoutError:
            await message.clear_reactions()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(UserStatistics(bot, UserStatisticsEventListener))
    else:
        bot.add_cog(Plugin(bot, UserStatisticsEventListener))
