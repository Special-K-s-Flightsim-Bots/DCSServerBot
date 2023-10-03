import asyncio
import os
import re
import shutil
import sys
import time
if sys.platform == 'win32':
    import win32api

from core import Extension, report, utils, ServiceRegistry, Server
from discord.ext import tasks
from services import ServiceBus
from typing import Optional, cast

TACVIEW_DEFAULT_DIR = os.path.normpath(os.path.expandvars(os.path.join('%USERPROFILE%', 'Documents', 'Tacview')))
rtt_ports: dict[int, str] = dict()
rcp_ports: dict[int, str] = dict()


class Tacview(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus: ServiceBus = cast(ServiceBus, ServiceRegistry.get('ServiceBus'))
        self.log_pos = -1
        self.exp = re.compile(r'TACVIEW.DLL \(Main\): Successfully saved (?P<filename>.*)')

    async def startup(self) -> bool:
        await super().startup()
        if self.config.get('target'):
            self.check_log.start()
        return True

    async def shutdown(self) -> bool:
        if self.locals.get('target'):
            self.check_log.cancel()
        return await super().shutdown()

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
            if options['Tacview'].get('tacviewExportPath', TACVIEW_DEFAULT_DIR) != path:
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
        path = os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\tacview.dll')
        if sys.platform == 'win32' and os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if not self.locals:
            return
        name = 'Tacview'
        if not self.locals.get('tacviewModuleEnabled', True):
            value = 'disabled'
        else:
            show_passwords = self.config.get('show_passwords', True)
            host = self.config.get('host', self.node.public_ip)
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

    @tasks.loop(hours=24.0)
    async def schedule(self):
        # check if delete_after is configured
        if 'delete_after' not in self.config:
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

    def is_installed(self) -> bool:
        global rtt_ports, rcp_ports

        base_dir = self.server.instance.home
        dll_installed = os.path.exists(os.path.join(base_dir, r'Mods\tech\Tacview\bin\tacview.dll'))
        exports_installed = (os.path.exists(os.path.join(base_dir, r'Scripts\TacviewGameExport.lua')) &
                             os.path.exists(os.path.join(base_dir, r'Scripts\Export.lua')))
        if exports_installed:
            with open(os.path.join(base_dir, r'Scripts\Export.lua'), 'r') as file:
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

    @tasks.loop(seconds=1)
    async def check_log(self):
        try:
            logfile = os.path.join(self.server.instance.home, 'Logs', 'dcs.log')
            if not os.path.exists(logfile):
                self.log_pos = 0
                return
            with open(logfile, encoding='utf-8') as file:
                # if we were started with an existing logfile, seek to the file end, else seek to the last position
                if self.log_pos == -1:
                    file.seek(0, 2)
                else:
                    file.seek(self.log_pos, 0)
                for line in file.readlines():
                    match = self.exp.search(line)
                    if match:
                        await self.send_tacview_file(match.group('filename')[1:-1])
                self.log_pos = file.tell()
        except Exception as ex:
            self.log.exception(ex)

    async def send_tacview_file(self, filename: str):
        # wait 60s for the file to appear
        for i in range(0, 60):
            if os.path.exists(filename):
                target = self.config['target']
                if target.startswith('<'):
                    if os.path.getsize(filename) > 25 * 1024 * 1024:
                        self.log.warning(f"Can't upload, TACVIEW file {filename} too large!")
                        return
                    try:
                        await self.bus.send_to_node_sync({
                            "command": "rpc",
                            "service": "Bot",
                            "method": "send_message",
                            "params": {
                                "channel": int(target[4:-1]),
                                "content": f"Tacview file for server {self.server.name}",
                                "server": self.server.name,
                                "filename": filename
                            }
                        })
                    except AttributeError:
                        self.log.warning(f"Can't upload TACVIEW file {filename}, "
                                         f"channel {target[4:-1]} incorrect!")
                    except Exception as ex:
                        self.log.warning(f"Can't upload, TACVIEW file {filename}: {ex}!")
                    return
                else:
                    try:
                        shutil.copy2(filename, os.path.expandvars(utils.format_string(target, server=self.server)))
                    except Exception as ex:
                        self.log.warning(f"Can't upload TACVIEW file {filename} to {target}: ", exc_info=ex)
                    return
            await asyncio.sleep(1)
        else:
            self.log.warning(f"Can't find TACVIEW file {filename} after 1 min of waiting.")
            return
