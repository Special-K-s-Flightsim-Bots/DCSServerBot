import asyncio
import discord
import os
import psycopg

from copy import deepcopy
from core import utils, Plugin, PluginRequiredError, Report, PaginationReport, Status, Server, \
    DataObjectFactory, PersistentReport, Channel, command, DEFAULT_TAG, Group, get_translation
from datetime import timezone
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import MISSING
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from services.bot import DCSServerBot
from typing import Union, Optional, Type

from .filter import StatisticsFilter, PeriodFilter, CampaignFilter, MissionFilter, PeriodTransformer, SquadronFilter, \
    TheatreFilter
from .listener import UserStatisticsEventListener
from .views import SquadronModal

# ruamel YAML support
from ruamel.yaml import YAML

from ..creditsystem.squadron import Squadron

yaml = YAML()

_ = get_translation(__name__.split('.')[1])


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


class UserStatistics(Plugin[UserStatisticsEventListener]):

    def __init__(self, bot: DCSServerBot, listener: Type[UserStatisticsEventListener]):
        super().__init__(bot, listener)
        if self.locals:
            self.persistent_highscore.start()
            if not self.locals.get(DEFAULT_TAG, {}).get('squadrons', {}).get('self_join', True):
                super().change_commands({
                    "squadron": {"join": {"enabled": False}}
                }, {x.name: x for x in self.get_app_commands()})
                super().change_commands({
                    "squadron": {"leave": {"enabled": False}}
                }, {x.name: x for x in self.get_app_commands()})

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == '3.2':
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
                self.locals = self.read_locals()
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
                await conn.execute("DELETE FROM statistics WHERE player_ucid = %s", (ucid, ))
                await conn.execute("DELETE FROM squadron_members WHERE player_ucid = %s", (ucid, ))
        elif days > -1:
            await conn.execute("""
                DELETE FROM statistics WHERE hop_off < (DATE(now() AT TIME ZONE 'utc') - %s::interval)
            """, (f'{days} days',))
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
        await conn.execute("UPDATE squadron_members SET player_ucid = %s WHERE player_ucid = %s", (new_ucid, old_ucid))

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
                        await conn.execute("TRUNCATE TABLE traps")
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
                                 flt=[PeriodFilter, CampaignFilter, MissionFilter, TheatreFilter]
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
        report = PaginationReport(interaction, self.plugin_name, file)
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
                                flt=[PeriodFilter, CampaignFilter, MissionFilter, TheatreFilter, SquadronFilter]
                            )]] = PeriodFilter(), limit: Optional[app_commands.Range[int, 3, 20]] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        file = 'highscore-campaign.json' if isinstance(period, CampaignFilter) else 'highscore.json'
        if not _server:
            report = PaginationReport(interaction, self.plugin_name, file)
            await report.render(interaction=interaction, server_name=None, flt=period, period=period.period,
                                limit=limit)
        else:
            report = Report(self.bot, self.plugin_name, file)
            env = await report.render(interaction=interaction, server_name=_server.name, flt=period,
                                      limit=limit)
            try:
                file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else MISSING
                msg = await interaction.original_response()
                await msg.edit(embed=env.embed, attachments=[file],
                               delete_after=self.bot.locals.get('message_autodelete'))
            finally:
                if env.buffer:
                    env.buffer.close()

    @command(description='Delete statistics for users')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin'])
    async def delete_statistics(self, interaction: discord.Interaction,
                                user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer]):

        if not user:
            user = interaction.user
        if isinstance(user, discord.Member):
            ucid = await self.bot.get_ucid_by_member(user)
            name = user.display_name
        else:
            ucid = user
            name = user

        if not ucid:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("User {} is not linked.").format(name), ephemeral=True)
            return

        ephemeral = utils.get_ephemeral(interaction)
        if await utils.yn_question(
                interaction, _('I\'m going to **DELETE ALL STATISTICS** of user "{}".\n\nAre you sure?').format(name),
                ephemeral=ephemeral):
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    for plugin in self.bot.cogs.values():  # type: Plugin
                        await plugin.prune(conn, ucids=[ucid])
                await interaction.followup.send(_('Statistics for user "{}" have been wiped.').format(name),
                                                ephemeral=ephemeral)

    # New command group "/squadron"
    squadron = Group(name="squadron", description="Commands to manage squadrons")

    @squadron.command(description='Create a squadron')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def create(self, interaction: discord.Interaction, name: str, locked: bool = False, role: Optional[discord.Role] = None,
                     channel: Optional[discord.TextChannel] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(SquadronModal(self, name, locked=locked, role=role, channel=channel))

    @squadron.command(description='Info about a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS Admin')
    async def info(self, interaction: discord.Interaction,  squadron_id: int):
        async with interaction.client.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, role, description FROM squadrons WHERE id = %s", (squadron_id, ))
                row = await cursor.fetchone()
        embed = discord.Embed(title=_('Info about Squadron {}').format(row['name']),
                              description=row['description'])
        if row['role']:
            embed.add_field(name="Role", value=self.bot.get_role(row['role']).name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=embed)

    @squadron.command(description='Edit a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS Admin')
    async def edit(self, interaction: discord.Interaction, squadron_id: int, role: Optional[discord.Role] = None,
                   channel: Optional[discord.TextChannel] = None):
        async with interaction.client.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, role, description FROM squadrons WHERE id = %s", (squadron_id, ))
                row = await cursor.fetchone()
                # TODO: role changes
                # TODO: role assignment on late add
                if not role:
                    role = self.bot.get_role(row['role'])
                name = row['name']
                description = row['description']
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(SquadronModal(self, name, role=role, channel=channel, 
                                                            description=description))

    @squadron.command(description='Add a user to a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.squadron_role_check()
    async def add(self, interaction: discord.Interaction, squadron_id: int,
                  user: app_commands.Transform[Union[discord.Member, str], utils.UserTransformer],
                  admin: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        # only DCS Admin can add admin users
        if admin and not utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("You're not allowed to add another squadron admin.",
                                                    ephemeral=True)
            return

        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute("SELECT role FROM squadrons WHERE id = %s", (squadron_id,))
                role = (await cursor.fetchone())[0]
                if isinstance(user, str):
                    member = self.bot.get_member_by_ucid(ucid=user, verified=True)
                    ucid = user
                else:
                    member = user
                    ucid = await self.bot.get_ucid_by_member(member, verified=True)
                    if not ucid:
                        await interaction.followup.send(
                            f"Member {member.display_name} needs to be linked to join this squadron!", ephemeral=True)
                        return
                if not member:
                    prefix = f"User {ucid}"
                else:
                    prefix = f"Member {member.display_name}"

                # add the user to the database
                try:
                    await conn.execute("""
                        INSERT INTO squadron_members (squadron_id, player_ucid, admin) 
                        VALUES (%s, %s, %s)
                    """, (squadron_id, ucid, admin))
                    await interaction.followup.send(f"{prefix} added to the squadron.", ephemeral=ephemeral)
                    if self.get_config().get('squadrons', {}).get('persist_list', False):
                        await self.persist_squadron_list(squadron_id)
                except UniqueViolation:
                    await interaction.followup.send(f"{prefix} is a member of this or another squadron already!",
                                                    ephemeral=True)

                # check if the user needs a role
                if role:
                    if not member:
                        if not await utils.yn_question(interaction,
                                                       f"{prefix} is not linked, but auto-role is configured for this "
                                                       f"squadron. Are you sure you want to add them?",
                                                       ephemeral=ephemeral):
                            await interaction.followup.send("Aborted", ephemeral=ephemeral)
                            return
                    elif role not in [x.id for x in member.roles]:
                        _role = self.bot.get_role(role)
                        if not await utils.yn_question(interaction,
                                                       f"{prefix} needs to have the \"{_role.name}\" role to join "
                                                       f"this squadron.\n"
                                                       f"Do you want to give them the role?", ephemeral=True):
                            await interaction.followup.send("Aborted", ephemeral=ephemeral)
                            return
                        try:
                            # auto-role will make them a member
                            await member.add_roles(self.bot.get_role(role))
                        except discord.Forbidden:
                            await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                        await interaction.followup.send(f"{prefix} added to the squadron.", ephemeral=ephemeral)
                        return

    @squadron.command(description='Deletes users from squadrons')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.autocomplete(user=utils.squadron_users_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.squadron_role_check()
    async def delete(self, interaction: discord.Interaction, squadron_id: int, user: Optional[str] = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not user:
            if not utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message("You're not allowed to delete a sqaudron.", ephemeral=True)
                return
            message = "Do you really want to delete this squadron?"
        else:
            if (user == interaction.user and
                    not utils.check_roles(interaction.client.roles['DCS Admin'], interaction.user)):
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message("Admins can't delete themseleves from a squadron.",
                                                        ephemeral=True)
                return
            message = "Do you really want to delete this user from this squadron?"
        if not await utils.yn_question(interaction, message, ephemeral=ephemeral):
            await interaction.followup.send('Aborted.')
            return

        sql = """
            SELECT m.player_ucid, s.role FROM squadron_members m, squadrons s 
            WHERE m.squadron_id = s.id AND squadron_id = %(squadron_id)s
        """
        if user:
            sql += " AND m.player_ucid = %(user)s"
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                async for row in await conn.execute(sql, {"squadron_id": squadron_id, "user": user}):
                    if row[1]:
                        member = self.bot.get_member_by_ucid(row[0], verified=True)
                        role = self.bot.get_role(row[1])
                        if member:
                            try:
                                await member.remove_roles(role)
                            except discord.Forbidden:
                                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                            await interaction.followup.send(f'User removed from squadron, role {role.name} removed',
                                                            ephemeral=ephemeral)
                            return
                    else:
                        await conn.execute("DELETE FROM squadron_members WHERE squadron_id = %s AND player_ucid = %s",
                                           (squadron_id, row[0]))
                if not user:
                    await conn.execute("DELETE FROM squadron_members WHERE squadron_id = %s", (squadron_id, ))
                    await conn.execute("DELETE FROM squadrons WHERE id = %s", (squadron_id, ))
                    await interaction.followup.send('Squadron deleted.', ephemeral=ephemeral)
                else:
                    await interaction.followup.send('User removed from squadron. ', ephemeral=ephemeral)
                if self.get_config().get('squadrons', {}).get('persist_list', False):
                    await self.persist_squadron_list(squadron_id)

    @squadron.command(description='Locks a squadrons')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.squadron_role_check()
    async def lock(self, interaction: discord.Interaction, squadron_id: int):
        ephemeral = utils.get_ephemeral(interaction)
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE squadrons SET locked=TRUE WHERE id = %s", (squadron_id, ))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message("Squadron locked.", ephemeral=ephemeral)

    @squadron.command(description='Unlocks a squadrons')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.squadron_role_check()
    async def unlock(self, interaction: discord.Interaction, squadron_id: int):
        ephemeral = utils.get_ephemeral(interaction)
        async with interaction.client.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE squadrons SET locked=FALSE WHERE id = %s", (squadron_id, ))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message("Squadron unlocked.", ephemeral=ephemeral)

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
                    cursor = await conn.execute("SELECT name, role, locked FROM squadrons WHERE id = %s", (squadron_id, ))
                    row = await cursor.fetchone()
                    if row:
                        if not row[2]:
                            await conn.execute(
                                "INSERT INTO squadron_members (squadron_id, player_ucid) VALUES (%s, %s)",
                                (squadron_id, ucid))
                            message = f"You have joined squadron {row[0]}"
                            if row[1]:
                                role = self.bot.get_role(row[1])
                                try:
                                    await interaction.user.add_roles(role)
                                    message += f" and got the {role.name} role"
                                except discord.Forbidden:
                                    await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                            # noinspection PyUnresolvedReferences
                            await interaction.response.send_message(message, ephemeral=True)
                            if self.get_config().get('squadrons', {}).get('persist_list', False):
                                await self.persist_squadron_list(squadron_id)
                        else:
                            # noinspection PyUnresolvedReferences
                            await interaction.response.send_message("This squadron is locked.", ephemeral=True)
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
                cursor = await conn.execute("SELECT name, role, locked FROM squadrons WHERE id = %s", (squadron_id, ))
                row = await cursor.fetchone()
                if row:
                    if not row[2]:
                        await conn.execute("DELETE FROM squadron_members where squadron_id= %s and player_ucid = %s",
                                           (squadron_id, ucid))
                        message = f"You have left squadron {row[0]}"
                        if row[1]:
                            role = self.bot.get_role(row[1])
                            try:
                                await interaction.user.remove_roles(role)
                                message += f" and lost the {role.name} role"
                            except discord.Forbidden:
                                await self.bot.audit('permission "Manage Roles" missing.', user=self.bot.member)
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message(message, ephemeral=True)
                        if self.get_config().get('squadrons', {}).get('persist_list', False):
                            await self.persist_squadron_list(squadron_id)
                    else:
                        # noinspection PyUnresolvedReferences
                        await interaction.response.send_message("This squadron is locked.", ephemeral=True)
                else:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message("This squadron does not exist.", ephemeral=True)

    @squadron.command(name='list', description='List members of a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction, squadron_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        embed = await self.render_squadron_list(squadron_id)
        await interaction.followup.send(embed=embed)

    async def get_credits(self, squadron_id: int) -> list[dict]:
        ret = []
        async with self.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT c.id, c.name
                FROM campaigns c LEFT OUTER JOIN squadron_credits s 
                ON (c.id = s.campaign_id AND s.squadron_id = %s) 
                WHERE (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
            """, (squadron_id,)):
                squadron = utils.get_squadron(node=self.node, squadron_id=squadron_id)
                squadron_obj = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                                       campaign_id=row[0])
                ret.append({"id": row[0], "name": row[1], "points": squadron_obj.points})
        return ret

    async def get_credits_log(self, squadron_id: int) -> list[dict]:
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT s.event, s.old_points, s.new_points, remark, time 
                    FROM squadron_credits_log s, campaigns c 
                    WHERE s.squadron_id = %s AND s.campaign_id = c.id 
                    AND (now() AT TIME ZONE 'utc') BETWEEN c.start AND COALESCE(c.stop, now() AT TIME ZONE 'utc') 
                    ORDER BY s.time DESC LIMIT 10
                """, (squadron_id, ))
                return await cursor.fetchall()

    @squadron.command(name='credits', description='Credit points of a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.squadron_role_check()
    async def credits(self, interaction: discord.Interaction, squadron_id: int):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        data = await self.get_credits(squadron_id)
        if not data:
            await interaction.followup.send(_('Squadron has no campaign credits.'), ephemeral=True)
            return
        name = utils.get_squadron(self.node, squadron_id=squadron_id)['name']
        embed = discord.Embed(title=_("Campaign Credits for Squadron {}").format(name), color=discord.Color.blue())
        campaigns = points = ''
        for row in data:
            campaigns += row['name'] + '\n'
            points += f"{row['points']}\n"
        embed.add_field(name=_('Campaign'), value=campaigns)
        embed.add_field(name=_('Credits'), value=points)
        embed.add_field(name='_ _', value='_ _')
        data = await self.get_credits_log(squadron_id)
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
        await interaction.followup.send(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    @squadron.command(name='donate', description='Donate credit points to a squadron')
    @app_commands.guild_only()
    @app_commands.autocomplete(squadron_id=utils.squadron_autocomplete)
    @app_commands.rename(squadron_id="squadron")
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def donate(self, interaction: discord.Interaction, squadron_id: int, points: int,
                     server: Optional[app_commands.Transform[Server, utils.ServerTransformer]] = None):
        # noinspection PyUnresolvedReferences
        await interaction.response.defer()
        campaign_id, name = utils.get_running_campaign(self.node, server)
        squadron = utils.get_squadron(self.node, squadron_id=squadron_id)
        squadron_obj = DataObjectFactory().new(Squadron, node=self.node, name=squadron['name'],
                                               campaign_id=campaign_id)
        squadron_obj.points += points
        squadron_obj.audit(event='Admin donate', points=points, remark='')
        await interaction.followup.send(_("{} points donated to squadron {}.").format(points, squadron['name']),
                                        ephemeral=utils.get_ephemeral(interaction))

    async def render_highscore(self, highscore: Union[dict, list], *, server: Optional[Server] = None,
                               mission_end: bool = False):
        # Handle the case where the highscore is a list.
        if isinstance(highscore, list):
            # Use asyncio.gather to process multiple items concurrently
            tasks = [
                self.render_highscore(h, server=server, mission_end=mission_end)
                for h in highscore
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.log.exception(result)
            return

        # Extract and validate parameters
        kwargs = highscore.get('params', {}).copy()  # Use a shallow copy instead of a deepcopy

        if mission_end != kwargs.get('mission_end', False):
            return

        # Evaluate 'period' and handle exceptions for missing keys
        try:
            period = kwargs.get('period') or (f"mission_id:{server.mission_id}" if mission_end else None)
            if not mission_end and period:
                period = utils.format_string(period, server=server, params=kwargs)
            kwargs['period'] = period
        except KeyError as ex:
            self.log.warning(f"Skipping faulty highscore element due to missing key: {ex}")
            return

        # Detect the stats-filter to use if necessary
        flt = StatisticsFilter.detect(self.bot, period) if period else None

        # Determine the report file and embed name
        file = highscore.get('report',
                             'highscore-campaign.json' if isinstance(flt, CampaignFilter) else 'highscore.json')
        embed_name = f'highscore-{period}'

        # Resolve the channel ID
        channel_id = highscore.get('channel') or (server.channels[Channel.STATUS] if server else None)
        if not channel_id:
            self.log.warning(f"Channel ID missing for highscore '{period}'")
            return

        # Handle report rendering based on the mission_end flag
        try:
            if not mission_end:
                # Persistent highscore rendering
                report = PersistentReport(self.bot, self.plugin_name, file, embed_name=embed_name, server=server,
                                          channel_id=channel_id)
                await report.render(interaction=None, server_name=server.name if server else None, flt=flt, **kwargs)
            else:
                # Mission-end report rendering
                report = Report(self.bot, self.plugin_name, file)
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    self.log.warning(f"Highscore generation failed: Channel {channel_id} does not exist.")
                    return

                env = await report.render(interaction=None, server_name=server.name if server else None, flt=flt,
                                          **kwargs)
                try:
                    file_attachment = discord.File(fp=env.buffer,
                                                   filename=env.filename) if env.filename else discord.utils.MISSING
                    await channel.send(embed=env.embed, file=file_attachment)
                finally:
                    if env.buffer:
                        env.buffer.close()
        except Exception as ex:
            self.log.exception(f"Failed to render highscore '{period}' with error: {ex}")

    async def render_squadron_list(self, squadron_id: int):
        embed = discord.Embed(color=discord.Color.blue())
        discord_ids = dcs_names = admins = ""
        async with self.node.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, description, image_url FROM squadrons WHERE id = %s",
                                     (squadron_id, ))
                row = await cursor.fetchone()
                embed.title = f"Members of Squadron \"{row['name']}\""
                embed.description = row['description'] or MISSING
                embed.set_thumbnail(url=row['image_url'])
                async for row in await cursor.execute("""
                    SELECT DISTINCT p.discord_id, p.name, m.admin
                    FROM players p JOIN squadron_members m
                    ON p.ucid = m.player_ucid
                    AND m.squadron_id = %s
                    ORDER BY m.admin DESC, p.name
                """, (squadron_id, )):
                    new_discord_id = f"<@{row['discord_id']}>\n" if row['discord_id'] != -1 else 'not linked\n'
                    new_dcs_name = row['name'] + '\n'
                    new_admin = 'Yes\n' if row['admin'] else 'No\n'
                    if len(discord_ids + new_discord_id) > 1024 or len(dcs_names + new_dcs_name) > 1024:
                        embed.add_field(name="Member", value=discord_ids)
                        embed.add_field(name="DCS Name", value=dcs_names)
                        embed.add_field(name='Admin', value=admins)
                        discord_ids = new_discord_id
                        dcs_names = new_dcs_name
                        admins = new_admin
                    else:
                        discord_ids += new_discord_id
                        dcs_names += new_dcs_name
                        admins += new_admin
        if discord_ids.strip():
            embed.add_field(name="Member", value=discord_ids)
            embed.add_field(name="DCS Name", value=dcs_names)
            embed.add_field(name='Admin', value=admins)
        embed.set_footer(text="Admin users can add or remove members from a squadron, as well as lock or unlock it.")
        return embed

    async def persist_squadron_list(self, squadron_id: int):
        async with self.node.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT name, channel FROM squadrons WHERE id = %s",
                                     (squadron_id, ))
                row = await cursor.fetchone()
                channel = row.get('channel')
                if channel:
                    embed = await self.render_squadron_list(squadron_id)
                    if embed.fields:
                        await self.bot.setEmbed(embed_name=f'squadron_{squadron_id}_embed', embed=embed,
                                                channel_id=channel)
                    else:
                        await cursor.execute("SELECT embed FROM message_persistence WHERE embed_name = %s",
                                             (f"squadron_{squadron_id}_embed", ))
                        row2 = await cursor.fetchone()
                        if row2:
                            try:
                                ch: discord.TextChannel = self.bot.get_channel(channel)
                                message = await ch.fetch_message(row2['embed'])
                                if message:
                                    await message.delete()
                            except Exception:
                                self.log.debug(f"Can't remove persistent embed for squadron {row['name']}.")

    @tasks.loop(hours=1)
    async def persistent_highscore(self):
        try:
            # Cache global config
            default_config = self.locals.get(DEFAULT_TAG)
            if default_config:
                # Render global highscore
                global_highscore = default_config.get('highscore')
                if global_highscore:
                    await self.render_highscore(global_highscore)

                # Render global squadrons highscore if applicable
                squadrons_highscore = default_config.get('squadrons', {}).get('highscore')
                if squadrons_highscore:
                    async with self.node.apool.connection() as conn:
                        query = """
                            SELECT name, channel FROM squadrons WHERE channel IS NOT NULL
                        """
                        async for row in await conn.execute(query):
                            # Avoid redundant deepcopy calls and modify a single config object.
                            config = deepcopy(squadrons_highscore)
                            config.update({
                                'channel': row[1],
                                'params': {
                                              "period": f"squadron:{row[0]}"
                                          } | config.get('params', {})
                            })
                            await self.render_highscore(config)

            # Render server-specific-highscores
            for server in self.bus.servers.values():
                server_config = self.locals.get(server.node.name, self.locals).get(server.instance.name)
                if server_config and server_config.get('highscore'):
                    await self.render_highscore(deepcopy(server_config['highscore']), server=server)

        except Exception as ex:
            # Improved logging with context
            self.log.exception("Error while rendering persistent highscores: %s", ex)

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

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # did a member change their roles?
        if before.roles == after.roles:
            return
        # only linked members are affected
        ucid = await self.bot.get_ucid_by_member(after, verified=True)
        if not ucid:
            return
        removed_roles = [x.id for x in set(before.roles) - set(after.roles)]
        new_roles = [x.id for x in set(after.roles) - set(before.roles)]
        try:
            # get possible squadron roles
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async for row in await conn.execute('SELECT id, role FROM squadrons WHERE role IS NOT NULL'):
                        # do we have to add the member to a squadron?
                        if row[1] in new_roles:
                            await conn.execute("""
                                INSERT INTO squadron_members VALUES (%s, %s) 
                                ON CONFLICT (squadron_id, player_ucid) DO NOTHING
                            """, (row[0], ucid))
                        # do we have to remove the member from a squadron?
                        elif row[1] in removed_roles:
                            await conn.execute("""
                                DELETE FROM squadron_members WHERE squadron_id = %s and player_ucid = %s
                            """, (row[0], ucid))
                        if self.get_config().get('squadrons', {}).get('persist_list', False):
                            await self.persist_squadron_list(row[0])
        except Exception as ex:
            self.log.exception(ex)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(UserStatistics(bot, UserStatisticsEventListener))
