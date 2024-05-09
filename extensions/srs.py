import aiohttp
import asyncio
import certifi
import json
import os
import psutil
import shutil
import subprocess
import ssl
import sys

from configparser import RawConfigParser
from core import Extension, utils, Server, ServiceRegistry, Autoexec
from discord.ext import tasks
from services import ServiceBus
from typing import Optional
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

ports: dict[int, str] = dict()
SRS_GITHUB_URL = "https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/latest"


class SRS(Extension, FileSystemEventHandler):
    def __init__(self, server: Server, config: dict):
        self.cfg = RawConfigParser()
        self.cfg.optionxform = str
        super().__init__(server, config)
        self.bus = ServiceRegistry.get(ServiceBus)
        self.process: Optional[psutil.Process] = None
        self.observer: Optional[Observer] = None
        self.clients: dict[str, set[int]] = {}

    def load_config(self) -> Optional[dict]:
        if 'config' in self.config:
            self.cfg.read(os.path.expandvars(self.config['config']), encoding='utf-8')
            return {s: {_name: Autoexec.parse(_value) for _name, _value in self.cfg.items(s)} for s in self.cfg.sections()}
        else:
            return {}

    def enable_autoconnect(self):
        # Change DCS-SRS-AutoConnectGameGUI.lua if necessary
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        host = self.config.get('host', self.node.public_ip)
        port = self.config.get('port', self.locals['Server Settings']['SERVER_PORT'])
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
        with open(os.path.join('extensions', 'lua', 'DCS-SRS-AutoConnectGameGUI.lua'), mode='r',
                  encoding='utf-8') as infile:
            with open(autoconnect, mode='w', encoding='utf-8') as outfile:
                for line in infile.readlines():
                    if line.startswith('SRSAuto.SERVER_SRS_HOST_AUTO = '):
                        line = "SRSAuto.SERVER_SRS_HOST_AUTO = false -- if set to true SRS will set the " \
                               "SERVER_SRS_HOST for you! - Currently disabled\n"
                    elif line.startswith('SRSAuto.SERVER_SRS_PORT = '):
                        line = f'SRSAuto.SERVER_SRS_PORT = "{port}" --  SRS Server default is 5002 TCP & UDP\n'
                    elif line.startswith('SRSAuto.SERVER_SRS_HOST = '):
                        line = f'SRSAuto.SERVER_SRS_HOST = "{host}" -- overridden if SRS_HOST_AUTO is true ' \
                               f'-- set to your PUBLIC ipv4 address\n'
                    elif line.startswith('SRSAuto.SRS_NUDGE_MESSAGE = ') and self.config.get('srs_nudge_message'):
                        line = f"SRSAuto.SRS_NUDGE_MESSAGE = \"{self.config.get('srs_nudge_message')}\"\n"
                    outfile.write(line)

    def disable_autoconnect(self):
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
            os.remove(autoconnect)

    def _maybe_update_config(self, section, key, value_key):
        if value_key in self.config:
            value = self.config[value_key]
            if Autoexec.parse(self.cfg[section][key]) != value:
                self.cfg.set(section, key, value)
                self.log.info(f"  => {self.server.name}: [{section}][{key}] set to {self.config[value_key]}")
                return True
        return False

    async def prepare(self) -> bool:
        global ports

        path = os.path.expandvars(self.config['config'])
        if 'client_export_file_path' not in self.config:
            self.config['client_export_file_path'] = os.path.join(os.path.dirname(path), 'clients-list.json')
        dirty = self._maybe_update_config('Server Settings', 'SERVER_PORT', 'port')
        dirty = self._maybe_update_config('Server Settings', 'CLIENT_EXPORT_FILE_PATH',
                                          'client_export_file_path') or dirty
        self.config['client_export_enabled'] = True
        dirty = self._maybe_update_config('General Settings', 'CLIENT_EXPORT_ENABLED',
                                          'client_export_enabled') or dirty
        # enable SRS on spectators for slot blocking
        self.config['spectators_audio_disabled'] = False
        dirty = self._maybe_update_config('General Settings', 'SPECTATORS_AUDIO_DISABLED',
                                          'spectators_audio_disabled') or dirty
        if 'LotAtc' in self.server.extensions:
            self.config['lotatc'] = True
            dirty = self._maybe_update_config('General Settings',
                                              'LOTATC_EXPORT_ENABLED',
                                              'lotatc') or dirty
            self.config['awacs'] = True

        if self.config.get('awacs', True):
            dirty = self._maybe_update_config('General Settings',
                                              'EXTERNAL_AWACS_MODE',
                                              'awacs') or dirty
            dirty = self._maybe_update_config('External AWACS Mode Settings',
                                              'EXTERNAL_AWACS_MODE_BLUE_PASSWORD',
                                              'blue_password') or dirty
            dirty = self._maybe_update_config('External AWACS Mode Settings',
                                              'EXTERNAL_AWACS_MODE_RED_PASSWORD',
                                              'red_password') or dirty

        if dirty:
            with open(path, mode='w', encoding='utf-8') as ini:
                self.cfg.write(ini)
            self.locals = self.load_config()
        # Check port conflicts
        port = self.config.get('port', int(self.cfg['Server Settings'].get('SERVER_PORT', '5002')))
        if ports.get(port, self.server.name) != self.server.name:
            self.log.error(f"  => {self.server.name}: {self.name} port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        if self.config.get('autoconnect', True):
            self.enable_autoconnect()
            self.log.info('  => SRS autoconnect is enabled for this server.')
        else:
            self.log.info('  => SRS autoconnect is NOT enabled for this server.')
        return await super().prepare()

    async def startup(self) -> bool:
        await super().startup()
        if self.config.get('autostart', True):
            self.log.debug(f"Launching SRS server with: \"{self.get_exe_path()}\" -cfg=\"{self.config['config']}\"")
            if sys.platform == 'win32' and self.config.get('minimized', True):
                import win32process
                import win32con

                info = subprocess.STARTUPINFO()
                info.dwFlags |= win32process.STARTF_USESHOWWINDOW
                info.wShowWindow = win32con.SW_SHOWMINNOACTIVE
            else:
                info = None
            out = subprocess.DEVNULL if not self.config.get('debug', False) else None

            def run_subprocess():
                return subprocess.Popen([
                    self.get_exe_path(),
                    f"-cfg={os.path.expandvars(self.config['config'])}"
                ], startupinfo=info, stdout=out, stderr=out, close_fds=True)

            p = await asyncio.to_thread(run_subprocess)
            try:
                self.process = psutil.Process(p.pid)
            except psutil.NoSuchProcess:
                self.log.error(f"Error during launch of {self.config['cmd']}!")
                return False
        return await asyncio.to_thread(self.is_running)

    def shutdown(self) -> bool:
        if self.config.get('autostart', True) and not self.config.get('no_shutdown', False):
            if self.is_running():
                try:
                    super().shutdown()
                    if not self.process:
                        self.process = utils.find_process('SR-Server.exe', self.server.instance.name)
                    if self.process:
                        utils.terminate_process(self.process)
                        self.process = None
                        return True
                    else:
                        self.log.warning(f"  => Could not find a running SRS server process.")
                        cfg_path = os.path.expandvars(self.config.get('config'))
                        if self.server.instance.name not in cfg_path:
                            self.log.warning(f"  => Please move your SRS configuration to "
                                             f"{os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')}")
                except Exception as ex:
                    self.log.error(f'Error during shutdown of SRS: {str(ex)}')
                    return False

            return True

    def on_modified(self, event: FileSystemEvent) -> None:
        self.process_export_file(event.src_path)

    def process_export_file(self, path: str):
        try:
            with open(path, mode='r', encoding='utf-8') as infile:
                data = json.load(infile)
            for client in data['Clients']:
                if client['Name'] == '---':
                    continue
                target = set(int(x['freq']) for x in client['RadioInfo']['radios'] if int(x['freq']) > 1E6)
                if client['Name'] not in self.clients:
                    self.clients[client['Name']] = target
                    self.bus.send_to_node({
                        "command": "onSRSConnect",
                        "server_name": self.server.name,
                        "player_name": client['Name'],
                        "side": client['Coalition'],
                        "unit": client['RadioInfo']['unit'],
                        "unit_id": client['RadioInfo']['unitId'],
                        "radios": list(self.clients[client['Name']])
                    })
                else:
                    actual = self.clients[client['Name']]
                    if actual != target:
                        self.clients[client['Name']] = target
                        self.bus.send_to_node({
                            "command": "onSRSUpdate",
                            "server_name": self.server.name,
                            "player_name": client['Name'],
                            "side": client['Coalition'],
                            "unit": client['RadioInfo']['unit'],
                            "unit_id": client['RadioInfo']['unitId'],
                            "radios": list(self.clients[client['Name']])
                        })
            all_clients = set(self.clients.keys())
            active_clients = set([x['Name'] for x in data['Clients']])
            # any clients disconnected?
            for client in all_clients - active_clients:
                self.bus.send_to_node({
                    "command": "onSRSDisconnect",
                    "server_name": self.server.name,
                    "player_name": client
                })
                del self.clients[client]
        except Exception:
            pass

    def is_running(self) -> bool:
        server_ip = self.locals['Server Settings'].get('SERVER_IP', '127.0.0.1')
        if server_ip == '0.0.0.0':
            server_ip = '127.0.0.1'
        running = utils.is_open(server_ip, self.locals['Server Settings'].get('SERVER_PORT', 5002))
        if running and not self.observer:
            path = self.locals['Server Settings']['CLIENT_EXPORT_FILE_PATH']
            if os.path.exists(path):
                self.process_export_file(path)
                self.observer = Observer()
                self.observer.schedule(self, path=os.path.dirname(path))
                self.observer.start()
        elif not running and self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            self.clients.clear()
        return running

    def get_inst_path(self) -> str:
        return os.path.join(
            os.path.expandvars(self.config.get('installation',
                                               os.path.join('%ProgramFiles%', 'DCS-SimpleRadio-Standalone'))))

    def get_exe_path(self) -> str:
        return os.path.join(self.get_inst_path(), 'SR-Server.exe')

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(self.get_exe_path())

    async def render(self, param: Optional[dict] = None) -> dict:
        if self.locals:
            host = self.config.get('host', self.node.public_ip)
            value = f"{host}:{self.locals['Server Settings']['SERVER_PORT']}"
            show_passwords = self.config.get('show_passwords', True)
            if show_passwords and self.locals['General Settings']['EXTERNAL_AWACS_MODE'] == 'true' and \
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

    def is_installed(self) -> bool:
        # check if SRS is installed
        exe_path = self.get_exe_path()
        if not os.path.exists(exe_path):
            self.log.error(f"  => SRS executable not found in {exe_path}")
            return False
        # do we have a proper config file?
        try:
            cfg_path = os.path.expandvars(self.config.get('config'))
            if not os.path.exists(cfg_path):
                self.log.error(f"  => SRS config not found for server {self.server.name}")
                return False
            if self.server.instance.name not in cfg_path:
                self.log.warning(f"  => Please move your SRS configuration from {cfg_path} to "
                                 f"{os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')}")
            return True
        except KeyError:
            self.log.error(f"  => SRS config not set for server {self.server.name}")
            return False

    @tasks.loop(minutes=5)
    async def schedule(self):
        if not self.config.get('autoupdate', False):
            return
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(SRS_GITHUB_URL) as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if version != self.version:
                            self.log.info(f"A new DCS-SRS update is available. Updating to version {version} ...")
                            cwd = self.get_inst_path()
                            subprocess.run(executable=os.path.join(cwd, 'SRS-AutoUpdater.exe'),
                                           args=['-server', '-autoupdate', f'-path=\"{cwd}\"'], cwd=cwd, shell=True)
        except OSError as ex:
            if ex.winerror == 740:
                self.log.error("You need to run DCSServerBot as Administrator to use the DCS-SRS AutoUpdater.")
