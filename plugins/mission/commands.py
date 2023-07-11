import asyncio
import discord
import os
import psycopg
import re
import traceback
import yaml

from core import utils, Plugin, Report, Status, Server, Coalition, Channel, Player, PluginRequiredError, MizFile, \
    Group, ReportEnv, UploadStatus
from datetime import datetime
from discord import Interaction, app_commands
from discord.app_commands import Range
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from services import DCSServerBot
from typing import Optional

from .listener import MissionEventListener
from .views import ServerView, PresetView


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_channel_name.start()
        self.afk_check.start()

    async def cog_unload(self):
        self.afk_check.cancel()
        self.update_channel_name.add_exception_type(AttributeError)
        self.update_channel_name.cancel()
        await super().cog_unload()

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE missions SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def prune(self, conn: psycopg.Connection, *, days: int = 0, ucids: list[str] = None):
        self.log.debug('Pruning Mission ...')
        if days > 0:
            conn.execute(f"DELETE FROM missions WHERE mission_end < (DATE(NOW()) - interval '{days} days')")
        self.log.debug('Mission pruned.')

    # New command group "/mission"
    mission = Group(name="mission", description="Commands to manage a DCS mission")

    @mission.command(description='Info about the running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        await interaction.response.defer()
        report = Report(self.bot, self.plugin_name, 'serverStatus.json')
        env: ReportEnv = await report.render(server=server)
        file = discord.File(env.filename) if env.filename else discord.utils.MISSING
        await interaction.followup.send(embed=env.embed, file=file)
        if env.filename and os.path.exists(env.filename):
            await asyncio.to_thread(os.remove, env.filename)

    @mission.command(description='Manage the active mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def manage(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        view = ServerView(server)
        embed = await view.render(interaction)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mission.command(description='Information about a specific airport')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(idx='airport')
    @app_commands.describe(idx='Airport for ATIS information')
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    async def atis(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])],
                   idx: int):
        airbase = server.current_mission.airbases[idx]
        data = await server.sendtoDCSSync({
            "command": "getWeatherInfo",
            "x": airbase['position']['x'],
            "y": airbase['position']['y'],
            "z": airbase['position']['z']
        })
        report = Report(self.bot, self.plugin_name, 'atis.json')
        env = await report.render(airbase=airbase, server_name=server.display_name, data=data)
        timeout = self.bot.locals.get('message_autodelete', 300)
        await interaction.response.send_message(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @mission.command(description='Shows briefing of the active mission')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def briefing(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer]):
        def read_passwords(server: Server) -> dict:
            with self.pool.connection() as conn:
                row = conn.execute(
                    'SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                    (server.name,)).fetchone()
                return {"Blue": row[0], "Red": row[1]}

        timeout = self.bot.locals.get('message_autodelete', 300)
        mission_info = await server.sendtoDCSSync({
            "command": "getMissionDetails"
        })
        mission_info['passwords'] = read_passwords(server)
        report = Report(self.bot, self.plugin_name, 'briefing.json')
        env = await report.render(mission_info=mission_info, server_name=server.name, interaction=interaction)
        await interaction.response.send_message(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @mission.command(description='Restarts the current active mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def restart(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                      delay: Optional[int] = 120, reason: Optional[str] = None):
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await interaction.response.send_message(
                f"Can't restart server {server.name} as it is {server.status.name}!", ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(interaction,
                                                                  'A restart is currently pending.\n'
                                                                  'Would you still like to restart the mission?'):
            return
        else:
            server.on_empty = dict()
        if server.is_populated():
            result = await utils.populated_question(interaction, "Do you really want to restart the mission?")
            if not result:
                await interaction.followup.send('Aborted.', ephemeral=True)
                return
            elif result == 'later':
                server.on_empty = {"command": "restart", "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send('Restart postponed when server is empty.', ephemeral=True)
                return

        server.restart_pending = True
        if not interaction.response.is_done():
            await interaction.response.defer()
        if server.is_populated():
            if delay > 0:
                message = f'!!! Server will be restarted in {utils.format_time(delay)}!!!'
            else:
                message = '!!! Server will be restarted NOW !!!'
            # have we got a message to present to the users?
            if reason:
                message += f' Reason: {reason}'

            msg = await interaction.followup.send(
                f'Restarting mission in {utils.format_time(delay)} (warning users before)...', ephemeral=True)
            server.sendPopupMessage(Coalition.ALL, message, sender=interaction.user.display_name)
            await asyncio.sleep(delay)
            await msg.delete()

        msg = await interaction.followup.send('Mission will restart now, please wait ...', ephemeral=True)
        if server.current_mission:
            await server.current_mission.restart()
        else:
            await server.stop()
            await server.start()
        await self.bot.audit("restarted mission", server=server, user=interaction.user)
        await msg.delete()
        await interaction.followup.send('Mission restarted.', ephemeral=True)

    @mission.command(description='(Re-)Loads a mission from the list')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def load(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.STOPPED, Status.RUNNING, Status.PAUSED])],
                   mission_id: int):
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await interaction.response.send_message(
                f"Can't load mission on server {server.name} as it is {server.status.name}!", ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(interaction,
                                                                  'A restart is currently pending.\n'
                                                                  'Would you still like to change the mission?'):
            await interaction.followup.send('Aborted', ephemeral=True)
            return
        else:
            server.on_empty = dict()

        if server.is_populated():
            result = await utils.populated_question(interaction, f"Do you really want to change the mission?")
            if not result:
                await interaction.followup.send('Aborted.', ephemeral=True)
                return
        else:
            result = "yes"

        if not interaction.response.is_done():
            await interaction.response.defer()
        if server.settings['missionList'][mission_id] == server.current_mission.filename:
            if result == 'later':
                server.on_empty = {"command": "restart", "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(f'Mission {server.current_mission.display_name} will be restarted '
                                                f'when server is empty.', ephemeral=True)
            else:
                await server.current_mission.restart()
                await interaction.followup.send(f'Mission {server.current_mission.display_name} restarted.',
                                                ephemeral=True)
        else:
            mission = server.settings['missionList'][mission_id]
            name = mission[:-4]
            if result == 'later':
                server.on_empty = {"command": "load", "id": mission_id + 1, "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(f'Mission {name} will be loaded when server is empty.',
                                                ephemeral=True)
            else:
                tmp = await interaction.followup.send(f'Loading mission {utils.escape_string(name)} ...',
                                                      ephemeral=True)
                await server.loadMission(mission_id + 1)
                await self.bot.audit("loaded mission", server=server, user=interaction.user)
                await tmp.delete()
                await interaction.followup.send(f'Mission {name} loaded.', ephemeral=True)

    @mission.command(description='Adds a mission to the list')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(path=utils.mizfile_autocomplete)
    async def add(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer], path: str):
        if not os.path.exists(path):
            interaction.response.send_message(f"File {path} could not be found.", ephemeral=True)
            return

        server.addMission(path)
        name = os.path.basename(path)[:-4]
        await interaction.response.send_message(f'Mission "{utils.escape_string(name)}" added.', ephemeral=True)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED] or \
                not await utils.yn_question(interaction, 'Do you want to load this mission?'):
            return
        for idx, mission in enumerate(server.settings['missionList']):
            if mission == path:
                tmp = await interaction.followup.send(f'Loading mission {utils.escape_string(name)} ...',
                                                      ephemeral=True)
                await server.loadMission(idx + 1)
                await self.bot.audit("loaded mission", server=server, user=interaction.user)
                await tmp.delete()
                await interaction.followup.send(f'Mission {utils.escape_string(name)} loaded.', ephemeral=True)
                break

    @mission.command(description='Deletes a mission from the list')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def delete(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer],
                     mission_id: int):
        filename = server.settings['missionList'][mission_id]
        if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and \
                filename == server.current_mission.filename:
            await interaction.response.send_message("You can't delete the (only) running mission.", ephemeral=True)
            return
        name = filename[:-4]

        if await utils.yn_question(interaction, f'Delete mission "{name}" from the mission list?'):
            server.deleteMission(mission_id + 1)
            await interaction.followup.send(f'Mission "{name}" removed from list.', ephemeral=True)
            if await utils.yn_question(interaction, f'Delete mission "{name}" also from disk?'):
                try:
                    os.remove(filename)
                    await interaction.followup.send(f'Mission "{name}" deleted.', ephemeral=True)
                except FileNotFoundError:
                    await interaction.followup.send(f'Mission "{name}" was already deleted.', ephemeral=True)

    @mission.command(description='Pauses the current running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def pause(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        if server.status == Status.RUNNING:
            await server.current_mission.pause()
            await interaction.response.send_message(f'Server "{server.display_name}" paused.', ephemeral=True)
        else:
            await interaction.response.send_message(f'Server "{server.display_name}" is not running.', ephemeral=True)

    @mission.command(description='Unpauses the running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unpause(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.PAUSED])]):
        if server.status == Status.PAUSED:
            await server.current_mission.unpause()
            await interaction.response.send_message(f'Server "{server.display_name}" unpaused.', ephemeral=True)
        elif server.status == Status.RUNNING:
            await interaction.response.send_message(f'Server "{server.display_name}" is already running.',
                                                    ephemeral=True)
        elif server.status == Status.LOADING:
            interaction.response.send_message(f'Server "{server.display_name}" is still loading... '
                                              f'please wait a bit and try again.', ephemeral=True)

    @mission.command(description='Modify mission with a preset')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def modify(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])]):
        try:
            with open('config/presets.yaml') as infile:
                presets = yaml.safe_load(infile)
        except FileNotFoundError:
            await interaction.response.send_message(
                f'No presets available, please configure them in config/presets.yaml.', ephemeral=True)
            return
        options = [
            discord.SelectOption(label=k)
            for k, v in presets.items()
            if 'hidden' not in v or not v['hidden']
        ]
        if len(options) > 25:
            self.log.warning("You have more than 25 presets created, you can only choose from 25!")

        if server.status in [Status.PAUSED, Status.RUNNING]:
            question = 'Do you want to stop the server to change the mission preset?'
            if server.is_populated():
                result = await utils.populated_question(interaction, question)
            else:
                result = await utils.yn_question(interaction, question)
            if not result:
                await interaction.followup.send('Aborted.', ephemeral=True)
                return

        view = PresetView(options[:25])
        if interaction.response.is_done():
            msg = await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
            msg = await interaction.original_response()
        try:
            if await view.wait():
                return
            elif not view.result:
                await interaction.followup.send('Aborted.', ephemeral=True)
                return
        finally:
            await msg.delete()
        if view.result == 'later':
            server.on_empty = {"command": "preset", "preset": view.result, "user": interaction.user}
            server.restart_pending = True
            await interaction.followup.send(f'Preset will be changed when server is empty.', ephemeral=True)
        else:
            msg = await interaction.followup.send('Changing presets...', ephemeral=True)
            stopped = False
            if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
                stopped = True
                await server.stop()
            await server.modifyMission([value for name, value in presets.items() if name in view.result])
            message = 'Preset changed to: {}.'.format(','.join(view.result))
            if stopped:
                await server.start()
                message += '\nServer restarted.'
            await self.bot.audit("changed preset", server=server, user=interaction.user)
            await msg.delete()
            await interaction.followup.send(message, ephemeral=True)

    @mission.command(description='Save mission preset')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def save_preset(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          name: str):
        miz = MizFile(self.bot, server.current_mission.filename)
        if os.path.exists('config/presets.yaml'):
            with open('config/presets.yaml', encoding='utf-8') as infile:
                presets = yaml.safe_load(infile)
        else:
            presets = dict()
        if name in presets and \
                not await utils.yn_question(interaction, f'Do you want to overwrite the existing preset "{name}"?'):
            await interaction.followup.send('Aborted.', ephemeral=True)
            return
        presets[name] = {
            "start_time": miz.start_time,
            "date": miz.date.strftime('%Y-%m-%d'),
            "temperature": miz.temperature,
            "clouds": miz.clouds,
            "wind": miz.wind,
            "groundTurbulence": miz.groundTurbulence,
            "enable_dust": miz.enable_dust,
            "dust_density": miz.dust_density if miz.enable_dust else 0,
            "qnh": miz.qnh,
            "enable_fog": miz.enable_fog,
            "fog": miz.fog if miz.enable_fog else {"thickness": 0, "visibility": 0},
            "halo": miz.halo,
            "forcedOptions": miz.forcedOptions,
            "miscellaneous": miz.miscellaneous,
            "difficulty": miz.difficulty
        }
        with open(f'config/presets.yaml', 'w', encoding='utf-8') as outfile:
            yaml.safe_dump(presets, outfile)
        if interaction.response.is_done():
            await interaction.followup.send(f'Preset "{name}" added.')
        else:
            await interaction.response.send_message(f'Preset "{name}" added.')

    # New command group "/player"
    player = Group(name="player", description="Commands to manage DCS players")

    @player.command(description='Lists the current players on this server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def list(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        timeout = self.bot.locals.get('message_autodelete', 300)
        report = Report(self.bot, self.plugin_name, 'players.json')
        env = await report.render(server=server, sides=utils.get_sides(interaction, server))
        await interaction.response.send_message(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @player.command(description='Kicks a player by name or UCID')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def kick(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        server.kick(player, reason)
        await self.bot.audit(f'kicked player {player.display_name} with reason "{reason}"', user=interaction.user)
        await interaction.response.send_message(f"Player {player.display_name} (ucid={player.ucid}) kicked.",
                                                ephemeral=True)

    @player.command(description='Bans a user by name or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def ban(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                  player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)]):

        class BanModal(Modal):
            reason = TextInput(label="Reason", default="n/a", max_length=80, required=False)
            period = TextInput(label="Time in Days", default=str(30), required=False)
            everywhere = TextInput(label="Ban on all servers", default="y", required=True, min_length=1, max_length=1)

            def __init__(self, server: Server, player: Player):
                super().__init__(title="Ban Details")
                self.server = server
                self.player = player

            async def on_submit(derived, interaction: discord.Interaction):
                if not derived.period.value.isnumeric():
                    raise ValueError("Period must be a number!")
                if derived.everywhere.value.casefold() != 'y':
                    derived.server.ban(derived.player.ucid, derived.reason.value, int(derived.period.value) * 86400)
                    await interaction.response.send_message(
                        f"Player {player.display_name} banned on server {derived.server.display_name} "
                        f"for {derived.period.value} days.")
                else:
                    for server in self.bot.servers.values():
                        server.ban(derived.player.ucid, derived.reason.value, int(derived.period.value) * 86400)
                    await interaction.response.send_message(f"Player {player.display_name} banned on all servers "
                                                            f"for {derived.period.value} days.")

        await interaction.response.send_modal(BanModal(server, player))

    @player.command(description='Unbans a user by name or ucid')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(ucid="player")
    async def unban(self, interaction: discord.Interaction, ucid: str):
        for server in self.bot.servers.values():
            server.unban(ucid)
        await interaction.response.send_message(f"Player with UCID {ucid} unbanned on all servers.")

    @player.command(description='Moves a player to spectators')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def spec(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        server.move_to_spectators(player)
        if reason:
            player.sendChatMessage(f"You have been moved to spectators. Reason: {reason}",
                                   interaction.user.display_name)
        await self.bot.audit(f'moved player {player.name} to spectators with reason "{reason}".', user=interaction.user)
        await interaction.response.send_message(f'User "{player.name}" moved to spectators.', ephemeral=True)

    @player.command(description='List of AFK players')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def afk(self, interaction: discord.Interaction,
                  server: Optional[app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]],
                  minutes: Optional[int] = 10):
        afk: list[Player] = list()
        for s in self.bot.servers.values():
            if server and s != server:
                continue
            for ucid, dt in s.afk.items():
                player = s.get_player(ucid=ucid, active=True)
                if not player:
                    continue
                if (datetime.now() - dt).total_seconds() > minutes * 60:
                    afk.append(player)

        if len(afk):
            title = 'AFK Players'
            if server:
                title += f' on {server.name}'
            embed = discord.Embed(title=title, color=discord.Color.blue())
            embed.description = f'These players are AFK for more than {minutes} minutes:'
            for player in sorted(afk, key=lambda x: x.server.name):
                embed.add_field(name='Name', value=player.display_name)
                embed.add_field(name='Time',
                                value=utils.format_time(int((datetime.now() -
                                                             player.server.afk[player.ucid]).total_seconds())))
                if server:
                    embed.add_field(name='_ _', value='_ _')
                else:
                    embed.add_field(name='Server', value=player.server.display_name)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(f"No player is AFK for more than {minutes} minutes.", ephemeral=True)

    @player.command(description='Sends a popup to a player')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                    message: str, time: Optional[Range[int, 1, 30]] = -1):
        player.sendPopupMessage(message, time, interaction.user.display_name)
        await interaction.response.send_message('Message sent.')

    @player.command(description='Sends a chat message to a player')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)], message: str):
        player.sendChatMessage(message, interaction.user.display_name)
        await interaction.response.send_message('Message sent.')

    @tasks.loop(minutes=5.0)
    async def update_channel_name(self):
        for server_name, server in self.bot.servers.items():
            if server.status == Status.UNREGISTERED:
                continue
            channel = await self.bot.fetch_channel(int(server.locals['channels'][Channel.STATUS.value]))
            # name changes of the status channel will only happen with the correct permission
            if channel.permissions_for(self.bot.member).manage_channels:
                name = channel.name
                # if the server owner leaves, the server is shut down
                if server.status in [Status.STOPPED, Status.SHUTDOWN, Status.LOADING]:
                    if name.find('［') == -1:
                        name = name + '［-］'
                    else:
                        name = re.sub('［.*］', f'［-］', name)
                else:
                    players = server.get_active_players()
                    current = len(players) + 1
                    max_players = server.settings.get('maxPlayers') or 0
                    if name.find('［') == -1:
                        name = name + f'［{current}／{max_players}］'
                    else:
                        name = re.sub('［.*］', f'［{current}／{max_players}］', name)
                try:
                    if name != channel.name:
                        await channel.edit(name=name)
                except Exception as ex:
                    self.log.debug("Exception in update_channel_name(): " + str(ex))

    @tasks.loop(minutes=1.0)
    async def afk_check(self):
        try:
            for server in self.bot.servers.values():
                max_time = server.locals.get('afk_time', -1)
                if max_time == -1:
                    continue
                for ucid, dt in server.afk.items():
                    player = server.get_player(ucid=ucid, active=True)
                    if player and (datetime.now() - dt).total_seconds() > max_time:
                        msg = self.get_config(server).get(
                            'message_afk', '{player.name}, you have been kicked for being AFK for '
                                           'more than {time}.'.format(player=player, time=utils.format_time(max_time)))
                        server.kick(player, msg)
        except Exception as ex:
            self.log.exception(ex)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain miz attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.miz'):
            return
        # only DCS Admin role is allowed to upload missions
        if not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        # check if the upload happens in the servers admin channel (if provided)
        server: Server = await self.bot.get_server(message)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            if self.bot.locals.get('admin_channel') and int(self.bot.locals['admin_channel']) != message.channel.id:
                return
            try:
                server = await utils.server_selection(self.bus, ctx, title="Where do you want to upload this mission to?")
                if not server:
                    await ctx.send('Aborted.')
                    return
            except Exception:
                traceback.print_exc()
                return
        att = message.attachments[0]
        try:
            rc = await server.uploadMission(att.filename, att.url)
            if rc == UploadStatus.FILE_IN_USE:
                if not await utils.yn_question(message.interaction,
                                               'A mission is currently active.\n'
                                               'Do you want me to stop the DCS-server to replace it?'):
                    await message.channel.send('Upload aborted.')
                    return
            elif rc == UploadStatus.FILE_EXISTS:
                if not await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?'):
                    await message.channel.send('Upload aborted.')
                    return
            if rc != UploadStatus.OK:
                await server.uploadMission(att.filename, att.url, force=True)

            filename = os.path.join(await server.get_missions_dir(), att.filename)
            name = os.path.basename(att.filename)[:-4]
            await message.channel.send(f'Mission "{name}" uploaded to server {server.name}.')
            await self.bot.audit(f'uploaded mission "{name}"', server=server, user=message.author)

            if server.status != Status.SHUTDOWN and server.current_mission.filename != filename and \
                    await utils.yn_question(ctx, 'Do you want to load this mission?'):
                data = await server.sendtoDCSSync({"command": "listMissions"})
                missions = data['missionList']
                for idx, mission in enumerate(missions):
                    if os.path.normpath(mission) == os.path.normpath(filename):
                        tmp = await message.channel.send('Loading mission {} ...'.format(utils.escape_string(name)))
                        await server.loadMission(idx + 1)
                        await self.bot.audit("loaded mission", server=server, user=message.author)
                        await tmp.delete()
                        await message.channel.send(f'Mission {name} loaded.')
                        break
        except Exception:
            traceback.print_exc()
        finally:
            await message.delete()

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        ucid = self.bot.get_ucid_by_member(member)
        if ucid:
            for server in self.bot.servers.values():
                server.ban(ucid, self.bot.locals.get('message_ban', 'User has been banned on Discord.'), 9999*86400)


async def setup(bot: DCSServerBot):
    if 'gamemaster' not in bot.plugins:
        raise PluginRequiredError('gamemaster')
    await bot.add_cog(Mission(bot, MissionEventListener))
