from __future__ import annotations
import asyncio
import discord
import json
import os
import random
import string
from copy import deepcopy
from core import Plugin, PluginRequiredError, utils, Status, MizFile, Autoexec, Extension, Server, Coalition, Channel
from discord.ui import Select, View
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from typing import Type, Optional, List, TYPE_CHECKING
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
            return super().read_locals()

    def install(self):
        super().install()
        for _, installation in utils.findDCSInstallations():
            if installation in self.bot.config:
                cfg = Autoexec(bot=self.bot, installation=installation)
                if cfg.crash_report_mode is None:
                    self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                    cfg.crash_report_mode = 'silent'
                elif cfg.crash_report_mode != 'silent':
                    self.log.warning('=> crash_report_mode is NOT "silent" in your autoexec.cfg! The Scheduler will '
                                     'not work properly on DCS crashes, please change it manually to "silent" to '
                                     'avoid that.')

    def migrate(self, version: str):
        if version != '1.1' or 'SRS_INSTALLATION' not in self.bot.config['DCS']:
            return
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
                "SRS": {"config": self.bot.config[old['configs'][c]['installation']]['SRS_CONFIG'].replace('%%', '%')}
            }
        os.rename('config/scheduler.json', 'config/scheduler.bak')
        with open('config/scheduler.json', 'w') as file:
            json.dump(new, file, indent=2)
            self.log.info('  => config/scheduler.json migrated to new format, please verify!')

    def get_config(self, server: Server) -> Optional[dict]:
        if server.name not in self._config:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:  # type: dict
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
    def check_server_state(server: Server, config: dict) -> Status:
        if 'schedule' in config and not server.maintenance:
            warn_times: list[int] = Scheduler.get_warn_times(config)
            restart_in: int = max(warn_times) if len(warn_times) and server.is_populated() else 0
            now: datetime = datetime.now()
            weekday = (now + timedelta(seconds=restart_in)).weekday()
            for period, daystate in config['schedule'].items():  # type: str, dict
                state = daystate[weekday]
                # check, if the server should be running
                if utils.is_in_timeframe(now, period) and state.upper() == 'Y' and server.status == Status.SHUTDOWN:
                    return Status.RUNNING
                elif utils.is_in_timeframe(now, period) and state.upper() == 'P' and server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not server.is_populated():
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now + timedelta(seconds=restart_in), period) and state.upper() == 'N' and server.status == Status.RUNNING:
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now, period) and state.upper() == 'N' and server.status in [Status.PAUSED, Status.STOPPED]:
                    return Status.SHUTDOWN
        return server.status

    async def launch_dcs(self, server: Server, config: dict, member: Optional[discord.Member] = None):
        # change the weather in the mission if provided
        if 'restart' in config and 'settings' in config['restart']:
            self.change_mizfile(server, config)
        self.log.info(f"  => DCS server \"{server.name}\" starting up ...")
        await server.startup()
        if not member:
            self.log.info(f"  => DCS server \"{server.name}\" started by "
                          f"{string.capwords(self.plugin_name)}.")
            await self.bot.audit(f"{string.capwords(self.plugin_name)} started DCS server", server=server)
        else:
            self.log.info(f"  => DCS server \"{server.name}\" started by "
                          f"{member.display_name}.")
            await self.bot.audit(f"started DCS server", user=member, server=server)

    @staticmethod
    def get_warn_times(config: dict) -> List[int]:
        if 'warn' in config and 'times' in config['warn']:
            return config['warn']['times']
        return []

    async def warn_users(self, server: Server, config: dict, what: str):
        if 'warn' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            warn_text = config['warn']['text'] if 'text' in config['warn'] \
                else '!!! {item} will {what} in {when} !!!'
            if what == 'restart_with_shutdown':
                what = 'restart'
                item = 'server'
            elif what == 'shutdown':
                item = 'server'
            else:
                item = 'mission'
            while restart_in > 0:
                for warn_time in warn_times:
                    if warn_time == restart_in:
                        server.sendPopupMessage(Coalition.ALL, warn_text.format(item=item, what=what,
                                                                                when=utils.format_time(warn_time)),
                                                self.bot.config['BOT']['MESSAGE_TIMEOUT'])
                        chat_channel = server.get_channel(Channel.CHAT)
                        if chat_channel:
                            await chat_channel.send(warn_text.format(item=item, what=what,
                                                                     when=utils.format_time(warn_time)))
                await asyncio.sleep(1)
                restart_in -= 1

    async def teardown_extensions(self, server: Server, config: dict, member: Optional[discord.Member] = None) -> list:
        retval = []
        for extension in config['extensions']:  # type: str
            ext: Extension = server.extensions[extension] if 'extensions' in server.extensions else None
            if not ext:
                if '.' not in extension:
                    ext = utils.str_to_class('extensions.builtin.' + extension)(self.bot, server, config['extensions'][extension])
                else:
                    ext = utils.str_to_class(extension)(self.bot, server, config['extensions'][extension])
                if ext.verify():
                    server.extensions[extension] = ext
            if await ext.is_running() and await ext.shutdown():
                retval.append(ext.name)
                if not member:
                    self.log.info(f"  => {ext.name} shut down for \"{server.name}\" by "
                                  f"{string.capwords(self.plugin_name)}.")
                    await self.bot.audit(f"{string.capwords(self.plugin_name)} shut {ext.name} down", server=server)
                else:
                    self.log.info(f"  => {ext.name} shut down for \"{server.name}\" by "
                                  f"{member.display_name}.")
                    await self.bot.audit(f"shut {ext.name} down", server=server, user=member)
        return retval

    async def teardown_dcs(self, server: Server, member: Optional[discord.Member] = None):
        self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
        await asyncio.sleep(1)
        self.bot.sendtoBot({"command": "onShutdown", "server_name": server.name})
        await asyncio.sleep(1)
        await server.shutdown()
        if not member:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {string.capwords(self.plugin_name)}.")
            await self.bot.audit(f"{string.capwords(self.plugin_name)} shut down DCS server", server=server)
        else:
            self.log.info(
                f"  => DCS server \"{server.name}\" shut down by {member.display_name}.")
            await self.bot.audit(f"shut down DCS server", server=server, user=member)

    async def teardown(self, server: Server, config: dict):
        # if we should not restart populated servers, wait for it to be unpopulated
        populated = server.is_populated()
        if 'populated' in config and not config['populated'] and populated:
            return
        elif not server.restart_pending:
            server.restart_pending = True
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            if restart_in > 0 and populated:
                self.log.info(f"  => DCS server \"{server.name}\" will be shut down "
                              f"by {string.capwords(self.plugin_name)} in {restart_in} seconds ...")
                await self.bot.audit(f"{string.capwords(self.plugin_name)} will shut down DCS server in {utils.format_time(restart_in)}",
                                     server=server)
                await self.warn_users(server, config, 'shutdown')
            await self.teardown_dcs(server)
            if 'extensions' in config:
                await self.teardown_extensions(server, config)
            server.restart_pending = False

    @staticmethod
    def change_mizfile(server: Server, config: dict, presets: Optional[str] = None):
        def apply_preset(preset: dict):
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

        filename = None
        if not server.current_mission or not server.current_mission.filename:
            for i in range(int(server.getServerSetting('listStartIndex')), 0, -1):
                filename = server.getServerSetting(i)
                if server.current_mission:
                    server.current_mission.filename = filename
                break
        else:
            filename = server.current_mission.filename
        if not filename:
            return
        now = datetime.now()
        if not presets:
            if isinstance(config['restart']['settings'], dict):
                for key, value in config['restart']['settings'].items():
                    if utils.is_in_timeframe(now, key):
                        presets = value
                        break
                if not presets:
                    # no preset found for the current time, so don't change anything
                    return
            elif isinstance(config['restart']['settings'], list):
                presets = random.choice(config['restart']['settings'])
        miz = MizFile(filename)
        for preset in [x.strip() for x in presets.split(',')]:
            value = config['presets'][preset]
            if isinstance(value, list):
                for inner_preset in value:
                    inner_value = config['presets'][inner_preset]
                    apply_preset(inner_value)
            elif isinstance(value, dict):
                apply_preset(value)
        miz.save()

    async def restart_mission(self, server: Server, config: dict):
        # check if the mission is still populated
        populated = server.is_populated()
        if 'populated' in config['restart'] and not config['restart']['populated'] and populated:
            return
        elif not server.restart_pending:
            server.restart_pending = True
            method = config['restart']['method']
            if populated and method != 'restart_with_shutdown' and 'mission_end' not in config['restart']:
                await self.warn_users(server, config, method)
            if method == 'restart_with_shutdown':
                if 'mission_end' in config['restart'] and config['restart']['mission_end']:
                    server.shutdown_pending = True
                else:
                    self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
                    await asyncio.sleep(1)
                    await server.shutdown()
                    await self.launch_dcs(server, config)
            elif method == 'restart':
                self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
                await asyncio.sleep(1)
                if 'settings' in config['restart']:
                    await server.stop()
                    self.change_mizfile(server, config)
                    await server.start()
                else:
                    await server.current_mission.restart()
            elif method == 'rotate':
                self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server.name})
                await asyncio.sleep(1)
                await server.loadNextMission()

    async def check_mission_state(self, server: Server, config: dict):
        if 'restart' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) and server.is_populated() else 0
            if 'mission_time' in config['restart'] and \
                    (server.current_mission.mission_time + restart_in) >= (int(config['restart']['mission_time']) * 60):
                asyncio.create_task(self.restart_mission(server, config))
            elif 'local_times' in config['restart']:
                now = datetime.now()
                if config['restart']['method'] != 'restart_with_shutdown' and 'mission_end' not in config['restart']:
                    now += timedelta(seconds=restart_in)
                for t in config['restart']['local_times']:
                    if utils.is_in_timeframe(now, t):
                        asyncio.create_task(self.restart_mission(server, config))

    @staticmethod
    def check_affinity(server: Server, config: dict):
        if not server.process:
            server.process = utils.find_process('DCS.exe', server.installation)
        if server.process:
            server.process.cpu_affinity(config['affinity'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # check all servers
        for server_name, server in self.bot.servers.items():
            # only care about servers that are not in the startup phase
            if server.status in [Status.UNREGISTERED, Status.LOADING] or server.restart_pending:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    if server.status == Status.RUNNING and 'affinity' in config:
                        self.check_affinity(server, config)
                    target_state = self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server.status == Status.SHUTDOWN:
                        asyncio.create_task(self.launch_dcs(server, config))
                    elif target_state == Status.SHUTDOWN and server.status in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                        asyncio.create_task(self.teardown(server, config))
                    elif server.status in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                    # if the server is running, and should run, check if all the extensions are running, too
                    if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and target_state == server.status:
                        for ext in server.extensions.values():
                            if not await ext.is_running() and await ext.startup():
                                self.log.info(f"  - {ext.name} v{ext.version} launched for \"{server.name}\".")
                                await self.bot.audit(f"{ext.name} started", server=server)
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
        if 'extensions' not in self.locals['configs'][0]:
            return
        for extension, config in self.locals['configs'][0]['extensions'].items():  # type: str, dict
            if '.' not in extension:
                ext: Extension = utils.str_to_class('extensions.builtin.' + extension)
            else:
                ext: Extension = utils.str_to_class(extension)
            ext.schedule(config, self.lastrun)
        self.lastrun = datetime.now()

    @commands.command(description='Starts a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def startup(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            config = self.get_config(server)
            if server.status == Status.STOPPED:
                await ctx.send(f"DCS server \"{server.name}\" is stopped.\n"
                               f"Please use {ctx.prefix}start instead.")
                return
            if server.status == Status.SHUTDOWN:
                msg = await ctx.send(f"DCS server \"{server.name}\" starting up ...")
                # set maintenance flag to prevent auto-stops of this server
                server.maintenance = True
                await self.launch_dcs(server, config, ctx.message.author)
                await msg.delete()
                await ctx.send(f"DCS server \"{server.name}\" started.\n"
                               f"Server in maintenance mode now! Use {ctx.prefix}clear to reset maintenance mode.")
                for ext in server.extensions.values():
                    if not await ext.is_running() and await ext.startup():
                        self.log.info(f"  - {ext.name} v{ext.version} launched for \"{server.name}\".")
                        await self.bot.audit(f"{ext.name} started", server=server)
            else:
                await ctx.send(f"DCS server \"{server.name}\" is already started.")

    @commands.command(description='Shutdown a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def shutdown(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            config = self.get_config(server)
            if server.status in [Status.UNREGISTERED, Status.LOADING]:
                await ctx.send('Server is currently starting up. Please wait and try again.')
                return
            elif server.status != Status.SHUTDOWN:
                question = f"Do you want to shut down the DCS server \"{server.name}\"?"
                if server.is_populated():
                    question += '\nPeople are flying on this server atm!'
                if not await utils.yn_question(ctx, question):
                    await ctx.send('Aborted.')
                    return
                msg = await ctx.send(f"Shutting down DCS server \"{server.name}\", please wait ...")
                # set maintenance flag to prevent auto-starts of this server
                server.maintenance = True
                server.restart_pending = True
                await self.teardown_dcs(server, ctx.message.author)
                await msg.delete()
                await ctx.send(f"DCS server \"{server.name}\" shut down.\n"
                               f"Server in maintenance mode now! Use {ctx.prefix}clear to reset maintenance mode.")
                server.restart_pending = False
            else:
                await ctx.send(f"DCS server \"{server.name}\" is already shut down.")
            if 'extensions' in config:
                for ext in await self.teardown_extensions(server, config, ctx.message.author):
                    await ctx.send(f"{ext} shut down for server \"{server.name}\".")

    @commands.command(description='Sets the servers maintenance flag')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def maintenance(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if not server.maintenance:
                server.maintenance = True
                await ctx.send(f"Maintenance mode set for server {server.name}.\n"
                               f"The {string.capwords(self.plugin_name)} will be set on hold until you use"
                               f" {ctx.prefix}clear again.")
                await self.bot.audit("set maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server.name} is already in maintenance mode.")

    @commands.command(description='Clears the servers maintenance flag')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def clear(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.maintenance:
                server.maintenance = False
                await ctx.send(f"Maintenance mode cleared for server {server.name}.\n"
                               f"The {string.capwords(self.plugin_name)} will take over the state handling now.")
                await self.bot.audit("cleared maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server.name} is not in maintenance mode.")

    @staticmethod
    def format_presets(data: list[str], marker, marker_emoji):
        embed = discord.Embed(title='Mission Presets', color=discord.Color.blue())
        embed.add_field(name='ID', value='\n'.join([chr(0x31 + x) + '\u20E3' for x in range(0, len(data))]))
        embed.add_field(name='Preset', value='\n'.join(data))
        embed.add_field(name='_ _', value='_ _')
        embed.set_footer(text='Press a number to select a preset.')
        return embed

    @commands.command(description='Change mission preset', aliases=['presets'])
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def preset(self, ctx):
        server: Server = await self.bot.get_server(ctx)
        if not server:
            return

        config = self.get_config(server)
        presets = [discord.SelectOption(label=p) for p in config['presets'].keys()]
        if not presets:
            await ctx.send('No presets available, please configure them in your scheduler.json.')
            return

        stopped = False
        if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
            question = f"Do you want to stop server \"{server.name}\" to change the mission preset?"
            if server.is_populated():
                question += '\nPeople are flying on this server atm!'
            if not await utils.yn_question(ctx, question):
                await ctx.send('Aborted.')
                return
            stopped = True
            await server.stop()

        select = Select(options=presets, placeholder="Select the preset(s) you want to apply", max_values=min(10, len(presets)))

        async def callback(interaction: discord.Interaction):
            await interaction.response.defer(thinking=True)
            for preset in select.values:
                self.change_mizfile(server, config, preset)
            if stopped:
                await server.start()
                await interaction.followup.send('Preset changed, server restarted.')
            else:
                await interaction.followup.send('Preset changed.')

        select.callback = callback
        view = View()
        view.add_item(select)
        await ctx.send(view=view)

    @commands.command(description='Create preset from running mission', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add_preset(self, ctx, *args):
        server: Server = await self.bot.get_server(ctx)
        if server:
            if server.status not in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"Server {server.name} not running.")
                return
            name = ' '.join(args)
            if not name:
                await ctx.send(f'Usage: {ctx.prefix}add_preset <name>')
                return
            miz = MizFile(server.current_mission.filename)
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
                    "fog": miz.fog if miz.enable_fog else {"thickness": 0, "visibility": 0}
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
            if server.status not in [Status.STOPPED, Status.SHUTDOWN]:
                if not await utils.yn_question(ctx, 'Do you want me to stop the server to reset the mission?'):
                    return
                stopped = True
                await server.stop()
            config = self.get_config(server)
            if 'reset' not in config:
                await ctx.send(f"No \"reset\" parameter found for server {server.name}.")
                return
            reset = config['reset']
            if isinstance(reset, list):
                for cmd in reset:
                    self.eventlistener._run(server, cmd)
            elif isinstance(reset, str):
                self.eventlistener._run(server, reset)
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
