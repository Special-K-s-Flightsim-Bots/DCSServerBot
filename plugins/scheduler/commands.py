from __future__ import annotations
import asyncio
import discord
import json
import os
import platform
import random
from copy import deepcopy
from discord import Interaction
from discord.ui import View, Select, Button
from core import Plugin, PluginRequiredError, utils, Status, MizFile, Autoexec, Extension, Server, Coalition, Channel
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from typing import Type, Optional, List, TYPE_CHECKING, cast
from .listener import SchedulerListener

if TYPE_CHECKING:
    from core import DCSServerBot, TEventListener


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()
        self.lastrun = None
        self.schedule_extensions.start()

    async def cog_unload(self):
        self.schedule_extensions.cancel()
        self.check_state.cancel()
        await super().cog_unload()

    def read_locals(self) -> dict:
        # create a base scheduler.json if non exists
        file = 'config/scheduler.json'
        if not os.path.exists(file):
            configs = []
            for _, installation in utils.findDCSInstallations():
                configs.append({"installation": installation})
            cfg = {"configs": configs}
            with open(file, 'w') as f:
                json.dump(cfg, f, indent=2)
            return cfg
        else:
            configs = super().read_locals()
            for cfg in configs['configs']:
                if 'presets' in cfg and isinstance(cfg['presets'], str):
                    with open(os.path.expandvars(cfg['presets'])) as file:
                        cfg['presets'] = json.load(file)
            return configs

    async def install(self):
        await super().install()
        for _, installation in utils.findDCSInstallations():
            if installation in self.bot.config:
                try:
                    cfg = Autoexec(bot=self.bot, installation=installation)
                    if cfg.crash_report_mode is None:
                        self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                        cfg.crash_report_mode = 'silent'
                    elif cfg.crash_report_mode != 'silent':
                        self.log.warning('=> crash_report_mode is NOT "silent" in your autoexec.cfg! The Scheduler '
                                         'will not work properly on DCS crashes, please change it manually to "silent" '
                                         'to avoid that.')
                except Exception as ex:
                    self.log.error(f"  => Error while parsing autoexec.cfg: {ex.__repr__()}")

    def migrate(self, version: str):
        dirty = False
        if version == '1.1' and 'SRS_INSTALLATION' in self.bot.config['DCS']:
            with open('config/scheduler.json') as file:
                old: dict = json.load(file)
            new = deepcopy(old)
            # search the default config or create one
            c = -1
            for i in range(0, len(old['configs'])):
                if 'installation' not in old['configs'][i]:
                    c = i
                    break
            if c == -1:
                new['configs'].append(dict())
                c = len(new['configs']) - 1
            new['configs'][c]['extensions'] = {
                "SRS": {"installation": self.bot.config['DCS']['SRS_INSTALLATION'].replace('%%', '%')}
            }
            # migrate the SRS configuration
            for c in range(0, len(old['configs'])):
                if 'installation' not in old['configs'][c] or \
                        'extensions' not in old['configs'][c] or \
                        'SRS' not in old['configs'][c]['extensions']:
                    continue
                new['configs'][c]['extensions'] = {
                    "SRS": {
                        "config": self.bot.config[old['configs'][c]['installation']]['SRS_CONFIG'].replace('%%', '%')
                    }
                }
                dirty = True
        elif version == '1.2':
            with open('config/scheduler.json') as file:
                old: dict = json.load(file)
            new = deepcopy(old)
            for config in new['configs']:
                if 'extensions' in config and 'Tacview' in config['extensions'] and \
                        'path' in config['extensions']['Tacview']:
                    config['extensions']['Tacview']['tacviewExportPath'] = config['extensions']['Tacview']['path']
                    del config['extensions']['Tacview']['path']
                    dirty = True
        elif version == '1.3':
            with open('config/scheduler.json') as file:
                old: dict = json.load(file)
            new = deepcopy(old)
            for config in new['configs']:
                if 'restart' in config and 'settings' in config['restart']:
                    config['settings'] = deepcopy(config['restart']['settings'])
                    del config['restart']['settings']
                    dirty = True
        else:
            return
        if dirty:
            os.rename('config/scheduler.json', 'config/scheduler.bak')
            with open('config/scheduler.json', 'w') as file:
                json.dump(new, file, indent=2)
                self.log.info('  => config/scheduler.json migrated to new format, please verify!')

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server.installation == element['installation']) or \
                                ('server_name' in element and server.name == element['server_name']):
                            specific = deepcopy(element)
                    else:
                        default = deepcopy(element)
                if default and not specific:
                    self._config[server.name] = default
                elif specific and not default:
                    self._config[server.name] = specific
                elif default and specific:
                    merged = default | specific
                    if 'extensions' in merged and 'extensions' not in specific:
                        del merged['extensions']
                    elif 'extensions' in default and 'extensions' in specific:
                        for ext in (default['extensions'] | specific['extensions']):
                            if ext in default['extensions'] and ext in specific['extensions']:
                                merged['extensions'][ext] = default['extensions'][ext] | specific['extensions'][ext]
                            elif ext in specific['extensions']:
                                merged['extensions'][ext] = specific['extensions'][ext]
                            elif ext in merged['extensions']:
                                del merged['extensions'][ext]
                    self._config[server.name] = merged
            else:
                return None
        return self._config[server.name] if server.name in self._config else None

    @staticmethod
    async def check_server_state(server: Server, config: dict) -> Status:
        if 'schedule' in config and not server.maintenance:
            warn_times: list[int] = Scheduler.get_warn_times(config) if server.is_populated() else [0]
            restart_in: int = max(warn_times)
            now: datetime = datetime.now()
            weekday = (now + timedelta(seconds=restart_in)).weekday()
            for period, daystate in config['schedule'].items():  # type: str, dict
                state = daystate[weekday]
                # check, if the server should be running
                if utils.is_in_timeframe(now, period) and state.upper() == 'Y' and server.status == Status.SHUTDOWN:
                    return Status.RUNNING
                elif utils.is_in_timeframe(now, period) and state.upper() == 'P' and \
                        server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not server.is_populated():
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now + timedelta(seconds=restart_in), period) and state.upper() == 'N' and \
                        server.status == Status.RUNNING:
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now, period) and state.upper() == 'N' and \
                        server.status in [Status.PAUSED, Status.STOPPED]:
                    return Status.SHUTDOWN
        return server.status

    async def init_extensions(self, server: Server, config: dict):
        if 'extensions' not in config:
            return
        for extension in config['extensions']:
            ext: Extension = server.extensions.get(extension)
            if not ext:
                if '.' not in extension:
                    ext = utils.str_to_class('extensions.' + extension)(self.bot, server,
                                                                        config['extensions'][extension])
                else:
                    ext = utils.str_to_class(extension)(self.bot, server, config['extensions'][extension])
                if ext.is_installed():
                    server.extensions[extension] = ext

    async def launch_dcs(self, server: Server, config: dict, member: Optional[discord.Member] = None):
        await self.init_extensions(server, config)
        for ext in sorted(server.extensions):
            await server.extensions[ext].prepare()
            await server.extensions[ext].beforeMissionLoad()
        # change the weather in the mission if provided
        if not server.maintenance and 'settings' in config:
            await self.change_mizfile(server, config)
        self.log.info(f"  => DCS server \"{server.name}\" starting up ...")
        await server.startup()
        if not member:
            self.log.info(f"  => DCS server \"{server.name}\" started by "
                          f"{self.plugin_name.title()}.")
            await self.bot.audit(f"{self.plugin_name.title()} started DCS server", server=server)
        else:
            self.log.info(f"  => DCS server \"{server.name}\" started by "
                          f"{member.display_name}.")
            await self.bot.audit(f"started DCS server", user=member, server=server)

    @staticmethod
    def get_warn_times(config: dict) -> List[int]:
        return sorted(config.get('warn', {}).get('times', [0]), reverse=True)

    async def warn_users(self, server: Server, config: dict, what: str, max_warn_time: Optional[int] = None):
        if 'warn' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max_warn_time or max(warn_times)
            warn_text = config['warn'].get('text', '!!! {item} will {what} in {when} !!!')
            if what == 'restart_with_shutdown':
                what = 'restart'
                item = 'server'
            elif what == 'shutdown':
                item = 'server'
            else:
                item = 'mission'
            while restart_in > 0 and server.status == Status.RUNNING and not server.maintenance:
                for warn_time in warn_times:
                    if warn_time == restart_in:
                        server.sendPopupMessage(Coalition.ALL, warn_text.format(item=item, what=what,
                                                                                when=utils.format_time(warn_time)),
                                                self.bot.config['BOT']['MESSAGE_TIMEOUT'])
                        if 'sound' in config['warn']:
                            server.playSound(Coalition.ALL, config['warn']['sound'])
                        events_channel = server.get_channel(Channel.EVENTS)
                        if events_channel:
                            await events_channel.send(warn_text.format(item=item, what=what,
                                                                       when=utils.format_time(warn_time)))
                await asyncio.sleep(1)
                restart_in -= 1

    async def teardown_dcs(self, server: Server, member: Optional[discord.Member] = None):
        self.bot.sendtoBot({"command": "onShutdown", "server_name": server.name})
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

    @staticmethod
    async def change_mizfile(server: Server, config: dict, presets: Optional[str] = None):
        def apply_preset(value: dict):
            if 'start_time' in value:
                miz.start_time = value['start_time']
            if 'date' in value:
                miz.date = datetime.strptime(value['date'], '%Y-%m-%d')
            if 'temperature' in value:
                miz.temperature = int(value['temperature'])
            if 'clouds' in value:
                if isinstance(value['clouds'], str):
                    miz.clouds = {"preset": value['clouds']}
                else:
                    miz.clouds = value['clouds']
            if 'wind' in value:
                miz.wind = value['wind']
            if 'groundTurbulence' in value:
                miz.groundTurbulence = int(value['groundTurbulence'])
            if 'enable_dust' in value:
                miz.enable_dust = value['enable_dust']
            if 'dust_density' in value:
                miz.dust_density = int(value['dust_density'])
            if 'qnh' in value:
                miz.qnh = int(value['qnh'])
            if 'enable_fog' in value:
                miz.enable_fog = value['enable_fog']
            if 'fog' in value:
                miz.fog = value['fog']
            if 'halo' in value:
                miz.halo = value['halo']
            if 'requiredModules' in value:
                miz.requiredModules = value['requiredModules']
            if 'accidental_failures' in value:
                miz.accidental_failures = value['accidental_failures']
            if 'forcedOptions' in value:
                miz.forcedOptions = value['forcedOptions']
            if 'miscellaneous' in value:
                miz.miscellaneous = value['miscellaneous']
            if 'difficulty' in value:
                miz.difficulty = value['difficulty']
            if 'files' in value:
                miz.files = value['files']

        filename = server.get_current_mission_file()
        if not filename:
            return
        now = datetime.now()
        if not presets:
            if isinstance(config['settings'], dict):
                for key, value in config['settings'].items():
                    if utils.is_in_timeframe(now, key):
                        presets = value
                        break
                if not presets:
                    # no preset found for the current time, so don't change anything
                    return
            elif isinstance(config['settings'], list):
                presets = random.choice(config['settings'])
        miz = MizFile(server.bot, filename)
        for preset in [x.strip() for x in presets.split(',')]:
            if preset not in config['presets']:
                server.log.error(f'Preset {preset} not found, ignored.')
                continue
            value = config['presets'][preset]
            if isinstance(value, list):
                for inner_preset in value:
                    if inner_preset not in config['presets']:
                        server.log.error(f'Preset {inner_preset} not found, ignored.')
                        continue
                    inner_value = config['presets'][inner_preset]
                    apply_preset(inner_value)
            elif isinstance(value, dict):
                apply_preset(value)
            server.bot.log.info(f"  => Preset {preset} applied.")
        miz.save()

    @staticmethod
    def is_mission_change(server: Server, config: dict) -> bool:
        if 'settings' in config:
            return True
        # check if someone overloaded beforeMissionLoad, which means the mission is likely to be changed
        for ext in server.extensions.values():
            if ext.__class__.beforeMissionLoad != Extension.beforeMissionLoad:
                return True
        return False

    async def restart_mission(self, server: Server, config: dict, rconf: dict, max_warn_time: int):
        # a restart is already pending, nothing more to do
        if server.restart_pending:
            return
        method = rconf['method']
        # shall we do something at mission end only?
        if rconf.get('mission_end', False):
            server.on_mission_end = {'command': method}
            server.restart_pending = True
            return
        # check if the server is populated
        if server.is_populated():
            if not rconf.get('populated', True):
                if not server.on_empty:
                    server.on_empty = {'command': method}
                if 'max_mission_time' not in rconf:
                    server.restart_pending = True
                    return
                elif server.current_mission.mission_time <= (rconf['max_mission_time'] * 60 - max_warn_time):
                    return
            server.restart_pending = True
            await self.warn_users(server, config, method, max_warn_time)
            # in the unlikely event that we did restart already in the meantime while warning users or
            # if the restart has been cancelled due to maintenance mode
            if not server.restart_pending or not server.is_populated():
                return
            else:
                server.on_empty.clear()
        else:
            server.restart_pending = True

        if 'shutdown' in method:
            await self.teardown_dcs(server)
        if method == 'restart_with_shutdown':
            try:
                await self.launch_dcs(server, config)
            except asyncio.TimeoutError:
                await self.bot.audit(f"{self.plugin_name.title()}: Timeout while starting server",
                                     server=server)
        elif method == 'restart':
            if self.is_mission_change(server, config):
                await server.stop()
                for ext in server.extensions.values():
                    await ext.beforeMissionLoad()
                if 'settings' in config:
                    await self.change_mizfile(server, config)
                await server.start()
            else:
                await server.current_mission.restart()
            await self.bot.audit(f"{self.plugin_name.title()} restarted mission "
                                 f"{server.current_mission.display_name}", server=server)
        elif method == 'rotate':
            await server.loadNextMission()
            if self.is_mission_change(server, config):
                await server.stop()
                for ext in server.extensions.values():
                    await ext.beforeMissionLoad()
                if 'settings' in config:
                    await self.change_mizfile(server, config)
                await server.start()
            await self.bot.audit(f"{self.plugin_name.title()} rotated to mission "
                                 f"{server.current_mission.display_name}", server=server)

    async def check_mission_state(self, server: Server, config: dict):
        def check_mission_restart(rconf: dict):
            # calculate the time when the mission has to restart
            if server.is_populated() and rconf.get('populated', 'True'):
                warn_times = Scheduler.get_warn_times(config)
            else:
                warn_times = [0]
            # we check the warn times in the opposite order to see which one fits best
            for warn_time in sorted(warn_times):
                if 'local_times' in rconf:
                    restart_time = datetime.now() + timedelta(seconds=warn_time)
                    for t in rconf['local_times']:
                        if utils.is_in_timeframe(restart_time, t):
                            asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                            return
                elif 'mission_time' in rconf:
                    if (server.current_mission.mission_time + warn_time) >= rconf['mission_time'] * 60:
                        asyncio.create_task(self.restart_mission(server, config, rconf, warn_time))
                        return

        if 'restart' in config and not server.restart_pending:
            if isinstance(config['restart'], list):
                for r in config['restart']:
                    check_mission_restart(r)
            else:
                check_mission_restart(config['restart'])

    @staticmethod
    async def check_affinity(server: Server, config: dict):
        if not server.process:
            for exe in ['DCS_server.exe', 'DCS.exe']:
                server.process = utils.find_process(exe, server.installation)
                if server.process:
                    break
        if server.process:
            server.process.cpu_affinity(config['affinity'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # check all servers
        for server_name, server in self.bot.servers.items():
            # only care about servers that are not in the startup phase
            if server.status in [Status.UNREGISTERED, Status.LOADING] or server.maintenance:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    if server.status == Status.RUNNING and 'affinity' in config:
                        await self.check_affinity(server, config)
                    target_state = await self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server.status == Status.SHUTDOWN:
                        asyncio.create_task(self.launch_dcs(server, config))
                    elif target_state == Status.SHUTDOWN and server.status in [
                        Status.STOPPED, Status.RUNNING, Status.PAUSED
                    ]:
                        asyncio.create_task(self.teardown(server, config))
                    elif server.status in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                    # if the server is running, and should run, check if all the extensions are running, too
                    if server.status in [
                        Status.RUNNING, Status.PAUSED, Status.STOPPED
                    ] and target_state == server.status:
                        for ext in server.extensions.values():
                            if not ext.is_running():
                                await ext.startup()
                except Exception as ex:
                    self.log.warning("Exception in check_state(): " + str(ex))

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()
        initialized = 0
        while initialized < len(self.bot.servers):
            initialized = 0
            for server_name, server in self.bot.servers.items():
                if server.status != Status.UNREGISTERED:
                    initialized += 1
            await asyncio.sleep(1)

    @tasks.loop(minutes=1.0)
    async def schedule_extensions(self):
        for server in self.bot.servers.values():
            for ext in server.extensions.values():
                try:
                    await ext.schedule()
                except Exception as ex:
                    self.log.exception(ex)

    @commands.command(description='Starts a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def startup(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            config = self.get_config(server)
            if server.status == Status.STOPPED:
                await ctx.send(f"DCS server \"{server.display_name}\" is stopped.\n"
                               f"Please use {ctx.prefix}start instead.")
                return
            if server.status == Status.LOADING:
                if not server.process.is_running():
                    server.status = Status.SHUTDOWN
                else:
                    if await utils.yn_question(ctx, "Server is in state LOADING. Do you want to kill and restart it?"):
                        await server.shutdown()
                    else:
                        return
            if server.status == Status.SHUTDOWN:
                msg = await ctx.send(f"DCS server \"{server.display_name}\" starting up ...")
                # set maintenance flag to prevent auto-stops of this server
                server.maintenance = True
                try:
                    await self.launch_dcs(server, config, ctx.message.author)
                    await ctx.send(f"DCS server \"{server.display_name}\" started.\n"
                                   f"Server in maintenance mode now! Use {ctx.prefix}clear to reset maintenance mode.")
                except asyncio.TimeoutError:
                    await ctx.send(f"Timeout while launching DCS server \"{server.display_name}\".\n"
                                   f"The server might be running anyway, check with {ctx.prefix}status.")
                finally:
                    await msg.delete()

            else:
                await ctx.send(f"DCS server \"{server.display_name}\" is already started.")

    @commands.command(description='Shutdown a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def shutdown(self, ctx, *params):
        async def do_shutdown(server: Server, force: bool = False):
            msg = await ctx.send(f"Shutting down DCS server \"{server.display_name}\", please wait ...")
            # set maintenance flag to prevent auto-starts of this server
            server.maintenance = True
            if force:
                await server.shutdown()
            else:
                await self.teardown_dcs(server, ctx.message.author)
            await msg.delete()
            await ctx.send(f"DCS server \"{server.display_name}\" shut down.\n"
                           f"Server in maintenance mode now! Use {ctx.prefix}clear to reset maintenance mode.")

        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status in [Status.UNREGISTERED, Status.LOADING]:
                if params and params[0] == '-force' or \
                        await utils.yn_question(ctx, f"Server is in state {server.status.name}.\n"
                                                     f"Do you want to force a shutdown?"):
                    await do_shutdown(server, True)
                else:
                    return
            elif server.status != Status.SHUTDOWN:
                if not params or params[0] != '-force':
                    question = f"Do you want to shut down DCS server \"{server.display_name}\"?"
                    if server.is_populated():
                        result = await utils.populated_question(ctx, question)
                    else:
                        result = await utils.yn_question(ctx, question)
                    if not result:
                        await ctx.send('Aborted.')
                        return
                    elif result == 'later':
                        server.on_empty = {"command": "shutdown", "user": ctx.message.author}
                        server.restart_pending = True
                        await ctx.send('Shutdown postponed when server is empty.')
                        return
                await do_shutdown(server)
            else:
                await ctx.send(f"DCS server \"{server.display_name}\" is already shut down.")

    @commands.command(description='Starts a stopped DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def start(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status == Status.STOPPED:
                msg = await ctx.send(f"Starting server {server.display_name} ...")
                await server.start()
                await msg.delete()
                await ctx.send(f"Server {server.display_name} started.")
                await self.bot.audit('started the server', server=server, user=ctx.message.author)
            elif server.status == Status.SHUTDOWN:
                await ctx.send(f"Server {server.display_name} is shut down. Use {ctx.prefix}startup to start it up.")
            elif server.status in [Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"Server {server.display_name} is already started.")
            else:
                await ctx.send(f"Server {server.display_name} is still {server.status.name}, please wait ...")

    @commands.command(description='Stops a DCS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def stop(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status in [Status.RUNNING, Status.PAUSED]:
                if server.is_populated() and \
                        not await utils.yn_question(ctx, "People are flying on this server atm.\n"
                                                         "Do you really want to stop it?"):
                    return
                await server.stop()
                await self.bot.audit('stopped the server', server=server, user=ctx.message.author)
                await ctx.send(f"Server {server.display_name} stopped.")
            elif server.status == Status.STOPPED:
                await ctx.send(
                    f"Server {server.display_name} is stopped already. Use {ctx.prefix}shutdown to terminate the "
                    f"dcs.exe process.")
            elif server.status == Status.SHUTDOWN:
                await ctx.send(f"Server {server.display_name} is shut down already.")
            else:
                await ctx.send(f"Server {server.display_name} is {server.status.name}, please wait ...")

    @commands.command(description='Status of the DCS-servers')
    @utils.has_role('DCS')
    @commands.guild_only()
    async def status(self, ctx):
        embed = discord.Embed(title=f"Server Status ({platform.node()})", color=discord.Color.blue())
        names = []
        status = []
        maintenance = []
        for server in self.bot.servers.values():
            names.append(server.display_name)
            status.append(server.status.name.title())
            maintenance.append('Y' if server.maintenance else 'N')
        if len(names):
            embed.add_field(name='Server', value='\n'.join(names))
            embed.add_field(name='Status', value='\n'.join(status))
            embed.add_field(name='Maint.', value='\n'.join(maintenance))
            embed.set_footer(text=f"Bot Version: v{self.bot.version}.{self.bot.sub_version}")
            await ctx.send(embed=embed)

    @commands.command(description='Sets the servers maintenance flag', aliases=['maint'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def maintenance(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if not server.maintenance:
                if (server.restart_pending or server.on_empty or server.on_mission_end) and \
                        not await utils.yn_question(ctx, "Server is configured for a pending restart.\n"
                                                    "Setting the maintenance flag will abort this restart.\n"
                                                    "Are you sure?"):
                    await ctx.send("Aborted.")
                    return
                server.maintenance = True
                server.restart_pending = False
                server.on_empty.clear()
                server.on_mission_end.clear()
                await ctx.send(f"Maintenance mode set for server {server.display_name}.\n"
                               f"The {self.plugin_name.title()} will be set on hold until you use"
                               f" {ctx.prefix}clear again.")
                await self.bot.audit("set maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server.display_name} is already in maintenance mode.")

    @commands.command(description='Clears the servers maintenance flag')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def clear(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.maintenance:
                server.maintenance = False
                await ctx.send(f"Maintenance mode cleared for server {server.display_name}.\n"
                               f"The {self.plugin_name.title()} will take over the state handling now.")
                await self.bot.audit("cleared maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server.display_name} is not in maintenance mode.")

    class PresetView(View):
        def __init__(self, ctx: commands.Context, options: list[discord.SelectOption]):
            super().__init__()
            self.ctx = ctx
            select: Select = cast(Select, self.children[0])
            select.options = options
            select.max_values = min(10, len(options))
            self.result = None

        @discord.ui.select(placeholder="Select the preset(s) you want to apply")
        async def callback(self, interaction: Interaction, select: Select):
            self.result = select.values
            await interaction.response.defer()

        @discord.ui.button(label='OK', style=discord.ButtonStyle.green)
        async def ok(self, interaction: Interaction, button: Button):
            await interaction.response.defer()
            self.stop()

        @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
        async def cancel(self, interaction: Interaction, button: Button):
            await interaction.response.defer()
            self.result = None
            self.stop()

        async def interaction_check(self, interaction: Interaction, /) -> bool:
            if interaction.user != self.ctx.author:
                await interaction.response.send_message('This is not your command, mate!', ephemeral=True)
                return False
            else:
                return True

    @commands.command(description='Change mission preset', aliases=['presets'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def preset(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return

        config = self.get_config(server)
        presets = [
            discord.SelectOption(label=k)
            for k, v in config.get('presets', {}).items()
            if 'hidden' not in v or not v['hidden']
        ]
        if not presets:
            await ctx.send('No presets available, please configure them in your scheduler.json.')
            return
        if len(presets) > 25:
            self.log.warning("You have more than 25 presets created, you can only choose from 25!")

        if server.status in [Status.PAUSED, Status.RUNNING]:
            question = 'Do you want to stop the server to change the mission preset?'
            if server.is_populated():
                result = await utils.populated_question(ctx, question)
            else:
                result = await utils.yn_question(ctx, question)
            if not result:
                await ctx.send('Aborted.')
                return
        elif server.status == Status.LOADING:
            await ctx.send("Server is still loading, can't change presets.")
            return
        else:
            result = None

        view = self.PresetView(ctx, presets[:25])
        msg = await ctx.send(view=view)
        try:
            if await view.wait():
                return
            elif not view.result:
                await ctx.send('Aborted.')
                return
        finally:
            await ctx.message.delete()
            await msg.delete()
        if result == 'later':
            server.on_empty = {"command": "preset", "preset": view.result, "user": ctx.message.author}
            server.restart_pending = True
            await ctx.send(f'Preset will be changed when server is empty.')
        else:
            msg = await ctx.send('Changing presets...')
            stopped = False
            if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
                stopped = True
                await server.stop()
            await self.change_mizfile(server, config, ','.join(view.result))
            message = 'Preset changed to: {}.'.format(','.join(view.result))
            if stopped:
                await server.start()
                message += '\nServer restarted.'
            await self.bot.audit("changed preset", server=server, user=ctx.message.author)
            await msg.edit(content=message)

    @commands.command(description='Create preset from running mission', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add_preset(self, ctx, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status not in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"No mission running on server {server.display_name}.")
                return
            name = ' '.join(args)
            if not name:
                await ctx.send(f'Usage: {ctx.prefix}add_preset <name>')
                return
            miz = MizFile(self.bot, server.current_mission.filename)
            if 'presets' not in self.locals['configs'][0]:
                self.locals['configs'][0]['presets'] = dict()
            if name in self.locals['configs'][0]['presets'] and \
                    not await utils.yn_question(ctx, f'Do you want to overwrite the existing preset "{name}"?'):
                await ctx.send('Aborted.')
                return
            self.locals['configs'][0]['presets'] |= {
                name: {
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
            }
            with open(f'config/{self.plugin_name}.json', 'w', encoding='utf-8') as file:
                json.dump(self.locals, file, indent=2)
            await ctx.send(f'Preset "{name}" added.')

    @commands.command(description='Reset a mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def reset(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            stopped = False
            if server.status in [Status.RUNNING, Status.PAUSED]:
                if not await utils.yn_question(ctx, 'Do you want me to stop the server to reset the mission?'):
                    await ctx.send('Aborted.')
                    return
                stopped = True
                await server.stop()
            elif not await utils.yn_question(ctx, 'Do you want to reset the mission?'):
                await ctx.send('Aborted.')
                return
            config = self.get_config(server)
            if 'reset' not in config:
                await ctx.send(f"No \"reset\" parameter found for server {server.display_name}.")
                return
            reset = config['reset']
            if isinstance(reset, list):
                for cmd in reset:
                    await self.eventlistener.run(server, cmd)
            elif isinstance(reset, str):
                await self.eventlistener.run(server, reset)
            else:
                await ctx.send('Incorrect format of "reset" parameter in scheduler.json')
            if stopped:
                await server.start()
                await ctx.send('Mission reset, server restarted.')
            else:
                await ctx.send('Mission reset.')


async def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    await bot.add_cog(Scheduler(bot, SchedulerListener))
