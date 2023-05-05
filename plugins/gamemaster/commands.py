import asyncio
import dateparser
import discord
import os
import platform
import psycopg
from contextlib import closing
from core import Plugin, utils, Report, Status, Server, Coalition, Channel
from discord import app_commands, TextStyle
from discord.app_commands import Range
from discord.ext import commands
from discord.ui import Modal, TextInput
from psycopg.rows import dict_row
from services import DCSServerBot
from typing import Optional, Literal

from .listener import GameMasterEventListener


class GameMaster(Plugin):

    async def install(self):
        await super().install()
        for server in self.bot.servers.values():
            if self.bot.config.getboolean(server.installation, 'COALITIONS'):
                self.log.debug(f'  - Updating "{server.name}":serverSettings.lua for coalitions')
                advanced = server.settings['advanced']
                if advanced['allow_players_pool'] != self.bot.config.getboolean(server.installation,
                                                                                'ALLOW_PLAYERS_POOL'):
                    advanced['allow_players_pool'] = self.bot.config.getboolean(server.installation,
                                                                                'ALLOW_PLAYERS_POOL')
                    server.settings['advanced'] = advanced

    def migrate(self, version: str):
        if version == '1.3':
            self.log.warning('  => Coalition system has been updated. All player coalitions have been reset!')

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
            if self.bot.config.getboolean(server.installation, 'COALITIONS'):
                sides = utils.get_sides(message, server)
                if Coalition.BLUE in sides and server.get_channel(Channel.COALITION_BLUE) == message.channel.id:
                    # TODO: ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.BLUE, message.content, message.author.display_name)
                    pass
                elif Coalition.RED in sides and server.get_channel(Channel.COALITION_RED) == message.channel.id:
                    # TODO:  ignore messages for now, as DCS does not understand the coalitions yet
                    # server.sendChatMessage(Coalition.RED, message.content, message.author.display_name)
                    pass
            if server.get_channel(Channel.CHAT) and server.get_channel(Channel.CHAT) == message.channel.id:
                if message.content.startswith(self.bot.config['BOT']['COMMAND_PREFIX']) is False:
                    server.sendChatMessage(Coalition.ALL, message.content, message.author.display_name)

    @app_commands.command(description='Send a chat message to a running DCS instance')
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

    @app_commands.command(description='Sends a popup to a coalition')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    to: Literal['all', 'red', 'blue'], message: str, time: Optional[Range[int, 1, 30]] = -1):
        server.sendPopupMessage(Coalition(to), message, time, interaction.user.display_name)
        await interaction.response.send_response('Message sent.')

    @app_commands.command(description='Sends a popup to all servers')
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

    @app_commands.command(description='Set or clear a flag inside the mission')
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

    @app_commands.command(description='Set or get a mission variable')
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

    @app_commands.command(description='Calls any function inside the mission')
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

    @app_commands.command(description='Loads a lua file into the mission')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def do_script_file(self, interaction: discord.Interaction,
                             server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                             filename: str):
        if not os.path.exists(os.path.join(os.path.expandvars(self.bot.config[server.installation]['DCS_HOME']), 
                                           filename)):
            interaction.response.send_message(f"File {filename} not found.", ephemeral=True)
        server.sendtoDCS({
            "command": "do_script_file",
            "file": filename.replace('\\', '/')
        })
        await interaction.response.send_message('Script loaded.', ephemeral=True)

    @app_commands.command(description='Mass coalition leave for users')
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
                                if not self.bot.config.getboolean(server.installation, 'COALITIONS'):
                                    continue
                                roles = {
                                    "red": discord.utils.get(
                                        interaction.guild.roles,
                                        name=self.bot.config[server.installation]['Coalition Red']
                                    ),
                                    "blue": discord.utils.get(
                                        interaction.guild.roles,
                                        name=self.bot.config[server.installation]['Coalition Blue']
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

    @staticmethod
    def format_campaigns(data, marker, marker_emoji):
        embed = discord.Embed(title="List of Campaigns", color=discord.Color.blue())
        ids = names = times = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            names += data[i]['name'] + '\n'
            times += f"{data[i]['start']:%y-%m-%d} - " + (f"{data[i]['stop']:%y-%m-%d}" if data[i]['stop'] else '') + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Name', value=names)
        embed.add_field(name='Start/End', value=times)
        embed.set_footer(text='Press a number to display details about that specific campaign.')
        return embed

    @staticmethod
    def format_servers(data):
        embed = discord.Embed(color=discord.Color.blue())
        embed.description = 'Select all servers for this campaign and press 🆗'
        ids = names = ''
        for i in range(0, len(data)):
            ids += (chr(0x31 + i) + '\u20E3' + '\n')
            names += data[i] + '\n'
        embed.add_field(name='ID', value=ids)
        embed.add_field(name='Server Name', value=names)
        return embed

    async def get_campaign_servers(self, ctx) -> list[Server]:
        servers: list[Server] = list()
        all_servers = utils.get_all_servers(self)
        if len(all_servers) == 0:
            return []
        elif len(all_servers) == 1:
            return [self.bot.servers[all_servers[0]]]
        for element in await utils.multi_selection_list(self.bot, ctx, all_servers, self.format_servers):
            servers.append(self.bot.servers[all_servers[element]])
        return servers

    @commands.command(brief='Campaign Management',
                      description='Add, remove, start, stop or delete campaigns.\n\n'
                                  '1) add <name> <start> [end]\n'
                                  '> Create a __new__ campaign in the respective timeframe. If no end is provided, end '
                                  'is open.\n'
                                  '2) start <name>\n'
                                  '> Create an instance campaign **or** add servers to an existing one.\n'
                                  '3) stop [name]\n'
                                  '> Stop the campaign with the provided name or the running campaign.\n'
                                  '4) delete [name]\n'
                                  '> Delete the campaign with the provided name or the running campaign.\n'
                                  '5) list [-all]\n'
                                  '> List the running campaign or all.',
                      aliases=['season', 'campaigns', 'seasons'])
    @utils.has_roles(['DCS Admin', 'GameMaster'])
    @commands.guild_only()
    async def campaign(self, ctx, command: Optional[str], name: Optional[str], start_time: Optional[str],
                       end_time: Optional[str]):
        server: Server = await self.bot.get_server(ctx)
        if not command:
            with self.pool.connection() as conn:
                with closing(conn.cursor(row_factory=dict_row)) as cursor:
                    cursor.execute("""
                        SELECT id, name, description, start, stop 
                        FROM campaigns 
                        WHERE NOW() BETWEEN start AND COALESCE(stop, NOW())
                    """)
                    if cursor.rowcount > 0:
                        report = Report(self.bot, self.plugin_name, 'campaign.json')
                        env = await report.render(campaign=dict(cursor.fetchone()), title='Active Campaign')
                        await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No running campaign found.')
        elif command.lower() == 'add':
            if not name or not start_time:
                await ctx.send(f"Usage: {ctx.prefix}.campaign add <name> <start> [stop]")
                return
            description = await utils.input_value(self.bot, ctx,
                                                  'Please enter a short description for this campaign (. for none):')
            servers: list[Server] = await self.get_campaign_servers(ctx)
            try:
                self.eventlistener.campaign(
                    'add', servers=servers, name=name, description=description,
                    start=dateparser.parse(start_time, settings={'TIMEZONE': 'UTC'}) if start_time else None,
                    end=dateparser.parse(end_time, settings={'TIMEZONE': 'UTC'}) if end_time else None
                )
                await ctx.send(f"Campaign {name} added.")
            except psycopg.errors.ExclusionViolation:
                await ctx.send(f"A campaign is already configured for this timeframe!")
            except psycopg.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists!")
        elif command.lower() == 'start':
            try:
                if not name:
                    await ctx.send(f"Usage: {ctx.prefix}.campaign start <name>")
                    return
                servers: list[Server] = await self.get_campaign_servers(ctx)
                self.eventlistener.campaign('start', servers=servers, name=name)
                await ctx.send(f"Campaign {name} started.")
            except psycopg.errors.ExclusionViolation:
                await ctx.send(f"There is a campaign already running on server {server.display_name}!")
            except psycopg.errors.UniqueViolation:
                await ctx.send(f"A campaign with this name already exists on server {server.display_name}!")
        elif command.lower() == 'stop':
            if not server and not name:
                await ctx.send(f'Usage: {ctx.prefix}campaign stop <name>')
                return
            if server and not name:
                _, name = utils.get_running_campaign(server)
                if not name:
                    await ctx.send('No running campaign found.')
                    return
            warn_text = f"Do you want to stop campaign \"{name}\"?"
            if await utils.yn_question(ctx, warn_text) is True:
                self.eventlistener.campaign('stop', name=name)
                await ctx.send(f"Campaign stopped.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() in ['del', 'delete']:
            if not server and not name:
                await ctx.send(f'Usage: {ctx.prefix}campaign delete <name>')
                return
            if server and not name:
                _, name = utils.get_running_campaign(server)
                if not name:
                    await ctx.send('No running campaign found.')
                    return
            warn_text = f"Do you want to delete campaign \"{name}\"?"
            if await utils.yn_question(ctx, warn_text) is True:
                self.eventlistener.campaign('delete', name=name)
                await ctx.send(f"Campaign deleted.")
            else:
                await ctx.send('Aborted.')
        elif command.lower() == 'list':
            with self.pool.connection() as conn:
                with closing(conn.cursor(row_factory=dict_row)) as cursor:
                    if name != "-all":
                        cursor.execute("""
                            SELECT id, name, description, start, stop 
                            FROM campaigns 
                            WHERE COALESCE(stop, NOW()) >= NOW() 
                            ORDER BY start DESC
                        """)
                    else:
                        cursor.execute("SELECT id, name, description, start, stop FROM campaigns ORDER BY start DESC")
                    if cursor.rowcount > 0:
                        campaigns = [dict(row) for row in cursor.fetchall()]
                        n = await utils.selection_list(self.bot, ctx, campaigns, self.format_campaigns)
                        if n != -1:
                            report = Report(self.bot, self.plugin_name, 'campaign.json')
                            env = await report.render(campaign=campaigns[n], title='Campaign Overview')
                            await ctx.send(embed=env.embed)
                    else:
                        await ctx.send('No campaigns found.')
        else:
            await ctx.send(f"Usage: {ctx.prefix}.campaign <add|start|stop|delete|list>")

    @app_commands.command(description='Displays your current player profile')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def profile(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not self.locals:
            await interaction.response.send_message(f'CreditSystem is not activated, /profile does not work.',
                                                    ephemeral=True)
            return
        config: dict = self.locals['configs'][0]
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
