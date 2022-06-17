import asyncio
import discord
import os
import psycopg2
import random
from contextlib import closing
from core import utils, DCSServerBot, Plugin, PluginRequiredError, Report, PaginationReport
from core.const import Status
from datetime import datetime
from discord.ext import commands, tasks
from typing import Union, Optional, Tuple
from .filter import StatisticsFilter
from .listener import UserStatisticsEventListener


def parse_params(self, ctx, member: Optional[Union[discord.Member, str]], *params) -> Tuple[Union[discord.Member, str], str]:
    num = len(params)
    if not member:
        member = ctx.message.author
        period = None
    elif isinstance(member, discord.Member):
        period = params[0] if num > 0 else None
    elif StatisticsFilter.detect(self.bot, member):
        period = member
        member = ctx.message.author
    else:
        i = 0
        name = member
        while i < num and not StatisticsFilter.detect(self.bot, params[i]):
            name += ' ' + params[i]
            i += 1
        member = name
        period = params[i] if i < num else None
    return member, period


class UserStatisticsAgent(Plugin):
    @commands.command(description='Deletes the statistics of a server')
    @utils.has_role('Admin')
    @commands.guild_only()
    async def reset_statistics(self, ctx):
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
                        await self.bot.audit('reset statistics', user=ctx.message.author, server=server)
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
            else:
                await ctx.send('Please stop server "{}" before deleteing the statistics!'.format(server_name))


