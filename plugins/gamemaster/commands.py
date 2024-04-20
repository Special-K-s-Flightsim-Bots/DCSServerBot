import aiohttp
import asyncio
import json
import discord
import os
import psycopg

from core import Plugin, utils, Report, Status, Server, Coalition, Channel, command, Group, Player, UploadStatus, \
    get_translation
from discord import app_commands
from discord.app_commands import Range
from discord.ext import commands
from jsonschema import validate, ValidationError
from services import DCSServerBot
from typing import Optional, Literal

from .listener import GameMasterEventListener
from .views import CampaignModal, ScriptModal

_ = get_translation(__name__.split('.')[1])


async def scriptfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=os.path.basename(x), value=os.path.basename(x))
            for x in await server.node.list_directory(os.path.join(await server.get_missions_dir(), 'Scripts'),
                                                      pattern='*.lua')
            if not current or current.casefold() in x.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class GameMaster(Plugin):

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
                    server: Optional[str] = None) -> None:
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
        server.send_to_dcs({
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
                    to: Literal['all', 'red', 'blue'], message: str, time: Optional[Range[int, 1, 30]] = -1):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Message sent.'), ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Sends a popup to all servers'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def broadcast(self, interaction: discord.Interaction, to: Literal['all', 'red', 'blue'], message: str,
                        time: Optional[Range[int, 1, 30]] = -1):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                await interaction.followup.send(_('Message NOT sent to server {server} because it is {status}.'
                                                  ).format(server=server.display_name, status=server.status.name),
                                                ephemeral=ephemeral)
                continue
            server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
            await interaction.followup.send(_('Message sent to server {}.').format(server.display_name),
                                            ephemeral=ephemeral)

    @command(description=_('Set or get a flag inside the mission'))
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def flag(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   flag: str, value: Optional[int] = None):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if value is not None:
            server.send_to_dcs({
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
                       name: str, value: Optional[str] = None):
        if server.status != Status.RUNNING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_("Server {} is not running.").format(server.name), ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if value is not None:
            server.send_to_dcs({
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
        server.send_to_dcs({
            "command": "do_script_file",
            "file": filename.replace('\\', '/')
        })
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(_('Script loaded.'), ephemeral=utils.get_ephemeral(interaction))

    @command(description=_('Mass coalition leave for users'))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset_coalitions(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        if not await utils.yn_question(interaction,
                                       _('Do you want to mass-reset all coalition-bindings from your players?'),
                                       ephemeral=ephemeral):
            await interaction.followup.send('Aborted.', ephemeral=ephemeral)
            return
        try:
            for server in self.bot.servers.values():
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
    async def _list(self, interaction: discord.Interaction, active: Optional[bool] = True):
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

    @campaign.command(description=_("Add a campaign"))
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction):
        ephemeral = utils.get_ephemeral(interaction)
        modal = CampaignModal(self.eventlistener)
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(modal)
        if await modal.wait():
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(_('Aborted.'), ephemeral=ephemeral)
            return
        try:
            servers = await utils.server_selection(self.bus, interaction,
                                                   title=_("Select all servers for this campaign"),
                                                   multi_select=True, ephemeral=ephemeral)
            if not servers:
                await interaction.followup.send(_('Aborted.'), ephemeral=True)
                return
            if not isinstance(servers, list):
                servers = [servers]
            try:
                await self.eventlistener.campaign('add', servers=servers, name=modal.name.value,
                                                  description=modal.description.value, start=modal.start, end=modal.end)
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
            servers = await utils.server_selection(self.bus, interaction,
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

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # did a member change their roles?
        if before.roles == after.roles:
            return
        for server in self.bot.servers.values():
            player: Player = server.get_player(discord_id=after.id)
            if player and player.verified:
                server.send_to_dcs({
                    'command': 'uploadUserRoles',
                    'ucid': player.ucid,
                    'roles': [x.id for x in after.roles]
                })

    async def _create_embed(self, message: discord.Message) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.get(message.attachments[0].url) as response:
                if response.status == 200:
                    data = await response.json(encoding="utf-8")
                    with open('plugins/gamemaster/schemas/embed_schema.json', mode='r') as infile:
                        schema = json.load(infile)
                    try:
                        validate(instance=data, schema=schema)
                    except ValidationError:
                        return
                    embed = utils.format_embed(data, bot=self.bot, bus=self.bus, node=self.bus.node,
                                               user=message.author)
                    msg = None
                    if 'message_id' in data:
                        try:
                            msg = await message.channel.fetch_message(int(data['message_id']))
                            await msg.edit(embed=embed)
                        except discord.errors.NotFound:
                            msg = None
                        except discord.errors.DiscordException as ex:
                            self.log.exception(ex)
                            await message.channel.send(_('Error while updating embed!'))
                            return
                    if not msg:
                        await message.channel.send(embed=embed)
                    await message.delete()
                else:
                    await message.channel.send(_('Error {} while reading JSON file!').format(response.status))

    async def _upload_lua(self, message: discord.Message) -> int:
        # check if the upload happens in the servers admin channel (if provided)
        server: Server = self.bot.get_server(message, admin_only=True)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            if self.bot.locals.get('admin_channel', 0) == message.channel.id:
                try:
                    server = await utils.server_selection(
                        self.bus, ctx, title=_("To which server do you want to upload this LUA to?"))
                    if not server:
                        await ctx.send(_('Aborted.'))
                        return -1
                except Exception as ex:
                    self.log.exception(ex)
                    return -1
            else:
                return -1
        num = 0
        for attachment in message.attachments:
            if not attachment.filename.endswith('.lua'):
                continue
            filename = os.path.normpath(os.path.join(await server.get_missions_dir(), 'Scripts', attachment.filename))
            rc = await server.node.write_file(filename, attachment.url)
            if rc == UploadStatus.OK:
                num += 1
                continue
            if not await utils.yn_question(ctx, _('File exists. Do you want to overwrite it?')):
                await message.channel.send(_('Aborted.'))
                continue
            rc = await server.node.write_file(filename, attachment.url, overwrite=True)
            if rc != UploadStatus.OK:
                await message.channel.send(_("File {} could not be uploaded.").format(attachment.filename))
            else:
                num += 1
        return num

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages
        if message.author.bot:
            return
        if message.attachments:
            if (message.attachments[0].filename.endswith('.json') and
                    utils.check_roles(self.bot.roles['DCS Admin'], message.author)):
                await self._create_embed(message)
            elif (message.attachments[0].filename.endswith('.lua') and
                  utils.check_roles(self.bot.roles['DCS Admin'], message.author)):
                num = await self._upload_lua(message)
                if num > 0:
                    await message.channel.send(
                        _("{num} LUA files uploaded. You can load any of them with {command} now.").format(
                            num=num, command=(await utils.get_command(self.bot, name='do_script_file')).mention
                        )
                    )
                    await message.delete()
        else:
            for server in self.bot.servers.values():
                if server.status != Status.RUNNING:
                    continue
                if 'coalitions' in server.locals:
                    sides = utils.get_sides(self.bot, message, server)
                    if Coalition.BLUE in sides and server.channels[Channel.COALITION_BLUE_CHAT] == message.channel.id:
                        # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                        # server.sendChatMessage(Coalition.BLUE, message.content, message.author.display_name)
                        pass
                    elif Coalition.RED in sides and server.channels[Channel.COALITION_RED_CHAT] == message.channel.id:
                        # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                        # server.sendChatMessage(Coalition.RED, message.content, message.author.display_name)
                        pass
                if server.channels[Channel.CHAT] and server.channels[Channel.CHAT] == message.channel.id:
                    if message.content.startswith('/') is False:
                        server.sendChatMessage(Coalition.ALL, message.content, message.author.display_name)

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
                if not server:
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
