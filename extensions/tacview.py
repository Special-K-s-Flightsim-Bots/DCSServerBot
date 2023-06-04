import asyncio
import discord
import os
import re
import time
import win32api
from collections import deque
from core import Extension, report, Server, utils
from datetime import datetime, timedelta
from typing import Optional


TACVIEW_DEFAULT_DIR = os.path.normpath(os.path.expandvars(r"%USERPROFILE%\Documents\Tacview"))
rtt_ports: dict[int, str] = dict()
rcp_ports: dict[int, str] = dict()


class Tacview(Extension):

    def load_config(self) -> Optional[dict]:
        if self.server.options['plugins']:
            options = self.server.options['plugins']
        else:
            options = {}
        if 'Tacview' not in options:
            options['Tacview'] = {
                "tacviewAutoDiscardFlights": 10,
                "tacviewDebugMode": 0,
                "tacviewExportPath": "",
                "tacviewFlightDataRecordingEnabled": True,
                "tacviewModuleEnabled": True,
                "tacviewMultiplayerFlightsAsClient": 2,
                "tacviewMultiplayerFlightsAsHost": 2,
                "tacviewRealTimeTelemetryEnabled": True,
                "tacviewRealTimeTelemetryPassword": "",
                "tacviewRealTimeTelemetryPort": "42674",
                "tacviewRemoteControlEnabled": False,
                "tacviewRemoteControlPassword": "",
                "tacviewRemoteControlPort": "42675",
                "tacviewSinglePlayerFlights": 2,
                "tacviewTerrainExport": 0
            }
            self.server.options['plugins'] = options
        return options['Tacview']

    async def prepare(self) -> bool:
        dirty = False
        options = self.server.options['plugins']
        if 'tacviewExportPath' in self.config:
            path = os.path.normpath(os.path.expandvars(self.config['tacviewExportPath']))
            if path != TACVIEW_DEFAULT_DIR and ('tacviewExportPath' not in options['Tacview'] or
                                                os.path.normpath(options['Tacview']['tacviewExportPath']) != path):
                options['Tacview']['tacviewExportPath'] = path
                dirty = True
                if not os.path.exists(path):
                    os.makedirs(path)
                self.log.info(f'  => {self.server.name}: Setting ["tacviewExportPath"] = "{path}".')
        for param in ['tacviewRealTimeTelemetryPort', 'tacviewRemoteControlPort']:
            if param in self.config:
                if param not in options['Tacview'] or int(options['Tacview'][param]) != int(self.config[param]):
                    options['Tacview'][param] = str(self.config[param])
                    dirty = True
        for param in ['tacviewRealTimeTelemetryPassword', 'tacviewRemoteControlPassword']:
            if param in self.config:
                if param not in options['Tacview'] or options['Tacview'][param] != self.config[param]:
                    options['Tacview'][param] = self.config[param]
                    dirty = True
        for param in ['tacviewPlaybackDelay']:
            if param in self.config:
                if param not in options['Tacview'] or int(options['Tacview'][param]) != int(self.config[param]):
                    options['Tacview'][param] = int(self.config[param])
                    dirty = True
        if 'tacviewPlaybackDelay' not in options['Tacview'] or not options['Tacview']['tacviewPlaybackDelay']:
            self.log.warning(f'  => {self.server.name}: tacviewPlaybackDelay is not set, you might see '
                             f'performance issues!')
        if dirty:
            self.server.options['plugins'] = options
        return True

    @property
    def version(self) -> str:
        installation = self.server.installation
        path = os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\tech\Tacview\bin\tacview.dll'
        if os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        name = 'Tacview'
        if not self.locals.get('tacviewModuleEnabled', True):
            value = 'disabled'
        else:
            show_passwords = self.config.get('show_passwords', True)
            host = self.config.get('host', self.bot.external_ip)
            value = ''
            if self.locals.get('tacviewRealTimeTelemetryEnabled', True):
                value += f"{host}:{self.locals.get('tacviewRealTimeTelemetryPort', 42674)}\n"
                if show_passwords and self.locals.get('tacviewRealTimeTelemetryPassword'):
                    value += f"Password: {self.locals['tacviewRealTimeTelemetryPassword']}\n"
            if self.locals.get('tacviewRemoteControlEnabled', False):
                value += f"**Remote Ctrl [{self.locals.get('tacviewRemoteControlPort', 42675)}]**\n"
                if show_passwords and self.locals.get('tacviewRemoteControlPassword'):
                    value += f"Password: {self.locals['tacviewRemoteControlPassword']}\n"
            if self.locals.get('tacviewPlaybackDelay', 0) > 0:
                value += f"Delay: {utils.format_time(self.locals['tacviewPlaybackDelay'])}"
            if len(value) == 0:
                value = 'enabled'
        embed.add_field(name=name, value=value)

    async def schedule(self):
        # check if autodelete is configured
        if 'delete_after' not in self.config:
            return
        # only run once a day
        if self.lastrun > (datetime.now() - timedelta(days=1)):
            return
        now = time.time()
        path = self.server.options['plugins']['Tacview'].get('tacviewExportPath')
        if not path:
            path = TACVIEW_DEFAULT_DIR
        if not os.path.exists(path):
            return
        for f in [os.path.join(path, x) for x in os.listdir(path)]:
            if os.stat(f).st_mtime < (now - self.config['delete_after'] * 86400):
                if os.path.isfile(f):
                    os.remove(f)
        self.lastrun = datetime.now()

    def is_installed(self) -> bool:
        global rtt_ports, rcp_ports

        dll_installed = os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                                       r'\Mods\tech\Tacview\bin\tacview.dll')
        exports_installed = os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                                           r'\Scripts\TacviewGameExport.lua') & \
                            os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                                           r'\Scripts\Export.lua')
        if exports_installed:
            with open(
                    os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) + r'\Scripts\Export.lua',
                    'r') as file:
                for line in file.readlines():
                    # best case we find the default line Tacview put in the Export.lua
                    if line.strip() == "local Tacviewlfs=require('lfs');dofile(Tacviewlfs.writedir().." \
                                       "'Scripts/TacviewGameExport.lua')":
                        break
                    # at least we found it, might still be wrong
                    elif not line.strip().startswith('--') and 'TacviewGameExport.lua'.casefold() in line.casefold():
                        break
                else:
                    exports_installed = False
        if not dll_installed or not exports_installed:
            self.log.error(f"  => {self.server.name}: Can't load extension, Tacview not correctly installed.")
            return False
        rtt_port = self.locals.get('tacviewRealTimeTelemetryPort', 42674)
        if rtt_port in rtt_ports and rtt_ports[rtt_port] != self.server.name:
            self.log.error(f"  =>  tacviewRealTimeTelemetryPort {rtt_port} already in use by "
                           f"server {rtt_ports[rtt_port]}!")
        else:
            rtt_ports[rtt_port] = self.server.name
        rcp_port = self.locals.get('tacviewRemoteControlPort', 42675)
        if rcp_port in rcp_ports and rcp_ports[rcp_port] != self.server.name:
            self.log.error(f"  =>  tacviewRemoteControlPort {rcp_port} already in use by "
                           f"server {rcp_ports[rcp_port]}!")
        else:
            rcp_ports[rcp_port] = self.server.name
        return True

    async def shutdown(self, data: dict):
        if 'channel' not in self.config:
            return
        server: Server = self.bot.servers[data['server_name']]
        log = self.locals.get('log',
                              os.path.expandvars(self.bot.config[server.installation]['DCS_HOME']) + '/Logs/dcs.log')
        exp = re.compile(r'Successfully saved \[(?P<filename>.*)\]')
        filename = None
        lines = deque(open(log, encoding='utf-8'), 50)
        for line in lines:
            match = exp.search(line)
            if match:
                filename = match.group('filename')
                break
        else:
            self.log.info("Can't find TACVIEW file to be sent. Was the server even running?")
            self.log.debug('First line to check: ' + lines[0])
            self.log.debug('Last line to check: ' + lines[-1])
        if filename:
            for i in range(0, 60):
                if os.path.exists(filename):
                    channel = self.bot.get_channel(self.config['channel'])
                    try:
                        await channel.send(file=discord.File(filename))
                    except discord.HTTPException:
                        self.log.warning(f"Can't upload, TACVIEW file {filename} too large!")
                    break
                await asyncio.sleep(1)
            else:
                self.log.warning(f"Can't find TACVIEW file {filename} after 1 min of waiting.")
        return True
