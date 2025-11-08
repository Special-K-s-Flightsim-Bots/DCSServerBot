import asyncio
import discord
import os
import psycopg
from psycopg.rows import dict_row

from core import Plugin, utils, Report, Status, Server, Coalition, Channel, command, Group, get_translation, PlayerType, \
    Player
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands
from services.bot import DCSServerBot
from typing import Literal

from .listener import GameMasterEventListener
from .upload import GameMasterUploadHandler
from .views import CampaignModal, ScriptModal, MessageModal, MessageView

_ = get_translation(__name__.split('.')[1])


async def scriptfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        base_dir = os.path.join(await server.get_missions_dir(), 'Scripts')
        exp_base, file_list = await server.node.list_directory(base_dir, pattern='*.lua', traverse=True)
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.relpath(x, exp_base), value=os.path.relpath(x, exp_base))
            for x in file_list
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def recipient_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT DISTINCT p.name, p.ucid 
                FROM players p, messages m
                WHERE p.ucid = m.player_ucid
                AND (name ILIKE %s OR ucid ILIKE %s)
            """, ('%' + current + '%', '%' + current + '%'))
            choices: list[app_commands.Choice[int]] = [
                app_commands.Choice(name=f"{row[0]} (ucid={row[1]})", value=row[1])
                async for row in cursor
            ]
            return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


async def campaign_servers_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        campaign_name = utils.get_interaction_param(interaction, 'campaign')
        async with interaction.client.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT DISTINCT server_name FROM campaigns_servers
                WHERE campaign_id IN (
                    SELECT id FROM campaigns WHERE name = %s 
                ) 
            """, (campaign_name, ))
            choices: list[app_commands.Choice[str]] = [
                app_commands.Choice(name=row[0], value=row[0])
                async for row in cursor
            ]
            return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)
        return []


