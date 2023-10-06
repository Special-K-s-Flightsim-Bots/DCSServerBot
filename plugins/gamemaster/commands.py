import aiohttp
import asyncio
import discord
import os
import platform
import psycopg

from core import Plugin, utils, Report, Status, Server, Coalition, Channel, command, Group, Player
from discord import app_commands, TextStyle
from discord.app_commands import Range
from discord.ext import commands
from discord.ui import Modal, TextInput
from services import DCSServerBot
from typing import Optional, Literal

from .listener import GameMasterEventListener
from .views import CampaignModal


class GameMaster(Plugin):

    async def install(self):
        await super().install()
        for server in self.bot.servers.values():
            if 'coalitions' in server.locals:
                self.log.debug(f'  - Updating "{server.name}":serverSettings.lua for coalitions')
                advanced = server.settings['advanced']
                if advanced['allow_players_pool'] != server.locals['coalitions'].get('allow_players_pool', False):
                    advanced['allow_players_pool'] = server.locals['coalitions'].get('allow_players_pool', False)
                    server.settings['advanced'] = advanced

    async def prune(self, conn, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Gamemaster ...')
        if days > -1:
            conn.execute(f"DELETE FROM campaigns WHERE stop < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Gamemaster pruned.')

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE campaigns_servers SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    @command(description='Send a chat message to a running DCS instance')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   message: str):
        server.send_to_dcs({
            "command": "sendChatMessage",
            "channel": interaction.channel.id,
            "message": message,
            "from": interaction.user.display_name
        })
        await interaction.response.send_message('Message sent.')

    @command(description='Sends a popup to a coalition')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    to: Literal['all', 'red', 'blue'], message: str, time: Optional[Range[int, 1, 30]] = -1):
        server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
        await interaction.response.send_message('Message sent.')

    @command(description='Sends a popup to all servers')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def broadcast(self, interaction: discord.Interaction, to: Literal['all', 'red', 'blue'], message: str,
                        time: Optional[Range[int, 1, 30]] = -1):
        await interaction.response.defer()
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                await interaction.followup.send(
                    f'Message NOT sent to server {server.display_name} because it is {server.status.name}.',
                    ephemeral=True)
                continue
            server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
            await interaction.followup.send(f'Message sent to server {server.display_name}.', ephemeral=True)

    @command(description='Set or clear a flag inside the mission')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def flag(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   flag: str, value: Optional[int] = None):
        if value is not None:
            server.send_to_dcs({
                "command": "setFlag",
                "flag": flag,
                "value": value
            })
            await interaction.response.send_message(f"Flag {flag} set to {value}.")
        else:
            data = await server.send_to_dcs_sync({"command": "getFlag", "flag": flag})
            await interaction.response.send_message(f"Flag {flag} has value {data['value']}.")

    @command(description='Set or get a mission variable')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def variable(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                       name: str, value: Optional[str] = None):
        if value is not None:
            server.send_to_dcs({
                "command": "setVariable",
                "name": name,
                "value": value
            })
            await interaction.response.send_message(f"Variable {name} set to {value}.")
        else:
            try:
                data = await server.send_to_dcs_sync({"command": "getVariable", "name": name})
            except asyncio.TimeoutError:
                await interaction.response.send_message('Timeout while retrieving variable. Most likely a lua error '
                                                        'occurred. Check your dcs.log.')
                return
            if 'value' in data:
                await interaction.response.send_message(f"Variable {name} has value {data['value']}.")
            else:
                await interaction.response.send_message(f"Variable {name} is not set.")

    @command(description='Calls any function inside the mission')
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    @app_commands.guild_only()
    async def do_script(self, interaction: discord.Interaction,
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                            Status.RUNNING, Status.PAUSED
                        ])]):
        class ScriptModal(Modal, title="Lua Script"):
            script = TextInput(label="Enter your script here", style=TextStyle.long, required=True)

        modal = ScriptModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            server.send_to_dcs({
                "command": "do_script",
                "script": ' '.join(modal.script.value)
            })
            await interaction.followup.send('Script sent.', ephemeral=True)
        else:
            await interaction.followup.send('Aborted', ephemeral=True)

    @command(description='Loads a lua file into the mission')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def do_script_file(self, interaction: discord.Interaction,
                             server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                                 Status.RUNNING, Status.PAUSED
                             ])],
                             filename: str):
        if not os.path.exists(os.path.join(server.instance.home, filename)):
            interaction.response.send_message(f"File {filename} not found.", ephemeral=True)
        server.send_to_dcs({
            "command": "do_script_file",
            "file": filename.replace('\\', '/')
        })
        await interaction.response.send_message('Script loaded.', ephemeral=True)

    @command(description='Mass coalition leave for users')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def reset_coalitions(self, interaction: discord.Interaction):
        if not await utils.yn_question(interaction, f'Do you want to mass-reset all coalition-bindings from your '
                                                    f'players on node {platform.node()}?'):
            await interaction.response.send_message('Aborted.')
            return
        try:
            for server in self.bot.servers.values():
                if not server.locals.get('coalitions'):
                    continue
                await self.eventlistener.reset_coalitions(server, True)
                await interaction.response.send_message(
                    f'Coalition bindings reset for all players.', ephemmeral=True)
        except discord.Forbidden:
            await interaction.response.send_message('The bot is missing the "Manage Roles" permission.', ephemeral=True)
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)

    # New command group "/mission"
    campaign = Group(name="campaign", description="Commands to manage DCS campaigns")

    @campaign.command(name='list', description="Lists all (active) campaigns")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.describe(active="Display only active campaigns")
    async def _list(self, interaction: discord.Interaction, active: Optional[bool] = True):
        report = Report(self.bot, self.plugin_name, 'active-campaigns.json' if active else 'all-campaigns.json')
        env = await report.render()
        await interaction.response.send_message(embed=env.embed, ephemeral=True)

    @campaign.command(description="Campaign info")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def info(self, interaction: discord.Interaction, campaign: str):
        report = Report(self.bot, self.plugin_name, 'campaign.json')
        env = await report.render(campaign=utils.get_campaign(self, campaign), title='Campaign Overview')
        await interaction.response.send_message(embed=env.embed)

    @campaign.command(description="Add new campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction):
        modal = CampaignModal(self.eventlistener)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.response.send_message('Aborted.', ephemeral=True)
            return
        try:
            servers = await utils.server_selection(self.bus, interaction, title="Select all servers for this campaign",
                                                   multi_select=True)
            if not servers:
                await interaction.followup.send('Aborted.', ephemeral=True)
                return
            try:
                self.eventlistener.campaign('add', servers=servers, name=modal.name.value,
                                            description=modal.description.value, start=modal.start, end=modal.end)
                await interaction.followup.send(f"Campaign {modal.name.value} added.", ephemeral=True)
            except psycopg.errors.ExclusionViolation:
                await interaction.followup.send(f"A campaign is already configured for this timeframe!", ephemeral=True)
            except psycopg.errors.UniqueViolation:
                await interaction.followup.send(f"A campaign with this name already exists!", ephemeral=True)
        except Exception as ex:
            self.log.exception(ex)

    @campaign.command(description="Add a server to an existing campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def add_server(self, interaction: discord.Interaction, campaign: str,
                         server: app_commands.Transform[Server, utils.ServerTransformer]):
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO campaigns_servers (campaign_id, server_name) 
                        SELECT id, %s FROM campaigns WHERE name = %s 
                        ON CONFLICT DO NOTHING
                        """, (server.name, campaign))
            await interaction.response.send_message(f"Server {server.name} added to campaign {campaign}.")
        except psycopg.errors.UniqueViolation:
            await interaction.response.send_message(f"Server {server.name} is already part of the campaign {campaign}!",
                                                    ephemeral=True)

    @campaign.command(description="Delete a campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def delete(self, interaction: discord.Interaction,
                     campaign: Optional[str]):
        if await utils.yn_question(interaction, f"Do you want to delete campaign \"{campaign}\"?"):
            self.eventlistener.campaign('delete', name=campaign)
            await interaction.followup.send(f"Campaign deleted.")
        else:
            await interaction.followup.send('Aborted.', ephemeral=True)

    @campaign.command(description="Start a campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def start(self, interaction: discord.Interaction, campaign: str):
        try:
            await interaction.response.defer(ephemeral=True)
            servers: list[Server] = await utils.server_selection(self.bus, interaction,
                                                                 title="Select all servers for this campaign",
                                                                 multi_select=True)
            self.eventlistener.campaign('start', servers=servers, name=campaign)
            await interaction.followup.send(f"Campaign {campaign} started.", ephemeral=True)
        except psycopg.errors.ExclusionViolation:
            await interaction.followup.send(f"A campaign is already configured for this timeframe!", ephemeral=True)
        except psycopg.errors.UniqueViolation:
            await interaction.followup.send(f"A campaign with this name already exists!", ephemeral=True)

    @campaign.command(description="Stop a campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def stop(self, interaction: discord.Interaction, campaign: str):
        if await utils.yn_question(interaction, f"Do you want to stop campaign \"{campaign}\"?"):
            self.eventlistener.campaign('stop', name=campaign)
            await interaction.followup.send("Campaign stopped.")
        else:
            await interaction.followup.send('Aborted.', ephemeral=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # did a member change its roles?
        if before.roles != after.roles:
            for server in self.bot.servers.values():
                player: Player = server.get_player(discord_id=after.id)
                if player:
                    player.member = after

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages
        if message.author.bot:
            return
        if message.attachments and message.attachments[0].filename.endswith('.json'):
            # only Admin role is allowed to upload config files
            if not utils.check_roles(self.bot.roles['Admin'], message.author):
                return
            async with aiohttp.ClientSession() as session:
                async with session.get(message.attachments[0].url) as response:
                    if response.status == 200:
                        data = await response.json(encoding="utf-8")
                        embed = utils.format_embed(data)
                        msg = None
                        if 'message_id' in data:
                            try:
                                msg = await message.channel.fetch_message(int(data['message_id']))
                                await msg.edit(embed=embed)
                            except discord.errors.NotFound:
                                msg = None
                            except discord.errors.DiscordException as ex:
                                self.log.exception(ex)
                                await message.channel.send(f'Error while updating embed!')
                                return
                        if not msg:
                            await message.channel.send(embed=embed)
                        await message.delete()
                    else:
                        await message.channel.send(f'Error {response.status} while reading JSON file!')
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


async def setup(bot: DCSServerBot):
    await bot.add_cog(GameMaster(bot, GameMasterEventListener))
