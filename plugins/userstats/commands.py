import discord
import os
import psycopg

from copy import deepcopy
from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Status, Server, \
    DataObjectFactory, PersistentReport, Channel, command, DEFAULT_TAG, Member, Group
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import MISSING
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Union, Optional

from .filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, SquadronFilter
from .listener import UserStatisticsEventListener
from .views import SquadronModal

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


def parse_params(self, ctx, member: Optional[Union[discord.Member, str]], *params) \
        -> tuple[Union[discord.Member, str], str]:
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
        if self.locals:
            self.persistent_highscore.start()

    def migrate(self, version: str) -> None:
        if version == '3.2':
            if not self.locals:
                return

            def migrate_instance(cfg: dict) -> bool:
                changed = False
                for name, instance in cfg.items():
                    if 'greeting_message_members' in instance:
                        del instance['greeting_message_members']
                        changed = True
                    if 'greeting_message_unmatched' in instance:
                        del instance['greeting_message_unmatched']
                        changed = True
                return changed

            dirty = False
            if self.node.name in self.locals:
                for node_name, node in self.locals.items():
                    dirty |= migrate_instance(node)
            else:
                dirty |= migrate_instance(self.locals)
            if dirty:
                path = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
                with open(path, mode='w', encoding='utf-8') as outfile:
                    yaml.dump(self.locals, outfile)
                self.log.warning(f"New file {path} written, please check for possible errors.")

    async def cog_unload(self):
        if self.locals:
            self.persistent_highscore.cancel()
        await super().cog_unload()

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: Optional[str] = None) -> None:
        self.log.debug('Pruning Userstats ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM statistics WHERE player_ucid = %s', (ucid, ))
        elif days > -1:
            await conn.execute(f"""
                DELETE FROM statistics WHERE hop_off < (DATE(now() AT TIME ZONE 'utc') - interval '{days} days')
            """)
        if server:
            await conn.execute("""
                DELETE FROM statistics WHERE mission_id in (
                    SELECT id FROM missions WHERE server_name = %s
                )
            """, (server, ))
            await conn.execute("""
                DELETE FROM statistics WHERE mission_id NOT IN (
                    SELECT id FROM missions
                )
            """)
        self.log.debug('Userstats pruned.')

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute("UPDATE statistics SET player_ucid = %s WHERE player_ucid = %s", (new_ucid, old_ucid))

    @command(description='Deletes the statistics of a server')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.rename(_server="server")
    async def reset_statistics(self, interaction: discord.Interaction,
                               _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None):
        if not _server:
            for s in self.bus.servers.values():
                if s.status in [Status.RUNNING, Status.PAUSED]:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(
                        f'Please stop all servers before deleting the statistics!', ephemeral=True)
                    return
        elif _server.status in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f'Please stop server "{_server.display_name}" before deleting the statistics!', ephemeral=True)
            return

        ephemeral = utils.get_ephemeral(interaction)
        message = "I'm going to **DELETE ALL STATISTICS**\n"
        if _server:
            message += f"of server \"{_server.display_name}\"!"
        else:
            message += f"of **ALL** servers!"
        message += "\n\nAre you sure?"
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                if _server:
                    await conn.execute("""
                        DELETE FROM statistics WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                        """, (_server.name,))
                    await conn.execute("""
                        DELETE FROM missionstats WHERE mission_id in (
                            SELECT id FROM missions WHERE server_name = %s
                        )
                    """, (_server.name,))
                    await conn.execute('DELETE FROM missions WHERE server_name = %s', (_server.name,))
                    await interaction.followup.send(f'Statistics for server "{_server.display_name}" have been wiped.',
                                                    ephemeral=ephemeral)
                    await self.bot.audit('reset statistics', user=interaction.user, server=_server)
                else:
                    await conn.execute("TRUNCATE TABLE statistics")
                    await conn.execute("TRUNCATE TABLE missionstats")
                    await conn.execute("TRUNCATE TABLE missions")
                    if 'greenieboard' in self.node.plugins:
                        await conn.execute("TRUNCATE TABLE greenieboard")
                    await interaction.followup.send(f'Statistics for ALL servers have been wiped.', ephemeral=ephemeral)
                    await self.bot.audit('reset statistics of ALL servers', user=interaction.user)

    @command(description='Shows player statistics')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.describe(user='Name of player, member or UCID')
    @app_commands.describe(period='Select one of the default periods or enter the name of a campaign or a mission')
    async def statistics(self, interaction: discord.Interaction,
                         period: Optional[app_commands.Transform[
                             StatisticsFilter, PeriodTransformer(
                                 flt=[PeriodFilter, CampaignFilter, MissionFilter]
                             )]] = PeriodFilter(),
                         user: Optional[app_commands.Transform[
                             Union[discord.Member, str], utils.UserTransformer]
                         ] = None):
        if not user:
            user = interaction.user
        if isinstance(user, discord.Member):
            name = user.display_name
        else:
            name = await self.bot.get_member_or_name_by_ucid(user)
            if isinstance(name, discord.Member):
                name = name.display_name
        file = 'userstats-campaign.json' if isinstance(period, CampaignFilter) else 'userstats.json'
        report = PaginationReport(self.bot, interaction, self.plugin_name, file)
        await report.render(member=user, member_name=name, server_name=None, period=period.period, flt=period)

    @command(description='Displays the top players of your server(s)')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(_server="server")
    @app_commands.describe(period='Select one of the default periods or enter the name of a campaign or a mission')
    async def highscore(self, interaction: discord.Interaction,
                        _server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None,
                        period: Optional[app_commands.Transform[
                            StatisticsFilter, PeriodTransformer(
                                flt=[PeriodFilter, CampaignFilter, MissionFilter, SquadronFilter]
                            )]] = PeriodFilter(), limit: Optional[app_commands.Range[int, 3, 20]] = None):
        file = 'highscore-campaign.json' if isinstance(period, CampaignFilter) else 'highscore.json'
        if not _server:
            report = PaginationReport(self.bot, interaction, self.plugin_name, file)
            await report.render(interaction=interaction, server_name=None, flt=period, period=period.period,
                                limit=limit)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer()
            report = Report(self.bot, self.plugin_name, file)
            env = await report.render(interaction=interaction, server_name=_server.name, flt=period,
                                      limit=limit)
            try:
                file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
                await interaction.followup.send(embed=env.embed, file=file)
            finally:
                if env.buffer:
                    env.buffer.close()

    @command(description='Delete statistics for users')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS', 'DCS Admin'])
    async def delete_statistics(self, interaction: discord.Interaction, user: Optional[discord.Member]):
        if not user:
            user = interaction.user
        elif user != interaction.user and not utils.check_roles(self.bot.roles['DCS Admin'], interaction.user):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f'You are not allowed to delete statistics of user {user.display_name}!')
            return
        member = DataObjectFactory().new(Member, name=user.name, node=self.node, member=user)
        if not member.verified:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"User {user.display_name} has non-verified links. Statistics can't be deleted.", ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if await utils.yn_question(interaction, f'I\'m going to **DELETE ALL STATISTICS** of user '
                                                f'"{user.display_name}".\n\nAre you sure?', ephemeral=ephemeral):
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, ucids=[member.ucid])
                await interaction.followup.send(f'Statistics for user "{user.display_name}" have been wiped.',
                                                ephemeral=ephemeral)

    # New command group "/squadron"
    squadron = Group(name="squadron", description="Commands to manage squadrons")

    @squadron.command(description='Create a squadron')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def create(self, interaction: discord.Interaction, name: str, role: Optional[discord.Role] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(SquadronModal(name, role))

    @squadron.command(description='Edit a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS Admin')
    async def edit(self, interaction: discord.Interaction, squadron_id: int, role: Optional[discord.Role] = None):
        async with interaction.client.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, role, description FROM squadrons WHERE id = %s", (squadron_id, ))
                row = await cursor.fetchone()
                if not role:
                    role = self.bot.get_role(row['role'])
                name = row['name']
                description = row['description']
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(SquadronModal(name, role, description))

    @squadron.command(description='Delete a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS Admin')
    async def delete(self, interaction: discord.Interaction, squadron_id: int):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction, "Do you really want to delete this Squadron?", ephemeral=ephemeral):
            await interaction.followup.send('Aborted.')
            return
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                async for row in await conn.execute("""
                    SELECT m.player_ucid, s.role FROM squadron_members m, squadrons s 
                    WHERE m.squadron_id = s.id AND squadron_id = %s
                """, (squadron_id,)):
                    if row[1]:
                        member = self.bot.get_member_by_ucid(row[0], verified=True)
                        role = self.bot.get_role(row[1])
                        if member:
                            try:
                                await member.remove_roles(role)
                            except discord.Forbidden:
                                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                    await conn.execute("DELETE FROM squadron_members WHERE squadron_id = %s AND player_ucid = %s",
                                       (squadron_id, row[0]))
                await conn.execute("DELETE FROM squadrons WHERE id = %s", (squadron_id, ))
        await interaction.followup.send('Squadron deleted.')

    @squadron.command(description='Join a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS')
    async def join(self, interaction: discord.Interaction, squadron_id: int):
        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Your user needs to be linked to use this command!",
                                                    ephemeral=True)
            return
        try:
            async with interaction.client.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("INSERT INTO squadron_members (squadron_id, player_ucid) VALUES (%s, %s)",
                                        (squadron_id, ucid))
                    cursor = await conn.execute("SELECT name, role FROM squadrons WHERE id = %s", (squadron_id, ))
                    row = await cursor.fetchone()
                    if row:
                        message = f"You have joined squadron {row[0]}"
                        if row[1]:
                            role = self.bot.get_role(row[1])
                            try:
                                await interaction.user.add_roles(role)
                                message += f" and be given the {role.name} role"
                            except discord.Forbidden:
                                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(message, ephemeral=True)
                    else:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message("This squadron does not exist.", ephemeral=True)
        except UniqueViolation:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("You are a member of this squadron already.", ephemeral=True)

    @squadron.command(description='Leave a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS')
    async def leave(self, interaction: discord.Interaction, squadron_id: int):
        ucid = await self.bot.get_ucid_by_member(interaction.user)
        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Your user needs to be linked to use this command!",
                                                    ephemeral=True)
            return
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM squadron_members where squadron_id= %s and player_ucid = %s",
                                   (squadron_id, ucid))
                cursor = await conn.execute("SELECT name, role FROM squadrons WHERE id = %s", (squadron_id, ))
                row = await cursor.fetchone()
                if row:
                    message = f"You have left squadron {row[0]}"
                    if row[1]:
                        role = self.bot.get_role(row[1])
                        try:
                            await interaction.user.remove_roles(role)
                            message += f" and be taken the {role.name} role"
                        except discord.Forbidden:
                            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(message, ephemeral=True)
                else:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message("This squadron does not exist.", ephemeral=True)

    @squadron.command(description='List members of a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS')
    async def list(self, interaction: discord.Interaction, squadron_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        embed = discord.Embed(color=discord.Color.blue())
        discord_ids = dcs_names = ""
        async with interaction.client.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, description, image_url FROM squadrons WHERE id = %s",
                                     (squadron_id, ))
                row = await cursor.fetchone()
                embed.title = f"Members of Squadron \"{row['name']}\""
                embed.description = row['description'] or MISSING
                embed.set_thumbnail(url=row['image_url'])
                async for row in await cursor.execute("""
                    SELECT DISTINCT p.discord_id, p.name
                    FROM players p JOIN squadron_members m
                    ON p.ucid = m.player_ucid
                    AND m.squadron_id = %s
                """, (squadron_id, )):
                    new_discord_id = f"<@{row['discord_id']}>\n"
                    new_dcs_name = row['name'] + '\n'
                if len(discord_ids + new_discord_id) > 1024 or len(dcs_names + new_dcs_name) > 1024:
                    embed.add_field(name="Member", value=discord_ids)
                    embed.add_field(name="DCS Name", value=dcs_names)
                    embed.add_field(name='_ _', value='_ _')
                    discord_ids = new_discord_id
                    dcs_names = new_dcs_name
                else:
                    discord_ids += new_discord_id
                    dcs_names += new_dcs_name
        if discord_ids.strip():
            embed.add_field(name="Member", value=discord_ids)
            embed.add_field(name="DCS Name", value=dcs_names)
            embed.add_field(name='_ _', value='_ _')
        await interaction.followup.send(embed=embed)

    async def render_highscore(self, highscore: Union[dict, list], server: Optional[Server] = None,
                               mission_end: Optional[bool] = False):
        if isinstance(highscore, list):
            for h in highscore:
                await self.render_highscore(h, server, mission_end)
            return
        kwargs = deepcopy(highscore.get('params', {}))
        if ((not mission_end and kwargs.get('mission_end', False)) or
                (mission_end and not kwargs.get('mission_end', False))):
            return
        try:
            if not mission_end:
                period = kwargs['period'] = utils.format_string(kwargs.get('period'), server=server, params=kwargs)
            else:
                period = kwargs['period'] = kwargs.get('period') or f'mission_id:{server.mission_id}'
        except KeyError as ex:
            self.log.warning(f'Skipping wrong highscore element due to missing key: {ex}')
            return
        flt = StatisticsFilter.detect(self.bot, period) if period else None
        file = highscore.get('report',
                             'highscore-campaign.json' if isinstance(flt, CampaignFilter) else 'highscore.json')
        embed_name = 'highscore-' + period
        channel_id = highscore.get('channel')
        if not mission_end:
            report = PersistentReport(self.bot, self.plugin_name, file, embed_name=embed_name, server=server,
                                      channel_id=channel_id or Channel.STATUS)
            await report.render(interaction=None, server_name=server.name if server else None, flt=flt, **kwargs)
        else:
            report = Report(self.bot, self.plugin_name, file)
            channel = self.bot.get_channel(channel_id)
            if not channel:
                self.log.warning(f"Can't generate highscore, channel {channel_id} does not exist.")
                return
            env = await report.render(interaction=None, server_name=server.name if server else None, flt=flt, **kwargs)
            try:
                file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else discord.utils.MISSING
                await channel.send(embed=env.embed, file=file)
            finally:
                if env.buffer:
                    env.buffer.close()

    @tasks.loop(hours=1)
    async def persistent_highscore(self):
        try:
            # global highscore
            if self.locals.get(DEFAULT_TAG) and self.locals[DEFAULT_TAG].get('highscore'):
                await self.render_highscore(self.locals[DEFAULT_TAG]['highscore'], None)
            for server in list(self.bus.servers.values()):
                config = self.locals.get(server.node.name, self.locals).get(server.instance.name)
                if not config or not config.get('highscore'):
                    continue
                await self.render_highscore(config['highscore'], server)
        except Exception as ex:
            self.log.exception(ex)

    @persistent_highscore.before_loop
    async def before_persistent_highscore(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if self.get_config().get('wipe_stats_on_leave', True):
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    cursor = await conn.execute('SELECT ucid FROM players WHERE discord_id = %s', (member.id,))
                    self.bot.log.debug(f'- Deleting their statistics due to wipe_stats_on_leave')
                    ucids = [row[0] async for row in cursor]
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, ucids=ucids)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(UserStatistics(bot, UserStatisticsEventListener))
