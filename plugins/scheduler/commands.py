import json
import string

from core import Plugin, DCSServerBot, PluginRequiredError, utils, TEventListener, Status
from datetime import datetime
from discord.ext import tasks, commands
from os import path
from typing import Type, Optional
from .listener import SchedulerEventListener


class Scheduler(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener)
        self.check_server_state.start()

    def cog_unload(self):
        self.check_server_state.cancel()
        super().cog_unload()

    def read_locals(self):
        filename = f'./config/{self.plugin}.json'
        if not path.exists(filename):
            # check all server configurations for possible restart settings
            locals = {
                'configs': []
            }
            for installation in utils.findDCSInstallations():
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
                            else:
                                settings['restart']['populated'] = True
                            if 'RESTART_SERVER' in config['RESTART_OPTIONS']:
                                settings['restart']['shutdown'] = True
                            else:
                                settings['restart']['shutdown'] = False
                        if 'RESTART_WARN_TIMES' in config:
                            settings['warn'] = {
                                "times": [int(x) for x in config['RESTART_WARN_TIMES'].split(',')]
                            }
                            if 'RESTART_WARN_TEXT' in config:
                                settings['warn']['text'] = config['RESTART_WARN_TEXT']
                    if 'AUTOSTART_DCS' in self.config[installation] and \
                            self.config.getboolean(installation, 'AUTOSTART_DCS') is True:
                        settings['schedule'] = {"00-24": "YYYYYYY"}
                    if 'AUTOSTART_SRS' in self.config[installation] and \
                            self.config.getboolean(installation, 'AUTOSTART_SRS') is True:
                        settings['extensions'] = ["SRS"]
                    locals['configs'].append(settings)
            with open(filename, 'w') as outfile:
                json.dump(locals, outfile, indent=2)
            self.log.info(f'  => Migrated data from dcsserverbot.ini into {filename}.\n     You can remove the '
                          f'AUTOSTART and RESTART options now from your dcsserverbot.ini.')
            return locals
        else:
            return super().read_locals()

    def read_config(self, server: dict) -> Optional[dict]:
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
                return default
            elif specific and not default:
                return specific
            elif default and specific:
                merged = default
                # specific settings will always overwrite default settings
                for key, value in specific.items():
                    merged[key] = value
                return merged
        return None

    @tasks.loop(minutes=1.0)
    async def check_server_state(self):
        for server_name, server in self.globals.items():
            if 'maintenance' in server or server['status'] in [Status.UNKNOWN, Status.LOADING]:
                continue
            if self.plugin not in server:
                config = self.read_config(server)
            else:
                config = server[self.plugin]
            if not config:
                continue
            if 'schedule' in config:
                now = datetime.now()
                hour = now.hour
                weekday = now.weekday()
                for period, daystate in config['schedule'].items():
                    start = int(period[:2])
                    end = int(period[-2:])
                    if hour in range(start, end):
                        state = daystate[weekday]
                        installation = server['installation']
                        # check, if the server should be running
                        if state.upper() == 'Y' and server['status'] not in [Status.RUNNING, Status.PAUSED]:
                            self.log.info(f'  => Launching DCS server "{server_name}" by {string.capwords(self.plugin)} ...')
                            utils.start_dcs(self, installation)
                            server['status'] = Status.LOADING
                            if 'SRS' in config['extensions'] and not utils.check_srs(self, installation):
                                self.log.info(f'  => Launching DCS-SRS server "{server_name}" by {string.capwords(self.plugin)} ...')
                                utils.start_srs(self, installation)
                        # check, if the server should be stopped
                        elif state.upper() == 'N' and server['status'] in [Status.RUNNING, Status.PAUSED]:
                            self.log.info(f'  => Stopping DCS server "{server_name}" by {string.capwords(self.plugin)} ...')
                            utils.stop_dcs(self, server)
                            self.bot.sendtoDCS(server, {'command': 'shutdown'})
                            if 'SRS' in config['extensions'] and utils.check_srs(self, installation):
                                self.log.info(f'  => Stopping DCS-SRS server "{server_name}" by {string.capwords(self.plugin)} ...')
                                utils.stop_srs(self, installation)
                        # check, if another mission should be loaded
                        elif state.isnumeric():
                            self.log.warning('Scheduled mission change is not implemented yet.')

    @check_server_state.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @commands.command(description='Clears the servers maintenance flag and lets the scheduler handle the state')
    @utils.has_role('DCS Admin')
    @commands.guild_only()
    async def clear(self, ctx):
        server = await utils.get_server(self, ctx)
        if server:
            if 'maintenance' in server:
                del server['maintenance']
                await ctx.send(f"Maintenance mode cleared for server {server['server_name']}.\n"
                               f"The {string.capwords(self.plugin)} will take over the state handling now.")
            else:
                await ctx.send(f"Server {server['server_name']} is not in maintenance mode.")


def setup(bot: DCSServerBot):
    if 'mission' not in bot.plugins:
        raise PluginRequiredError('mission')
    bot.add_cog(Scheduler(bot, SchedulerEventListener))
