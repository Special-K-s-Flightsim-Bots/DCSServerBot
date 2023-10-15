import os
import re
import sys
if sys.platform == 'win32':
    import win32api

from core import Extension, report
from typing import Any, Optional, TextIO

ports: dict[int, str] = dict()


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
        instance = self.server.instance
        if os.path.exists(os.path.join(instance.home, 'Mods/services/LotAtc/config.lua')):
            with open(os.path.join(instance.home, 'Mods/services/LotAtc/config.lua'), 'r') as file:
                read_file(file, cfg)
        if os.path.exists(os.path.join(instance.home, 'Mods/services/LotAtc/config.custom.lua')):
            with open(os.path.join(instance.home, 'Mods/services/LotAtc/config.custom.lua'), 'r') as file:
                read_file(file, cfg)
        return cfg

    @property
    def version(self) -> str:
        path = os.path.join(self.server.instance.home, r'Mods\services\LotAtc\bin\lotatc.dll')
        if sys.platform == 'win32' and os.path.exists(path):
            info = win32api.GetFileVersionInfo(path, '\\')
            version = "%d.%d.%d" % (info['FileVersionMS'] / 65536,
                                    info['FileVersionMS'] % 65536,
                                    info['FileVersionLS'] / 65536)
        else:
            version = 'n/a'
        return version

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        if self.locals:
            host = self.config.get('host', self.node.public_ip)
            value = f"{host}:{self.locals.get('port', 10310)}"
            show_passwords = self.config.get('show_passwords', True)
            blue = self.locals.get('blue_password', '')
            red = self.locals.get('red_password', '')
            if show_passwords and (blue or red):
                value += f"\n🔹 Pass: {blue}\n🔸 Pass: {red}"
            embed.add_field(name='LotAtc', value=value)

    def is_installed(self) -> bool:
        global ports

        if not os.path.exists(os.path.join(self.server.instance.home, 'Mods/services/LotAtc/bin/lotatc.dll')):
            return False
        if not os.path.exists(os.path.join(self.server.instance.home, 'Mods/services/LotAtc/config.lua')):
            return False
        port = self.locals.get('port', 10310)
        if port in ports and ports[port] != self.server.name:
            self.log.error(f"  => LotAtc port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        return True

    async def shutdown(self) -> bool:
        return True

    def is_running(self) -> bool:
        return True
