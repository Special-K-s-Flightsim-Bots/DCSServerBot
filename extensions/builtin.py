import os
import re
import subprocess
import win32api
from configparser import ConfigParser
from core import Extension, DCSServerBot, utils, report
from typing import Any, Optional


class SRS(Extension):
    def __init__(self, bot: DCSServerBot, server: dict, config: dict):
        super().__init__(bot, server, config)
        self.process = None

    def load_config(self) -> Optional[dict]:
        cfg = ConfigParser()
        cfg.read(os.path.expandvars(self.config['config']))
        return {s: dict(cfg.items(s)) for s in cfg.sections()}

    async def startup(self) -> bool:
        self.log.debug(r'Launching SRS server with: "{}\SR-Server.exe" -cfg="{}"'.format(
            os.path.expandvars(self.config['installation']), os.path.expandvars(self.config['config'])))
        self.process = subprocess.Popen(['SR-Server.exe', '-cfg={}'.format(
            os.path.expandvars(self.config['config']))],
                                        executable=os.path.expandvars(self.config['installation']) + r'\SR-Server.exe')
        return await self.check()

    async def shutdown(self):
        p = self.process or utils.find_process('SR-Server.exe', self.server['installation'])
        if p:
            p.kill()
            self.process = None
            return True
        else:
            return False

    async def check(self) -> bool:
        if self.process:
            return not self.process.poll()
        server_ip = self.locals['Server Settings']['server_ip']
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

    def render(self, embed: report.EmbedElement, param: dict):
        show_password = param['show_password'] if 'show_password' in param else True
        if show_password and self.locals['General Settings']['EXTERNAL_AWACS_MODE'.lower()] and \
                'External AWACS Mode Settings' in self.locals:
            blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'.lower()]
            red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'.lower()]
            value = f'🔹 Pass: {blue}\n🔸 Pass: {red}'
        else:
            value = '_ _'
        embed.add_field(name=f"SRS [{self.locals['Server Settings']['server_port']}]", value=value)


class LotAtc(Extension):
    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('"'):
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
        exp = re.compile(r'(?P<key>.*) = (?P<value>.*)')
        cfg = dict()
        installation = self.server['installation']
        if os.path.exists(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + '/Mods/services/LotAtc/config.lua'):
            with open(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + '/Mods/services/LotAtc/config.lua', 'r') as file:
                for line in file.readlines():
                    match = exp.match(line)
                    if match:
                        cfg[match.group('key')] = self.parse(match.group('value'))
        if os.path.exists(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + '/Mods/services/LotAtc/config.custom.lua'):
            with open(os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + '/Mods/services/LotAtc/config.custom.lua',
                      'r') as file:
                for line in file.readlines():
                    match = exp.match(line)
                    if match:
                        cfg[match.group('key')] = self.parse(match.group('value'))
        return cfg

    @property
    def version(self) -> str:
        installation = self.server['installation']
        info = win32api.GetFileVersionInfo(
            os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\services\LotAtc\bin\lotatc.dll', '\\')
        version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                info['FileVersionMS'] % 65536,
                                info['FileVersionLS'] / 65536)
        return version

    def render(self, embed: report.EmbedElement, param: dict):
        show_password = param['show_password'] if 'show_password' in param else True
        if show_password and 'blue_password' in self.locals and 'red_password' in self.locals:
            value = f"🔹 Pass: {self.locals['blue_password']}\n🔸 Pass: {self.locals['red_password']}"
        else:
            value = '_ _'
        if 'port' in self.locals:
            embed.add_field(name=f"LotAtc [{self.locals['port']}]", value=value)
        else:
            embed.add_field(name='LotAtc', value=value)


class Tacview(Extension):
    def _load_config(self) -> dict:
        if 'Tacview' in self.server['options']['plugins']:
            return self.server['options']['plugins']['Tacview']
        else:
            return dict()

    def load_config(self) -> Optional[dict]:
        if 'options' in self.server:
            return self._load_config()
        else:
            return None

    @property
    def version(self) -> str:
        installation = self.server['installation']
        info = win32api.GetFileVersionInfo(
            os.path.expandvars(self.bot.config[installation]['DCS_HOME']) + r'\Mods\tech\Tacview\bin\tacview.dll', '\\')
        version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                info['FileVersionMS'] % 65536,
                                info['FileVersionLS'] / 65536)
        return version

    def render(self, embed: report.EmbedElement, param: dict):
        if not self.locals:
            self.server = self.globals[self.server['server_name']]
            self.locals = self._load_config()
        name = 'Tacview'
        if ('tacviewModuleEnabled' in self.locals and not self.locals['tacviewModuleEnabled']) or \
                ('tacviewFlightDataRecordingEnabled' in self.locals and
                 not self.locals['tacviewFlightDataRecordingEnabled']):
            value = 'disabled'
        else:
            show_password = param['show_password'] if 'show_password' in param else True
            value = ''
            if 'tacviewRealTimeTelemetryEnabled' in self.locals and self.locals['tacviewRealTimeTelemetryEnabled']:
                name += ' RT'
                if show_password and 'tacviewRealTimeTelemetryPassword' in self.locals and \
                        len(self.locals['tacviewRealTimeTelemetryPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRealTimeTelemetryPassword']}\n"
            elif show_password and 'tacviewHostTelemetryPassword' in self.locals \
                    and len(self.locals['tacviewHostTelemetryPassword']) > 0:
                value += f"Password: {self.locals['tacviewHostTelemetryPassword']}\n"
            if 'tacviewRealTimeTelemetryPort' in self.locals and len(self.locals['tacviewRealTimeTelemetryPort']) > 0:
                name += f" [{self.locals['tacviewRealTimeTelemetryPort']}]"
            if 'tacviewRemoteControlEnabled' in self.locals and self.locals['tacviewRemoteControlEnabled']:
                value += f"**Remote Ctrl [{self.locals['tacviewRemoteControlPort']}]**\n"
                if show_password and 'tacviewRemoteControlPassword' in self.locals and \
                        len(self.locals['tacviewRemoteControlPassword']) > 0:
                    value += f"Password: {self.locals['tacviewRemoteControlPassword']}"
            if len(value) == 0:
                value = 'enabled'
        embed.add_field(name=name, value=value)
