import aiofiles
import asyncio
import os
import re
import shutil

from core import Extension, utils, ServiceRegistry, Server
from discord.ext import tasks
from services import ServiceBus, BotService
from typing import Optional, Any

TACVIEW_DEFAULT_DIR = os.path.normpath(os.path.expandvars(os.path.join('%USERPROFILE%', 'Documents', 'Tacview')))
rtt_ports: dict[int, str] = dict()
rcp_ports: dict[int, str] = dict()


class Tacview(Extension):

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.log_pos = -1
        self.exp = re.compile(r'Successfully saved \[(?P<filename>.*?)\]')

    async def startup(self) -> bool:
        await super().startup()
        if self.config.get('target') and not self.check_log.is_running():
            self.check_log.start()
        return True

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

    def set_option(self, options: dict, name: str, value: Any, default: Optional[Any] = None) -> bool:
        if options['Tacview'].get(name, default) != value:
            options['Tacview'][name] = value
            self.log.info(f'  => {self.server.name}: Setting ["{name}"] = {value}')
            return True
        return False

    async def prepare(self) -> bool:
        global rtt_ports, rcp_ports

        options = self.server.options['plugins']
        dirty = False
        for name, value in self.config.items():
            if not name.startswith('tacview'):
                continue
            if name == 'tacviewExportPath':
                path = os.path.normpath(os.path.expandvars(self.config['tacviewExportPath']))
                os.makedirs(path, exist_ok=True)
                dirty = self.set_option(options, name, path, TACVIEW_DEFAULT_DIR) or dirty
            # Unbelievable but true. Tacview can only work with strings as ports.
            elif name in ['tacviewRealTimeTelemetryPort', 'tacviewRemoteControlPort']:
                dirty = self.set_option(options, name, str(value)) or dirty
            else:
                dirty = self.set_option(options, name, value) or dirty

        if not options['Tacview'].get('tacviewPlaybackDelay', 0):
            self.log.warning(
                f'  => {self.server.name}: tacviewPlaybackDelay is not set, you might see performance issues!')
        if dirty:
            self.server.options['plugins'] = options
            self.locals = options['Tacview']
        rtt_port = int(self.locals.get('tacviewRealTimeTelemetryPort', 42674))
        if rtt_ports.get(rtt_port, self.server.name) != self.server.name:
            self.log.error(f"  =>  {self.server.name}: tacviewRealTimeTelemetryPort {rtt_port} already in use by "
                           f"server {rtt_ports[rtt_port]}!")
            return False
        rtt_ports[rtt_port] = self.server.name
        rcp_port = int(self.locals.get('tacviewRemoteControlPort', 42675))
        if rcp_ports.get(rcp_port, self.server.name) != self.server.name:
            self.log.error(f"  =>  {self.server.name}: tacviewRemoteControlPort {rcp_port} already in use by "
                           f"server {rcp_ports[rcp_port]}!")
            return False
        rcp_ports[rcp_port] = self.server.name
        return True

    @property
    def version(self) -> str:
        return utils.get_windows_version(os.path.join(self.server.instance.home, r'Mods\tech\Tacview\bin\tacview.dll'))

    async def render(self, param: Optional[dict] = None) -> dict:
        if not self.locals:
            return {}
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
            if int(self.locals.get('tacviewPlaybackDelay', 0)) > 0:
                value += f"Delay: {utils.format_time(self.locals['tacviewPlaybackDelay'])}"
            if len(value) == 0:
                value = 'enabled'
        return {
            "name": name,
            "version": self.version,
            "value": value
        }

    def is_installed(self) -> bool:
        base_dir = self.server.instance.home
        dll_installed = os.path.exists(os.path.join(base_dir, r'Mods\tech\Tacview\bin\tacview.dll'))
        exports_installed = (os.path.exists(os.path.join(base_dir, r'Scripts\TacviewGameExport.lua')) &
                             os.path.exists(os.path.join(base_dir, r'Scripts\Export.lua')))
        if exports_installed:
            with open(os.path.join(base_dir, r'Scripts\Export.lua'), mode='r', encoding='utf-8') as file:
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
        return True

    @tasks.loop(seconds=1)
    async def check_log(self):
        try:
            logfile = os.path.expandvars(self.config.get('log',
                                                         os.path.join(self.server.instance.home, 'Logs', 'dcs.log')))
            if not os.path.exists(logfile):
                self.log_pos = 0
                return
            async with aiofiles.open(logfile, mode='r', encoding='utf-8', errors='ignore') as file:
                # if we were started with an existing logfile, seek to the file end, else seek to the last position
                if self.log_pos == -1:
                    await file.seek(0, 2)
                else:
                    await file.seek(self.log_pos, 0)
                lines = await file.readlines()
                for line in lines:
                    if 'End of flight data recorder.' in line:
                        self.check_log.cancel()
                        self.log_pos = -1
                        return
                    match = self.exp.search(line)
                    if match:
                        await self.send_tacview_file(match.group('filename'))
                self.log_pos = await file.tell()
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
                            "service": BotService.__name__,
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
                    except Exception:
                        self.log.warning(f"Can't upload TACVIEW file {filename} to {target}: ", exc_info=True)
                    return
            await asyncio.sleep(1)
        else:
            self.log.warning(f"Can't find TACVIEW file {filename} after 1 min of waiting.")
            return
