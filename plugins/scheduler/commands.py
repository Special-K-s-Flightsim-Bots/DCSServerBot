import asyncio
import discord
import json
import os
import psutil
import random
import string
from core import Plugin, DCSServerBot, PluginRequiredError, utils, TEventListener, Status, MizFile, Autoexec, Extension
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from typing import Type, Optional, List
from .listener import SchedulerListener


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()
        self.lastrun = None
        self.schedule_extensions.start()

    def cog_unload(self):
        self.schedule_extensions.cancel()
        self.check_state.cancel()
        super().cog_unload()

    def install(self):
        super().install()
        for _, installation in utils.findDCSInstallations():
            if installation in self.config:
                cfg = Autoexec(bot=self.bot, installation=installation)
                if cfg.crash_report_mode is None:
                    self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                    cfg.crash_report_mode = 'silent'
                elif cfg.crash_report_mode != 'silent':
                    self.log.warning('=> crash_report_mode is NOT "silent" in your autoexec.cfg! The Scheduler will '
                                     'not work properly on DCS crashes, please change it manually to "silent" to '
                                     'avoid that.')

    def migrate(self, version: str):
        if version != 'v1.1' or 'SRS_INSTALLATION' not in self.config['DCS']:
            return
        with open('config/scheduler.json') as file:
            old: dict = json.load(file)
        new = old.copy()
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
            "SRS": {"installation": self.config['DCS']['SRS_INSTALLATION']}
        }
        # migrate the SRS configuration
        for c in range(0, len(old['configs'])):
            if 'installation' not in old['configs'][c] or \
                    'extensions' not in old['configs'][c] or \
                    'SRS' not in old['configs'][c]['extensions']:
                continue
            new['configs'][c]['extensions'] = {
                "SRS": {"config": self.config[old['configs'][c]['installation']]['SRS_CONFIG']}
            }
        os.rename('config/scheduler.json', 'config/scheduler.bak')
        with open('config/scheduler.json', 'w') as file:
            json.dump(new, file, indent=2)
            self.log.info('  => config/scheduler.json migrated to new format, please verify!')

    def get_config(self, server: dict) -> Optional[dict]:
        if self.plugin_name not in server:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server['installation'] == element['installation']) or \
                                ('server_name' in element and server['server_name'] == element['server_name']):
                            specific = element.copy()
                    else:
                        default = element.copy()
                if default and not specific:
                    server[self.plugin_name] = default
                elif specific and not default:
                    server[self.plugin_name] = specific
                elif default and specific:
                    server[self.plugin_name] = default | specific
                    if 'extensions' in default and 'extensions' in specific:
                        server[self.plugin_name]['extensions'] = default['extensions']
                        for key, value in specific['extensions'].items():
                            if key in default['extensions']:
                                server[self.plugin_name]['extensions'][key] = \
                                    default['extensions'][key] | specific['extensions'][key]
                            else:
                                server[self.plugin_name]['extensions'][key] = specific['extensions'][key]
            else:
                return None
        return server[self.plugin_name] if self.plugin_name in server else None

    def check_server_state(self, server: dict, config: dict) -> Status:
        if 'schedule' in config and 'maintenance' not in server:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) and utils.is_populated(self, server) else 0
            now = datetime.now()
            weekday = (now + timedelta(seconds=restart_in)).weekday()
            for period, daystate in config['schedule'].items():
                state = daystate[weekday]
                # check, if the server should be running
                if utils.is_in_timeframe(now, period) and state.upper() == 'Y' and server['status'] == Status.SHUTDOWN:
                    return Status.RUNNING
                elif utils.is_in_timeframe(now, period) and state.upper() == 'P' and server['status'] in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and not utils.is_populated(self, server):
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now + timedelta(seconds=restart_in), period) and state.upper() == 'N' and server['status'] == Status.RUNNING:
                    return Status.SHUTDOWN
                elif utils.is_in_timeframe(now, period) and state.upper() == 'N' and server['status'] in [Status.PAUSED, Status.STOPPED]:
                    return Status.SHUTDOWN
        return server['status']

    async def launch_dcs(self, server: dict, config: dict, member: Optional[discord.Member] = None):
        # change the weather in the mission if provided
        if 'restart' in config and 'settings' in config['restart']:
            if 'filename' not in server:
                for i in range(utils.getServerSetting(server, 'listStartIndex'), 0, -1):
                    filename = utils.getServerSetting(server, i)
                    if filename:
                        server['filename'] = filename
                        self.change_mizfile(server, config)
                        break
        utils.startup_dcs(self, server)
        if not member:
            self.log.info(f"  => DCS server \"{server['server_name']}\" started by "
                          f"{string.capwords(self.plugin_name)}.")
            await self.bot.audit(f"{string.capwords(self.plugin_name)} started DCS server", server=server)
        else:
            self.log.info(f"  => DCS server \"{server['server_name']}\" started by "
                          f"{member.display_name}.")
            await self.bot.audit(f"started DCS server", user=member, server=server)

    @staticmethod
    def get_warn_times(config: dict) -> List[int]:
        if 'warn' in config and 'times' in config['warn']:
            return config['warn']['times']
        return []

    async def warn_users(self, server: dict, config: dict, what: str):
        if 'warn' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            warn_text = config['warn']['text'] if 'text' in config['warn'] \
                else '!!! Server will {what} in {when} !!!'
            chat_channel = int(self.globals[server['server_name']]['chat_channel'])
            while restart_in > 0:
                for warn_time in warn_times:
                    if warn_time == restart_in:
                        self.bot.sendtoDCS(
                            server, {
                                'command': 'sendPopupMessage',
                                'message': warn_text.format(what=what, when=utils.format_time(warn_time)),
                                'to': 'all',
                                'time': self.config['BOT']['MESSAGE_TIMEOUT']
                            }
                        )
                        if chat_channel != -1:
                            channel = self.bot.get_channel(chat_channel)
                            await channel.send(warn_text.format(what=what, when=utils.format_time(warn_time)))
                await asyncio.sleep(1)
                restart_in -= 1

    async def teardown_extensions(self, server: dict, config: dict, member: Optional[discord.Member] = None) -> list:
        retval = []
        for extension in config['extensions']:
            ext: Extension = server['extensions'][extension] if 'extensions' in server else None
            if not ext:
                if '.' not in extension:
                    ext = utils.str_to_class('extensions.builtin.' + extension)(self.bot, server, config['extensions'][extension])
                else:
                    ext = utils.str_to_class(extension)(self.bot, server, config['extensions'][extension])
            if await ext.check() and await ext.shutdown():
                retval.append(ext.name)
                if not member:
                    self.log.info(f"  => {ext.name} shut down for \"{server['server_name']}\" by "
                                  f"{string.capwords(self.plugin_name)}.")
                    await self.bot.audit(f"{string.capwords(self.plugin_name)} shut {ext.name} down", server=server)
                else:
                    self.log.info(f"  => {ext.name} shut down for \"{server['server_name']}\" by "
                                  f"{member.display_name}.")
                    await self.bot.audit(f"shut {ext.name} down", server=server, user=member)
        return retval

    async def teardown_dcs(self, server: dict, config: dict, member: Optional[discord.Member] = None):
        self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server['server_name']})
        await asyncio.sleep(1)
        self.bot.sendtoBot({"command": "onShutdown", "server_name": server['server_name']})
        await asyncio.sleep(1)
        await utils.shutdown_dcs(self, server)
        if not member:
            self.log.info(
                f"  => DCS server \"{server['server_name']}\" shut down by {string.capwords(self.plugin_name)}.")
            await self.bot.audit(f"{string.capwords(self.plugin_name)} shut down DCS server", server=server)
        else:
            self.log.info(
                f"  => DCS server \"{server['server_name']}\" shut down by {member.display_name}.")
            await self.bot.audit(f"shut down DCS server", server=server, user=member)

    async def teardown(self, server: dict, config: dict):
        # if we should not restart populated servers, wait for it to be unpopulated
        populated = utils.is_populated(self, server)
        if 'populated' in config and not config['populated'] and populated:
            return
        elif 'restart_pending' not in server:
            server['restart_pending'] = True
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) else 0
            if restart_in > 0 and populated:
                self.log.info(f"  => DCS server \"{server['server_name']}\" will be shut down "
                              f"by {string.capwords(self.plugin_name)} in {restart_in} seconds ...")
                await self.bot.audit(f"{string.capwords(self.plugin_name)} will shut down DCS server in {utils.format_time(restart_in)}",
                                     server=server)
                await self.warn_users(server, config, 'shutdown')
            await self.teardown_dcs(server, config)
            if 'extensions' in config:
                await self.teardown_extensions(server, config)
            del server['restart_pending']

    @staticmethod
    def change_mizfile(server: dict, config: dict, preset: Optional[str] = None):
        now = datetime.now()
        value = None
        if not preset:
            if isinstance(config['restart']['settings'], dict):
                for key, preset in config['restart']['settings'].items():
                    if utils.is_in_timeframe(now, key):
                        value = config['presets'][preset]
                        break
            elif isinstance(config['restart']['settings'], list):
                value = config['presets'][random.choice(config['restart']['settings'])]
            if not value:
                raise ValueError("No preset found for the current time.")
        else:
            value = config['presets'][preset]
        miz = MizFile(server['filename'])
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
        if 'dust_density' in value:
            miz.dust_density = int(value['dust_density'])
        if 'qnh' in value:
            miz.qnh = int(value['qnh'])
        miz.save()

    async def restart_mission(self, server: dict, config: dict):
        # check if the mission is still populated
        populated = utils.is_populated(self, server)
        if 'populated' in config['restart'] and not config['restart']['populated'] and populated:
            return
        elif 'restart_pending' not in server:
            server['restart_pending'] = True
            method = config['restart']['method']
            if populated:
                await self.warn_users(server, config, 'restart' if method == 'restart_with_shutdown' else method)
            if method == 'restart_with_shutdown':
                self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server['server_name']})
                await asyncio.sleep(1)
                await utils.shutdown_dcs(self, server)
                await self.launch_dcs(server, config)
            elif method == 'restart':
                self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server['server_name']})
                await asyncio.sleep(1)
                if 'settings' in config['restart']:
                    self.bot.sendtoDCS(server, {"command": "stop_server"})
                    for i in range(0, 30):
                        await asyncio.sleep(1)
                        if server['status'] == Status.STOPPED:
                            break
                    self.change_mizfile(server, config)
                    self.bot.sendtoDCS(server, {"command": "start_server"})
                else:
                    self.bot.sendtoDCS(server, {"command": "restartMission"})
            elif method == 'rotate':
                self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server['server_name']})
                await asyncio.sleep(1)
                self.bot.sendtoDCS(server, {"command": "startNextMission"})

    async def check_mission_state(self, server: dict, config: dict):
        if 'restart' in config:
            warn_times = Scheduler.get_warn_times(config)
            restart_in = max(warn_times) if len(warn_times) and utils.is_populated(self, server) else 0
            if 'mission_time' in config['restart'] and \
                    (server['mission_time'] + restart_in) >= (int(config['restart']['mission_time']) * 60):
                asyncio.create_task(self.restart_mission(server, config))
            elif 'local_times' in config['restart']:
                now = datetime.now() + timedelta(seconds=restart_in)
                for t in config['restart']['local_times']:
                    if utils.is_in_timeframe(now, t):
                        asyncio.create_task(self.restart_mission(server, config))

    @staticmethod
    def check_affinity(server, config):
        if 'PID' not in server:
            p = utils.find_process('DCS.exe', server['installation'])
            server['PID'] = p.pid
        pid = server['PID']
        ps = psutil.Process(pid)
        ps.cpu_affinity(config['affinity'])

    @tasks.loop(minutes=1.0)
    async def check_state(self):
        # check all servers
        for server_name, server in self.globals.items():
            # only care about servers that are not in the startup phase
            if server['status'] in [Status.UNREGISTERED, Status.LOADING] or 'restart_pending' in server:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    if server['status'] == Status.RUNNING and 'affinity' in config:
                        self.check_affinity(server, config)
                    target_state = self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server['status'] == Status.SHUTDOWN:
                        asyncio.create_task(self.launch_dcs(server, config))
                    elif target_state == Status.SHUTDOWN and server['status'] in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                        asyncio.create_task(self.teardown(server, config))
                    elif server['status'] in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                except Exception as ex:
                    self.log.warning("Exception in check_state(): " + str(ex))

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1.0)
    async def schedule_extensions(self):
        if 'extensions' not in self.locals['configs'][0]:
            return
        for extension, config in self.locals['configs'][0]['extensions'].items():
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
        server = await utils.get_server(self, ctx)
        if server:
            config = self.get_config(server)
            if server['status'] == Status.STOPPED:
                await ctx.send(f"DCS server \"{server['server_name']}\" is stopped.\n"
                               f"Please use {self.config['DCS']['COMMAND_PREFIX']}start instead.")
                return
            if server['status'] == Status.SHUTDOWN:
                await ctx.send(f"DCS server \"{server['server_name']}\" starting up ...")
                # set maintenance flag to prevent auto-stops of this server
                server['maintenance'] = True
                await self.launch_dcs(server, config, ctx.message.author)
            else:
                await ctx.send(f"DCS server \"{server['server_name']}\" is already started.")

    @commands.command(description='Shutdown a DCS/DCS-SRS server')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def shutdown(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            config = self.get_config(server)
            if server['status'] in [Status.UNREGISTERED, Status.LOADING]:
                await ctx.send('Server is currently starting up. Please wait and try again.')
                return
            elif server['status'] != Status.SHUTDOWN:
                if await utils.yn_question(self, ctx, f"Do you want to shut down the "
                                                      f"DCS server \"{server['server_name']}\"?") is True:
                    await ctx.send(f"Shutting down DCS server \"{server['server_name']}\", please wait ...")
                    # set maintenance flag to prevent auto-starts of this server
                    server['maintenance'] = True
                    server['restart_pending'] = True
                    await self.teardown_dcs(server, config, ctx.message.author)
                    await ctx.send(f"DCS server \"{server['server_name']}\" shut down.")
                    del server['restart_pending']
            else:
                await ctx.send(f"DCS server \"{server['server_name']}\" is already shut down.")
            if 'extensions' in config:
                for ext in await self.teardown_extensions(server, config, ctx.message.author):
                    await ctx.send(f"{ext} shut down for server \"{server['server_name']}\".")

    @commands.command(description='Clears the servers maintenance flag')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def clear(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if 'maintenance' in server:
                del server['maintenance']
                await ctx.send(f"Maintenance mode cleared for server {server['server_name']}.\n"
                               f"The {string.capwords(self.plugin_name)} will take over the state handling now.")
                await self.bot.audit("cleared maintenance flag", user=ctx.message.author, server=server)
            else:
                await ctx.send(f"Server {server['server_name']} is not in maintenance mode.")

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
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
                await ctx.send('You need to stop / shutdown the server to change the mission preset.')
                return
            config = self.get_config(server)
            presets = list(config['presets'].keys())
            n = await utils.selection_list(self, ctx, presets, self.format_presets)
            if n < 0:
                return
            self.change_mizfile(server, config, presets[n])
            await ctx.send('Preset changed.')

    @commands.command(description='Create preset from running mission', usage='<name>')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def add_preset(self, ctx, *args):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] not in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                await ctx.send(f"Server {server['server_name']} not running.")
                return
            name = ' '.join(args)
            miz = MizFile(server['filename'])
            if 'presets' not in self.locals['configs'][0]:
                self.locals['configs'][0]['presets'] = dict()
            if name in self.locals['configs'][0]['presets'] and \
                    not await utils.yn_question(self, ctx, f'Do you want to overwrite the existing preset "{name}"?'):
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
                    "qnh": miz.qnh
                }
            }
            with open(f'config/{self.plugin_name}.json', 'w', encoding='utf-8') as file:
                json.dump(self.locals, file, indent=2)
            await ctx.send(f'Preset "{name}" added.')

    @commands.command(description='Reset a mission')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def reset(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if server['status'] not in [Status.STOPPED, Status.SHUTDOWN]:
                await ctx.send('You need to stop / shutdown the server to reset the mission.')
                return
            config = self.get_config(server)
            if 'reset' not in config:
                await ctx.send(f"No \"reset\" parameter found for server {server['server_name']}.")
                return
            reset = config['reset']
            if isinstance(reset, list):
                for cmd in reset:
                    self.eventlistener.run(server, cmd)
            elif isinstance(reset, str):
                self.eventlistener.run(server, reset)
            else:
                await ctx.send('Incorrect format of "reset" parameter in scheduler.json')
            await ctx.send('Mission reset.')


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(Scheduler(bot, SchedulerListener))
