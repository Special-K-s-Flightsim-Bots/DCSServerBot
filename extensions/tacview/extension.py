import aiofiles
import asyncio
import os
import re
import shutil
import sys

from core import Extension, utils, ServiceRegistry, Server, get_translation, InstallException
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import Optional, Any

_ = get_translation(__name__.split('.')[1])

TACVIEW_DEFAULT_DIR = os.path.normpath(os.path.expandvars(os.path.join('%USERPROFILE%', 'Documents', 'Tacview')))
TACVIEW_EXPORT_LINE = "local Tacviewlfs=require('lfs');dofile(Tacviewlfs.writedir()..'Scripts/TacviewGameExport.lua')\n"
TACVIEW_PATTERN_MATCH = r'Successfully saved \[(?P<filename>.*?)\]'

rtt_ports: dict[int, str] = dict()
rcp_ports: dict[int, str] = dict()

__all__ = [
    "Tacview",
    "TACVIEW_DEFAULT_DIR"
]


class Tacview(Extension):

    CONFIG_DICT = {
        "tacviewRealTimeTelemetryPort": {
            "type": int,
            "label": _("Tacview Port"),
            "placeholder": _("Unique port number for Tacview"),
            "required": True
        },
        "tacviewRealTimeTelemetryPassword": {
            "type": str,
            "label": _("Tacview Password"),
            "placeholder": _("Password for Tacview"),
        },
        "tacviewRemoteControlPort": {
            "type": int,
            "label": _("Remote Control Port"),
            "placeholder": _("Unique port number for remote control"),
        },
        "tacviewRemoteControlPassword": {
            "type": str,
            "label": _("Remote Control Password"),
            "placeholder": _("Password for remote control"),
        },
        "tacviewPlaybackDelay": {
            "type": int,
            "label": _("Playback Delay"),
            "placeholder": _("Playback delay time"),
            "required": True
        }
    }

    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.log_pos = -1
        self.exp = re.compile(TACVIEW_PATTERN_MATCH)
        self._inst_path = None
        self.stop_event = asyncio.Event()
        self.stopped = asyncio.Event()

    async def startup(self) -> bool:
        self.stop_event.clear()
        self.stopped.clear()
        # noinspection PyAsyncCall
        asyncio.create_task(self.check_log())
        return await super().startup()

    async def _shutdown(self):
        await self.stopped.wait()
        super().shutdown()

    def shutdown(self) -> bool:
        self.loop.create_task(self._shutdown())
        self.stop_event.set()
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

        await self.update_instance(False)
        options = self.server.options['plugins']
        dirty = False
        for name, value in self.config.items():
            if not name.startswith('tacview'):
                continue
            if name == 'tacviewExportPath':
                path = os.path.normpath(os.path.expandvars(self.config.get('tacviewExportPath', TACVIEW_DEFAULT_DIR)))
                os.makedirs(path, exist_ok=True)
                dirty = self.set_option(options, name, path) or dirty
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

    def get_inst_path(self) -> str:
        if not self._inst_path:
            inst_path = os.path.join(
                os.path.expandvars(self.config.get('installation', os.path.join('%ProgramFiles(x86)%', 'Tacview'))))
            # is the installation path configured, or is it the standard windows one?
            if os.path.exists(inst_path):
                self._inst_path = inst_path
            # no, we are probably on Win32/steam
            elif sys.platform == 'win32':
                import winreg

                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", 0)
                path = winreg.QueryValueEx(key, 'SteamPath')[0]
                inst_path = os.path.join(path, 'steamapps', 'common', 'Tacview')
                if os.path.exists(inst_path):
                    self._inst_path = inst_path
                else:
                    raise InstallException(f"Can't find the {self.name} installation dir, "
                                           "please specify it manually in your nodes.yaml!")
            else:
                raise InstallException(f"Can't find the {self.name} installation dir, "
                                       "please specify it manually in your nodes.yaml!")

        return self._inst_path

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
        if not super().is_installed():
            return False

        base_dir = self.server.instance.home
        dll_installed = os.path.exists(os.path.join(base_dir, r'Mods\tech\Tacview\bin\tacview.dll'))
        exports_installed = (os.path.exists(os.path.join(base_dir, r'Scripts\TacviewGameExport.lua')) &
                             os.path.exists(os.path.join(base_dir, r'Scripts\Export.lua')))
        if exports_installed:
            with open(os.path.join(base_dir, 'Scripts', 'Export.lua'), mode='r', encoding='utf-8') as file:
                for line in file.readlines():
                    # best case we find the default line Tacview put in the Export.lua
                    if line == TACVIEW_EXPORT_LINE:
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

    async def check_log(self):
        try:
            logfile = os.path.expandvars(
                self.config.get('log', os.path.join(self.server.instance.home, 'Logs', 'dcs.log'))
            )
            while not self.stop_event.is_set():
                if not os.path.exists(logfile):
                    self.log_pos = 0
                    await asyncio.sleep(1)
                    continue
                async with aiofiles.open(logfile, mode='r', encoding='utf-8', errors='ignore') as file:
                    max_pos = os.fstat(file.fileno()).st_size
                    # no new data has been added to the log, so continue
                    if max_pos == self.log_pos:
                        await asyncio.sleep(1)
                        continue
                    # if we were started with an existing logfile, seek to the file end, else seek to the last position
                    if self.log_pos == -1:
                        await file.seek(0, 2)
                        self.log_pos = max_pos
                    else:
                        # if the log was rotated, reset the pointer to 0
                        if max_pos < self.log_pos:
                            self.log_pos = 0
                        await file.seek(self.log_pos, 0)
                    lines = await file.readlines()
                    for line in lines:
                        if 'End of flight data recorder.' in line or '=== Log closed.' in line:
                            self.log_pos = -1
                            return
                        match = self.exp.search(line)
                        if match:
                            # noinspection PyAsyncCall
                            asyncio.create_task(self.send_tacview_file(match.group('filename')))
                    self.log_pos = max_pos
        except Exception as ex:
            self.log.exception(ex)
        finally:
            self.stopped.set()

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
                                "content": _("Tacview file for server {}").format(self.server.name),
                                "server": self.server.name,
                                "filename": filename
                            }
                        })
                    except AttributeError:
                        self.log.warning(f"Can't upload TACVIEW file {filename}, "
                                         f"channel {target[4:-1]} incorrect!")
                    except Exception as ex:
                        self.log.warning(f"Can't upload TACVIEW file {filename}: {ex}!")
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

    def get_inst_version(self) -> Optional[str]:
        if not self.get_inst_path():
            self.log.error("You need to specify an installation path for Tacview!")
            return None
        path = os.path.join(self.get_inst_path(), 'DCS', 'Mods', 'tech', 'Tacview', 'bin')
        return utils.get_windows_version(os.path.join(path, 'tacview.dll'))

    async def install(self) -> bool:
        if not self.get_inst_path():
            self.log.error("You need to specify an installation path for Tacview!")
            return False
        from_path = os.path.join(self.get_inst_path(), 'DCS')
        shutil.copytree(from_path, self.server.instance.home, dirs_exist_ok=True)
        export_file = os.path.join(self.server.instance.home, 'Scripts', 'Export.lua')
        async with aiofiles.open(export_file, mode='r', encoding='utf-8') as infile:
            lines = await infile.readlines()
        if TACVIEW_EXPORT_LINE not in lines:
            lines.append(TACVIEW_EXPORT_LINE)
            async with aiofiles.open(export_file, mode='w', encoding='utf-8') as outfile:
                await outfile.writelines(lines)
        self.log.info(f"  => {self.name} {self.version} installed into instance {self.server.instance.name}.")
        return True

    async def uninstall(self) -> bool:
        if not self.get_inst_path():
            self.log.error("You need to specify an installation path for Tacview!")
            return False
        version = self.version
        from_path = os.path.join(self.get_inst_path(), 'DCS')
        for root, dirs, files in os.walk(from_path, topdown=False):
            for name in files:
                file_x = os.path.join(root, name)
                file_y = file_x.replace(from_path, self.server.instance.home)
                if os.path.exists(file_y):
                    os.remove(file_y)
            for name in dirs:
                dir_x = os.path.join(root, name)
                dir_y = dir_x.replace(from_path, self.server.instance.home)
                if os.path.exists(dir_y):
                    try:
                        os.rmdir(dir_y)  # only removes empty directories
                    except OSError:
                        pass  # directory not empty
        self.log.info(f"  => {self.name} {version} uninstalled from instance {self.server.instance.name}.")

    async def update_instance(self, force: bool) -> bool:
        version = self.get_inst_version()
        if version != self.version:
            if force or self.config.get('autoupdate', False):
                if not await self.uninstall():
                    return False
                if not await self.install():
                    return False
                await ServiceRegistry.get(ServiceBus).send_to_node({
                    "command": "rpc",
                    "service": BotService.__name__,
                    "method": "audit",
                    "params": {
                        "message": _("Tacview updated to version {version} on instance {instance}.").format(
                            ersion=version, instance=self.server.instance.name)
                    }
                })
                return True
            else:
                self.log.info(f"  => {self.name}: Instance {self.server.instance.name} is running version "
                              f"{self.version}, where {version} is available!")
        return False
