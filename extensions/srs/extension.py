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
import zipfile

from configparser import RawConfigParser
from contextlib import suppress
from core import (Extension, utils, ServiceRegistry, Autoexec, get_translation, InstallException, Server,
                  ServerMaintenanceManager, PortType, Port)
from discord.ext import tasks
from io import BytesIO
from packaging.version import parse
from services.bot import BotService
from services.servicebus import ServiceBus
from threading import Thread
from typing_extensions import override
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

_ = get_translation(__name__.split('.')[1])

SRS_GITHUB_URL = "https://api.github.com/repos/ciribob/DCS-SimpleRadioStandalone/releases/latest"
SRS_BETA_URL = "https://api.github.com/repos/ciribob/DCS-SimpleRadioStandalone/releases"
SRS_DOWNLOAD_URL = "https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/download/{version}/DCS-SimpleRadioStandalone-{version}.zip"

__all__ = [
    "SRS"
]


class SRS(Extension, FileSystemEventHandler):
    _ports: dict[int, str] = {}
    _export_ports: dict[int, str] = {}
    _lock = asyncio.Lock()

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
            "placeholder": _("Password for blue GCI, . for none"),
            "required": True,
            "default": "blue"
          },
        "red_password": {
            "type": str,
            "label": _("Red Password"),
            "placeholder": _("Password for red GCI, . for none"),
            "required": True,
            "default": "red"
        }
    }

    def __init__(self, server: Server, config: dict):
        self.cfg = RawConfigParser()
        self.cfg.optionxform = str
        self.bus = ServiceRegistry.get(ServiceBus)
        self.process: psutil.Process | None = None
        self.observer: Observer | None = None
        self.first_run = True
        self._inst_path: str | None = None
        self.exe_name = None
        self.clients: dict[str, set[int]] = {}
        self.client_names: dict[str, str] = {}
        super().__init__(server, config)

    def get_config_path(self) -> str:
        config_path = self.config.get('config')
        if not config_path:
            config_path = os.path.join(self.get_inst_path(), 'server.cfg')
            self.log.warning(f"  => {self.name}: No config parameter given, using default config path: {config_path}")
        return os.path.expandvars(config_path.format(server=self.server, instance=self.server.instance))

    @override
    def load_config(self) -> dict | None:
        if 'config' in self.config:
            self.cfg.read(self.get_config_path(), encoding='utf-8')
            return {
                s: {_name: Autoexec.parse(_value) for _name, _value in self.cfg.items(s)}
                for s in self.cfg.sections()
            }
        else:
            return {}

    async def enable_autoconnect(self):
        # Change DCS-SRS-AutoConnectGameGUI.lua if necessary
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        host = self.config.get('host', self.node.public_ip)
        port = self.config.get('port', self.locals['Server Settings']['SERVER_PORT'])
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
        else:
            shutil.copy2(os.path.join(self.get_inst_path(), 'Scripts', 'DCS-SRS-AutoConnectGameGUI.lua'), autoconnect)

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
                            line = 'SRSAuto.SRS_NUDGE_ENABLED = true -- set to true to enable the message below'
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

    @override
    async def prepare(self) -> bool:
        await self.handle_update()
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
            self.config['lotatc_export_port'] = self.config.get('lotatc_export_port', 10712)
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

        if dirty:
            with open(path, mode='w', encoding='utf-8') as ini:
                self.cfg.write(ini)
            self.locals = self.load_config()

        # Check IP settings
        if self.cfg['Server Settings']['SERVER_IP'] != '0.0.0.0':
            self.log.warning(f"  => {self.server.name}: SERVER_IP is not set to 0.0.0.0 in your {self.get_config_path()}")

        # Check port conflicts
        port = self.config.get('port', int(self.cfg['Server Settings'].get('SERVER_PORT', '5002')))
        if type(self)._ports.get(port, self.server.name) != self.server.name:
            self.log.error(
                f"  => {self.server.name}: {self.name} SERVER_PORT {port} already in use by server {type(self)._ports[port]}!")
            return False
        else:
            type(self)._ports[port] = self.server.name
        # only check LotAtc Export Port if LotAtc is there
        if self.config.get('lotatc', False):
            export_port = self.config.get('lotatc_export_port', int(self.cfg['General Settings'].get('LOTATC_EXPORT_PORT', '10712')))
            if type(self)._export_ports.get(export_port, self.server.name) != self.server.name:
                self.log.error(
                    f"  => {self.server.name}: {self.name} LOTATC_EXPORT_PORT {export_port} already in use by server {type(self)._ports[port]}!")
                return False
            else:
                type(self)._export_ports[export_port] = self.server.name

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

            def run_subprocess():
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

                proc = subprocess.Popen([
                    self.get_exe_path(),
                    f"-cfg={self.get_config_path()}"
                ], startupinfo=info, stdout=out, stderr=err, close_fds=True)

                if self.config.get('debug', False):
                    Thread(target=log_output, args=(proc.stdout,logging.DEBUG), daemon=True).start()
                    Thread(target=log_output, args=(proc.stderr,logging.ERROR), daemon=True).start()

                return proc

            try:
                async with type(self)._lock:
                    if self.is_running():
                        return True
                    p = await asyncio.to_thread(run_subprocess)
                    self.process = psutil.Process(p.pid)
                    if not self.observer:
                        self.start_observer()
            except psutil.NoSuchProcess:
                self.log.error(f"Error during launch of {self.get_exe_path()}!")
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
        if self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.process_export_file(event.src_path), self.loop)

    async def process_export_file(self, path: str):
        try:
            with open(path, mode='r', encoding='utf-8') as infile:
                data = json.load(infile)
            for client in data.get('Clients', {}):
                if client['Name'] == '---':
                    continue
                target = set(int(x['freq']) for x in client['RadioInfo']['radios'] if int(x['freq']) > 1E6)
                if client['ClientGuid'] not in self.clients:
                    self.clients[client['ClientGuid']] = target
                    self.client_names[client['ClientGuid']] = client['Name']
                    await self.bus.send_to_node({
                        "command": "onSRSConnect",
                        "server_name": self.server.name,
                        "player_name": client['Name'],
                        "side": client['Coalition'],
                        "unit": client['RadioInfo']['unit'],
                        "unit_id": client['RadioInfo']['unitId'],
                        "radios": list(self.clients[client['ClientGuid']])
                    })
                else:
                    actual = self.clients[client['ClientGuid']]
                    if actual != target:
                        self.clients[client['ClientGuid']] = target
                        await self.bus.send_to_node({
                            "command": "onSRSUpdate",
                            "server_name": self.server.name,
                            "player_name": client['Name'],
                            "side": client['Coalition'],
                            "unit": client['RadioInfo']['unit'],
                            "unit_id": client['RadioInfo']['unitId'],
                            "radios": list(self.clients[client['ClientGuid']])
                        })
            all_clients = set(self.clients.keys())
            active_clients = set([x['ClientGuid'] for x in data['Clients']])
            # any clients disconnected?
            for client in all_clients - active_clients:
                await self.bus.send_to_node({
                    "command": "onSRSDisconnect",
                    "server_name": self.server.name,
                    "player_name": self.client_names[client]
                })
                del self.clients[client]
                del self.client_names[client]
        except Exception:
            pass

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

    @override
    def is_running(self) -> bool:
        if not self.process and self.exe_name:
            self.process = next(utils.find_process(self.exe_name, self.server.instance.name), None)
            running = self.process is not None and self.process.is_running()
            if not running:
                self.log.debug("SRS: is NOT running (process)")
        else:
            server_ip = self.locals['Server Settings'].get('SERVER_IP', '127.0.0.1')
            if server_ip == '0.0.0.0':
                server_ip = '127.0.0.1'
            running = utils.is_open(server_ip, self.locals['Server Settings'].get('SERVER_PORT', 5002))
            if not running:
                self.log.debug("SRS: is NOT running (port)")
                self.process = None
        # start the observer if we were started to a running SRS server
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

        host = self.config.get('host', self.node.public_ip)
        value = f"{host}:{self.locals['Server Settings']['SERVER_PORT']}"
        show_passwords = self.config.get('show_passwords', True)
        if show_passwords and self.locals['General Settings'].get('EXTERNAL_AWACS_MODE', False) and \
                'External AWACS Mode Settings' in self.locals:
            blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD']
            red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD']
            if blue or red:
                value += f'\n🔹 Pass: {blue}\n🔸 Pass: {red}'
        return {
            "name": self.name,
            "version": self.version,
            "value": value
        }

    @override
    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        # check if SRS is installed
        exe_path = self.get_exe_path()
        if not os.path.exists(exe_path):
            self.log.error(f"  => SRS executable not found in {exe_path}")
            return False
        # do we have a proper config file?
        try:
            cfg_path = self.get_config_path()
            if not os.path.exists(cfg_path) or not os.path.isfile(cfg_path):
                self.log.error(f"  => SRS config not found for server {self.server.name}")
                return False
            if self.server.instance.name not in cfg_path:
                self.log.warning(f"  => Please move your SRS configuration from {cfg_path} to "
                                 f"{os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')}")
            return True
        except KeyError:
            self.log.error(f"  => SRS config not set for server {self.server.name}")
            return False

    async def check_for_updates(self) -> str | None:
        with suppress(aiohttp.ClientError):
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                url = SRS_BETA_URL if self.config.get('beta', False) else SRS_GITHUB_URL
                async with session.get(url, proxy=self.node.proxy, proxy_auth=self.node.proxy_auth,
                                       raise_for_status=True) as response:
                    data = await response.json()
                    if isinstance(data, list):
                        data = data[0]
                    version = data.get('tag_name', '').strip('v')
                    if parse(version) != parse(self.version):
                        return version
        return None

    async def do_update(self, version: str):
        # make sure the monitoring does not interfere
        async with ServerMaintenanceManager(self.node, shutdown=False):
            vc_redist = False
            installation_dir = self.get_inst_path()
            async with aiohttp.ClientSession() as session:
                async with session.get(SRS_DOWNLOAD_URL.format(version=version), raise_for_status=True,
                                       proxy=self.node.proxy, proxy_auth=self.node.proxy_auth) as response:
                    with zipfile.ZipFile(BytesIO(await response.content.read())) as z:
                        # stop any existing SRS process
                        if parse(self.version) >= parse('2.2.0.0'):
                            srs_exes = ['SR-ClientRadio.exe', 'SRS-Server.exe', 'SRS-Server-Commandline.exe']
                        else:
                            srs_exes = ['SR-Server.exe']
                        for exe in srs_exes:
                            for process in utils.find_process(os.path.basename(exe)):
                                if process and process.is_running():
                                    process.terminate()
                        # unpack files
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

    async def handle_update(self):
        # don't run if autoupdate is disabled
        if not self.config.get('autoupdate', False):
            return

        # make sure we're not called twice
        async with type(self)._lock:
            try:
                version = await self.check_for_updates()
                if version:
                    self.log.info(f"A new DCS-SRS update is available. Updating to version {version} ...")
                    await self.do_update(version)
                    self.log.info("DCS-SRS updated.")
                    bus = ServiceRegistry.get(ServiceBus)
                    await bus.send_to_node({
                        "command": "rpc",
                        "service": BotService.__name__,
                        "method": "audit",
                        "params": {
                            "message": f"{self.name} updated to version {version} on node {self.node.name}."
                        }
                    })
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
                        params = {
                            "channel": config['channel'],
                            "embed": embed.to_dict()
                        }
                        if 'mention' in config:
                            params['mention'] = config['mention']
                        await bus.send_to_node({
                            "command": "rpc",
                            "service": BotService.__name__,
                            "method": "send_message",
                            "params": params
                        })

            except Exception as ex:
                self.log.error(f"DCS-SRS update failed: {ex}")

    @tasks.loop(minutes=30)
    async def schedule(self):
        await self.handle_update()

    @override
    def get_ports(self) -> dict[str, Port]:
        if self.enabled:
            rc: dict[str, Port] = {
                "SRS Port": Port(self.locals['Server Settings']['SERVER_PORT'], PortType.BOTH, public=True)
            }
            if self.locals['General Settings'].get('LOTATC_EXPORT_ENABLED', False):
                rc["LotAtc Export Port"] = Port(self.locals['General Settings'].get('LOTATC_EXPORT_PORT', 10712), PortType.UDP)
        else:
            rc = {}
        return rc
