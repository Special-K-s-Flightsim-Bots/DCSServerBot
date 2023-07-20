import asyncio
import discord
import os
import psycopg2
import random
from contextlib import closing
from core import utils, DCSServerBot, Plugin, PluginRequiredError, Report, PaginationReport, Status, Server, Player, \
    DataObjectFactory, Member, Coalition, Side, PersistentReport, Channel
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
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status not in [Status.RUNNING, Status.PAUSED]:
                conn = self.pool.getconn()
                try:
                    if await utils.yn_question(ctx, f'I\'m going to **DELETE ALL STATISTICS**\n'
                                                    f'of server "{server.display_name}".\n\nAre you sure?'):
                        with closing(conn.cursor()) as cursor:
                            cursor.execute(
                                'DELETE FROM statistics WHERE mission_id in (SELECT id FROM missions WHERE '
                                'server_name = %s)', (server.name, ))
                            cursor.execute(
                                'DELETE FROM missionstats WHERE mission_id in (SELECT id FROM missions WHERE '
                                'server_name = %s)', (server.name, ))
                            cursor.execute('DELETE FROM missions WHERE server_name = %s', (server.name, ))
                            conn.commit()
                        await ctx.send(f'Statistics for server "{server.display_name}" have been wiped.')
                        await self.bot.audit('reset statistics', user=ctx.message.author, server=server)
                except (Exception, psycopg2.DatabaseError) as error:
                    self.log.exception(error)
                    conn.rollback()
                finally:
                    self.pool.putconn(conn)
            else:
                await ctx.send(f'Please stop server "{server.display_name}" before deleting the statistics!')

    # To allow super()._link() calls
    async def _link(self, ctx, member: discord.Member, ucid: str):
        # change the link status of that member if they are an active player
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = member
                player.verified = True
                return

    @commands.command(brief='Links a member to a DCS user',
                      description="Used to link a Discord member to a DCS user by linking the Discord ID to the "
                                  "respective UCID of that user.\nThe bot needs this information to be able to "
                                  "display the statistics and other information for the user.\nIf a user is manually "
                                  "linked, their link is approved, which means that they can use specific commands "
                                  "in the in-game chat, if they belong to an elevated Discord role.",
                      usage='<member> <ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid: str):
        await self._link(ctx, member, ucid)

    # To allow super()._unlink() calls
    async def _unlink(self, ctx, member: Union[discord.Member, str]):
        if isinstance(member, discord.Member):
            ucid = self.bot.get_ucid_by_member(member)
        else:
            ucid = member
        # change the link status of that member if they are an active player
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = None
                player.verified = False

    @commands.command(brief='Unlinks a member',
                      description="Removes any link between this Discord member and a DCS users. Might be used, if a "
                                  "mislink happend, either manually or due to the bots auto-link functionality not "
                                  "having linked correctly.\n\nStatistics will not be deleted for this UCID, so "
                                  "if you link the UCID to the correct member, they still see all their valuable data.",
                      usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unlink(self, ctx, member: Union[discord.Member, str]):
        await self._unlink(ctx, member)


class UserStatisticsMaster(UserStatisticsAgent):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.expire_token.start()
        if 'configs' in self.locals:
            self.persistent_highscore.start()

    async def cog_unload(self):
        if 'configs' in self.locals:
            self.persistent_highscore.cancel()
        self.expire_token.cancel()
        await super().cog_unload()

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Userstats ...')
        with closing(conn.cursor()) as cursor:
            if ucids:
                for ucid in ucids:
                    cursor.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
            elif days > 0:
                cursor.execute(f"DELETE FROM statistics WHERE hop_off < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Userstats pruned.')

    @commands.command(brief='Shows player statistics',
                      description='Displays the users statistics, either for a specific period or for a running '
                                  'campaign.\nPeriod might be one of _day, yesterday, month, week_ or _year_. Campaign '
                                  'has to be one of your configured campaigns.\nIf no period is given, default is '
                                  'everything, unless a campaign is configured. Then it\'s the running campaign.',
                      usage='[member] [period]', aliases=['stats'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def statistics(self, ctx, member: Optional[Union[discord.Member, str]], *params):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            member, period = parse_params(self, ctx, member, *params)
            if not member:
                await ctx.send('No player found with that nickname.', delete_after=timeout if timeout > 0 else None)
                return
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            if isinstance(member, str):
                if utils.is_ucid(member):
                    ucid = member
                    member = self.bot.get_member_or_name_by_ucid(ucid)
                else:
                    ucid, member = self.bot.get_ucid_by_name(member)
            else:
                ucid = self.bot.get_ucid_by_member(member)
            file = 'userstats-campaign.json' if flt.__name__ == "CampaignFilter" else 'userstats.json'
            report = PaginationReport(self.bot, ctx, self.plugin_name, file, timeout if timeout > 0 else None)
            await report.render(member=member if isinstance(member, discord.Member) else ucid,
                                member_name=utils.escape_string(member.display_name) if isinstance(member, discord.Member) else member,
                                period=period, server_name=None, flt=flt)
        finally:
            await ctx.message.delete()

    @commands.command(brief='Sends player statistics as DM',
                      description='Sends the users statistics, either for a specific period or for a running '
                                  'campaign in a DM.\nPeriod might be one of _day, yesterday, month, week_ or _year_. '
                                  'Campaign has to be one of your configured campaigns.\nIf no period is given, '
                                  'default is everything, unless a campaign is configured. Then it\'s the running '
                                  'campaign.', usage='[member] [period]')
    @utils.has_role('DCS')
    async def statsme(self, ctx, period: Optional[str]):
        try:
            member = ctx.message.author
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            await ctx.send('Your statistics will be sent in a DM.', delete_after=30)
            file = 'userstats-campaign.json' if flt.__name__ == "CampaignFilter" else 'userstats.json'
            report = PaginationReport(self.bot, await ctx.message.author.create_dm(), self.plugin_name,
                                      file, timeout if timeout > 0 else None)
            await report.render(member=member, member_name=utils.escape_string(member.display_name), period=period,
                                server_name=None, flt=flt)
        finally:
            await ctx.message.delete()

    @commands.command(brief='Shows actual highscores',
                      description='Displays the highscore, either for a specific period, a set of missions matching a '
                                  'pattern or for a running campaign:\n\n'
                                  '```.hs period:day      - day, yesterday, month, week or year\n'
                                  '.hs campaign:name   - configured campaign name\n'
                                  '.hs mission:pattern - missions matching this pattern\n'
                                  '.hs month:pattern   - month matching this pattern```\n\n'
                                  'If no period is given, default is to display everything, unless a campaign is '
                                  'configured. Then it\'s the running campaign.',
                      usage='[period]', aliases=['hs'])
    @utils.has_role('DCS')
    @commands.guild_only()
    async def highscore(self, ctx, period: Optional[str]):
        try:
            timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
            server: Server = await self.bot.get_server(ctx)
            flt = StatisticsFilter.detect(self.bot, period)
            if period and not flt:
                await ctx.send('Please provide a valid period or campaign name.')
                return
            file = 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json'
            if not server:
                report = PaginationReport(self.bot, ctx, self.plugin_name, file, timeout if timeout > 0 else None)
                await report.render(period=period, sides=[Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value],
                                    flt=flt, server_name=None)
            else:
                tmp = utils.get_sides(ctx.message, server)
                sides = [0]
                if Coalition.RED in tmp:
                    sides.append(Side.RED.value)
                if Coalition.BLUE in tmp:
                    sides.append(Side.BLUE.value)
                # in this specific case, we want to display all data, if in public channels
                if len(sides) == 0:
                    sides = [Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value]
                report = Report(self.bot, self.plugin_name, file)
                env = await report.render(period=period, server_name=server.name, sides=sides, flt=flt)
                file = discord.File(env.filename)
                await ctx.send(embed=env.embed, file=file, delete_after=timeout if timeout > 0 else None)
                if env.filename and os.path.exists(env.filename):
                    os.remove(env.filename)
        finally:
            await ctx.message.delete()

    @commands.command(description="Links a member to a DCS user", usage='<member> <ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def link(self, ctx, member: discord.Member, ucid: str):
        await super()._link(ctx, member, ucid)
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (member.id, ucid))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()
        await ctx.send('Member {} linked to ucid {}'.format(utils.escape_string(member.display_name), ucid))
        await self.bot.audit('linked member {} to ucid {}.'.format(utils.escape_string(member.display_name), ucid),
                             user=ctx.message.author)

    @commands.command(description='Unlinks a member', usage='<member|ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def unlink(self, ctx, member: Union[discord.Member, str]):
        if isinstance(member, discord.Member):
            ucid = self.bot.get_ucid_by_member(member)
        else:
            ucid = member
        if not ucid:
            await ctx.send('UCID/Member not linked.')
            return
        await super()._unlink(ctx, member)
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()
        if isinstance(member, discord.Member):
            await ctx.send('Member {} unlinked.'.format(utils.escape_string(member.display_name)))
            await self.bot.audit('unlinked member {}.'.format(utils.escape_string(member.display_name)),
                                 user=ctx.message.author)
        else:
            await ctx.send(f'ucid {ucid} unlinked.')
            await self.bot.audit(f'unlinked ucid {member}.', user=ctx.message.author)

    @commands.command(description='Shows player information', usage='<@member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def info(self, ctx, member: Union[discord.Member, str], *params):
        if isinstance(member, str):
            name = member
            if len(params):
                name += ' ' + ' '.join(params)
            if utils.is_ucid(name):
                ucid = member
            else:
                ucid, name = self.bot.get_ucid_by_name(name)
            if ucid:
                member = self.bot.get_member_by_ucid(ucid)
            else:
                await ctx.send('Player not found.')
                return
        else:
            ucid = self.bot.get_ucid_by_member(member)

        player: Optional[Player] = None
        for server in self.bot.servers.values():
            if isinstance(member, discord.Member):
                player = server.get_player(discord_id=member.id, active=True)
            elif ucid:
                player = server.get_player(ucid=ucid, active=True)
            else:
                player = server.get_player(name=member, active=True)
            if player:
                break

        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        report = Report(self.bot, self.plugin_name, 'info.json')
        env = await report.render(member=member or ucid, player=player)
        message = await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)
        try:
            _member: Optional[Member] = None
            if isinstance(member, discord.Member):
                _member = DataObjectFactory().new('Member', bot=self.bot, member=member)
                if len(_member.ucids):
                    await message.add_reaction('🔀')
                    if not _member.verified:
                        await message.add_reaction('💯')
                    await message.add_reaction('✅' if _member.banned else '⛔')
            elif ucid:
                await message.add_reaction('✅' if utils.is_banned(self, ucid) else '⛔')
            if player:
                await message.add_reaction('⏏️')
            await message.add_reaction('⏹️')
            react = await utils.wait_for_single_reaction(self.bot, ctx, message)
            if react.emoji == '🔀':
                await self.unlink(ctx, member)
            elif react.emoji == '💯':
                _member.verified = True
                if player:
                    player.verified = True
            elif react.emoji == '✅':
                await ctx.invoke(self.bot.get_command('unban'), user=member or ucid)
                if player:
                    player.banned = False
            elif react.emoji == '⛔':
                await ctx.invoke(self.bot.get_command('ban'), user=member or ucid)
                if player:
                    player.banned = True
        except asyncio.TimeoutError:
            pass
        finally:
            await message.delete()

    @staticmethod
    def format_unmatched(data, marker, marker_emoji):
        embed = discord.Embed(title='Unlinked Players', color=discord.Color.blue())
        embed.description = 'These players could be possibly linked:'
        ids = players = members = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            players += "{}\n".format(utils.escape_string(data[i]['name']))
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
        message = await ctx.send('Please wait, this might take a bit ...')
        conn = self.bot.pool.getconn()
        try:
            with closing(conn.cursor(cursor_factory=psycopg2.extras.DictCursor)) as cursor:
                # check all unmatched players
                unmatched = []
                cursor.execute('SELECT ucid, name FROM players WHERE discord_id = -1 AND name IS NOT NULL ORDER BY last_seen DESC')
                for row in cursor.fetchall():
                    matched_member = self.bot.match_user(dict(row), True)
                    if matched_member:
                        unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
                await message.delete()
                if len(unmatched) == 0:
                    await ctx.send('No unmatched member could be matched.')
                    return
                n = await utils.selection_list(self.bot, ctx, unmatched, self.format_unmatched)
                if n != -1:
                    cursor.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (unmatched[n]['match'].id, unmatched[n]['ucid']))
                    await self.bot.audit(f"linked ucid {unmatched[n]['ucid']} to user {unmatched[n]['match'].display_name}.",
                                         user=ctx.message.author)
                    await ctx.send("DCS player {} linked to member {}.".format(utils.escape_string(unmatched[n]['name']),
                                                                               unmatched[n]['match'].display_name))
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
                        matched_member = self.bot.match_user(dict(row), True)
                        if not matched_member:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member})
                        elif matched_member.id != member.id:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member, "match": matched_member})
                if len(suspicious) == 0:
                    await ctx.send('No mislinked players found.')
                    return
                n = await utils.selection_list(self.bot, ctx, suspicious, self.format_suspicious)
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
        async def send_token(ctx, token: str):
            try:
                channel = await ctx.message.author.create_dm()
                await channel.send(f"**Your secure TOKEN is: {token}**\nTo link your user, log into any of the DCS "
                                   f"servers and type the following into the in-game chat window:"
                                   f"```{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}linkme {token}```\n"
                                   f"**The TOKEN will expire in 2 days.**")
            except discord.Forbidden:
                await ctx.send("I do not have permission to send you a DM!\n"
                               "Please change your privacy settings to allow this!")

        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute('SELECT ucid, manual FROM players WHERE discord_id = %s ORDER BY manual',
                               (ctx.message.author.id, ))
                for row in cursor.fetchall():
                    if len(row[0]) == 4:
                        await send_token(ctx, row[0])
                        return
                    elif row[1] is False:
                        if not await utils.yn_question(ctx, 'Automatic user mapping found.\n'
                                                            'Do you want to re-link your user?'):
                            return
                        else:
                            for server in self.bot.servers.values():
                                player = server.get_player(ucid=row[0])
                                if player:
                                    player.member = None
                                    continue
                            cursor.execute('UPDATE players SET discord_id = -1 WHERE ucid = %s', (row[0],))
                            break
                    elif not await utils.yn_question(ctx, 'You already have a linked DCS account!\n'
                                                          'Are you sure you want to link a second account? '
                                                          '(Ex: Switched from Steam to Standalone)'):
                        return
                # in the very unlikely event that we have generated the very same random number twice
                while True:
                    try:
                        token = str(random.randrange(1000, 9999))
                        cursor.execute('INSERT INTO players (ucid, discord_id, last_seen) VALUES (%s, %s, NOW())',
                                       (token, ctx.message.author.id))
                        break
                    except psycopg2.DatabaseError:
                        pass
                await send_token(ctx, token)
                conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)
            await ctx.message.delete()

    @commands.command(description='Show inactive users')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def inactive(self, ctx: commands.Context, *param) -> None:
        period = ' '.join(param) if len(param) else None
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        report = Report(self.bot, self.plugin_name, 'inactive.json')
        env = await report.render(period=period)
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @commands.command(description='Deletes the statistics of a specific user')
    @utils.has_roles(['DCS', 'DCS Admin'])
    @commands.guild_only()
    async def delete_statistics(self, ctx: commands.Context, user: Optional[discord.Member]):
        if not user:
            user = ctx.message.author
        elif user != ctx.message.author and not utils.check_roles(['DCS Admin'], ctx.message.author):
            await ctx.send(f'You are not allowed to delete statistics of user {user.display_name}!')
            return
        member = DataObjectFactory().new('Member', bot=self.bot, member=user)
        if not member.verified:
            await ctx.send(f'User {user.display_name} has non-verified links. Statistics can not be deleted.')
            return
        conn = self.pool.getconn()
        try:
            if await utils.yn_question(ctx, f'I\'m going to **DELETE ALL STATISTICS** of user '
                                            f'"{user.display_name}".\n\nAre you sure?'):
                with closing(conn.cursor()) as cursor:
                    for ucid in member.ucids:
                        cursor.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
                        cursor.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid, ))
                        cursor.execute('DELETE FROM credits WHERE player_ucid = %s', (ucid,))
                        if self.bot.cogs.get('GreenieBoardMaster'):
                            cursor.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
                    conn.commit()
                await ctx.send(f'Statistics for user "{user.display_name}" have been wiped.')
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @tasks.loop(hours=1)
    async def expire_token(self):
        conn = self.pool.getconn()
        try:
            with closing(conn.cursor()) as cursor:
                cursor.execute("DELETE FROM players WHERE LENGTH(ucid) = 4 AND last_seen < (DATE(NOW()) - interval '2 "
                               "days')")
            conn.commit()
        except (Exception, psycopg2.DatabaseError) as error:
            self.log.exception(error)
            conn.rollback()
        finally:
            self.pool.putconn(conn)

    @tasks.loop(hours=1)
    async def persistent_highscore(self):
        def get_server_by_installation(installation: str) -> Optional[Server]:
            for server in self.bot.servers.values():
                if server.installation == installation:
                    return server
            return None

        async def render_highscore(highscore: dict):
            kwargs = highscore.get('params', {})
            period = kwargs.get('period')
            flt = StatisticsFilter.detect(self.bot, period) if period else None
            file = 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json'
            embed_name = 'highscore-' + (server_name or 'all') + '-' + period
            sides = [Side.SPECTATOR.value, Side.BLUE.value, Side.RED.value]
            report = PersistentReport(self.bot, self.plugin_name, file, server, embed_name,
                                      channel_id=highscore.get('channel', Channel.STATUS))
            await report.render(server_name=server_name, flt=flt, sides=sides, **kwargs)

        try:
            for config in self.locals['configs']:
                if 'highscore' not in config:
                    continue
                if "installation" in config:
                    server: Server = get_server_by_installation(config['installation'])
                    if not server:
                        self.log.error(f"Server {config['installation']} is not registered.")
                        return
                    server_name = server.name
                else:
                    server: Server = list(self.bot.servers.values())[0]
                    server_name = None
                if isinstance(config['highscore'], list):
                    for highscore in config['highscore']:
                        await render_highscore(highscore)
                else:
                    await render_highscore(config['highscore'])

        except Exception as ex:
            self.log.exception(ex)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    if bot.config.getboolean('BOT', 'MASTER') is True:
        await bot.add_cog(UserStatisticsMaster(bot, UserStatisticsEventListener))
    else:
        await bot.add_cog(UserStatisticsAgent(bot, UserStatisticsEventListener))
