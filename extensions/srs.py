import aiohttp
import certifi
import os
import shutil
import subprocess
import ssl
import sys

from discord.ext import tasks

if sys.platform == 'win32':
    import win32api
    import win32con

from configparser import RawConfigParser
from core import Extension, utils, report, Server
from typing import Optional

ports: dict[int, str] = dict()


class SRS(Extension):
    def __init__(self, server: Server, config: dict):
        self.cfg = RawConfigParser()
        self.cfg.optionxform = str
        super().__init__(server, config)
        self.process = None

    def load_config(self) -> Optional[dict]:
        if 'config' in self.config:
            self.cfg.read(os.path.expandvars(self.config['config']), encoding='utf-8')
            return {s: dict(self.cfg.items(s)) for s in self.cfg.sections()}
        else:
            return {}

    async def prepare(self) -> bool:
        # Set SRS port if necessary
        dirty = False
        if 'port' in self.config and int(self.cfg['Server Settings']['SERVER_PORT']) != int(self.config['port']):
            self.cfg.set('Server Settings', 'SERVER_PORT', str(self.config['port']))
            self.log.info(f"  => {self.server.name}: SERVER_PORT set to {self.config['port']}")
            dirty = True
        if 'awacs' in self.config and self.cfg['General Settings']['EXTERNAL_AWACS_MODE'] != str(self.config['awacs']).lower():
            self.cfg.set('General Settings', 'EXTERNAL_AWACS_MODE', str(self.config['awacs']).lower())
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE set to {self.config['awacs']}")
            dirty = True
        if 'blue_password' in self.config and self.cfg['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'] != self.config['blue_password']:
            self.cfg.set('External AWACS Mode Settings', 'EXTERNAL_AWACS_MODE_BLUE_PASSWORD', self.config['blue_password'])
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE_BLUE_PASSWORD set to {self.config['blue_password']}")
            dirty = True
        if 'red_password' in self.config and self.cfg['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'] != self.config['red_password']:
            self.cfg.set('External AWACS Mode Settings', 'EXTERNAL_AWACS_MODE_RED_PASSWORD', self.config['red_password'])
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE_RED_PASSWORD set to {self.config['red_password']}")
            dirty = True
        if dirty:
            path = os.path.expandvars(self.config['config'])
            with open(path, 'w') as ini:
                self.cfg.write(ini)
            self.locals = self.load_config()
        # Change DCS-SRS-AutoConnectGameGUI.lua if necessary
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        host = self.config.get('host', self.node.public_ip)
        port = self.config.get('port', self.locals['Server Settings']['SERVER_PORT'])
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
            with open(os.path.join('extensions', 'lua', 'DCS-SRS-AutoConnectGameGUI.lua')) as infile:
                with open(autoconnect, 'w') as outfile:
                    for line in infile.readlines():
                        if line.startswith('SRSAuto.SERVER_SRS_HOST_AUTO = '):
                            line = "SRSAuto.SERVER_SRS_HOST_AUTO = false -- if set to true SRS will set the " \
                                   "SERVER_SRS_HOST for you! - Currently disabled\n"
                        elif line.startswith('SRSAuto.SERVER_SRS_PORT = '):
                            line = f'SRSAuto.SERVER_SRS_PORT = "{port}" --  SRS Server default is 5002 TCP & UDP\n'
                        elif line.startswith('SRSAuto.SERVER_SRS_HOST = '):
                            line = f'SRSAuto.SERVER_SRS_HOST = "{host}" -- overridden if SRS_HOST_AUTO is true ' \
                                   f'-- set to your PUBLIC ipv4 address\n'
                        outfile.write(line)
        else:
            self.log.info('- SRS autoconnect is not enabled for this server.')
        return True

    async def startup(self) -> bool:
        await super().startup()
        if self.config.get('autostart', True):
            self.log.debug(r'Launching SRS server with: "{}\SR-Server.exe" -cfg="{}"'.format(
                os.path.expandvars(self.config['installation']), os.path.expandvars(self.config['config'])))
            if sys.platform == 'win32' and self.config.get('minimized', False):
                info = subprocess.STARTUPINFO()
                info.dwFlags = subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = win32con.SW_MINIMIZE
            else:
                info = None
            self.process = subprocess.Popen(
                ['SR-Server.exe', '-cfg={}'.format(os.path.expandvars(self.config['config']))],
                executable=os.path.join(os.path.expandvars(self.config['installation']), 'SR-Server.exe'),
                startupinfo=info
            )
        return self.is_running()

    async def shutdown(self):
        if self.config.get('autostart', True):
            p = self.process or utils.find_process('SR-Server.exe', self.server.instance.name)
            if p:
                p.kill()
                self.process = None
        return await super().shutdown()

    def is_running(self) -> bool:
        server_ip = self.locals['Server Settings'].get('SERVER_IP', '127.0.0.1')
        if server_ip == '0.0.0.0':
            server_ip = '127.0.0.1'
        return utils.is_open(server_ip, self.locals['Server Settings'].get('SERVER_PORT', 5002))

    @property
    def version(self) -> Optional[str]:
        if sys.platform == 'win32':
            info = win32api.GetFileVersionInfo(
                os.path.join(os.path.expandvars(self.config['installation']), 'SR-Server.exe'), '\\')
            version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                       info['FileVersionMS'] % 65536,
                                       info['FileVersionLS'] / 65536,
                                       info['FileVersionLS'] % 65536)
        else:
            version = None
        return version

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
                "name": "SRS (online)" if self.is_running() else "SRS (offline)",
                "version": self.version,
                "value": value
            }

    def is_installed(self) -> bool:
        global ports

        # check if SRS is installed
        exe_path = os.path.join(
            os.path.expandvars(self.config.get('installation',
                                               os.path.join('%ProgramFiles%', 'DCS-SimpleRadio-Standalone'))),
            'SR-Server.exe'
        )
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
        except KeyError:
            self.log.error(f"  => SRS config not set for server {self.server.name}")
            return False

        port = self.config.get('port', int(self.cfg['Server Settings'].get('SERVER_PORT', '5002')))
        if port in ports and ports[port] != self.server.name:
            self.log.error(f"  => SRS port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        return True

    @tasks.loop(minutes=5)
    async def schedule(self):
        if not self.config.get('autoupdate', False):
            return
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get("https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/latest") as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if version != self.version:
                            self.log.info(f"A new DCS-SRS update is available. Updating to version {version} ...")
                            cwd = os.path.expandvars(self.config['installation'])
                            subprocess.run(executable=os.path.join(cwd, 'SRS-AutoUpdater.exe'),
                                           args=['-server', '-autoupdate', f'-path=\"{cwd}\"'], cwd=cwd, shell=True)
        except OSError as ex:
            if ex.winerror == 740:
                self.log.error("You need to run DCSServerBot as Administrator to use the DCS-SRS AutoUpdater.")
