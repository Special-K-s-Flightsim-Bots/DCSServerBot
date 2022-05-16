import asyncio
import discord
import json
import psutil
import string
from core import Plugin, DCSServerBot, PluginRequiredError, utils, TEventListener, Status, MizFile, Autoexec
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from os import path
from typing import Type, Optional, List
from .listener import SchedulerListener


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_state.start()

    def cog_unload(self):
        self.check_state.cancel()
        super().cog_unload()

    def install(self):
        super().install()
        for _, installation in utils.findDCSInstallations():
            cfg = Autoexec(bot=self.bot, installation=installation)
            if cfg.crash_report_mode is None:
                self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                cfg.crash_report_mode = 'silent'
            elif cfg.crash_report_mode != 'silent':
                self.log.warning('  => crash_report_mode is NOT silent! The Scheduler will not work properly on DCS '
                                 'crashes.')

    # TODO: remove in a later version
    def migrate(self, filename: str) -> dict:
        # check all server configurations for possible restart settings
        locals = {
            'configs': []
        }
        for _, installation in utils.findDCSInstallations():
            if installation in self.config:
                config = self.config[installation]
                settings = {
                    'installation': installation,
                }
                if 'RESTART_METHOD' in self.config[installation]:
                    settings['restart'] = {
                        "method": config['RESTART_METHOD']
                    }
                    if 'RESTART_LOCAL_TIMES' in config:
                        settings['restart']['local_times'] = \
                            [x.strip() for x in config['RESTART_LOCAL_TIMES'].split(',')]
                    elif 'RESTART_MISSION_TIME' in config:
                        settings['restart']['mission_time'] = int(config['RESTART_MISSION_TIME'])
                    if 'RESTART_OPTIONS' in config:
                        if 'NOT_POPULATED' in config['RESTART_OPTIONS']:
                            settings['restart']['populated'] = False
                        if 'RESTART_SERVER' in config['RESTART_OPTIONS']:
                            settings['restart']['method'] = 'restart_with_shutdown'
                    if 'RESTART_WARN_TIMES' in config:
                        settings['warn'] = {
                            "times": [int(x) for x in config['RESTART_WARN_TIMES'].split(',')]
                        }
                        if 'RESTART_WARN_TEXT' in config:
                            settings['warn']['text'] = config['RESTART_WARN_TEXT']
                if 'AUTOSTART_DCS' in self.config[installation] and \
                        self.config.getboolean(installation, 'AUTOSTART_DCS') is True:
                    settings['schedule'] = {"00:00-23:59": "YYYYYYY"}
                if 'AUTOSTART_SRS' in self.config[installation] and \
                        self.config.getboolean(installation, 'AUTOSTART_SRS') is True:
                    settings['extensions'] = ["SRS"]
                locals['configs'].append(settings)
        with open(filename, 'w') as outfile:
            json.dump(locals, outfile, indent=2)
        self.log.info(f'  => Migrated data from dcsserverbot.ini into {filename}.\n     You can remove the '
                      f'AUTOSTART and RESTART options now from your dcsserverbot.ini.\n     Please check the newly '
                      f'created {filename} for any needed amendments.')
        return locals

    def read_locals(self):
        filename = f'./config/{self.plugin_name}.json'
        if not path.exists(filename):
            return self.migrate(filename)
        else:
            return super().read_locals()

    def get_config(self, server: dict) -> Optional[dict]:
        if self.plugin_name not in server:
            if 'configs' in self.locals:
                specific = default = None
                for element in self.locals['configs']:
                    if 'installation' in element or 'server_name' in element:
                        if ('installation' in element and server['installation'] == element['installation']) or \
                                ('server_name' in element and server['server_name'] == element['server_name']):
                            specific = element
                    else:
                        default = element
                if default and not specific:
                    server[self.plugin_name] = default
                elif specific and not default:
                    server[self.plugin_name] = specific
                elif default and specific:
                    merged = default.copy()
                    # specific settings will always overwrite default settings
                    for key, value in specific.items():
                        merged[key] = value
                    server[self.plugin_name] = merged
            else:
                return None
        return server[self.plugin_name] if self.plugin_name in server else None

    def check_server_state(self, server: dict, config: dict) -> Status:
        if 'schedule' in config:
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

    async def launch_extensions(self, server: dict, config: dict):
        for extension in config['extensions']:
            if extension == 'SRS' and not utils.check_srs(self, server):
                self.log.info(f"  => Launching DCS-SRS server \"{server['server_name']}\" by {string.capwords(self.plugin_name)} ...")
                utils.startup_srs(self, server)
                await self.bot.audit(f"{string.capwords(self.plugin_name)} started DCS-SRS server", server=server)

    async def launch(self, server: dict, config: dict):
        # change the weather in the mission if provided
        if 'restart' in config and 'settings' in config['restart']:
            if 'filename' not in server:
                server['filename'] = utils.getServerSetting(server, utils.getServerSetting(server, 'listStartIndex'))
            self.change_mizfile(server, config)
        self.log.info(f"  => Launching DCS server \"{server['server_name']}\" by "
                      f"{string.capwords(self.plugin_name)} ...")
        utils.startup_dcs(self, server)
        await self.bot.audit(f"{string.capwords(self.plugin_name)} started DCS server", server=server)
        if 'extensions' in config:
            await self.launch_extensions(server, config)

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

    async def shutdown_extensions(self, server: dict, config: dict):
        for extension in config['extensions']:
            if extension == 'SRS' and utils.check_srs(self, server):
                self.log.info(f"  => Shutting down DCS-SRS server \"{server['server_name']}\" by "
                              f"{string.capwords(self.plugin_name)} ...")
                await utils.shutdown_srs(self, server)
                await self.bot.audit(f"{string.capwords(self.plugin_name)} shut DCS-SRS server down", server=server)

    async def shutdown(self, server: dict, config: dict):
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
            else:
                self.log.info(
                    f"  => Shutting DCS server \"{server['server_name']}\" down by {string.capwords(self.plugin_name)} ...")
                await self.bot.audit(f"{string.capwords(self.plugin_name)} shut down DCS server", server=server)
            self.bot.sendtoBot({"command": "onMissionEnd", "server_name": server['server_name']})
            await asyncio.sleep(1)
            self.bot.sendtoBot({"command": "onShutdown", "server_name": server['server_name']})
            await asyncio.sleep(1)
            await utils.shutdown_dcs(self, server)
            if 'extensions' in config:
                await self.shutdown_extensions(server, config)

    def change_mizfile(self, server: dict, config: dict, preset: Optional[str] = None):
        now = datetime.now()
        value = None
        if not preset:
            for key, preset in config['restart']['settings'].items():
                if utils.is_in_timeframe(now, key):
                    value = config['presets'][preset]
                    break
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
            miz.temperature = value['temperature']
        if 'clouds' in value:
            miz.preset = value['clouds']
        if 'wind' in value:
            miz.wind = value['wind']
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
                await self.launch(server, config)
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
            if server['status'] in [Status.UNREGISTERED, Status.LOADING] or \
                    'maintenance' in server or 'restart_pending' in server:
                continue
            config = self.get_config(server)
            # if no config is defined for this server, ignore it
            if config:
                try:
                    if server['status'] == Status.RUNNING and 'affinity' in config:
                        self.check_affinity(server, config)
                    target_state = self.check_server_state(server, config)
                    if target_state == Status.RUNNING and server['status'] == Status.SHUTDOWN:
                        asyncio.create_task(self.launch(server, config))
                    elif target_state == Status.SHUTDOWN and server['status'] in [Status.STOPPED, Status.RUNNING, Status.PAUSED]:
                        asyncio.create_task(self.shutdown(server, config))
                    elif server['status'] in [Status.RUNNING, Status.PAUSED]:
                        await self.check_mission_state(server, config)
                except Exception as ex:
                    self.log.warning("Exception in check_state(): " + str(ex))

    @check_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

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


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(Scheduler(bot, SchedulerListener))