class GameMaster(Plugin[GameMasterEventListener]):

    async def install(self) -> bool:
        init = await super().install()
        for server in self.bot.servers.values():
            if 'coalitions' in server.locals:
                self.log.debug(f'  - Updating "{server.name}":serverSettings.lua for coalitions')
                advanced = server.settings['advanced']
                if advanced['allow_players_pool'] != server.locals['coalitions'].get('allow_players_pool', False):
                    advanced['allow_players_pool'] = server.locals['coalitions'].get('allow_players_pool', False)
                    server.settings['advanced'] = advanced
        return init

    async def prune(self, conn: psycopg.AsyncConnection, *, days: int = -1, ucids: list[str] = None,
                    server: str | None = None) -> None:
        self.log.debug('Pruning Gamemaster ...')
        if ucids:
            for ucid in ucids:
                await conn.execute('DELETE FROM coalitions WHERE player_ucid = %s', (ucid, ))
        if days > -1:
            await conn.execute("DELETE FROM campaigns WHERE stop < (DATE(now() AT TIME ZONE 'utc') - %s::interval)",
                               (f'{days} days', ))
        if server:
            await conn.execute("DELETE FROM campaigns_servers WHERE server_name = %s", (server, ))
            await conn.execute("DELETE FROM coalitions WHERE server_name = %s", (server, ))
        self.log.debug('Gamemaster pruned.')

    async def rename(self, conn: psycopg.AsyncConnection, old_name: str, new_name: str):
        await conn.execute('UPDATE campaigns_servers SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def update_ucid(self, conn: psycopg.AsyncConnection, old_ucid: str, new_ucid: str) -> None:
        await conn.execute('UPDATE coalitions SET player_ucid = %s WHERE player_ucid = %s', (new_ucid, old_ucid))

    @command(description=_('Send a chat message to DCS'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   message: str):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        await server.send_to_dcs({
            "command": "sendChatMessage",
            "channel": interaction.channel.id,
            "message": message,
            "from": interaction.user.display_name
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Sends a popup to a coalition\n'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    to: Literal['all', 'red', 'blue'], message: str, time: Range[int, 1, 30] | None = -1):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        await server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Sends a popup to all servers'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def broadcast(self, interaction: discord.Interaction, to: Literal['all', 'red', 'blue'], message: str,
                        time: Range[int, 1, 30] | None = -1):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        received: dict[str, bool] = {}
        for server in self.bot.get_servers(manager=interaction.user).values():
            if server.status != Status.RUNNING:
                received[server.display_name] = False
                continue
            await server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
            received[server.display_name] = True
        embed = discord.Embed(colour=discord.Colour.blue())
        embed.title = _("The message was sent to the following servers")
        embed.description = f"```{message}```"
        names = []
        status = []
        for name, stat in received.items():
            names.append(name)
            status.append(':white_check_mark:' if stat else ':x:')
        embed.add_field(name=_("Server"), value='\n'.join(names))
        embed.add_field(name=_("Message sent"), value='\n'.join(status))
        await interaction.followup.send(embed=embed, ephemeral=ephemeral)

    @command(description=_('Set or get a flag inside the mission'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def flag(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   flag: str, value: int | None = None):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if value is not None:
            await server.send_to_dcs({
                "command": "setFlag",
                "flag": flag,
                "value": value
            })
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Flag {flag} set to {value}.").format(flag=flag, value=value),
                                                    ephemeral=ephemeral)
        else:
            data = await server.send_to_dcs_sync({"command": "getFlag", "flag": flag})
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Flag {flag} has value {value}.").format(
                flag=flag, value=data['value']), ephemeral=ephemeral)

    @command(description=_('Set or get a mission variable'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def variable(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                       name: str, value: str | None = None):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if value is not None:
            await server.send_to_dcs({
                "command": "setVariable",
                "name": name,
                "value": value
            })
            await interaction.followup.send(_("Variable {name} set to {value}.").format(name=name, value=value),
                                            ephemeral=ephemeral)
        else:
            try:
                data = await server.send_to_dcs_sync({"command": "getVariable", "name": name})
            except (TimeoutError, asyncio.TimeoutError):
                await interaction.followup.send(
                    _('Timeout while retrieving variable. Most likely a lua error occurred. Check your dcs.log.'),
                    ephemeral=True)
                return
            if 'value' in data:
                await interaction.followup.send(
                    _("Variable {name} has value {value}.").format(name=name, value=data['value']), ephemeral=ephemeral)
            else:
                await interaction.followup.send(_("Variable {} is not set.").format(name), ephemeral=ephemeral)

    @command(description=_('Calls any function inside the mission'))
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    @app_commands.guild_only()
    async def do_script(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                            Status.RUNNING, Status.PAUSED
                        ])]):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        modal = ScriptModal(server, utils.get_ephemeral(interaction))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)

    @command(description=_('Loads a lua file into the mission'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    @app_commands.autocomplete(filename=scriptfile_autocomplete)
    async def do_script_file(self, interaction: discord.Interaction,
                             server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                                 Status.RUNNING, Status.PAUSED
                             ])],
                             filename: str):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        filename = os.path.join('Missions', 'Scripts', filename)
        await server.send_to_dcs({
            "command": "do_script_file",
            "file": filename.replace('\\', '/')
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Script loaded.'), ephemeral=utils.get_ephemeral(interaction))
        await self.bot.audit(f"loaded LUA script {filename}", user=interaction.user, server=server)

    @command(description=_('Reset coalition cooldown'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset_coalition(self, interaction: discord.Interaction,
                              server: app_commands.Transform[Server, utils.ServerTransformer(
                                  status=[Status.PAUSED, Status.RUNNING])],
                              player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)]):
        ephemeral = utils.get_ephemeral(interaction)
        if not server.locals.get('coalitions'):
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("The coalition system is not enabled on this server."),
                                                    ephemeral=True)
            return

        if not await utils.yn_question(
                interaction,
                _('Do you want to reset the coalition-bindings from player {}?').format(player.display_name),
                ephemeral=ephemeral
        ):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        try:
            await self.eventlistener.reset_coalition(server, player)
            await interaction.followup.send(_('Coalition bindings for player {} reset.').format(player.display_name),
                                            ephemeral=ephemeral)
        except discord.Forbidden:
            await interaction.followup.send(_('The bot is missing the "Manage Roles" permission!'), ephemeral=ephemeral)
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)

    @command(description=_('Reset all coalition cooldowns'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset_coalitions(self, interaction: discord.Interaction,
                               server: app_commands.Transform[Server, utils.ServerTransformer(
                                   status=[Status.PAUSED, Status.RUNNING])] | None = None):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction,
                                       _('Do you want to mass-reset all coalition-bindings from your players?'),
                                       ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        try:
            if server:
                await self.eventlistener.reset_coalitions(server, True)
                await interaction.followup.send(
                    _('Coalition bindings reset for all players on server {}.').format(server.display_name),
                    ephemeral=ephemeral
                )
            else:
                for server in self.bot.get_servers(manager=interaction.user).values():
                    if not server.locals.get('coalitions'):
                        continue
                    await self.eventlistener.reset_coalitions(server, True)
                await interaction.followup.send(_('Coalition bindings reset for all players.'), ephemeral=ephemeral)
        except discord.Forbidden:
            await interaction.followup.send(_('The bot is missing the "Manage Roles" permission!'), ephemeral=ephemeral)
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)

    # New command group "/campaign"
    campaign = Group(name="campaign", description=_("Commands to manage DCS campaigns"))

    @campaign.command(name='list', description=_("Lists all (active) campaigns"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(active=_("Display only active campaigns"))
    async def _list(self, interaction: discord.Interaction, active: bool | None = True):
        report = Report(self.bot, self.plugin_name, 'active-campaigns.json' if active else 'all-campaigns.json')
        env = await report.render()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @campaign.command(description=_("Campaign info"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def info(self, interaction: discord.Interaction, campaign: str):
        report = Report(self.bot, self.plugin_name, 'campaign.json')
        env = await report.render(campaign=await utils.get_campaign(self, campaign), title=_('Campaign Overview'))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @campaign.command(description=_("Edit Campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def edit(self, interaction: discord.Interaction, campaign: str):
        ephemeral = utils.get_ephemeral(interaction)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("""
                    SELECT * FROM campaigns WHERE name = %s
                """, (campaign, ))
                row = await cursor.fetchone()
                if not row:
                    # noinspection PyUnresolvedReferences
                    await interaction.response.send_message(_('Campaign not found.'), ephemeral=True)
                    return
                modal = CampaignModal(name=campaign, start=row['start'], end=row['stop'], description=row['description'],
                                      image_url=row['image_url'])
                # noinspection PyUnresolvedReferences
                await interaction.response.send_modal(modal)
                if await modal.wait():
                    return
                async with conn.transaction():
                    await conn.execute("""
                        UPDATE campaigns SET start=%s, stop=%s, description=%s, image_url=%s 
                        WHERE name=%s
                    """, (modal.start, modal.end, modal.description.value, modal.image_url.value, campaign))
                await interaction.followup.send(_('Campaign {} updated.').format(campaign),
                                                        ephemeral=ephemeral)

    @campaign.command(description=_("Add a campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        modal = CampaignModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        try:
            servers = await utils.server_selection(self.bot, interaction,
                                                   title=_("Select all servers for this campaign"),
                                                   multi_select=True, ephemeral=ephemeral)
            if not servers:
                await interaction.followup.send(_('Aborted.'), ephemeral=True)
                return
            if not isinstance(servers, list):
                servers = [servers]
            try:
                await self.eventlistener.campaign(
                    'add',
                    servers=servers,
                    name=modal.name.value,
                    description=modal.description.value,
                    image_url=modal.image_url.value,
                    start=modal.start,
                    end=modal.end
                )
                await interaction.followup.send(_("Campaign {} added.").format(modal.name.value), ephemeral=ephemeral)
            except psycopg.errors.ExclusionViolation:
                await interaction.followup.send(_("A campaign is already configured for this timeframe!"),
                                                ephemeral=ephemeral)
            except psycopg.errors.UniqueViolation:
                await interaction.followup.send(_("A campaign with this name already exists!"), ephemeral=ephemeral)
        except Exception as ex:
            self.log.exception(ex)

    @campaign.command(description=_("Add a server to an existing campaign\n"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def add_server(self, interaction: discord.Interaction, campaign: str,
                         server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO campaigns_servers (campaign_id, server_name) 
                        SELECT id, %s FROM campaigns WHERE name = %s 
                        ON CONFLICT DO NOTHING
                        """, (server.name, campaign))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {server} added to campaign {campaign}.").format(server=server.name, campaign=campaign),
                ephemeral=ephemeral)
        except psycopg.errors.UniqueViolation:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                _("Server {server} is already part of the campaign {campaign}!").format(
                    server=server.name, campaign=campaign), ephemeral=ephemeral)

    @campaign.command(description=_("Delete a server from a campaign\n"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    @app_commands.rename(server_name='server')
    @app_commands.autocomplete(server_name=campaign_servers_autocomplete)
    async def delete_server(self, interaction: discord.Interaction, campaign: str, server_name: str):
        ephemeral = utils.get_ephemeral(interaction)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    DELETE FROM campaigns_servers
                    WHERE campaign_id = (
                        SELECT id FROM campaigns WHERE name = %s 
                    ) AND server_name = %s 
                    """, (campaign, server_name))
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(
            _("Server {server} deleted from campaign {campaign}.").format(server=server_name, campaign=campaign),
            ephemeral=ephemeral)

    @campaign.command(description=_("Delete a campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def delete(self, interaction: discord.Interaction, campaign: str):
        ephemeral = utils.get_ephemeral(interaction)
        if await utils.yn_question(interaction, _('Do you want to delete campaign "{}"?').format(campaign),
                                   ephemeral=ephemeral):
            await self.eventlistener.campaign('delete', name=campaign)
            await interaction.followup.send(_("Campaign deleted."), ephemeral=ephemeral)
        else:
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)

    @campaign.command(description=_("Start a campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def start(self, interaction: discord.Interaction, campaign: str):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=True)
            servers = await utils.server_selection(self.bot, interaction,
                                                   title=_("Select all servers for this campaign"),
                                                   multi_select=True, ephemeral=ephemeral)
            if not isinstance(servers, list):
                servers = [servers]
            await self.eventlistener.campaign('start', servers=servers, name=campaign)
            await interaction.followup.send(_("Campaign {} started.").format(campaign), ephemeral=ephemeral)
        except psycopg.errors.ExclusionViolation:
            await interaction.followup.send(_("A campaign is already configured for this timeframe!"),
                                            ephemeral=ephemeral)
        except psycopg.errors.UniqueViolation:
            await interaction.followup.send(_("A campaign with this name already exists!"), ephemeral=ephemeral)

    @campaign.command(description=_("Stop a campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def stop(self, interaction: discord.Interaction, campaign: str):
        ephemeral = utils.get_ephemeral(interaction)
        if await utils.yn_question(interaction, _('Do you want to stop campaign "{}"?').format(campaign),
                                   ephemeral=ephemeral):
            await self.eventlistener.campaign('stop', name=campaign)
            await interaction.followup.send(_("Campaign stopped."), ephemeral=ephemeral)
        else:
            await interaction.followup.send(_('Aborted.'), ephemeral=ephemeral)

    # New command group "/message"
    message = Group(name="message", description=_("Commands to manage user messages"))

    @message.command(description=_('Sends a message to a user'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def send(self, interaction: discord.Interaction,
                   to: app_commands.Transform[discord.Member | str, utils.UserTransformer(
                       sel_type=PlayerType.PLAYER)], acknowledge: bool | None = True):
        modal = MessageModal()
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        if isinstance(to, str):
            ucid = to
        elif isinstance(to, discord.Member):
            ucid = await self.bot.get_ucid_by_member(to)
            if not ucid:
                await interaction.followup.send(_("User is not linked."), ephemeral=True)
                return
        else:
            await interaction.followup.send(_("Unknown user {}!").format(to), ephemeral=True)
            return
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    INSERT INTO messages (sender, player_ucid, message, ack) 
                    VALUES (%s, %s, %s, %s)
                """, (interaction.user.display_name, ucid, modal.message.value, acknowledge))
                await interaction.followup.send(_("Message will be displayed to the user."),
                                                ephemeral=utils.get_ephemeral(interaction))

    @message.command(description=_('Edit or delete a user-message'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    @app_commands.autocomplete(ucid=recipient_autocomplete)
    async def edit(self, interaction: discord.Interaction, ucid: str):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        async with self.apool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT * FROM messages WHERE player_ucid = %s ORDER BY id", (ucid, ))
                messages = await cursor.fetchall()
        if not messages:
            await interaction.followup.send(_("No messages found."), ephemeral=ephemeral)
        user = await self.bot.get_member_or_name_by_ucid(ucid)
        view = MessageView(messages, user)
        embed = await view.render()
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=ephemeral)
        try:
            await view.wait()
        finally:
            await msg.delete()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        pattern = ['.lua', '.json']

        if GameMasterUploadHandler.is_valid(message, pattern=pattern, roles=self.bot.roles['DCS Admin']):
            server = await GameMasterUploadHandler.get_server(message)
            if not server:
                return
            handler = GameMasterUploadHandler(plugin=self, server=server, message=message, pattern=pattern)
            try:
                base_dir = os.path.join(await handler.server.get_missions_dir(), 'Scripts')
                await handler.upload(base_dir)
            except Exception as ex:
                self.log.exception(ex)
            finally:
                await message.delete()
        elif not message.author.bot:
            for server in self.bot.servers.values():
                if server.status != Status.RUNNING:
                    continue
                if 'coalitions' in server.locals:
                    sides = utils.get_sides(self.bot, message, server)
                    if Coalition.BLUE in sides and server.channels[Channel.COALITION_BLUE_CHAT] == message.channel.id:
                        # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                        # await server.sendChatMessage(Coalition.BLUE, message.content, message.author.display_name)
                        pass
                    elif Coalition.RED in sides and server.channels[Channel.COALITION_RED_CHAT] == message.channel.id:
                        # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                        # await server.sendChatMessage(Coalition.RED, message.content, message.author.display_name)
                        pass
                if server.channels[Channel.CHAT] and server.channels[Channel.CHAT] == message.channel.id:
                    if not message.content.startswith('/'):
                        await server.sendChatMessage(Coalition.ALL, message.content, message.author.display_name)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        async with self.apool.connection() as conn:
            async for row in await conn.execute("""
                SELECT DISTINCT c.server_name, c.coalition 
                FROM players p 
                JOIN coalitions c ON p.ucid = c.player_ucid
                WHERE p.discord_id = %s
                AND c.coalition_leave IS NULL
            """, (member.id, )):
                server = self.bot.get_server(row[0])
                if not server or 'coalitions' not in server.locals:
                    return
                roles = {
                    'red': self.bot.get_role(server.locals['coalitions']['red_role']),
                    'blue': self.bot.get_role(server.locals['coalitions']['blue_role'])
                }
                try:
                    await member.add_roles(roles[row[1]])
                    self.log.debug(
                        f"=> Rejoined member {member.display_name} got their role {roles[row[1]].name} back.")
                except discord.Forbidden:
                    await self.bot.audit(_('permission "Manage Roles" missing.'), user=self.bot.member)


async def setup(bot: DCSServerBot):
    await bot.add_cog(GameMaster(bot, GameMasterEventListener))
