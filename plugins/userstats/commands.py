import asyncio
import discord
import os
import psycopg
import random
from contextlib import closing

from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Status, Server, Player, \
    DataObjectFactory, PersistentReport, Channel, command, DEFAULT_TAG, PlayerType
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands, tasks
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Union, Optional, Tuple, Literal

from .filter import StatisticsFilter
from .listener import UserStatisticsEventListener
from .views import InfoView


def parse_params(self, ctx, member: Optional[Union[discord.Member, str]], *params) \
        -> Tuple[Union[discord.Member, str], str]:
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
        if self.locals:
            self.persistent_highscore.start()

    async def cog_unload(self):
        if self.locals:
            self.persistent_highscore.cancel()
        self.expire_token.cancel()
        await super().cog_unload()

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Userstats ...')
        if ucids:
            for ucid in ucids:
                conn.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
        elif days > -1:
            conn.execute(f"DELETE FROM statistics WHERE hop_off < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Userstats pruned.')

    @command(description='Deletes the statistics of a server')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(_server="server")
    async def reset_statistics(self, interaction: discord.Interaction,
                               _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None):
        if not _server:
            for s in self.bus.servers.values():
                if s.status in [Status.RUNNING, Status.PAUSED]:
                    await interaction.response.send_message(
                        f'Please stop all servers before deleting the statistics!', ephemeral=True)
                    return
        elif _server.status in [Status.RUNNING, Status.PAUSED]:
            await interaction.response.send_message(
                f'Please stop server "{_server.display_name}" before deleting the statistics!', ephemeral=True)
            return

        message = "I'm going to **DELETE ALL STATISTICS**\n"
        if _server:
            message += f"of server \"{_server.display_name}\"!"
        else:
            message += f"of **ALL** servers!"
        message += "\n\nAre you sure?"
        if not await utils.yn_question(interaction, message):
            await interaction.followup.send('Aborted.', ephemeral=True)
            return
        with self.pool.connection() as conn:
            with conn.transaction():
                if _server:
                    conn.execute("""
                        DELETE FROM statistics WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                        """, (_server.name,))
                    conn.execute("""
                        DELETE FROM missionstats WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                    """, (_server.name,))
                    conn.execute('DELETE FROM missions WHERE server_name = %s', (_server.name,))
                    await interaction.followup.send(f'Statistics for server "{_server.display_name}" have been wiped.',
                                                    ephemeral=True)
                    await self.bot.audit('reset statistics', user=interaction.user, server=_server)
                else:
                    conn.execute("TRUNCATE TABLE statistics")
                    conn.execute("TRUNCATE TABLE missionstats")
                    conn.execute("TRUNCATE TABLE missions")
                    if 'greenieboard' in self.bot.node.plugins:
                        conn.execute("TRUNCATE TABLE greenieboard")
                    await interaction.followup.send(f'Statistics for ALL servers have been wiped.', ephemeral=True)
                    await self.bot.audit('reset statistics of ALL servers', user=interaction.user)

    @command(description='Shows player statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(user='Name of player, member or UCID')
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

    @command(description='Displays the top players of your server(s)')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(_server="server")
    async def highscore(self, interaction: discord.Interaction,
                        _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None,
                        period: Optional[str] = None):
        flt = StatisticsFilter.detect(self.bot, period)
        if period and not flt:
            await interaction.response.send_message('Please provide a valid period or campaign name.', ephemeral=True)
            return
        file = 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json'
        if not _server:
            report = PaginationReport(self.bot, interaction, self.plugin_name, file)
            await report.render(interaction=interaction, period=period, server_name=None, flt=flt)
        else:
            await interaction.response.defer()
            report = Report(self.bot, self.plugin_name, file)
            env = await report.render(interaction=interaction, period=period, server_name=_server.name, flt=flt)
            file = discord.File(env.filename)
            await interaction.followup.send(embed=env.embed, file=file)
            if env.filename and os.path.exists(env.filename):
                await asyncio.to_thread(os.remove, env.filename)

    @command(description="Links a member to a DCS user")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(ucid="user")
    async def link(self, interaction: discord.Interaction, member: discord.Member,
                   ucid: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(
                       sel_type=PlayerType.PLAYER, linked=False)]
                   ):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s', (member.id, ucid))
                # delete a token, if one existed
                conn.execute('DELETE FROM players WHERE discord_id = %s AND LENGTH(ucid) = 4', (member.id, ))
        await interaction.response.send_message(
            f'Member {utils.escape_string(member.display_name)} linked to ucid {ucid}', ephemeral=True)
        await self.bot.audit(f'linked member {utils.escape_string(member.display_name)} to ucid {ucid}.',
                             user=interaction.user)
        # check if they are an active player on any of our servers
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = member
                player.verified = True
                break

    @command(description='Unlinks a member or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(user='Name of player, member or UCID')
    async def unlink(self, interaction: discord.Interaction,
                     user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer(linked=True)]):
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
            f'Member {utils.escape_string(member.display_name)} unlinked from ucid {ucid}.', ephemeral=True)
        await self.bot.audit(f'unlinked member {utils.escape_string(member.display_name)} from ucid {ucid}',
                             user=interaction.user)
        # change the link status of that member if they are an active player
        for server_name, server in self.bot.servers.items():
            player = server.get_player(ucid=ucid)
            if player:
                player.member = None
                player.verified = False

    @command(description='Shows player information')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction,
                   member: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]):
        if isinstance(member, str):
            ucid = member
            member = self.bot.get_member_by_ucid(ucid)
        player: Optional[Player] = None
        for server in self.bot.servers.values():
            if isinstance(member, discord.Member):
                player = server.get_player(discord_id=member.id, active=True)
            else:
                player = server.get_player(ucid=ucid, active=True)
            if player:
                break
        else:
            server = None

        view = InfoView(member=member or ucid, bot=self.bot, player=player, server=server)
        embed = await view.render()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

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

    @command(description='Show players that could be linked')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def linkcheck(self, interaction: discord.Interaction):
        # await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.response.defer(thinking=True)
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                # check all unmatched players
                unmatched = []
                for row in cursor.execute("""
                    SELECT ucid, name FROM players 
                    WHERE discord_id = -1 AND name IS NOT NULL 
                    ORDER BY last_seen DESC
                """).fetchall():
                    matched_member = self.bot.match_user(dict(row), True)
                    if matched_member:
                        unmatched.append({"name": row['name'], "ucid": row['ucid'], "match": matched_member})
            if len(unmatched) == 0:
                await interaction.followup.send('No unmatched member could be matched.', ephemeral=True)
                return
        n = await utils.selection_list(self.bot, interaction, unmatched, self.format_unmatched)
        if n != -1:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute('UPDATE players SET discord_id = %s, manual = TRUE WHERE ucid = %s',
                                 (unmatched[n]['match'].id, unmatched[n]['ucid']))
                    await self.bot.audit(
                        f"linked ucid {unmatched[n]['ucid']} to user {unmatched[n]['match'].display_name}.",
                        user=interaction.user)
                    await interaction.followup.send(
                        "DCS player {} linked to member {}.".format(utils.escape_string(unmatched[n]['name']),
                                                                    unmatched[n]['match'].display_name),
                        ephemeral=True)

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

    @command(description='Show possibly mislinked players')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def mislinks(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
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
                    await interaction.followup.send('No mislinked players found.', ephemeral=True)
                    return
        n = await utils.selection_list(self.bot, interaction, suspicious, self.format_suspicious)
        if n != -1:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute('UPDATE players SET discord_id = %s, manual = %s WHERE ucid = %s',
                                 (suspicious[n]['match'].id if 'match' in suspicious[n] else -1,
                                  'match' in suspicious[n], suspicious[n]['ucid']))
                    await self.bot.audit(
                        f"unlinked ucid {suspicious[n]['ucid']} from user {suspicious[n]['mismatch'].display_name}.",
                        user=interaction.user)
                    if 'match' in suspicious[n]:
                        await self.bot.audit(
                            f"linked ucid {suspicious[n]['ucid']} to user {suspicious[n]['match'].display_name}.",
                            user=interaction.user)
                        await interaction.followup.send(
                            f"Member {suspicious[n]['mismatch'].display_name} unlinked and re-linked to member "
                            f"{suspicious[n]['match'].display_name}.", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Member {suspicious[n]['mismatch'].display_name} unlinked.",
                                                        ephemeral=True)

    @command(description='Link your DCS and Discord user')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def linkme(self, interaction: discord.Interaction):
        async def send_token(token: str):
            await interaction.followup.send(f"**Your secure TOKEN is: {token}**\nTo link your user, type in the "
                                            f"following into the DCS chat of one of our servers:"
                                            f"```{self.bot.node.config['chat_command_prefix']}linkme {token}```\n"
                                            f"**The TOKEN will expire in 2 days.**", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        with self.pool.connection() as conn:
            with conn.transaction():
                with closing(conn.cursor()) as cursor:
                    for row in cursor.execute('SELECT ucid, manual FROM players WHERE discord_id = %s ORDER BY manual',
                                              (interaction.user.id, )).fetchall():
                        if len(row[0]) == 4:
                            await send_token(row[0])
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
                                                         "You already have a linked DCS account!\n"
                                                         "Are you sure you want to link a second account? "
                                                         "(Ex: Switched from Steam to Standalone)"):
                            return
                    # in the very unlikely event that we have generated the very same random number twice
                    while True:
                        try:
                            token = str(random.randrange(1000, 9999))
                            cursor.execute('INSERT INTO players (ucid, discord_id, last_seen) VALUES (%s, %s, NOW())',
                                           (token, interaction.user.id))
                            break
                        except psycopg.DatabaseError:
                            pass
            await send_token(token)

    @command(description='Shows inactive users')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def inactive(self, interaction: discord.Interaction, period: Literal['days', 'weeks', 'months', 'years'],
                       number: Range[int, 1]):
        report = Report(self.bot, self.plugin_name, 'inactive.json')
        env = await report.render(period=f"{number} {period}")
        await interaction.response.send_message(embed=env.embed, ephemeral=True)

    @command(description='Delete statistics for users')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS', 'DCS Admin'])
    async def delete_statistics(self, interaction: discord.Interaction, user: Optional[discord.Member]):
        if not user:
            user = interaction.user
        elif user != interaction.user and not utils.check_roles(['DCS Admin'], interaction.user):
            await interaction.response.send_message(
                f'You are not allowed to delete statistics of user {user.display_name}!', ephemeral=True)
            return
        member = DataObjectFactory().new('Member', bot=self.bot, member=user)
        if not member.verified:
            await interaction.response.send_message(
                f'User {user.display_name} has non-verified links. Statistics can not be deleted.', ephemeral=True)
            return
        if await utils.yn_question(interaction, f'I\'m going to **DELETE ALL STATISTICS** of user '
                                                f'"{user.display_name}".\n\nAre you sure?'):
            with self.pool.connection() as conn:
                with conn.transaction():
                    for ucid in member.ucids:
                        # TODO: change that to prune()-calls
                        conn.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
                        conn.execute('DELETE FROM missionstats WHERE init_id = %s', (ucid, ))
                        conn.execute('DELETE FROM credits WHERE player_ucid = %s', (ucid,))
                        if 'greenieboard' in self.bot.node.plugins:
                            conn.execute('DELETE FROM greenieboard WHERE player_ucid = %s', (ucid,))
                    conn.commit()
                await interaction.followup.send(f'Statistics for user "{user.display_name}" have been wiped.', 
                                                ephemeral=True)

    @tasks.loop(hours=1)
    async def expire_token(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "DELETE FROM players WHERE LENGTH(ucid) = 4 AND last_seen < (DATE(NOW()) - interval '2 days')")

    @tasks.loop(hours=1)
    async def persistent_highscore(self):
        def get_server_by_instance(instance: str) -> Optional[Server]:
            for server in self.bot.servers.values():
                if server.instance.name == instance:
                    return server
            return None

        async def render_highscore(highscore: dict, server: Optional[Server] = None):
            kwargs = highscore.get('params', {})
            period = kwargs.get('period')
            flt = StatisticsFilter.detect(self.bot, period) if period else None
            file = highscore.get('report',
                                 'highscore-campaign.json' if flt.__name__ == "CampaignFilter" else 'highscore.json')
            embed_name = 'highscore-' + period
            report = PersistentReport(self.bot, self.plugin_name, file, embed_name=embed_name, server=server,
                                      channel_id=highscore.get('channel', Channel.STATUS))
            await report.render(interaction=None, server_name=server.name if server else None, flt=flt, **kwargs)

        try:
            for instance_name, config in self.locals.items():
                if 'highscore' not in config:
                    continue
                if instance_name != DEFAULT_TAG:
                    server = get_server_by_instance(instance_name)
                    if not server:
                        self.log.debug(
                            f"Server {instance_name} is not (yet) registered, skipping highscore update.")
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

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self.get_config().get('wipe_stats_on_leave', True):
            with self.pool.connection() as conn:
                with conn.transaction():
                    self.bot.log.debug(f'- Deleting their statistics due to WIPE_STATS_ON_LEAVE')
                    ucids = [row[0] for row in conn.execute(
                        'SELECT ucid FROM players WHERE discord_id = %s', (member.id, )).fetchall()]
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, ucids=ucids)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(UserStatistics(bot, UserStatisticsEventListener))
