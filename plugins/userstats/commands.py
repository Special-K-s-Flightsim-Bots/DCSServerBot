import asyncio
import discord
import os
import psycopg
import random
from contextlib import closing
from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Status, Server, Player, \
    DataObjectFactory, Member, PersistentReport, Channel
from discord import app_commands
from discord.ext import commands, tasks
from psycopg.rows import dict_row
from services import DCSServerBot
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


class UserStatistics(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.expire_token.add_exception_type(psycopg.DatabaseError)
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
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
        elif days > 0:
            conn.execute(f"DELETE FROM statistics WHERE hop_off < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Userstats pruned.')

    @app_commands.command(description='Deletes the statistics of a server')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.describe(server='Select a server from the list')
    @app_commands.autocomplete(server=utils.server_autocomplete)
    async def reset_statistics(self, interaction: discord.Interaction,
                               server: app_commands.Transform[Server, utils.ServerTransformer]):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            if not await utils.yn_question(interaction, f'I\'m going to **DELETE ALL STATISTICS**\n'
                                                        f'of server "{server.display_name}".\n\nAre you sure?'):
                await interaction.followup.send('Aborted.', ephemeral=True)
                return
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        DELETE FROM statistics WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                        """, (server.name, ))
                    conn.execute("""
                        DELETE FROM missionstats WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                    """, (server.name, ))
                    conn.execute('DELETE FROM missions WHERE server_name = %s', (server.name, ))
            await interaction.followup.send(f'Statistics for server "{server.display_name}" have been wiped.',
                                            ephemeral=True)
            await self.bot.audit('reset statistics', user=interaction.user, server=server)
        else:
            await interaction.response.send_message(
                f'Please stop server "{server.display_name}" before deleting the statistics!', ephemeral=True)

    @app_commands.command(description='Shows player statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(user='Name of player, member or UCID')
    @app_commands.autocomplete(user=utils.all_users_autocomplete)
    @app_commands.describe(period='day, month, year, month:may, campaign:name, mission:name')
    async def statistics(self, interaction: discord.Interaction,
                         user: Optional[app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]],
                         period: Optional[str]):
        flt = StatisticsFilter.detect(self.bot, period)
        if period and not flt:
            await interaction.response.send_message('Please provide a valid period or campaign name.', ephemeral=True)
            return
        if not user:
            user = interaction.user
        if isinstance(user, discord.Member):
            member = user
            name = member.display_name
            ucid = self.bot.get_ucid_by_member(member)
        else:
            ucid = user
            member, name = self.bot.get_member_or_name_by_ucid(ucid)
        file = 'userstats-campaign.json' if flt.__name__ == "CampaignFilter" else 'userstats.json'
        report = PaginationReport(self.bot, interaction, self.plugin_name, file)
        await report.render(member=member or ucid, member_name=name, period=period, server_name=None, flt=flt)

    @app_commands.command(description='Displays the top players of your server(s)')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(server_name="server")
    @app_commands.autocomplete(server_name=utils.server_autocomplete)
    async def highscore(self, interaction: discord.Interaction, server_name: Optional[str], period: Optional[str]):
        flt = StatisticsFilter.detect(self.bot, period)
        if period and not flt:
            await interaction.response.send_message('Please provide a valid period or campaign name.', ephemeral=True)
            return
        file = 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json'
        if not server_name:
            report = PaginationReport(self.bot, interaction, self.plugin_name, file)
            await report.render(interaction=interaction, period=period, server_name=None, flt=flt)
        else:
            await interaction.response.defer()
            report = Report(self.bot, self.plugin_name, file)
            env = await report.render(interaction=interaction, period=period, server_name=server_name, flt=flt)
            file = discord.File(env.filename)
            await interaction.followup.send(embed=env.embed, file=file)
            if env.filename and os.path.exists(env.filename):
                await asyncio.to_thread(os.remove, env.filename)

    @app_commands.command(description="Links a member to a DCS user")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(ucid=utils.all_players_autocomplete)
    async def link(self, interaction: discord.Interaction, member: discord.Member, ucid: str):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (member.id, ucid))
        await interaction.response.send_message(
            f'Member {utils.escape_string(member.display_name)} linked to ucid {ucid}')
        await self.bot.audit(f'linked member {utils.escape_string(member.display_name)} to ucid {ucid}.',
                             user=interaction.user)
        # check if they are an active player on any of our servers
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = member
                player.verified = True
                break

    @app_commands.command(description='Unlinks a member or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(user='Name of player, member or UCID')
    @app_commands.autocomplete(user=utils.all_users_autocomplete)
    async def unlink(self, interaction: discord.Interaction,
                     user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]):
        if isinstance(user, discord.Member):
            member = user
            ucid = self.bot.get_ucid_by_member(member)
        else:
            ucid = user
            member = self.bot.get_member_by_ucid(ucid)
        if not ucid or not member:
            await interaction.response.send_message('Member not linked!')
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = -1, manual = FALSE WHERE ucid = %s', (ucid, ))
        await interaction.response.send_message(
            f'Member {utils.escape_string(member.display_name)} unlinked from ucid {ucid}.')
        await self.bot.audit(f'unlinked member {utils.escape_string(member.display_name)} from ucid {ucid}',
                             user=interaction.user)
        # change the link status of that member if they are an active player
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = None
                player.verified = False

    @commands.command(description='Shows player information', usage='<@member / ucid>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def info(self, ctx, member: Union[discord.Member, str], *params):
        if isinstance(member, str):
            name = member
            if len(params):
                name += ' ' + ' '.join(params)
            if len(name) == 32:
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
                _member = DataObjectFactory().new('Member', main=self.bot, member=member)
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
        with self.pool.connection() as conn:
            # check all unmatched players
            unmatched = []
            for row in conn.execute("""
                SELECT ucid, name FROM players 
                WHERE discord_id = -1 AND name IS NOT NULL 
                ORDER BY last_seen DESC
            """).fetchall():
                matched_member = self.bot.match_user(dict(row), True)
                if matched_member:
                    unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
            await message.delete()
            if len(unmatched) == 0:
                await ctx.send('No unmatched member could be matched.')
                return
        n = await utils.selection_list(self.bot, ctx, unmatched, self.format_unmatched)
        if n != -1:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s',
                                 (unmatched[n]['match'].id, unmatched[n]['ucid']))
                    await self.bot.audit(
                        f"linked ucid {unmatched[n]['ucid']} to user {unmatched[n]['match'].display_name}.",
                        user=ctx.message.author)
                    await ctx.send(
                        "DCS player {} linked to member {}.".format(utils.escape_string(unmatched[n]['name']),
                                                                    unmatched[n]['match'].display_name))

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
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                # check all matched members
                suspicious = []
                for member in self.bot.get_all_members():
                    # ignore bots
                    if member.bot:
                        continue
                    for row in cursor.execute("""
                        SELECT ucid, name FROM players 
                        WHERE discord_id = %s AND name IS NOT NULL AND manual = FALSE 
                        ORDER BY last_seen DESC
                    """, (member.id, )).fetchall():
                        matched_member = self.bot.match_user(dict(row), True)
                        if not matched_member:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member})
                        elif matched_member.id != member.id:
                            suspicious.append({"name": row['name'], "ucid": row['ucid'], "mismatch": member,
                                               "match": matched_member})
                if len(suspicious) == 0:
                    await ctx.send('No mislinked players found.')
                    return
        n = await utils.selection_list(self.bot, ctx, suspicious, self.format_suspicious)
        if n != -1:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                                 (suspicious[n]['match'].id if 'match' in suspicious[n] else -1,
                                  'match' in suspicious[n], suspicious[n]['ucid']))
                    await self.bot.audit(
                        f"unlinked ucid {suspicious[n]['ucid']} from user {suspicious[n]['mismatch'].display_name}.",
                        user=ctx.message.author)
                    if 'match' in suspicious[n]:
                        await self.bot.audit(
                            f"linked ucid {suspicious[n]['ucid']} to user {suspicious[n]['match'].display_name}.",
                            user=ctx.message.author)
                        await ctx.send(f"Member {suspicious[n]['mismatch'].display_name} unlinked and re-linked to "
                                       f"member {suspicious[n]['match'].display_name}.")
                    else:
                        await ctx.send(f"Member {suspicious[n]['mismatch'].display_name} unlinked.")

    @app_commands.command(description='Link your DCS and Discord user')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def linkme(self, interaction: discord.Interaction):
        async def send_token(interaction: discord.Interaction, token: str):
            try:
                channel = await interaction.user.create_dm()
                await channel.send(f"**Your secure TOKEN is: {token}**\nTo link your user, type in the "
                                   f"following into the DCS chat of one of our servers:"
                                   f"```{self.bot.config['BOT']['CHAT_COMMAND_PREFIX']}linkme {token}```\n"
                                   f"**The TOKEN will expire in 2 days.**")
            except discord.Forbidden:
                await interaction.response.send_message("I do not have permission to send you a DM!\n"
                                                        "Please change your privacy settings to allow this!",
                                                        ephemeral=True)

        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    for row in cursor.execute('SELECT ucid, manual FROM players WHERE discord_id = %s ORDER BY manual',
                                              (interaction.user.id, )).fetchall():
                        if len(row[0]) == 4:
                            await send_token(interaction, row[0])
                            return
                        elif row[1] is False:
                            if not await utils.yn_question(interaction, 'Automatic user mapping found.\n'
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
                        elif not await utils.yn_question(interaction,
                                                         '__Verified__ user mapping found.\n'
                                                         'Have you switched from Steam to Standalone or your PC?'):
                            return
                    # in the very unlikely event that we have generated the very same random number twice
                    while True:
                        try:
                            token = str(random.randrange(1000, 9999))
                            cursor.execute('INSERT INTO players (ucid, discord_id, last_seen) VALUES (%s, %s, NOW())',
                                           (token, ctx.message.author.id))
                            break
                        except psycopg.DatabaseError:
                            pass
            await send_token(interaction, token)

    @commands.command(description='Show inactive users')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def inactive(self, ctx: commands.Context, *param) -> None:
        period = ' '.join(param) if len(param) else None
        timeout = int(self.bot.config['BOT']['MESSAGE_AUTODELETE'])
        report = Report(self.bot, self.plugin_name, 'inactive.json')
        env = await report.render(period=period)
        await ctx.send(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @tasks.loop(hours=1)
    async def expire_token(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM players WHERE LENGTH(ucid) = 4 AND last_seen < (DATE(NOW()) - interval '2 days')")

    @tasks.loop(hours=1)
    async def persistent_highscore(self):
        def get_server_by_installation(installation: str) -> Optional[Server]:
            for server in self.bot.servers.values():
                if server.installation == installation:
                    return server
            return None

        async def render_highscore(highscore: dict, server: Optional[Server] = None):
            kwargs = highscore.get('params', {})
            period = kwargs.get('period')
            flt = StatisticsFilter.detect(self.bot, period) if period else None
            file = 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json'
            embed_name = 'highscore-' + period
            report = PersistentReport(self.bot, self.plugin_name, file, embed_name=embed_name, server=server,
                                      channel_id=highscore.get('channel', Channel.STATUS))
            await report.render(interaction=None, server_name=server.name if server else None, flt=flt, **kwargs)

        try:
            for config in self.locals['configs']:
                if 'highscore' not in config:
                    continue
                if "installation" in config:
                    server: Server = get_server_by_installation(config['installation'])
                    if not server:
                        self.log.debug(
                            f"Server {config['installation']} is not (yet) registered, skipping highscore update.")
                        return
                else:
                    server = None
                if isinstance(config['highscore'], list):
                    for highscore in config['highscore']:
                        await render_highscore(highscore, server)
                else:
                    await render_highscore(config['highscore'], server)

        except Exception as ex:
            self.log.exception(ex)

    @persistent_highscore.before_loop
    async def before_persistent_highscore(self):
        await self.bot.wait_until_ready()


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(UserStatistics(bot, UserStatisticsEventListener))
