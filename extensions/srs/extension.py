import aiofiles
import aiohttp
import asyncio
import atexit
import certifi
import discord
import json
import logging
import os
import psutil
import shutil
import subprocess
import ssl
import sys
import tempfile
import time
import zipfile

from configparser import RawConfigParser
from contextlib import suppress
from core import (utils, Autoexec, get_translation, InstallException, Server, ServerMaintenanceManager, PortType, Port,
                  ProcessManager, InstallableExtension)
from io import BytesIO
from json import JSONDecodeError
from packaging.version import parse
from threading import Thread
from typing_extensions import override
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_ = get_translation(__name__.split('.')[1])

SRS_GITHUB_URL = "https://api.github.com/repos/ciribob/DCS-SimpleRadioStandalone/releases/{version}"
SRS_BETA_URL = "https://api.github.com/repos/ciribob/DCS-SimpleRadioStandalone/releases"
SRS_DOWNLOAD_URL = "https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/download/{version}"

__all__ = [
    "SRS"
]


class SRS(InstallableExtension, FileSystemEventHandler):
    _ports: dict[int, str] = {}
    _http_ports: dict[int, str] = {}
    _export_ports: dict[int, str] = {}
    _lock = asyncio.Lock()

    NODE_CONFIG_DICT = {
        "installation": {
            "type": str,
            "label": _("Installation Path"),
            "placeholder": _("Path to SRS installation"),
            "required": True,
            "default": r"C:\Program Files\DCS-SimpleRadio-Standalone"
        },
        "beta": {
            "type": bool,
            "label": _("Use Beta"),
            "default": False
        },
        "autoupdate": {
            "type": bool,
            "label": _("Auto Update"),
            "default": True
        }
    }

    CONFIG_DICT = {
        "port": {
            "type": int,
            "label": _("SRS Port"),
            "placeholder": _("Unique port number for SRS"),
            "required": True,
            "default": 5002
        },
        "blue_password": {
            "type": str,
            "label": _("Blue Password"),
            "placeholder": _("Password for blue GCI"),
            "required": False,
            "default": "blue"
          },
        "red_password": {
            "type": str,
            "label": _("Red Password"),
            "placeholder": _("Password for red GCI"),
            "required": False,
            "default": "red"
        },
        "gui_server": {
            "type": bool,
            "label": _("GUI Server"),
            "default": False,
            "required": True
        },
        "autoconnect": {
            "type": bool,
            "label": _("Autoconnect"),
            "default": True,
            "required": True
        }
    }

    def __init__(self, server: Server, config: dict):
        self.cfg = RawConfigParser()
        self.cfg.optionxform = str
        self.process: psutil.Process | None = None
        self.observer: Observer | None = None
        self.first_run = True
        self._inst_path: str | None = None
        self.exe_name = None
        self.clients: dict[str, dict] = {}
        self.client_names: dict[str, str] = {}
        self._missing_loops: dict[str, int] = {}  # consecutive "not in export" counts per ClientGuid
        self._config_path: str | None = None
        super().__init__(server, config)
        self.disconnect_grace_loops: int = int(self.config.get('disconnect_grace_loops', 3))

    def get_config_path(self) -> str:
        if not self._config_path:
            config_path = os.path.expandvars(utils.format_string(
                self.config.get('config', os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')),
                server=self.server,
                instance=self.server.instance,
                node=self.server.node
            ))
            if not os.path.exists(config_path):
                base_config = os.path.join(os.path.dirname(self.get_exe_path()), 'server.cfg')
                if os.path.exists(base_config):
                    shutil.copy2(base_config, config_path)
                    self.log.info(f"  => {self.name}: Copying {base_config} to {config_path} ...")
                else:
                    self.log.warning(f"  => {self.name}: No {config_path} found, SRS running with defaults.")
            self._config_path = config_path
        return self._config_path

    @override
    def load_config(self) -> dict:
        cfg_file = self.get_config_path()
        if not os.path.exists(cfg_file):
            self.log.warning(f"  => {self.name}: Config file {cfg_file} not found!")
            return {}
        with open(cfg_file, 'rb') as f:
            raw_bytes = f.read()

        # UTF8-BOM handling (due to SRS change)
        BOM = b'\xef\xbb\xbf'
        if raw_bytes.startswith(BOM):
            cleaned_bytes = raw_bytes[len(BOM):]
        else:
            cleaned_bytes = raw_bytes

        clean_content_string = cleaned_bytes.decode('utf-8')
        self.cfg.read_string(clean_content_string)
        return {
            s: {_name: Autoexec.parse(_value) for _name, _value in self.cfg.items(s)}
            for s in self.cfg.sections()
        }

    async def enable_autoconnect(self):
        # Change DCS-SRS-AutoConnectGameGUI.lua if necessary
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        host = self.config.get('host', self.node.public_ip)
        port = self.config.get('port', self.locals['Server Settings']['SERVER_PORT'])
        original = os.path.join(self.get_inst_path(), 'Scripts', 'DCS-SRS-AutoConnectGameGUI.lua')
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
        if not os.path.exists(autoconnect) or os.path.getmtime(autoconnect) < os.path.getmtime(original):
            shutil.copy2(original, autoconnect)

        tempfile_name = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tempfile_:
                tempfile_name = tempfile_.name
                async with aiofiles.open(autoconnect, mode='r', encoding='utf-8') as infile, \
                        aiofiles.open(tempfile_name, mode='w', encoding='utf-8') as outfile:

                    lines = await infile.readlines()
                    for line in lines:
                        if line.startswith('SRSAuto.SERVER_SRS_HOST_AUTO = '):
                            line = "SRSAuto.SERVER_SRS_HOST_AUTO = false -- if set to true SRS will set the " \
                                   "SERVER_SRS_HOST for you! - Currently disabled\n"
                        elif line.startswith('SRSAuto.SERVER_SRS_PORT = '):
                            line = f'SRSAuto.SERVER_SRS_PORT = "{port}" --  SRS Server default is 5002 TCP & UDP\n'
                        elif line.startswith('SRSAuto.SERVER_SRS_HOST = '):
                            line = f'SRSAuto.SERVER_SRS_HOST = "{host}" -- overridden if SRS_HOST_AUTO is true ' \
                                   f'-- set to your PUBLIC ipv4 address\n'
                        elif line.startswith('SRSAuto.SRS_NUDGE_ENABLED') and self.config.get('srs_nudge_message'):
                            line = 'SRSAuto.SRS_NUDGE_ENABLED = true -- set to true to enable the message below\n'
                        elif line.startswith('SRSAuto.SRS_NUDGE_MESSAGE = ') and self.config.get('srs_nudge_message'):
                            line = f"SRSAuto.SRS_NUDGE_MESSAGE = \"{self.config.get('srs_nudge_message')}\"\n"

                        await outfile.write(line)

            shutil.move(tempfile_name, autoconnect)
        finally:
            if os.path.exists(tempfile_name):
                os.remove(tempfile_name)

    async def disable_autoconnect(self):
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
            os.remove(autoconnect)

    def _maybe_update_config(self, section, key, value_key):
        if value_key in self.config:
            value = self.config[value_key]
            if section not in self.cfg:
                self.cfg[section] = {}
            if not self.cfg[section].get(key) or Autoexec.parse(self.cfg[section][key]) != value:
                self.cfg.set(section, key, value)
                self.log.info(f"  => {self.server.name}: [{section}][{key}] set to {self.config[value_key]}")
                return True
        return False

    def _check_port_conflict(self, port_type: str, port: int, registry: dict[int, str]) -> bool:
        if registry.get(port, self.server.name) != self.server.name:
            self.log.error(
                f"  => {self.server.name}: {self.name} {port_type} {port} already in use by "
                f"server {registry[port]}!")
            return False
        registry[port] = self.server.name
        return True

    @override
    async def prepare(self) -> bool:
        path = self.get_config_path()
        dirty = False
        if 'client_export_file_path' not in self.config:
            self.config['client_export_file_path'] = os.path.join(os.path.dirname(path), 'clients-list.json')
        dirty |= self._maybe_update_config('Server Settings', 'SERVER_PORT', 'port')
        if 'use_upnp' not in self.config:
            self.config['use_upnp'] = self.node.locals.get('use_upnp', False)
        dirty |= self._maybe_update_config('Server Settings', 'UPNP_ENABLED', 'use_upnp')
        dirty |= self._maybe_update_config('Server Settings', 'CLIENT_EXPORT_FILE_PATH',
                                           'client_export_file_path')
        self.config['client_export_enabled'] = True
        dirty |= self._maybe_update_config('General Settings', 'CLIENT_EXPORT_ENABLED',
                                           'client_export_enabled')
        # enable SRS on spectators for slot blocking
        self.config['spectators_audio_disabled'] = False
        dirty |= self._maybe_update_config('General Settings', 'SPECTATORS_AUDIO_DISABLED',
                                           'spectators_audio_disabled')
        # new instructor mode
        if parse(self.version) >= parse('2.3.3.0'):
            dirty |= self._maybe_update_config('General Settings', 'ALLOW_INSTRUCTOR_MODE',
                                               'instructor_mode')
        # disable effects (for music plugin)
        # TODO: better alignment with the music plugin!
        dirty |= self._maybe_update_config('General Settings', 'RADIO_EFFECT_OVERRIDE',
                                           'radio_effect_override')
        dirty |= self._maybe_update_config('General Settings', 'GLOBAL_LOBBY_FREQUENCIES',
                                           'global_lobby_frequencies')
        # new HTTP server (as of SRS 2.3)
        dirty |= self._maybe_update_config('Server Settings', 'HTTP_SERVER_ENABLED',
                                           'http_server_enabled')
        dirty |= self._maybe_update_config('Server Settings', 'HTTP_SERVER_PORT',
                                           'http_server_port')

        extension = self.server.extensions.get('LotAtc')
        if extension:
            self.config['lotatc'] = True
            dirty |= self._maybe_update_config('General Settings','LOTATC_EXPORT_ENABLED','lotatc')
            dirty |= self._maybe_update_config('General Settings','LOTATC_EXPORT_IP','127.0.0.1')
            dirty |= self._maybe_update_config('General Settings','LOTATC_EXPORT_PORT',
                                               'lotatc_export_port')
            self.config['awacs'] = True

        if self.config.get('awacs', True):
            dirty |= self._maybe_update_config('General Settings','EXTERNAL_AWACS_MODE','awacs')
            dirty |= self._maybe_update_config('External AWACS Mode Settings',
                                               'EXTERNAL_AWACS_MODE_BLUE_PASSWORD','blue_password')
            dirty |= self._maybe_update_config('External AWACS Mode Settings',
                                               'EXTERNAL_AWACS_MODE_RED_PASSWORD','red_password')

        # Check IP settings
        if self.cfg['Server Settings']['SERVER_IP'] != '0.0.0.0':
            self.log.warning(f"  => {self.server.name}: SERVER_IP is not set to 0.0.0.0 in {self.get_config_path()}")

        # Check port conflicts
        port = self.config.get('port', int(self.cfg['Server Settings'].get('SERVER_PORT', '5002')))
        if not self._check_port_conflict("SERVER_PORT", port, type(self)._ports):
            return False

        # Check HTTP port
        http_server_enabled = self.config.get(
            'http_server_enabled',
            Autoexec.parse(self.cfg['Server Settings'].get('HTTP_SERVER_ENABLED', 'true'))
        )
        if http_server_enabled:
            http_port = self.config.get(
                'http_server_port',
                int(self.cfg['Server Settings'].get('HTTP_SERVER_PORT', '8080'))
            )
            if not self._check_port_conflict("HTTP_SERVER_PORT", http_port, type(self)._http_ports):
                return False
        else:
            dirty |= self._maybe_update_config('Server Settings', 'HTTP_SERVER_ENABLED', False)

        # only check LotAtc Export Port if LotAtc is there
        if self.config.get('lotatc', False):
            export_port = self.config.get('lotatc_export_port',
                                          int(self.cfg['General Settings'].get('LOTATC_EXPORT_PORT', '10712')))
            if not self._check_port_conflict("LOTATC_EXPORT_PORT", export_port, type(self)._export_ports):
                return False

        # write a new SRS config file
        if dirty:
            with open(path, mode='w', encoding='utf-8') as ini:
                self.cfg.write(ini)
            self.locals = self.load_config()

        if self.config.get('autoconnect', True):
            await self.enable_autoconnect()
            self.log.info('  => SRS autoconnect is enabled for this server.')
        else:
            self.log.info('  => SRS autoconnect is NOT enabled for this server.')
            await self.disable_autoconnect()
        if self.config.get('always_on', False):
            # no_shutdown defaults to True for always_on
            self.config['no_shutdown'] = self.config.get('no_shutdown', True)
            if not await asyncio.to_thread(self.is_running):
                asyncio.create_task(self.startup())
        return await super().prepare()

    @override
    async def startup(self, *, quiet: bool = False) -> bool:
        if self.config.get('autostart', True):
            self.log.debug(f"Launching SRS server with: \"{self.get_exe_path()}\" -cfg=\"{self.get_config_path()}\"")

            def log_output(pipe, level=logging.INFO):
                # Iterate until we get an empty byte string (EOF)
                for raw_line in iter(pipe.readline, b''):
                    # Decode the raw bytes – replace undecodable bytes if necessary
                    line = raw_line.decode('utf-8', errors='replace').rstrip()
                    # Log the decoded line
                    self.log.log(level, f"{self.name}: {line}")

            def run_subprocess() -> psutil.Process:
                if sys.platform == 'win32' and self.config.get('minimized', True):
                    import win32process
                    import win32con

                    info = subprocess.STARTUPINFO()
                    info.dwFlags |= win32process.STARTF_USESHOWWINDOW
                    info.wShowWindow = win32con.SW_SHOWMINNOACTIVE
                else:
                    info = None
                out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
                err = subprocess.PIPE if self.config.get('debug', False) else subprocess.STDOUT
                # we want the SRS logfile in our normal logs folder
                cwd = os.path.join(self.server.instance.home, 'Logs')

                proc = ProcessManager().launch_process(
                    [
                        self.get_exe_path(),
                        f"-cfg={self.get_config_path()}"
                    ],
                    cwd=cwd,
                    startupinfo=info,
                    stdout=out,
                    stderr=err,
                    close_fds=True,
                    min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
                    max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
                    quality=self.config.get('auto_affinity', {}).get('quality', 0),
                    instance=self.server.instance.name
                )

                if self.config.get('debug', False):
                    Thread(target=log_output, args=(proc.stdout,logging.DEBUG), daemon=True).start()
                    Thread(target=log_output, args=(proc.stderr,logging.ERROR), daemon=True).start()

                return proc

            try:
                async with type(self)._lock:
                    if self.is_running():
                        return True
                    self.process = await asyncio.to_thread(run_subprocess)
                    if not self.observer:
                        self.start_observer()
            except Exception as ex:
                self.log.error(f"Error during launch of {self.get_exe_path()}: {ex}")
                return False
        # Give SRS 10s to start
        for _ in range(0, 10):
            if self.is_running():
                break
            await asyncio.sleep(1)
        else:
            return False
        return await super().startup()

    @override
    def shutdown(self, *, quiet: bool = False) -> bool:
        if self.config.get('autostart', True) and not self.config.get('no_shutdown', False):
            if self.is_running():
                try:
                    super().shutdown()
                    if not self.process:
                        self.process = next(utils.find_process(self.exe_name, self.server.instance.name), None)
                    if self.process:
                        utils.terminate_process(self.process)
                        self.process = None
                        return True
                    else:
                        self.log.warning(f"  => Could not find a running SRS server process.")
                        cfg_path = self.get_config_path()
                        if self.server.instance.name not in cfg_path:
                            self.log.warning(f"  => Please move your SRS configuration to "
                                             f"{os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')}")
                except Exception as ex:
                    self.log.error(f'Error during shutdown of SRS', exc_info=ex)
                    return False
                finally:
                    if self.observer:
                        self.stop_observer()
        return True

    @override
    def on_modified(self, event: FileSystemEvent) -> None:
        path = os.path.expandvars(self.locals['Server Settings']['CLIENT_EXPORT_FILE_PATH'])
        if self.loop.is_running() and event.src_path == path:
            asyncio.run_coroutine_threadsafe(self.process_export_file(event.src_path), self.loop)

    async def process_export_file(self, path: str):
        try:
            with open(path, mode='r', encoding='utf-8') as infile:
                data = json.load(infile)

            for client in data.get('Clients', {}):
                if client['Name'] == '---' or client['RadioInfo'] is None:
                    continue

                guid = client['ClientGuid']
                self._missing_loops.pop(guid, None)  # seen this loop => not missing anymore

                target = {
                    "player_name": client['Name'],
                    "side": client['Coalition'],
                    "unit": client['RadioInfo']['unit'],
                    "unit_id": client['RadioInfo']['unitId'],
                    "radios": [(x['freq'], x['modulation']) for x in client['RadioInfo']['radios'] if int(x['freq']) > 1E6]
                }
                if guid not in self.clients:
                    self.clients[guid] = target
                    self.client_names[guid] = client['Name']
                    await self.bus.send_to_node({
                        "command": "onSRSConnect",
                        "server_name": self.server.name
                    } | target)
                else:
                    actual = self.clients[guid]
                    if actual != target:
                        self.clients[guid] = target
                        await self.bus.send_to_node({
                            "command": "onSRSUpdate",
                            "server_name": self.server.name
                        } | target)

            all_clients = set(self.clients.keys())
            active_clients = set(x['ClientGuid'] for x in data.get('Clients', []))

            # any clients disconnected? (with grace)
            missing_now = all_clients - active_clients
            for guid in missing_now:
                self._missing_loops[guid] = self._missing_loops.get(guid, 0) + 1
                if self._missing_loops[guid] < self.disconnect_grace_loops:
                    continue

                await self.bus.send_to_node({
                    "command": "onSRSDisconnect",
                    "server_name": self.server.name,
                    "player_name": self.client_names[guid]
                })
                del self.clients[guid]
                del self.client_names[guid]
                self._missing_loops.pop(guid, None)

        except (PermissionError, JSONDecodeError):
            # Happens if SRS writes the file again when we try to read it.
            # Just ignore, we get the file on the next try.
            pass
        except Exception as ex:
            self.log.exception(ex)

    def start_observer(self):
        path = self.locals['Server Settings']['CLIENT_EXPORT_FILE_PATH']
        if os.path.exists(path):
            asyncio.run_coroutine_threadsafe(self.process_export_file(path), self.loop)
            self.observer = Observer()
            self.observer.schedule(self, path=os.path.dirname(path))
            self.observer.start()
            if self.first_run:
                atexit.register(self.stop_observer)
                self.first_run = False

    def stop_observer(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=10)
            self.observer = None
            self.clients.clear()
            self.client_names.clear()
            self._missing_loops.clear()

    @override
    def is_running(self) -> bool:
        running = False
        try:
            # 1. If we have a process handle, check if it's still alive
            if self.process and self.process.is_running():
                running = True
                return True

            # 2. If no handle, try to find the process by name
            if self.exe_name:
                discovered_process = next(utils.find_process(self.exe_name, self.server.instance.name), None)
                if discovered_process and discovered_process.is_running():
                    self.process = discovered_process
                    # Only assign if it was found externally (extension didn't start it)
                    ProcessManager().assign_process(
                        self.process,
                        min_cores=self.config.get('auto_affinity', {}).get('min_cores', 1),
                        max_cores=self.config.get('auto_affinity', {}).get('max_cores', 1),
                        quality=self.config.get('auto_affinity', {}).get('quality', 0),
                        instance=self.server.instance.name
                    )
                    running = True
                    return True

            self.process = None
            server_ip = self.locals.get('Server Settings', {}).get('SERVER_IP', '127.0.0.1')
            if server_ip == '0.0.0.0':
                server_ip = '127.0.0.1'

            running = utils.is_open(server_ip, self.locals['Server Settings'].get('SERVER_PORT', 5002), timeout=2.0)
            if not running:
                self.log.debug("SRS: is NOT running")

        finally:
            if running and not self.observer:
                self.start_observer()
        return running

    def get_inst_path(self) -> str:
        if not self._inst_path:
            if self.config.get('installation'):
                self._inst_path = os.path.join(os.path.expandvars(self.config.get('installation')))
                if not os.path.exists(self._inst_path):
                    raise InstallException(
                        f"The {self.name} installation dir could not be found at {self.config.get('installation')}!")
            elif sys.platform == 'win32':
                import winreg

                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DCS-SR-Standalone", 0)
                self._inst_path = winreg.QueryValueEx(key, 'SRPathStandalone')[0]
                if not os.path.exists(self._inst_path):
                    raise InstallException(f"Can't detect the {self.name} installation dir, "
                                           "please specify it manually in your nodes.yaml!")
            else:
                self._inst_path = os.path.join(os.path.expandvars('%ProgramFiles%'), 'DCS-SimpleRadio-Standalone')
                if not os.path.exists(self._inst_path):
                    raise InstallException(f"Can't detect the {self.name} installation dir, "
                                           "please specify it manually in your nodes.yaml!")
        return self._inst_path

    def get_exe_path(self) -> str:
        if parse(self.version) >= parse('2.2.0.0'):
            if self.config.get('gui_server', False):
                self.exe_name = 'SRS-Server.exe'
                return os.path.join(self.get_inst_path(), 'Server', self.exe_name)
            else:
                os_dir = 'ServerCommandLine-Windows' if sys.platform == 'win32' else 'ServerCommandLine-Linux'
                self.exe_name = 'SRS-Server-Commandline.exe' if sys.platform == 'win32' else 'SRS-Server-Commandline'
                return os.path.join(self.get_inst_path(), os_dir, self.exe_name)
        else:
            self.exe_name = 'SR-Server.exe'
            return os.path.join(self.get_inst_path(), self.exe_name)

    def get_ext_audio_exe_path(self) -> str:
        if parse(self.server.extensions['SRS'].version) >= parse('2.2.0.0'):
            return os.path.join(self.get_inst_path(), "ExternalAudio", "DCS-SR-ExternalAudio.exe")
        else:
            return os.path.join(self.get_inst_path(), "DCS-SR-ExternalAudio.exe")

    async def play_external_audio(self, config: dict, *, file: str | None = None, text: str | None = None) -> psutil.Process:
        def run_subprocess() -> psutil.Process:
            def _log_output(p: psutil.Popen):
                for line in iter(p.stdout.readline, b''):
                    self.log.debug(line.decode('utf-8', errors='replace').rstrip())

            debug = config.get('debug', False)
            out = subprocess.PIPE if debug else subprocess.DEVNULL
            err = subprocess.PIPE if debug else subprocess.DEVNULL

            args = [
                self.get_ext_audio_exe_path(),
                "-f", str(config['frequency']),
                "-m", config['modulation'],
                "-c", str(config['coalition']),
                "-v", str(config.get('volume', 1.0)),
                "-p", str(self.locals['Server Settings']['SERVER_PORT']),
                "-n", config.get('display_name', 'DCSSB')
            ]
            if 'lat' in config:
                args.extend(["-L", str(config['lat'])])
                args.extend(["-O", str(config['lon'])])
                args.extend(["-A", str(config['alt'])])
            if file:
                args.extend(["-i", file])
            elif text:
                args.extend(["-t", '"' + text + '"'])

            if debug:
                self.log.debug(f"Running {' '.join(args)}")
            p = ProcessManager().launch_process(
                args,
                min_cores=config.get('auto_affinity', {}).get('min_cores', 1),
                max_cores=config.get('auto_affinity', {}).get('max_cores', 1),
                quality=config.get('auto_affinity', {}).get('quality', 1),
                instance=self.server.instance.name,
                stdout=out, stderr=err
            )
            if debug:
                Thread(target=_log_output, args=(p,), daemon=True).start()
            return p

        return await asyncio.to_thread(run_subprocess)

    @override
    @property
    def version(self) -> str | None:
        version = utils.get_windows_version(os.path.join(self.get_inst_path(), 'SRS-AutoUpdater.exe'))
        if not version:
            raise InstallException(f"Can't detect the {self.name} version, SRS-AutoUpdater.exe not found!")
        return version

    @override
    async def render(self, param: dict | None = None) -> dict:
        if not self.locals:
            raise NotImplementedError()

        ret = await super().render(param)
        host = self.config.get('host', self.node.public_ip)
        value = f"{host}:{self.locals['Server Settings']['SERVER_PORT']}"
        show_passwords = self.config.get('show_passwords', True)
        if show_passwords and self.locals['General Settings'].get('EXTERNAL_AWACS_MODE', False) and \
                'External AWACS Mode Settings' in self.locals:
            blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD']
            red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD']
            if blue or red:
                value += f'\n🔹 Pass: {blue}\n🔸 Pass: {red}'
        return ret | {
            "value": value
        }

    @override
    def is_available(self) -> bool:
        return os.path.exists(self.get_exe_path())

    @override
    async def get_latest_version(self) -> str | None:
        with suppress(aiohttp.ClientError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                url = SRS_BETA_URL if self.config.get('beta', False) else SRS_GITHUB_URL.format(version="latest")
                async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth,
                                       raise_for_status=True) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        data = data[0]
                    return data.get('tag_name', '').strip('v')
        return None

    async def do_update(self, version: str):
        def stop_processes():
            if parse(self.version) >= parse('2.2.0.0'):
                srs_exes = [
                    'SR-ClientRadio.exe',
                    'SRS-Server.exe',
                    'SRS-Server-Commandline.exe',
                    'DCS-SR-ExternalAudio.exe'
                ]
            else:
                srs_exes = [
                    'SR-Server.exe',
                    'DCS-SR-ExternalAudio.exe'
                ]
            for exe in srs_exes:
                for process in utils.find_process(os.path.basename(exe)):
                    if process and process.is_running():
                        process.terminate()

        # make sure the monitoring does not interfere
        async with ServerMaintenanceManager(self.node, shutdown=False):
            vc_redist = False
            installation_dir = self.get_inst_path()
            download_url = SRS_DOWNLOAD_URL.format(version=version) + '/DCS-SimpleRadioStandalone-{version}.zip'.format(version=version)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        download_url,
                        proxy=self.node.proxy,
                        proxy_auth=self.node.proxy_auth,
                        raise_for_status=True
                ) as response:
                    # stop any existing SRS process
                    if self.is_installed():
                        await asyncio.to_thread(stop_processes)

                    # unpack files
                    with zipfile.ZipFile(BytesIO(await response.content.read())) as z:
                        z.extractall(path=installation_dir)

                        # Handle VC_redist.x64.exe separately if needed
                        if 'VC_redist.x64.exe' in [os.path.basename(m) for m in z.namelist()]:
                            vc_redist = True
            if vc_redist:
                def run_subprocess():
                    self.log.info("Installing Visual Studio Redistributable ...")
                    exe_path = os.path.join(installation_dir, 'VC_redist.x64.exe')
                    try:
                        subprocess.run([exe_path, '/install', '/quiet', '/norestart'],
                                       cwd=installation_dir, check=True)
                    except subprocess.CalledProcessError as ex:
                        # Return code 1638 means "Already installed" - this is OK
                        if ex.returncode == 1638:
                            self.log.info("Visual Studio Redistributable is already installed.")
                        else:
                            raise

                await asyncio.to_thread(run_subprocess)
                os.remove(os.path.join(installation_dir, 'VC_redist.x64.exe'))

    @override
    async def update(self, version: str | None = None) -> bool:
        # make sure we're not called twice
        async with type(self)._lock:
            try:
                self.log.info(f"A new DCS-SRS update is available. Updating to version {version} ...")
                await self.do_update(version)
                self.log.info("DCS-SRS updated.")
                await self.bot.audit(f"{self.name} updated to version {version} on node {self.node.name}.")
                config = self.config.get('announce')
                if config:
                    servers = []
                    for instance in self.node.instances.values():
                        if instance.locals.get('extensions', {}).get(self.name) and instance.locals['extensions'][self.name].get('enabled', True):
                            servers.append(instance.server.display_name)
                    embed = discord.Embed(
                        colour=discord.Colour.blue(),
                        title=config.get(
                            'title', 'DCS-SRS has been updated to version {}!').format(version),
                        url=f"https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/{version}")
                    embed.set_thumbnail(url="https://raw.githubusercontent.com/ciribob/DCS-SimpleRadioStandalone/master/Scripts/DCS-SRS/Theme/icon.png")
                    embed.description = config.get('description', 'The following servers have been updated:')
                    embed.add_field(name=_('Server'),
                                    value='\n'.join([f'- {x}' for x in servers]), inline=False)
                    embed.set_footer(
                        text=config.get('footer', 'Please make sure you update your DCS-SRS client also!'))
                    params = {}
                    if 'mention' in config:
                        params['mention'] = config['mention']
                    await self.bot.send_message(channel=config['channel'], embed=embed.to_dict(), **params)

                return True

            except Exception as ex:
                self.log.error(f"DCS-SRS update failed: {ex}")
                return False

    @override
    def get_ports(self) -> dict[str, Port]:
        if self.enabled:
            rc: dict[str, Port] = {
                "SRS Port": Port(self.locals['Server Settings']['SERVER_PORT'], PortType.BOTH, public=True)
            }
            if self.locals['General Settings'].get('LOTATC_EXPORT_ENABLED', False):
                rc["LotAtc Export Port"] = Port(self.locals['General Settings'].get('LOTATC_EXPORT_PORT', 10712), PortType.UDP)
            if self.locals['Server Settings'].get('HTTP_SERVER_ENABLED', False):
                rc["HTTP Server Port"] = Port(self.locals['Server Settings'].get('HTTP_SERVER_PORT', 8080), PortType.TCP)
        else:
            rc = {}
        return rc

    @override
    def rename_server(self, old_name: str, new_name: str):
        for port, server_name in type(self)._ports.items():
            if server_name == old_name:
                type(self)._ports[port] = new_name
                break
        for port, server_name in type(self)._export_ports.items():
            if server_name == old_name:
                type(self)._export_ports[port] = new_name
                break

    @override
    def is_installed(self) -> bool:
        try:
            return os.path.exists(self.get_inst_path())
        except Exception:
            return False

    async def do_install(self, version: str) -> bool:
        download_url = SRS_DOWNLOAD_URL.format(version=version) + '/SRS-AutoUpdater.exe'
        with tempfile.TemporaryDirectory() as temp_dir:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        download_url,
                        proxy=self.node.proxy,
                        proxy_auth=self.node.proxy_auth,
                        raise_for_status=True
                ) as response:
                    async with aiofiles.open(os.path.join(temp_dir, 'SRS-AutoUpdater.exe'), 'wb') as f:
                        while True:
                            chunk = await response.content.read(1024)
                            if not chunk:
                                break
                            await f.write(chunk)

            def run_subprocess() -> bool:
                self.log.info(f"Installing DCS-SRS version {version} ...")
                try:
                    install_dir = os.path.expandvars(
                        self.config.get('installation', r'C:\Program Files\DCS-SimpleRadio-Standalone')
                    )
                    safe_path = os.path.normpath(install_dir).replace('\\', '/')
                    exe_path = os.path.join(temp_dir, 'SRS-AutoUpdater.exe')
                    args = ['-server', '-autoupdate', f'-path="{safe_path}"']
                    if sys.platform == 'win32':
                        utils.run_elevated(exe_path, temp_dir, *args)
                    else:
                        subprocess.run(
                            [exe_path] + args,
                            cwd=temp_dir,
                            shell=False,
                            stderr=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL
                        )

                    # wait until the installer finished
                    updater = os.path.join(self.get_inst_path(), 'SRS-AutoUpdater.exe')
                    while not os.path.exists(updater):
                        time.sleep(0.5)
                    exe = self.get_exe_path()
                    while not os.path.exists(exe):
                        time.sleep(0.5)

                    # create a base server.cfg by launching SRS once
                    cwd = os.path.dirname(exe)
                    base_config = os.path.join(cwd, 'server.cfg')
                    p = subprocess.Popen([exe], cwd=cwd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    while not os.path.exists(base_config):
                        time.sleep(1)
                    p.kill()
                    self.log.info(f"{self.name} version {version} installed.")
                    return True
                except OSError as ex:
                    if ex.winerror == 740:
                        self.log.error("You need to disable User Access Control (UAC) to use the DCS-SRS AutoUpdater.")
                    return False

            return await asyncio.to_thread(run_subprocess)

    @override
    async def install(self, version: str | None = None) -> bool:
        if self.is_installed():
            return True
        try:
            if not version:
                version = await self.get_latest_version()
            return await self.do_install(version)
        except Exception as ex:
            self.log.error(f"Failed to install {self.name}: {ex}")
            return False

    @override
    async def uninstall(self) -> bool:
        # we do not "uninstall" SRS, we only disable it on the respective instance
        # (which is disabling autoconnect in that case)
        await self.disable_autoconnect()
        return True

    @override
    async def repair(self) -> bool:
        if self.is_installed():
            self.log.info(f"Deleting {self.name} installation ...")
            try:
                utils.safe_rmtree(self.get_inst_path())
                self.log.info(f"{self.name} installation deleted.")
            except Exception as ex:
                self.log.warning(f"Failed to delete {self.name} installation: {ex}\nContinuing anyway ...")
        return await self.install()
