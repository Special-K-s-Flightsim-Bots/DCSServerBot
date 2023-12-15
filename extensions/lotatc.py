import luadata
import os

from core import Extension, utils, Server
from typing import Optional

ports: dict[int, str] = dict()


class LotAtc(Extension):
    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'LotAtc')
        super().__init__(server, config)

    def load_config(self) -> Optional[dict]:
        cfg = {}
        for path in [os.path.join(self.home, 'config.lua'), os.path.join(self.home, 'config.custom.lua')]:
            with open(path, 'r', encoding='utf-8') as file:
                content = file.read()
            content = content.replace('lotatc_inst.options', 'cfg')
            cfg |= luadata.unserialize(content)
        return cfg

    async def prepare(self) -> bool:
        config = self.config.copy()
        if 'enabled' in config:
            del config['enabled']
        if 'show_passwords' in config:
            del config['show_passwords']
        if 'host' in config:
            del config['host']
        if len(config):
            new_cfg = self.locals
            new_cfg |= self.config
            del new_cfg['enabled']
            path = os.path.join(self.home, 'config.custom.lua')
            with open(path, 'wb') as outfile:
                outfile.write((f"lotatc_inst.options = " + luadata.serialize(new_cfg, indent='\t',
                                                                             indent_level=0)).encode('utf-8'))
            self.log.debug(f"  => New {path} written.")
        return True

    @property
    def version(self) -> str:
        return utils.get_windows_version(os.path.join(self.home, r'bin', 'lotatc.dll'))

    async def render(self, param: Optional[dict] = None) -> dict:
        if self.locals:
            host = self.config.get('host', self.node.public_ip)
            value = f"{host}:{self.locals.get('port', 10310)}"
            show_passwords = self.config.get('show_passwords', True)
            blue = self.locals.get('blue_password', '')
            red = self.locals.get('red_password', '')
            if show_passwords and (blue or red):
                value += f"\n🔹 Pass: {blue}\n🔸 Pass: {red}"
            return {
                "name": "LotAtc",
                "version": self.version,
                "value": value
            }
        else:
            return {}

    def is_installed(self) -> bool:
        global ports

        if (not os.path.exists(os.path.join(self.home, 'bin', 'lotatc.dll')) or
                not os.path.exists(os.path.join(self.home, 'config.lua'))):
            self.log.error(f"  => {self.server.name}: Can't load extension, LotAtc not correctly installed.")
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
