import os
import re

from core import Extension, Server, get_translation
from typing import Optional, Any, TextIO

_ = get_translation(__name__.split('.')[1])

ports: dict[int, str] = dict()

__all__ = [
    "gRPC"
]


class gRPC(Extension):

    CONFIG_DICT = {
        "port": {
            "type": int,
            "label": _("Port"),
            "required": True
        }
    }

    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'tech', 'DCS-gRPC')
        super().__init__(server, config)

    @property
    def name(self):
        return 'DCS-gRPC'

    @staticmethod
    def parse(value: str) -> Any:
        if value.startswith('{'):
            return value[1:-1].split(',')
        elif value.startswith('"'):
            return value.strip('"')
        elif value.startswith("'"):
            return value.strip("'")
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        elif '.' in value:
            return float(value)
        else:
            return int(value)

    @staticmethod
    def unparse(value: Any) -> str:
        if isinstance(value, bool):
            return value.__repr__().lower()
        elif isinstance(value, str):
            return '"' + value + '"'
        else:
            return value

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
        path = os.path.join(self.server.instance.home, 'Config', 'dcs-grpc.lua')
        cfg = dict()
        if os.path.exists(path):
            with open(path, mode='r', encoding='utf-8') as file:
                read_file(file, cfg)
        return cfg

    async def prepare(self) -> bool:
        config = self.config.copy()
        filename = os.path.join(self.node.installation, 'Scripts', 'MissionScripting.lua')
        with open(filename, mode='r', encoding='utf-8') as infile:
            orig = infile.readlines()
        dirty = False
        for idx, line in enumerate(orig):
            if ("dofile('Scripts/ScriptingSystem.lua')" in line and
                    r"dofile(lfs.writedir()..[[Scripts\DCS-gRPC\grpc-mission.lua]])" not in orig[idx+1]):
                orig.insert(idx+1, r"dofile(lfs.writedir()..[[Scripts\DCS-gRPC\grpc-mission.lua]])")
                dirty = True
                break
        if dirty:
            with open(filename, mode='w', encoding='utf-8') as outfile:
                outfile.writelines(orig)
            self.log.info(f"  => {self.name}: MissionScripting.lua amended.")
        if 'enabled' in config:
            del config['enabled']
        if len(config):
            self.locals = self.locals | config
            self.locals['autostart'] = True
            extension = self.server.extensions.get('SRS')
            if extension:
                srs_port = extension.config.get('port', extension.locals['Server Settings']['SERVER_PORT'])
                self.locals['srs.addr'] = f"127.0.0.1:{srs_port}"
            path = os.path.join(self.server.instance.home, 'Config', 'dcs-grpc.lua')
            with open(path, mode='w', encoding='utf-8') as outfile:
                for key, value in self.locals.items():
                    outfile.write(f"{key} = {self.unparse(value)}\n")
        port = self.locals.get('port', 50051)
        if ports.get(port, self.server.name) != self.server.name:
            self.log.error(f"  => {self.server.name}: {self.name} port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        host = self.locals.get('host', '127.0.0.1')
        if host != '127.0.0.1':
            self.log.warning(
                'Please consider changing the host in your dcs-grpc.lua to 127.0.0.1 for security reasons!')
        return await super().prepare()

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        if not os.path.exists(os.path.join(self.home, 'dcs_grpc.dll')):
            self.log.error(f"  => {self.server.name}: Can't load extension, {self.name} not correctly installed.")
            return False
        return True

    async def get_ports(self) -> dict:
        return {
            "gRPC": self.locals.get('port', 50051)
        } if self.enabled else {}
