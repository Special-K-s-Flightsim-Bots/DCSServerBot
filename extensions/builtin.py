import os
import re
import subprocess
import time
import win32api
from configparser import ConfigParser
from core import Extension, DCSServerBot, utils, report, Server
from datetime import datetime, timedelta
from typing import Any, Optional, TextIO


class SRS(Extension):
    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        super().__init__(bot, server, config)
        self.process = None

    def load_config(self) -> Optional[dict]:
        cfg = ConfigParser()
        path = os.path.expandvars(self.config['config'])
        if os.path.exists(path):
            cfg.read(path)
            return {s: dict(cfg.items(s)) for s in cfg.sections()}
        else:
            self.log.warning(f"Can't load SRS config from {path}!")

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
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            if show_passwords and self.locals['General Settings']['EXTERNAL_AWACS_MODE'.lower()] and \
                    'External AWACS Mode Settings' in self.locals:
                blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'.lower()]
                red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'.lower()]
                value = f'🔹 Pass: {blue}\n🔸 Pass: {red}'
            else:
                value = '_ _'
            embed.add_field(name=f"SRS [{self.locals['Server Settings']['server_port']}]", value=value)

    def verify(self) -> bool:
        # check if SRS is installed
        if 'installation' not in self.config or \
                not os.path.exists(os.path.expandvars(self.config['installation']) + r'\SR-Server.exe'):
            return False
        # do we have a proper config file?
        if 'config' not in self.config or not os.path.exists(os.path.expandvars(self.config['config'])):
            return False
        return True


class LotAtc(Extension):
    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('{'):
            return value[1:-1].split(',')
        elif value.startswith('"'):
            return value.strip('"')
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        elif '.' in value:
            return float(value)
        else:
            return int(value)

    def load_config(self) -> Optional[dict]:
        def read_file(file: TextIO, cfg: dict):
            for line in file.readlines():
                match = exp.match(line)
                if match:
                    key = match.group('key').strip()
                    if key.startswith('--'):
                        continue
                    value = match.group('value').strip(' ,')
                    cfg[key] = self.parse(value)

        exp = re.compile(r'(?P<key>.*) = (?P<value>.*)')
        cfg = dict()
        installation = self.server.installation
        if os.path.exists(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) +
                          '/Mods/services/LotAtc/config.lua'):
            with open(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) +
                      '/Mods/services/LotAtc/config.lua', 'r') as file:
                read_file(file, cfg)
        if os.path.exists(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) +
                          '/Mods/services/LotAtc/config.custom.lua'):
            with open(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) +
                      '/Mods/services/LotAtc/config.custom.lua', 'r') as file:
                read_file(file, cfg)
        return cfg

    @property
    def version(self) -> str:
        installation = self.server.installation
        path = os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\services\LotAtc\bin\lotatc.dll'
        if os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if self.locals:
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            if show_passwords and 'blue_password' in self.locals and 'red_password' in self.locals:
                value = f"🔹 Pass: {self.locals['blue_password']}\n🔸 Pass: {self.locals['red_password']}"
            else:
                value = '_ _'
            if 'port' in self.locals:
                embed.add_field(name=f"LotAtc [{self.locals['port']}]", value=value)
            else:
                embed.add_field(name='LotAtc', value=value)

    def verify(self) -> bool:
        if not os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              '/Mods/services/LotAtc/bin/lotatc.dll'):
            return False
        if not os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              '/Mods/services/LotAtc/config.lua'):
            return False
        return True


class Tacview(Extension):
    def _load_config(self) -> dict:
        if 'Tacview' in self.server.options['plugins']:
            # check config for errors
            tacview = self.server.options['plugins']['Tacview']
            if 'tacviewRealTimeTelemetryEnabled' in tacview and tacview['tacviewRealTimeTelemetryEnabled']:
                if 'tacviewPlaybackDelay' in tacview and tacview['tacviewPlaybackDelay'] > 0:
                    self.log.warning(f'  - {self.server.name}: Realtime Telemetry is enabled but tacviewPlaybackDelay is set!')
            elif 'tacviewPlaybackDelay' not in tacview or tacview['tacviewPlaybackDelay'] == 0:
                self.log.warning(f'  - {self.server.name}: tacviewPlaybackDelay is not set, you might see performance issues!')
            return tacview
        else:
            return dict()

    def load_config(self) -> Optional[dict]:
        if self.server.options:
            return self._load_config()
        else:
            return None

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
        if not self.locals:
            self.locals = self._load_config()
        name = 'Tacview'
        if ('tacviewModuleEnabled' in self.locals and not self.locals['tacviewModuleEnabled']) or \
                ('tacviewFlightDataRecordingEnabled' in self.locals and
                 not self.locals['tacviewFlightDataRecordingEnabled']):
            value = 'disabled'
        else:
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            value = ''
            if 'tacviewRealTimeTelemetryEnabled' in self.locals and self.locals['tacviewRealTimeTelemetryEnabled']:
                name += ' RT'
                if show_passwords and 'tacviewRealTimeTelemetryPassword' in self.locals and \
                        len(self.locals['tacviewRealTimeTelemetryPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRealTimeTelemetryPassword']}\n"
            elif show_passwords and 'tacviewHostTelemetryPassword' in self.locals \
                    and len(self.locals['tacviewHostTelemetryPassword']) > 0:
                value += f"Password: {self.locals['tacviewHostTelemetryPassword']}\n"
            if 'tacviewRealTimeTelemetryPort' in self.locals and len(self.locals['tacviewRealTimeTelemetryPort']) > 0:
                name += f" [{self.locals['tacviewRealTimeTelemetryPort']}]"
            if 'tacviewRemoteControlEnabled' in self.locals and self.locals['tacviewRemoteControlEnabled']:
                value += f"**Remote Ctrl [{self.locals['tacviewRemoteControlPort']}]**\n"
                if show_passwords and 'tacviewRemoteControlPassword' in self.locals and \
                        len(self.locals['tacviewRemoteControlPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRemoteControlPassword']}"
            if len(value) == 0:
                value = 'enabled'
        embed.add_field(name=name, value=value)

    @staticmethod
    def schedule(config: dict, lastrun: Optional[datetime] = None):
        DEFAULT_DIR = r"%USERPROFILE%\Documents\Tacview"

        # check if autodelete is configured
        if 'delete_after' not in config:
            return
        # only run once a day
        if lastrun and lastrun > (datetime.now() - timedelta(days=1)):
            return
        now = time.time()
        path = os.path.expandvars(config['path']) if 'path' in config else os.path.expandvars(DEFAULT_DIR)
        for f in [os.path.join(path, x) for x in os.listdir(path)]:
            if os.stat(f).st_mtime < (now - config['delete_after'] * 86400):
                if os.path.isfile(f):
                    os.remove(f)

    def verify(self) -> bool:
        return os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              r'\Mods\tech\Tacview\bin\tacview.dll')
