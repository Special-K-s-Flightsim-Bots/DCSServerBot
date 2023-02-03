import os
import shutil
import subprocess
import win32api
from configparser import ConfigParser
from core import Extension, DCSServerBot, utils, report, Server
from typing import Optional


class SRS(Extension):
    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        super().__init__(bot, server, config)
        self.process = None

    def load_config(self) -> Optional[dict]:
        cfg = ConfigParser()
        path = os.path.expandvars(self.config['config'])
        if os.path.exists(path):
            cfg.read(path, encoding='utf-8')
            return {s: dict(cfg.items(s)) for s in cfg.sections()}
        else:
            self.log.warning(f"Can't load SRS config from {path}!")

    async def prepare(self) -> bool:
        autoconnect = os.path.expandvars(f"%USERPROFILE%\\Saved Games\\{self.server.installation}\\Scripts\\Hooks\\DCS-SRS-AutoConnectGameGUI.lua")
        port = self.locals['Server Settings']['server_port']
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
            with open('extensions\\lua\\DCS-SRS-AutoConnectGameGUI.lua') as infile:
                with open(autoconnect, 'w') as outfile:
                    for line in infile.readlines():
                        if line.startswith('SRSAuto.SERVER_SRS_HOST_AUTO = '):
                            line = "SRSAuto.SERVER_SRS_HOST_AUTO = false -- if set to true SRS will set the " \
                                   "SERVER_SRS_HOST for you! - Currently disabled\n"
                        elif line.startswith('SRSAuto.SERVER_SRS_PORT = '):
                            line = f'SRSAuto.SERVER_SRS_PORT = "{port}" --  SRS Server default is 5002 TCP & UDP\n'
                        elif line.startswith('SRSAuto.SERVER_SRS_HOST = '):
                            line = f'SRSAuto.SERVER_SRS_HOST = "{self.bot.external_ip}" -- overridden if SRS_HOST_AUTO is true -- set to your PUBLIC ipv4 address\n'
                        outfile.write(line)
        else:
            self.log.info('- SRS autoconnect is not enabled for this server.')
        return True

    async def startup(self) -> bool:
        self.log.debug(r'Launching SRS server with: "{}\SR-Server.exe" -cfg="{}"'.format(
            os.path.expandvars(self.config['installation']), os.path.expandvars(self.config['config'])))
        self.process = subprocess.Popen(['SR-Server.exe', '-cfg={}'.format(
            os.path.expandvars(self.config['config']))],
                                        executable=os.path.expandvars(self.config['installation']) + r'\SR-Server.exe')
        return await self.is_running()

    async def shutdown(self):
        p = self.process or utils.find_process('SR-Server.exe', self.server.installation)
        if p:
            p.kill()
            self.process = None
            return True
        else:
            return False

    async def is_running(self) -> bool:
        if self.process:
            return self.process.poll() is None
        server_ip = self.locals['Server Settings']['server_ip'] if 'server_ip' in self.locals['Server Settings'] else '127.0.0.1'
        if server_ip == '0.0.0.0':
            server_ip = '127.0.0.1'
        return utils.is_open(server_ip, self.locals['Server Settings']['server_port'])

    @property
    def version(self) -> str:
        info = win32api.GetFileVersionInfo(
            os.path.expandvars(self.config['installation']) + r'\SR-Server.exe', '\\')
        version = "%d.%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                   info['FileVersionMS'] % 65536,
                                   info['FileVersionLS'] / 65536,
                                   info['FileVersionLS'] % 65536)
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if self.locals:
            value = f"{self.bot.external_ip}:{self.locals['Server Settings']['server_port']}"
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            if show_passwords and self.locals['General Settings']['EXTERNAL_AWACS_MODE'.lower()] and \
                    'External AWACS Mode Settings' in self.locals:
                blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'.lower()]
                red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'.lower()]
                if blue or red:
                    value += f'\n🔹 Pass: {blue}\n🔸 Pass: {red}'
            embed.add_field(name=f"SRS", value=value)

    def verify(self) -> bool:
        # check if SRS is installed
        if 'installation' not in self.config or \
                not os.path.exists(os.path.expandvars(self.config['installation']) + r'\SR-Server.exe'):
            self.log.debug("SRS executable not found in {}".format(self.config['installation'] + r'\SR-Server.exe'))
            return False
        # do we have a proper config file?
        if 'config' not in self.config or not os.path.exists(os.path.expandvars(self.config['config'])):
            self.log.debug(f"SRS config not found in {self.config['config']}")
            return False
        return True
