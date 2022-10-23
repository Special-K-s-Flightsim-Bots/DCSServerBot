import os
import re
import win32api
from core import Extension, report
from typing import Any, Optional, TextIO


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
            value = f"{self.bot.external_ip}:{self.locals['port']}" if 'port' in self.locals else ''
            show_passwords = self.config['show_passwords'] if 'show_passwords' in self.config else True
            blue = self.locals['blue_password'] if 'blue_password' in self.locals else ''
            red = self.locals['red_password'] if 'red_password' in self.locals else ''
            if show_passwords and (blue or red):
                value += f"\n🔹 Pass: {blue}\n🔸 Pass: {red}"
            if not len(value):
                value = '_ _'
            embed.add_field(name='LotAtc', value=value)

    def verify(self) -> bool:
        if not os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              '/Mods/services/LotAtc/bin/lotatc.dll'):
            return False
        if not os.path.exists(os.path.expandvars(self.bot.config[self.server.installation]['DCS_HOME']) +
                              '/Mods/services/LotAtc/config.lua'):
            return False
        return True
