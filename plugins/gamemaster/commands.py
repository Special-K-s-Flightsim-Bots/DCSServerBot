import asyncio
import discord
import os
import platform
import psycopg
from contextlib import closing
from core import Plugin, utils, Report, Status, Server, Coalition, Channel, command, Group, DEFAULT_TAG
from discord import app_commands, TextStyle, SelectOption
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

    async def prune(self, conn, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Gamemaster ...')
        if days > 0:
            conn.execute(f"DELETE FROM campaigns WHERE stop < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Gamemaster pruned.')

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE campaigns_servers SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    @commands.Cog.listener()
    async def on_message(self, message):
        # ignore bot messages
        if message.author.bot:
            return
        for server in self.bot.servers.values():
            if server.status != Status.RUNNING:
                continue
            if 'coalitions' in server.locals:
                sides = utils.get_sides(message, server)
                if Coalition.BLUE in sides and server.channels[Channel.COALITION_BLUE] == message.channel.id:
                    # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.BLUE, message.content, message.author.display_name)
                    pass
                elif Coalition.RED in sides and server.channels[Channel.COALITION_RED] == message.channel.id:
                    # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.RED, message.content, message.author.display_name)
                    pass
            if server.channels[Channel.CHAT] and server.channels[Channel.CHAT] == message.channel.id:
                if message.content.startswith('/') is False:
                    server.sendChatMessage(Coalition.ALL, message.content, message.author.display_name)

    @command(description='Send a chat message to a running DCS instance')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   message: str):
        server.sendtoDCS({
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
            server.sendtoDCS({
                "command": "setFlag",
                "flag": flag,
                "value": value
            })
            await interaction.response.send_message(f"Flag {flag} set to {value}.")
        else:
            data = await server.sendtoDCSSync({"command": "getFlag", "flag": flag})
            await interaction.response.send_message(f"Flag {flag} has value {data['value']}.")

    @command(description='Set or get a mission variable')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def variable(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                       name: str, value: Optional[str] = None):
        if value is not None:
            server.sendtoDCS({
                "command": "setVariable",
                "name": name,
                "value": value
            })
            await interaction.response.send_message(f"Variable {name} set to {value}.")
        else:
            try:
                data = await server.sendtoDCSSync({"command": "getVariable", "name": name})
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
                        server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        class ScriptModal(Modal, "Lua Script"):
            script = TextInput(label="Enter your script here", style=TextStyle.long, required=True)

        modal = ScriptModal()
        await interaction.response.send_modal(modal)
        if await modal.wait():
            server.sendtoDCS({
                "command": "do_script",
                "script": ' '.join(modal.script.value)
            })
            await interaction.response.send_message('Script sent.', ephemeral=True)
        else:
            await interaction.response.send_message('Aborted', ephemeral=True)

    @command(description='Loads a lua file into the mission')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def do_script_file(self, interaction: discord.Interaction,
                             server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                             filename: str):
        if not os.path.exists(os.path.join(os.path.expandvars(server.locals['home']),
                                           filename)):
            interaction.response.send_message(f"File {filename} not found.", ephemeral=True)
        server.sendtoDCS({
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
            with self.pool.connection() as conn:
                with conn.pipeline():
                    with conn.transaction():
                        with closing(conn.cursor()) as cursor:
                            for server in self.bot.servers.values():
                                if not server.locals.get('coalitions'):
                                    continue
                                roles = {
                                    "red": discord.utils.get(
                                        interaction.guild.roles,
                                        name=server.locals['coalition']['red']
                                    ),
                                    "blue": discord.utils.get(
                                        interaction.guild.roles,
                                        name=server.locals['coalition']['blue']
                                    )
                                }
                                for row in cursor.execute("""
                                    SELECT p.ucid, p.discord_id, c.coalition 
                                    FROM players p, coalitions c 
                                    WHERE p.ucid = c.player_ucid and c.server_name = %s AND c.coalition IS NOT NULL
                                """, (server.name,)).fetchall():
                                    if row[1] != -1:
                                        member = self.bot.guilds[0].get_member(row[1])
                                        await member.remove_roles(roles[row[2]])
                                    cursor.execute('DELETE FROM coalitions WHERE server_name = %s AND player_ucid = %s',
                                                   (server.name, row[0]))
                    await interaction.response.send_message(
                        f'Coalition bindings reset for all players.', ephemmeral=True)
        except discord.Forbidden:
            await interaction.response.send_message('The bot is missing the "Manage Roles" permission.', ephemeral=True)
            await self.bot.audit(f'permission "Manage Roles" missing.', user=self.bot.member)

    # New command group "/mission"
    campaign = Group(name="campaign", description="Commands to manage DCS campaigns")

    async def get_campaign_servers(self, interaction: discord.Interaction) -> list[Server]:
        servers: list[Server] = list()
        all_servers = list(self.bot.servers.keys())
        if len(all_servers) == 0:
            return []
        elif len(all_servers) == 1:
            return [self.bot.servers[all_servers[0]]]
        for element in await utils.selection(interaction, title="Select all servers for this campaign",
                                             options=[SelectOption(label=x, value=x) for x in all_servers]):
            servers.append(self.bot.servers[element])
        return servers

    @campaign.command(description="Campaign info")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(campaign=utils.campaign_autocomplete)
    async def info(self, interaction: discord.Interaction, campaign: Optional[str]):
        report = Report(self.bot, self.plugin_name, 'campaign.json')
        env = await report.render(campaign=utils.get_campaign(self, campaign), title='Campaign Overview')
        await interaction.response.send_message(embed=env.embed)

    @campaign.command(description="Add new campaign")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def add(self, interaction: discord.Interaction):
        servers = await self.get_campaign_servers(interaction)
        modal = CampaignModal(self.eventlistener, servers)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.response.send_message('Aborted.', ephemeral=True)
            return

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
                        VALUES SELECT id, %s FROM campaigns WHERE name = %s 
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
            await interaction.response.defer()
            servers: list[Server] = await self.get_campaign_servers(interaction)
            self.eventlistener.campaign('start', servers=servers, name=campaign)
            await interaction.followup.send(f"Campaign {campaign} started.")
        except (psycopg.errors.ExclusionViolation, psycopg.errors.UniqueViolation):
            await interaction.followup.send(f"A campaign with this name already exists.", ephemeral=True)

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

    @command(description='Displays your current player profile')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def profile(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not self.locals:
            await interaction.response.send_message(f'CreditSystem is not activated, /profile does not work.',
                                                    ephemeral=True)
            return
        config: dict = self.get_config()
        if not member:
            member = interaction.user
        embed = discord.Embed(title="User Campaign Profile", colour=discord.Color.blue())
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        if 'achievements' in config:
            for achievement in config['achievements']:
                if utils.check_roles([achievement['role']], member):
                    embed.add_field(name='Rank', value=achievement['role'])
                    break
            else:
                embed.add_field(name='Rank', value='n/a')
        ucid = self.bot.get_ucid_by_member(member, True)
        if ucid:
            campaigns = {}
            for row in self.get_credits(ucid):
                campaigns[row[1]] = {
                    "points": row[2],
                    "playtime": self.eventlistener.get_flighttime(ucid, row[0])
                }

            for campaign_name, value in campaigns.items():
                embed.add_field(name='Campaign', value=campaign_name)
                embed.add_field(name='Playtime', value=utils.format_time(value['playtime'] - value['playtime'] % 60))
                embed.add_field(name='Points', value=value['points'])
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: DCSServerBot):
    await bot.add_cog(GameMaster(bot, GameMasterEventListener))
