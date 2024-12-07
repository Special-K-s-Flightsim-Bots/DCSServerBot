import asyncio
import discord
import os
import psycopg

from contextlib import suppress
from core import Plugin, PluginRequiredError, utils, Status, Server, Coalition, Channel, TEventListener, Group, Node, \
    Instance, DEFAULT_TAG
from datetime import datetime, timedelta, timezone
from discord import app_commands
from discord.ext import tasks
from discord.ui import Modal, TextInput
from functools import partial
from pathlib import Path
from services.bot import DCSServerBot
from typing import Type, Optional, Literal, Union
from zoneinfo import ZoneInfo

from .listener import SchedulerListener
from .views import ConfigView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()

    async def cog_unload(self):
        self.check_state.cancel()
        await super().cog_unload()

    def read_locals(self) -> dict:
        config = super().read_locals()
        if not config:
            config = {self.node.name: {}}
            for instance in self.bus.node.instances:
                config[self.node.name][instance.name] = {}
            os.makedirs(os.path.join(self.node.config_dir, 'plugins'), exist_ok=True)
            with open(os.path.join(self.node.config_dir, 'plugins', 'scheduler.yaml'), mode='w',
                      encoding='utf-8') as outfile:
                yaml.dump(config, outfile)
        return config

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if new_version == '3.1':
            def _change_instance(instance: dict):
                if 'restart' in instance:
                    instance['action'] = instance.pop('restart')
                    if isinstance(instance['action'], list):
                        for action in instance['action']:
                            if action['method'] == 'restart_with_shutdown':
                                action['method'] = 'restart'
                                action['shutdown'] = True
                    else:
                        if instance['action']['method'] == 'restart_with_shutdown':
                            instance['action']['method'] = 'restart'
                            instance['action']['shutdown'] = True

            config = os.path.join(self.node.config_dir, 'plugins', f'{self.plugin_name}.yaml')
            data = yaml.load(Path(config).read_text(encoding='utf-8'))
            if self.node.name in data.keys():
                for name, node in data.items():
                    if name == DEFAULT_TAG:
                        _change_instance(node)
                        continue
                    for instance in node.values():
                        _change_instance(instance)
            else:
                for instance in data.values():
                    _change_instance(instance)
            with open(config, mode='w', encoding='utf-8') as outfile:
                yaml.dump(data, outfile)

    @staticmethod
    async def check_server_state(server: Server, config: dict) -> Status:
        if 'schedule' in config and not server.maintenance:
            warn_times: list[int] = Scheduler.get_warn_times(config) if server.is_populated() else [0]
            restart_in: int = max(warn_times)
            now: datetime = datetime.now()
            tz = now.astimezone().tzinfo
            now = now.replace(tzinfo=tz)
            weekday = (now + timedelta(seconds=restart_in)).weekday()
            for period, daystate in config['schedule'].items():  # type: str, str
                if period == 'timezone':
                    tz = ZoneInfo(daystate)
                    continue
                if len(daystate) != 7:
                    server.log.error(f"Error in scheduler.yaml: {daystate} has to be 7 characters long!")
                state = daystate[weekday]
                # check, if the server should be running
                if (utils.is_in_timeframe(now, period, tz) and state.upper() == 'Y' and
                        server.status == Status.SHUTDOWN):
                    return Status.RUNNING
                elif (utils.is_in_timeframe(now, period, tz) and state.upper() == 'P' and
                      server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not server.is_populated()):
                    return Status.SHUTDOWN
                elif (utils.is_in_timeframe(now + timedelta(seconds=restart_in), period, tz) and
                      state.upper() == 'N' and server.status == Status.RUNNING):
                    return Status.SHUTDOWN
                elif (utils.is_in_timeframe(now, period, tz) and state.upper() == 'N' and
                      server.status in [Status.PAUSED, Status.STOPPED]):
                    return Status.SHUTDOWN
        return server.status

    async def launch_dcs(self, server: Server, member: Optional[discord.Member] = None,
                         modify_mission: Optional[bool] = True, ignore_exception: Optional[bool] = False):
        self.log.info(f'  => DCS server "{server.name}" starting up ...')
        try:
            await server.startup(modify_mission=modify_mission)
            if not member:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{self.plugin_name.title()}.')
                await self.bot.audit(f"{self.plugin_name.title()} started DCS server", server=server)
            else:
                self.log.info(f'  => DCS server "{server.name}" started by '
                              f'{member.display_name}.')
                await self.bot.audit(f"started DCS server", user=member, server=server)
        except (TimeoutError, asyncio.TimeoutError):
            if server.status == Status.SHUTDOWN:
                self.log.warning(f'  => DCS server "{server.name}" was closed / crashed while launching!')
            else:
                self.log.warning(f'  => DCS server "{server.name}" timeout while launching.')
            if not ignore_exception:
                raise

    @staticmethod
    def get_warn_times(config: dict) -> list[int]:
        times = config.get('warn', {}).get('times', [0])
        if isinstance(times, list):
            return sorted(times, reverse=True)
        elif isinstance(times, dict):
            return sorted(times.keys(), reverse=True)

    async def warn_users(self, server: Server, config: dict, what: str, max_warn_time: Optional[int] = None):
        if 'warn' not in config:
            return
        times: Union[list, dict] = config.get('warn', {}).get('times', [0])
        if isinstance(times, list):
            warn_times = sorted(times, reverse=True)
            warn_text = config['warn'].get('text', '!!! {item} will {what} in {when} !!!')
        elif isinstance(times, dict):
            warn_times = sorted(times.keys(), reverse=True)
        else:
            self.log.warning("Scheduler: warn structure mangled in scheduler.yaml, no user warning!")
            return
        if max_warn_time is None:
            restart_in = max(warn_times)
        else:
            restart_in = max_warn_time
        self.log.debug(f"Scheduler: Restart in {restart_in} seconds...")

        if what == 'restart_with_shutdown':
            what = 'restart'
            item = 'Server'
        elif what == 'shutdown':
            item = 'Server'
        else:
            item = 'Mission'

        async def do_warn(warn_time: int):
            nonlocal warn_text

            sleep_time = restart_in - warn_time
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
            if server.status == Status.RUNNING:
                if isinstance(times, dict):
                    warn_text = times[warn_time]
                await server.sendPopupMessage(Coalition.ALL, warn_text.format(item=item, what=what,
                                                                              when=utils.format_time(warn_time)),
                                              server.locals.get('message_timeout', 10))
                if 'sound' in config['warn']:
                    await server.playSound(Coalition.ALL, utils.format_string(config['warn']['sound'],
                                                                              time=warn_time))
            with suppress(Exception):
                events_channel = self.bot.get_channel(server.channels.get(Channel.EVENTS, -1))
                if events_channel:
                    await events_channel.send(warn_text.format(item=item, what=what,
                                                               when=utils.format_time(warn_time)))
            self.log.debug(f"Scheduler: Warning for {warn_time} fired.")

        tasks = [asyncio.create_task(do_warn(i)) for i in warn_times if i <= restart_in]
        await asyncio.gather(*tasks)
        # sleep until the restart should happen
        await asyncio.sleep(min(restart_in, min(warn_times)))

    async def teardown_dcs(self, server: Server, member: Optional[discord.Member] = None):
        await self.bot.bus.send_to_node({"command": "onShutdown", "server_name": server.name})
        await asyncio.sleep(1)
        await server.shutdown()
        if not member:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {self.plugin_name.title()}.")
            await self.bot.audit(f"{self.plugin_name.title()} shut down DCS server", server=server)
        else:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {member.display_name}.")
            await self.bot.audit(f"shut down DCS server", server=server, user=member)

    async def teardown(self, server: Server, config: dict):
        # if we should not restart populated servers, wait for it to be unpopulated
        populated = server.is_populated()
        if populated and not config.get('populated', True):
            return
        elif not server.restart_pending:
            server.restart_pending = True
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            if restart_in > 0 and populated:
                self.log.info(f"  => DCS server \"{server.name}\" will be shut down "
                              f"by {self.plugin_name.title()} in {restart_in} seconds ...")
                await self.bot.audit(
                    f"{self.plugin_name.title()} will shut down DCS server in {utils.format_time(restart_in)}",
                    server=server)
                await self.warn_users(server, config, 'shutdown')
            # if the shutdown has been cancelled due to maintenance mode
            if not server.restart_pending:
                return
            await self.teardown_dcs(server)
            server.restart_pending = False

    async def restart_mission(self, server: Server, config: dict, rconf: dict, max_warn_time: int):
        # a restart is already pending, nothing more to do
        if server.restart_pending:
            return
        self.log.debug(f"Scheduler: restart_mission(server={server.name}, method={rconf['method']}) triggered.")
        method = rconf['method']
        # shall we do something at mission end only?
        if rconf.get('mission_end', False):
            self.log.debug(f"Scheduler: setting mission_end trigger.")
            server.on_mission_end = {'command': method}
            server.restart_pending = True
            return
        # check if the server is populated
        if server.is_populated():
            self.log.debug(f"Scheduler: Server is populated.")
            # max_mission_time overwrites the populated false
            if not rconf.get('populated', True) and not rconf.get('max_mission_time'):
                if not server.on_empty:
                    server.on_empty = {'command': method}
                    self.log.debug("Scheduler: Setting on_empty trigger.")
                server.restart_pending = True
                return
            server.restart_pending = True
            self.log.debug("Scheduler: Warning users ...")
            if max_warn_time < 60:
                max_warn_time = 60
            await self.warn_users(server, config, method, max_warn_time)
            # in the unlikely event that we did restart already in the meantime while warning users or
            # if the restart has been cancelled due to maintenance mode
            if not server.restart_pending:
                return
            else:
                server.on_empty.clear()
        else:
            server.restart_pending = True

        try:
            if method == 'shutdown' or rconf.get('shutdown', False):
                self.log.debug(f"Scheduler: Shutting down DCS Server {server.name} ...")
                await self.teardown_dcs(server)
            if method == 'restart':
                try:
                    modify_mission = rconf.get('run_extensions', True)
                    if server.status == Status.SHUTDOWN:
                        self.log.debug(f"Scheduler: Starting DCS Server {server.name}")
                        await asyncio.sleep(self.get_config(server).get('startup_delay', 0))
                        await self.launch_dcs(server, modify_mission=modify_mission)
                    else:
                        self.log.debug(f"Scheduler: Restarting mission on server {server.name} ...")
                        await server.restart(modify_mission=modify_mission)
                    await self.bot.audit(f"{self.plugin_name.title()} restarted mission "
                                         f"{server.current_mission.display_name}", server=server)
                except (TimeoutError, asyncio.TimeoutError):
                    await self.bot.audit(f"{self.plugin_name.title()}: Timeout while starting server",
                                         server=server)
            elif method == 'rotate':
                try:
                    self.log.debug(f"Scheduler: Rotating mission on server {server.name} ...")
                    modify_mission = rconf.get('run_extensions', True)
                    if server.status == Status.SHUTDOWN:
                        await server.setStartIndex(server.settings['listStartIndex'] + 1)
                        self.log.debug(f"Scheduler: Starting DCS Server {server.name} ...")
                        await self.launch_dcs(server, modify_mission=modify_mission)
                    else:
                        await server.loadNextMission(modify_mission=modify_mission)
                    await self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                         f"{server.current_mission.display_name}", server=server)
                except (TimeoutError, asyncio.TimeoutError):
                    await self.bot.audit(f"{self.plugin_name.title()}: Timeout while starting server",
                                         server=server)
            elif method == 'load':
                try:
                    mission_id = rconf.get('mission_id')
                    if not mission_id:
                        filename = rconf.get('mission_file')
                    else:
                        filename = None
                    if not mission_id and not filename:
                        self.log.error("You need to provide either mission_id or filename to your load configuration!")
                        return
                    filename = utils.format_string(filename, instance=server.instance, server=server)
                    if not mission_id:
                        for idx, mission in enumerate(server.settings['missionList']):
                            if os.path.basename(mission).lower() == filename.lower():
                                mission_id = idx + 1
                                break
                        else:
                            self.log.error(f"No mission with name {filename} found in your serverSettings.lua!")
                            return
                    self.log.debug(f"Scheduler: Loading mission {mission_id} on server {server.name} ...")
                    modify_mission = rconf.get('run_extensions', True)
                    if server.status == Status.SHUTDOWN:
                        await server.setStartIndex(mission_id)
                        self.log.debug(f"Scheduler: Starting DCS Server {server.name} ...")
                        await self.launch_dcs(server, modify_mission=modify_mission)
                    else:
                        if not await server.loadMission(mission=mission_id, modify_mission=modify_mission):
                            self.log.error(f"Mission {mission_id} not loaded on server {server.name}")
                            return
                    await self.bot.audit(f"{self.plugin_name.title()} loaded mission "
                                         f"{server.current_mission.display_name}", server=server)
                except (TimeoutError, asyncio.TimeoutError):
                    await self.bot.audit(f"{self.plugin_name.title()}: Timeout while starting server",
                                         server=server)

        except Exception as ex:
            self.log.error(f"Error with method {method} on server {server.name}: {ex}", exc_info=True)
            server.restart_pending = False

    async def check_mission_state(self, server: Server, config: dict):
        def check_action(rconf: dict):
            # calculate the time when the mission has to restart
            if server.is_populated():
                warn_times = Scheduler.get_warn_times(config)
            else:
                warn_times = [0]
            # we check the warn times from small to large, to find the first that fits
            for warn_time in sorted(warn_times):
                if 'local_times' in rconf:
                    restart_time = datetime.now() + timedelta(seconds=warn_time)
                    for t in rconf['local_times']:
                        if utils.is_in_timeframe(restart_time, t):
                            asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                            return
                elif 'utc_times' in rconf:
                    restart_time = datetime.now(tz=timezone.utc) + timedelta(seconds=warn_time)
                    for t in rconf['utc_times']:
                        if utils.is_in_timeframe(restart_time, t, tz=timezone.utc):
                            asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                            return
                elif 'mission_time' in rconf:
                    # check the maximum time the mission is allowed to run
                    if 'max_mission_time' in rconf and server.is_populated() and not rconf.get('populated', True):
                        max_mission_time = rconf['max_mission_time'] * 60
                    else:
                        max_mission_time = rconf['mission_time'] * 60
                    if server.current_mission and (server.current_mission.mission_time + warn_time) >= max_mission_time:
                        restart_in = int(max_mission_time - server.current_mission.mission_time)
                        if restart_in < 0:
                            restart_in = 0
                        asyncio.create_task(self.restart_mission(server, config, rconf, restart_in))
                        return
                elif 'real_time' in rconf:
                    real_time = rconf['real_time'] * 60
                    if server.current_mission and (server.current_mission.real_time + warn_time) >= real_time:
                        restart_in = int(real_time - server.current_mission.real_time)
                        if restart_in < 0:
                            restart_in = 0
                        if rconf['method'] == 'restart':
                            rconf['shutdown'] = True
                        asyncio.create_task(self.restart_mission(server, config, rconf, restart_in))
                        return
                elif 'idle_time' in rconf and server.idle_since:
                    if (datetime.now(tz=timezone.utc) - server.idle_since).total_seconds() / 60 >= rconf['idle_time']:
                        asyncio.create_task(self.restart_mission(server, config, rconf, 0))

        if 'action' in config and not server.restart_pending:
            if isinstance(config['action'], list):
                for r in config['action']:
                    check_action(r)
            else:
                check_action(config['action'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        next_startup = 0
        startup_delay = self.get_config().get('startup_delay', 10)
        for server_name, server in self.bot.servers.items():
            # only care about servers that are not in the startup phase
            if server.status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTTING_DOWN] or server.maintenance:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    target_state = await self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server.status == Status.SHUTDOWN:
                        server.status = Status.LOADING
                        if config.get('startup') and config['startup'].get('mission_id'):
                            await server.setStartIndex(config['startup']['mission_id'])
                        self.loop.call_later(
                            delay=next_startup, callback=partial(asyncio.create_task,
                                                                 self.launch_dcs(server, ignore_exception=True)))
                        next_startup += startup_delay
                    elif target_state == Status.SHUTDOWN and server.status in [
                        Status.STOPPED, Status.RUNNING, Status.PAUSED
                    ]:
                        # noinspection PyAsyncCall
                        asyncio.create_task(self.teardown(server, config))
                    elif server.status in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                except Exception as ex:
                    self.log.exception(ex)

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        while True:
            if all(server.status != Status.UNREGISTERED for server in self.bus.servers.values()):
                break
            await asyncio.sleep(1)

    group = Group(name="server", description="Commands to manage a DCS server")

    @group.command(name='list', description='List of all registered servers')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction):
        if not self.bot.servers:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("No servers registered.", ephemeral=True)
            return
        embed = discord.Embed(title=f"All Servers", color=discord.Color.blue())
        names = []
        status = []
        players = []
        for server in self.bot.servers.values():
            names.append(server.display_name)
            status.append(server.status.name.title())
            if server.status in [Status.RUNNING, Status.PAUSED]:
                players.append(f"{len(server.players) + 1}/{server.settings.get('maxPlayers', 0)}")
            else:
                players.append('-')
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Players', value='\n'.join(players))
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, ephemeral=utils.get_ephemeral(interaction))

    @group.command(description='Launches a DCS server')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def startup(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.SHUTDOWN])],
                      maintenance: Optional[bool] = False, run_extensions: Optional[bool] = True,
                      mission_id: Optional[int] = None):
        if server.status == Status.STOPPED:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is stopped.\n"
                                                    f"Please use /server start instead.", ephemeral=True)
        elif server.status == Status.LOADING:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is loading.\n"
                                                    f"Please wait or use /server shutdown force instead.",
                                                    ephemeral=True)
        elif server.status == Status.SHUTTING_DOWN:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is shutting down.\n"
                                                    f"Please wait or use /server shutdown force instead.",
                                                    ephemeral=True)
        elif server.status == Status.SHUTDOWN:
            ephemeral = utils.get_ephemeral(interaction)
            # noinspection PyUnresolvedReferences
            if not interaction.response.is_done():
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
            msg = await interaction.followup.send(f"Starting DCS server \"{server.display_name}\", please wait ...",
                                                  ephemeral=ephemeral)
            # set maintenance flag. default is true to prevent auto stops of this server if configured to be stopped.
            server.maintenance = maintenance
            try:
                if mission_id is not None:
                    await server.setStartIndex(mission_id + 1)
                await self.launch_dcs(server, interaction.user, modify_mission=run_extensions)
                if maintenance:
                    embed = utils.create_warning_embed(
                        title=f"DCS server \"{server.display_name}\" started.",
                        text="Server is in maintenance mode!\n"
                             "Use {} to reset maintenance mode.".format(
                            (await utils.get_command(self.bot, group='scheduler', name='clear')).mention
                        )
                    )
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await interaction.followup.send(f"DCS server \"{server.display_name}\" started.",
                                                    ephemeral=ephemeral)
            except (TimeoutError, asyncio.TimeoutError):
                if server.status == Status.SHUTDOWN:
                    embed = utils.create_warning_embed(
                        title=f"DCS server \"{server.display_name}\" crashed!",
                        text="The server has crashed while starting.\n"
                             "You should look for a cause in its dcs.log.")
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    embed = utils.create_warning_embed(
                        title=f"Timeout while launching \"{server.display_name}\"!",
                        text="The server might be running anyway.\n"
                             "Check with {}.".format(
                            (await utils.get_command(self.bot, group='server', name='list')).mention
                        )
                    )
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            except Exception as ex:
                self.log.error(ex)
                await interaction.followup.send(ex, ephemeral=ephemeral)
            finally:
                try:
                    await msg.delete()
                except discord.NotFound:
                    pass
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"DCS server \"{server.display_name}\" is started already.",
                                                    ephemeral=True)

    @group.command(description='Shuts a DCS server down')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def shutdown(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[
                               Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.LOADING, Status.SHUTTING_DOWN
                           ])], force: Optional[bool] = False, maintenance: Optional[bool] = True):
        async def do_shutdown(*, force: bool = False):
            await interaction.followup.send(f"Shutting down DCS server \"{server.display_name}\", please wait ...",
                                            ephemeral=ephemeral)
            # set maintenance flag to prevent auto-starts of this server
            server.maintenance = maintenance
            if force:
                await server.shutdown(force=True)
            else:
                await self.teardown_dcs(server, interaction.user)
            if maintenance:
                embed = utils.create_warning_embed(
                    title=f"DCS server \"{server.display_name}\" shut down.",
                    text="Server is in maintenance mode!\n"
                         "Use {} to reset maintenance mode.".format(
                        (await utils.get_command(self.bot, group='scheduler', name='clear')).mention
                    )
                )
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            else:
                await interaction.followup.send(f"DCS server \"{server.display_name}\" shut down.", ephemeral=ephemeral)

        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.status in [Status.UNREGISTERED, Status.LOADING, Status.SHUTTING_DOWN]:
            if force or await utils.yn_question(interaction, f"Server is in state {server.status.name}.\n"
                                                             f"Do you want to force a shutdown?", ephemeral=ephemeral):
                await do_shutdown(force=True)
            else:
                return
        elif server.status != Status.SHUTDOWN:
            if not force:
                question = f"Do you want to shut down DCS server \"{server.display_name}\"?"
                if server.is_populated():
                    result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
                else:
                    result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
                if not result:
                    await interaction.followup.send('Aborted.', ephemeral=ephemeral)
                    return
                elif result == 'later':
                    server.on_empty = {"command": "shutdown", "user": interaction.user}
                    server.restart_pending = True
                    await interaction.followup.send('Shutdown postponed when server is empty.', ephemeral=ephemeral)
                    return
            await do_shutdown(force=force)
        else:
            await interaction.followup.send(f"DCS server \"{server.display_name}\" is already shut down.",
                                            ephemeral=ephemeral)

    @group.command(description='Restarts a DCS server')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def restart(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[
                              Status.RUNNING, Status.PAUSED, Status.STOPPED
                          ])],
                      force: Optional[bool] = False, run_extensions: Optional[bool] = True,
                      mission_id: Optional[int] = None):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        question = f"Do you want to restart DCS server \"{server.display_name}\"?"
        if server.is_populated():
            question += "\nPeople are flying on this server atm."
        if not await utils.yn_question(interaction, question, ephemeral=ephemeral):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return

        msg = await interaction.followup.send(f"Shutting down DCS server \"{server.display_name}\" ...",
                                              ephemeral=ephemeral)
        try:
            maintenance = server.maintenance
            server.maintenance = True
            if force:
                await server.shutdown(force=True)
            else:
                await self.teardown_dcs(server, interaction.user)
            await msg.edit(content=f"Server \"{server.display_name}\" shut down. Restarting ...")
            server.maintenance = maintenance
            if mission_id is not None:
                await server.setStartIndex(mission_id + 1)
            await self.launch_dcs(server, interaction.user, modify_mission=run_extensions)
            await msg.edit(content=f"DCS server \"{server.display_name}\" restarted.")
        except (TimeoutError, asyncio.TimeoutError):
            if server.status == Status.SHUTDOWN:
                embed = utils.create_warning_embed(
                    title=f"DCS server \"{server.display_name}\" crashed!",
                    text="The server has crashed while restarting.\n"
                         "You should look for a cause in its dcs.log.")
                await msg.edit(content="", embed=embed)
            else:
                embed = utils.create_warning_embed(
                    title=f"Timeout while restarting \"{server.display_name}\"!",
                    text="The server might be running anyway.\n"
                         "Check with {}.".format(
                        (await utils.get_command(self.bot, group='server', name='list')).mention
                    )
                )
                await msg.edit(content="", embed=embed)
        except Exception as ex:
            self.log.error(ex)
            await msg.edit(content=ex)

    @group.command(description='Starts a stopped DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def start(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.STOPPED])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.STOPPED:
            # noinspection PyUnresolvedReferences
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)
            try:
                if not await server.start():
                    embed = utils.create_warning_embed(
                        title=f"Error while starting server \"{server.display_name}\"!",
                        text="Please check manually, if the server has started.\n"
                             "If not, check the dcs.log for errors.")
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                    return
            except (TimeoutError, asyncio.TimeoutError):
                embed = utils.create_warning_embed(
                    title=f"Timeout while starting server \"{server.display_name}\"!",
                    text="Please check manually, if the server has started.\n"
                         "If not, check the dcs.log for errors.")
                await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                return
            await interaction.followup.send(f"Server {server.display_name} started.", ephemeral=ephemeral)
            await self.bot.audit('started the server', server=server, user=interaction.user)
        elif server.status == Status.SHUTDOWN:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"Server {server.display_name} is shut down. Use /server startup to start it up.", ephemeral=ephemeral)
        elif server.status in [Status.RUNNING, Status.PAUSED]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is already started.",
                                                    ephemeral=ephemeral)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f"Server {server.display_name} is still {server.status.name}, please wait ...", ephemeral=ephemeral)

    @group.command(description='Stops a running DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def stop(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.is_populated() and \
                not await utils.yn_question(interaction, "People are flying on this server atm.\n"
                                                         "Do you really want to stop it?", ephemeral=ephemeral):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return
        msg = None
        try:
            msg = await interaction.followup.send(f"Stopping server {server.name} ...", ephemeral=ephemeral)
            await server.stop()
        except (TimeoutError, asyncio.TimeoutError):
            embed = utils.create_warning_embed(
                title=f"Timeout while stopping server \"{server.display_name}\"!",
                text="Please check manually, if the server has stopped.\n"
                     "If not, check the dcs.log for errors.")
            await interaction.followup.send(embed=embed, ephemeral=ephemeral)
            return
        finally:
            try:
                if msg:
                    await msg.delete()
            except discord.NotFound:
                pass
        await interaction.followup.send(f"Server {server.display_name} stopped.", ephemeral=ephemeral)
        await self.bot.audit('stopped the server', server=server, user=interaction.user)

    @group.command(description='Change the password of a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def password(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       coalition: Optional[Literal['red', 'blue']] = None):
        class PasswordModal(Modal, title="Enter Password"):
            password = TextInput(label="New Password" + (f" for coalition {coalition}:" if coalition else ":"),
                                 style=discord.TextStyle.short, required=False)

            async def on_submit(derived, interaction: discord.Interaction):
                ephemeral = utils.get_ephemeral(interaction)
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
                if coalition:
                    await server.setCoalitionPassword(Coalition(coalition), derived.password.value)
                    await self.bot.audit(f"changed password for coalition {coalition}",
                                         user=interaction.user, server=server)
                else:
                    await server.setPassword(derived.password.value)
                    await self.bot.audit(f"changed password", user=interaction.user, server=server)
                await interaction.followup.send("Password changed.", ephemeral=ephemeral)

        if server.status in [Status.PAUSED, Status.RUNNING]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f'Server "{server.display_name}" has to be stopped or shut down '
                                                    f'to change the password.', ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        await interaction.response.send_modal(PasswordModal())

    @group.command(description='Change the configuration of a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def config(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer]):
        if server.status in [Status.RUNNING, Status.PAUSED]:
            if await utils.yn_question(interaction, question='Server has to be stopped to change its configuration.\n'
                                                             'Do you want to stop it?'):
                await server.stop()
            else:
                await interaction.followup.send('Aborted.')
                return

        view = ConfigView(self.bot, server)
        embed = discord.Embed(title=f'Please edit the configuration of server\n"{server.display_name}"')
        # noinspection PyUnresolvedReferences
        if interaction.response.is_done():
            msg = await interaction.followup.send(embed=embed, view=view)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
        try:
            if not await view.wait() and not view.cancelled:
                if view.channel_update:
                    if not await view.wait() and not view.cancelled and view.channel_update:
                        config_file = os.path.join(self.node.config_dir, 'servers.yaml')
                        with open(config_file, mode='r', encoding='utf-8') as infile:
                            config = yaml.load(infile)
                        config[server.name] = {
                            "channels": {
                                "status": server.locals.get('channels', {}).get('status', -1),
                                "chat": server.locals.get('channels', {}).get('chat', -1)
                            }
                        }
                        if not self.bot.locals.get('channels', {}).get('admin'):
                            config[server.name]['channels']['admin'] = server.locals.get('channels', {}).get('admin',
                                                                                                             -1)
                        with open(config_file, mode='w', encoding='utf-8') as outfile:
                            yaml.dump(config, outfile)
                        await server.reload()
                await interaction.followup.send(f'Server configuration for server "{server.display_name}" updated.')
        finally:
            try:
                await msg.delete()
            except discord.NotFound:
                pass

    @group.command(name='rename', description='Rename a DCS server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def _rename(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.SHUTDOWN])], new_name: str):
        if server.status not in [Status.SHUTDOWN]:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.name} has to be shut down for renaming.",
                                                    ephemeral=True)
            return
        if server.name == new_name:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("The server has this name already. Aborted.", ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        old_name = server.name
        await server.rename(new_name, True)
        await self.bot.audit(f"renamed from {old_name}", server=server, user=interaction.user)
        await interaction.followup.send(f"Server {old_name} renamed to {new_name}.", ephemeral=ephemeral)

    @group.command(name="migrate", description="Migrate a server from one instance to another")
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    async def _migrate(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer],
                       node: app_commands.Transform[Node, utils.NodeTransformer],
                       instance: app_commands.Transform[Instance, utils.InstanceTransformer]):
        if server.instance == instance:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(
                f'Server "{server.name}" is already bound to instance "{instance.name}".', ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        if instance.server:
            if not await utils.yn_question(interaction, f"Instance {instance.name} is not empty.\n"
                                                        f"Do you want to unlink (and probably shutdown) server "
                                                        f"{instance.server.name} first?", ephemeral=ephemeral):
                await interaction.followup.send("Aborted.", ephemeral=ephemeral)
        maintenance = server.maintenance
        running = False
        server.maintenance = True
        try:
            if server.status != Status.SHUTDOWN:
                if not await utils.yn_question(interaction,
                                               f"Do you want to shut down server \"{server.name}\" for migration?",
                                               ephemeral=ephemeral):
                    await interaction.followup.send("Aborted", ephemeral=ephemeral)
                running = True
                await server.shutdown()
            # noinspection PyUnresolvedReferences
            if not interaction.response.is_done():
                # noinspection PyUnresolvedReferences
                await interaction.response.defer(ephemeral=ephemeral)
            # prepare server for migration
            await server.persist_settings()
            if instance.server:
                await instance.server.persist_settings()
                if instance.server.status != Status.SHUTDOWN:
                    await instance.server.shutdown()
            await node.migrate_server(server, instance)
            await interaction.followup.send(f"DCS server {server.name} migrated to instance {instance.name}.",
                                            ephemeral=ephemeral)
            await self.bot.audit(f"migrated DCS server to node {node.name} instance {instance.name}",
                                 user=interaction.user, server=server)
            if running:
                msg: discord.Message = await interaction.followup.send("Starting up ...", ephemeral=ephemeral)
                await server.startup()
                if maintenance:
                    await msg.delete()
                    embed = utils.create_warning_embed(
                        title=f"DCS server \"{server.display_name}\" started.",
                        text="Server is in maintenance mode!\n"
                             "Use {} to reset maintenance mode.".format(
                            (await utils.get_command(self.bot, group='scheduler', name='clear')).mention
                        )
                    )
                    await interaction.followup.send(embed=embed, ephemeral=ephemeral)
                else:
                    await msg.edit(content=f"DCS server \"{server.display_name}\" started.")
        finally:
            server.maintenance = maintenance

    @group.command(name="timeleft", description="Time until server / mission restart")
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    @app_commands.rename(_server="server")
    async def timeleft(self, interaction: discord.Interaction,
                       _server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[
                               Status.RUNNING, Status.PAUSED
                           ])
                       ]):
        config = self.get_config(_server).get('action')
        if not config:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("No restart configured for this server.", ephemeral=True)
            return
        elif _server.maintenance:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Server is in maintenance mode, it will not restart.",
                                                    ephemeral=True)
            return
        elif not _server.restart_time:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message("Please try again in a minute.", ephemeral=True)
            return
        # noinspection PyUnresolvedReferences
        restart_in, rconf = self.eventlistener.get_next_restart(_server, config)
        what = rconf['method']
        if what == 'shutdown' or rconf.get('shutdown', False):
            item = f'Server {_server.name}'
        else:
            item = f'The mission on server {_server.name}'
        message = f"{item} will {what}"
        if (any(key in rconf for key in ['local_times', 'utc_times', 'real_time', 'idle_time']) or
                _server.status == Status.RUNNING):
            if _server.restart_time >= datetime.now(tz=timezone.utc):
                message += f" <t:{int(_server.restart_time.timestamp())}:R>"
            else:
                message += " now"
            if not rconf.get('populated', True) and _server.is_populated() and not rconf.get('max_mission_time'):
                message += ", if all players have left."
        else:
            if restart_in:
                message += f" after {utils.format_time(restart_in)}"
            else:
                message += " immediately"
            message += f", if the mission is unpaused again."
        # noinspection PyUnresolvedReferences
        await interaction.response.send_message(message, delete_after=60)

    @group.command(name="cleanup", description="Clear the temp directory")
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def cleanup(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.SHUTDOWN])]):
        ephemeral = utils.get_ephemeral(interaction)
        # noinspection PyUnresolvedReferences
        await interaction.response.defer(ephemeral=ephemeral)
        if server.status != Status.SHUTDOWN:
            if not await utils.yn_question(
                interaction, f"Do you want to shut down server \"{server.display_name}\" for a cleanup?",
                ephemeral=ephemeral):
                return
            await server.shutdown()
        await server.cleanup()
        await interaction.followup.send(f"Server \"{server.display_name}\" cleaned up.", ephemeral=ephemeral)

    # /scheduler commands
    scheduler = Group(name="scheduler", description="Commands to manage the Scheduler")

    @scheduler.command(description='Sets the servers maintenance flag')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def maintenance(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(maintenance=False)]):
        ephemeral = utils.get_ephemeral(interaction)
        if not server.maintenance:
            if (server.restart_pending or server.on_empty or server.on_mission_end) and \
                    not await utils.yn_question(
                        interaction, "Server is configured for a pending restart.\n"
                                     "Setting the maintenance flag will abort this restart.\n"
                                     "Are you sure?", ephemeral=ephemeral):
                await interaction.followup.send("Aborted.", ephemeral=ephemeral)
                return
            server.maintenance = True
            server.restart_pending = False
            server.on_empty.clear()
            server.on_mission_end.clear()
            # noinspection PyUnresolvedReferences
            if interaction.response.is_done():
                await interaction.followup.send(f"Maintenance mode set for server {server.display_name}.",
                                                ephemeral=ephemeral)
            else:
                # noinspection PyUnresolvedReferences
                await interaction.response.send_message(f"Maintenance mode set for server {server.display_name}.",
                                                        ephemeral=ephemeral)
            await self.bot.audit("set maintenance flag", user=interaction.user, server=server)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is already in maintenance mode.",
                                                    ephemeral=ephemeral)

    @scheduler.command(description='Clears the servers maintenance flag')
    @utils.app_has_role('DCS Admin')
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(maintenance=True)]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.maintenance:
            server.maintenance = False
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Maintenance mode cleared for server {server.display_name}.",
                                                    ephemeral=ephemeral)
            await self.bot.audit("cleared maintenance flag", user=interaction.user, server=server)
        else:
            # noinspection PyUnresolvedReferences
            await interaction.response.send_message(f"Server {server.display_name} is not in maintenance mode.",
                                                    ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Scheduler(bot, SchedulerListener))