class UserStatisticsMaster(UserStatisticsAgent):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.expire_token.start()

    def cog_unload(self):
        self.expire_token.cancel()
        super().cog_unload()

    @commands.command(description='Shows player statistics', usage='[member] [period]', aliases=['stats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def statistics(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            report = PaginationReport(self.bot, ctx, self.plugin_name, 'userstats.json', timeout if timeout > 0 else None)
            await report.render(member=member if isinstance(member, discord.Member) else utils.get_ucid_by_name(self, member),
                                member_name=member.display_name if isinstance(member, discord.Member) else member,
                                period=period, server_name=None, flt=flt)
        finally:
            await ctx.message.delete()

    @commands.command(description='Shows actual highscores', usage='[period]', aliases=['hs'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def highscore(self, ctx, period: Optional[str]):
        try:
            timeout = int(self.config['BOT']['MESSAGE_AUTODELETE'])
            server = await utils.get_server(self, ctx)
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            if not server:
                report = PaginationReport(self.bot, ctx, self.plugin_name, 'highscore.json', timeout if timeout > 0 else None)
                await report.render(period=period, message=ctx.message, flt=flt, server_name=None)
            else:
                report = Report(self.bot, self.plugin_name, 'highscore.json')
                env = await report.render(period=period, message=ctx.message, server_name=server['server_name'], flt=flt)
                file = discord.File(env.filename)
                await ctx.send(embed=env.embed, file=file, delete_after=timeout if timeout > 0 else None)
                if file:
                    os.remove(env.filename)
        finally:
            await ctx.message.delete()

    @commands.command(description='Links a member to a DCS user', usage='<member> <ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid: str):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (member.id, ucid))
                conn.commit()
                await ctx.send('Member {} linked to ucid {}'.format(member.display_name, ucid))
                await self.bot.audit(f'linked member {member.display_name} to ucid {ucid}.', user=ctx.message.author)
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Unlinks a member', usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unlink(self, ctx, member: Union[discord.Member, str]):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                if isinstance(member, discord.Member):
                    cursor.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE discord_id = %s', (member.id, ))
                    await ctx.send('Member {} unlinked.'.format(member.display_name))
                    await self.bot.audit(f'unlinked member {member.display_name}.', user=ctx.message.author)
                else:
                    cursor.execute('UPDATE players SET discord_id = -1 WHERE ucid = %s', (member, ))
                    await ctx.send('ucid {} unlinked.'.format(member))
                    await self.bot.audit(f'unlinked ucid {member}.', user=ctx.message.author)
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @commands.command(description='Shows player information', usage='<@member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def info(self, ctx, member: Union[discord.Member, str], *params):
        sql = 'SELECT p.discord_id, p.ucid, p.last_seen, p.manual, COALESCE(p.name, \'?\') AS NAME, ' \
              'COALESCE(ROUND(SUM(EXTRACT(EPOCH FROM (s.hop_off - s.hop_on))) / 3600), 0) AS playtime, ' \
              'CASE WHEN p.ucid = b.ucid THEN 1 ELSE 0 END AS banned ' \
              'FROM players p ' \
              'LEFT OUTER JOIN statistics s ON (s.player_ucid = p.ucid) ' \
              'LEFT OUTER JOIN bans b ON (b.ucid = p.ucid) ' \
              'WHERE p.discord_id = '
        if isinstance(member, str):
            if len(params):
                member += ' ' + ' '.join(params)
            sql += f"(SELECT discord_id FROM players WHERE ucid = '{member}' AND discord_id != -1) OR " \
                   f"p.ucid = '{member}' OR LOWER(p.name) = '{member.casefold()}' "
        else:
            sql += f"'{member.id}'"
        sql += ' GROUP BY p.ucid, b.ucid'
        message = None
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
                    elif row['manual']:
                        embed.add_field(name='Status', value='Approved')
                    else:
                        embed.add_field(name='Status', value='Not approved')
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
                                    mismatched_members.append({"ucid": row['ucid'], "name": row['name'], "member": matched_member})
                                    match = False
                            else:
                                mismatched_members.append({"ucid": row['ucid'], "name": row['name'], "member": matched_member})
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
                                if utils.get_player(self, server_name, ucid=ucids[i], active=True) is not None:
                                    servers.append(server_name)
                                    dcs_names.append(names[i])
                                    break
                    if len(servers):
                        embed.add_field(name='Active on Server', value='\n'.join(servers))
                        embed.add_field(name='DCS Name', value='\n'.join(dcs_names))
                        embed.add_field(name='_ _', value='_ _')
                        embed.add_field(name='▬' * 32, value='_ _', inline=False)
                    footer = '🔀 Unlink all DCS players from this user\n' if isinstance(member, discord.Member) else ''
                    footer += f'🔄 Relink ucid(s) to the correct user\n' if not match else ''
                    if not row['manual']:
                        footer += '💯 Approve this link to be correct\n'
                    footer += '⏏️ Kick this user from the active server\n' if len(servers) > 0 else ''
                    footer += '✅ Unban this user\n' if banned else '⛔ Ban this user (DCS only)\n'
                    footer += '⏹️Cancel'
                    embed.set_footer(text=footer)
                    message = await ctx.send(embed=embed)
                    if isinstance(member, discord.Member):
                        await message.add_reaction('🔀')
                        if not row['manual']:
                            await message.add_reaction('💯')
                    if not match:
                        await message.add_reaction('🔄')
                    if len(servers) > 0:
                        await message.add_reaction('⏏️')
                    await message.add_reaction('✅' if banned else '⛔')
                    await message.add_reaction('⏹️')
                    react = await utils.wait_for_single_reaction(self, ctx, message)
                    if react.emoji == '🔀':
                        await self.unlink(ctx, member)
                    elif react.emoji == '💯':
                        cursor.execute('UPDATE players SET manual = TRUE WHERE discord_id = %s', (member.id, ))
                        await ctx.send('Discord/DCS-link approved.')
                    elif react.emoji == '🔄':
                        for match in mismatched_members:
                            cursor.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s',
                                           (match['member'].id, match['ucid']))
                            await self.bot.audit(f"relinked DCS player {match['name']}(ucid={match['ucid']}) to member "
                                                 f"{match['member'].display_name}.", user=ctx.message.author)
                        await ctx.send(f"DCS player {match['name']} has been relinked to member {match['member'].display_name}.")
                    elif react.emoji == '⏏️':
                        for server in self.globals.values():
                            for ucid in ucids:
                                self.bot.sendtoDCS(server, {"command": "kick", "ucid": ucid, "reason": "Kicked by admin."})
                        await ctx.send(f"User has been kicked from server \"{server['server_name']}\".")
                        await self.bot.audit(f' kicked ' + (f'user {member.display_name}.' if isinstance(member, discord.Member) else f'ucid {member}'),
                                             user=ctx.message.author)
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
                        await ctx.send('User has been banned from all DCS servers.')
                        await self.bot.audit(f'banned ' + (f' user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member}'),
                                             user=ctx.message.author)
                    elif react.emoji == '✅':
                        for ucid in ucids:
                            cursor.execute('DELETE FROM bans WHERE ucid = %s', (ucid, ))
                            for server in self.globals.values():
                                self.bot.sendtoDCS(server, {
                                    "command": "unban",
                                    "ucid": ucid
                                })
                        await ctx.send('User has been unbanned from the DCS servers.')
                        await self.bot.audit(f'unbanned ' + (f' user {member.display_name}.' if isinstance(member, discord.Member) else f' ucid {member}'),
                                             user=ctx.message.author)
                    conn.commit()
                else:
                    await ctx.send(f'No data found for user "{member if isinstance(member, str) else member.display_name}".')
        except asyncio.TimeoutError:
            pass
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
        finally:
            self.bot.pool.putconn(conn)
            if message:
                await message.delete()

    @staticmethod
    def format_unmatched(data, marker, marker_emoji):
        embed = discord.Embed(title='Unlinked Players', color=discord.Color.blue())
        embed.description = 'These players could be possibly linked:'
        ids = players = members = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            players += f"{data[i]['name']}\n"
            members += f"{data[i]['match'].display_name}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='DCS Player', value=players)
        embed.add_field(name='Member', value=members)
        embed.set_footer(text='Press a number to link this specific user.')
        return embed

    @commands.command(description='Show players that could be linked')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def linkcheck(self, ctx):
        await ctx.send('Please wait, this might take a bit ...')
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                # check all unmatched players
                unmatched = []
                cursor.execute('SELECT ucid, name FROM players WHERE discord_id = -1 AND name IS NOT NULL ORDER BY last_seen DESC')
                for row in cursor.fetchall():
                    matched_member = utils.match_user(self, dict(row), True)
                    if matched_member:
                        unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
                if len(unmatched) == 0:
                    await ctx.send('No unmatched member could be matched.')
                    return
                n = await utils.selection_list(self, ctx, unmatched, self.format_unmatched)
                if n != -1:
                    cursor.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (unmatched[n]['match'].id, unmatched[n]['ucid']))
                    await self.bot.audit(f"linked ucid {unmatched[n]['ucid']} to user {unmatched[n]['match'].display_name}.",
                                         user=ctx.message.author)
                    await ctx.send(f"DCS player {unmatched[n]['name']} linked to member {unmatched[n]['match'].display_name}.")
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @staticmethod
    def format_suspicious(data, marker, marker_emoji):
        embed = discord.Embed(title='Possibly Mislinked Players', color=discord.Color.blue())
        embed.description = 'These players could be possibly mislinked:'
        ids = players = members = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            players += f"{data[i]['name']}\n"
            members += f"{data[i]['mismatch'].display_name}\n"
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='DCS Player', value=players)
        embed.add_field(name='Member', value=members)
        embed.set_footer(text='Press a number to unlink this specific user.')
        return embed

    @commands.command(description='Show possibly mislinked players', aliases=['mislinked'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def mislinks(self, ctx):
        await ctx.send('Please wait, this might take a bit ...')
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                # check all matched members
                suspicious = []
                for member in self.bot.get_all_members():
                    # ignore bots
                    if member.bot:
                        continue
                    cursor.execute('SELECT ucid, name FROM players WHERE discord_id = %s AND name IS NOT NULL AND '
                                   'manual = FALSE ORDER BY last_seen DESC', (member.id, ))
                    for row in cursor.fetchall():
                        matched_member = utils.match_user(self, dict(row), True)
                        if not matched_member:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member})
                        elif matched_member.id != member.id:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member, "match": matched_member})
                if len(suspicious) == 0:
                    await ctx.send('No mislinked players found.')
                    return
                n = await utils.selection_list(self, ctx, suspicious, self.format_suspicious)
                if n != -1:
                    cursor.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                                   (suspicious[n]['match'].id if 'match' in suspicious[n] else -1,
                                    'match' in suspicious[n],
                                    suspicious[n]['ucid']))
                    await self.bot.audit(f"unlinked ucid {suspicious[n]['ucid']} from user {suspicious[n]['mismatch'].display_name}.",
                                         user=ctx.message.author)
                    if 'match' in suspicious[n]:
                        await self.bot.audit(f"linked ucid {suspicious[n]['ucid']} to user {suspicious[n]['match'].display_name}.",
                                             user=ctx.message.author)
                        await ctx.send(f"Member {suspicious[n]['mismatch'].display_name} unlinked and re-linked to member {suspicious[n]['match'].display_name}.")
                    else:
                        await ctx.send(f"Member {suspicious[n]['mismatch'].display_name} unlinked.")
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.bot.log.exception(error)
            conn.rollback()
        finally:
            self.bot.pool.putconn(conn)

    @commands.command(description='Link your DCS and Discord user')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def linkme(self, ctx):
        async def send_token(token):
            channel = await ctx.message.author.create_dm()
            await channel.send(f"**Your secure TOKEN is: {token}**\nTo link your user, type in the "
                               f"following into the DCS chat of one of our servers:```-linkme {token}```\n"
                               f"**The TOKEN will expire in 2 days.**")

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT ucid, manual FROM players WHERE discord_id = %s', (ctx.message.author.id, ))
                if cursor.rowcount >= 1:
                    row = cursor.fetchone()
                    ucid = row[0]
                    manual = row[1]
                    if len(ucid) == 4:
                        # resend the TOKEN, if there is one already
                        await send_token(ucid)
                        return
                    elif not manual:
                        if not await utils.yn_question(self, ctx, 'You have an automatic user mapping already.\nDo '
                                                                  'you want to continue and re-link your user?'):
                            return
                        else:
                            cursor.execute('UPDATE players SET discord_id = -1 WHERE discord_id = %s AND manual = FALSE',
                                           (ctx.message.author.id,))
                    elif not await utils.yn_question(self, ctx, 'You have a __verified__ user mapping already.\nHave '
                                                                'you switched from Steam to Standalone or your PC?\n'):
                        return
                # in the very unlikely event that we have generated the very same random number twice
                while True:
                    try:
                        token = random.randrange(1000, 9999)
                        cursor.execute('INSERT INTO players (ucid, discord_id, last_seen) VALUES (%s, %s, NOW())',
                                       (token, ctx.message.author.id))
                        break
                    except psycopg2.DatabaseError:
                        pass
                await send_token(token)
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            if isinstance(error, discord.Forbidden):
                await ctx.send("Please allow me to send you the secret TOKEN in a DM!", delete_after=10)
            else:
                self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()

    @tasks.loop(hours=1.0)
    async def expire_token(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM players WHERE LENGTH(ucid) = 4 AND last_seen < (DATE(NOW()) - interval '2 days')")
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        bot.add_cog(UserStatisticsMaster(bot, UserStatisticsEventListener))
    else:
        bot.add_cog(UserStatisticsAgent(bot, UserStatisticsEventListener))
