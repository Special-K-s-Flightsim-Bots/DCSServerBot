from __future__ import annotations

import json
import os
import subprocess
from typing import Optional
from core import Extension, report, DCSServerBot, Server, Status


class Sneaker(Extension):
    _instance = None
    _process = None
    _servers: set[str] = set()

    def __new__(cls, bot: DCSServerBot, server: Server, config: dict) -> Sneaker:
        if cls._instance is None:
            cls._instance = super(Sneaker, cls).__new__(cls)
            cls.bot = bot
            cls.log = bot.log
            cls.server = server
            cls.config = config
        return cls._instance

    def create_config(self):
        cfg = {"servers": []}
        if os.path.exists('config\\sneaker.json'):
            with open('config\\sneaker.json') as file:
                cfg = json.load(file)
        for s in cfg['servers']:
            if s['name'] == self.server.name:
                s['port'] = int(self.server.options['plugins']['Tacview']['tacviewRealTimeTelemetryPort'])
                break
        else:
            cfg['servers'].append({
                "name": self.server.name,
                "hostname": self.server.host,
                "port": int(self.server.options['plugins']['Tacview']['tacviewRealTimeTelemetryPort']),
                "radar_refresh_rate": 5,
                "enable_friendly_ground_units": True,
                "enable_enemy_ground_units": True
            })
        # filter out servers that are not running
        cfg['servers'] = [x for x in cfg['servers'] if x['name'] in [y.name for y in self.bot.servers.values() if y.status not in [Status.UNREGISTERED, Status.SHUTDOWN]]]
        with open('config\\sneaker.json', 'w') as file:
            json.dump(cfg, file, indent=2)

    async def startup(self) -> bool:
        if 'Tacview' not in self.server.options['plugins']:
            self.log.warning('Sneaker needs Tacview to be enabled in your server!')
            return False
        if 'config' not in self.config:
            self.create_config()
            if self._process:
                self._process.kill()
            cmd = os.path.basename(self.config['cmd'])
            self._process = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config", 'config\\sneaker.json'],
                                             executable=os.path.expandvars(self.config['cmd']))
        else:
            if not self._process:
                cmd = os.path.basename(self.config['cmd'])
                self._process = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config",
                                                  os.path.expandvars(self.config['config'])],
                                                 executable=os.path.expandvars(self.config['cmd']))
        self._servers.add(self.server.name)
        return True

    async def shutdown(self) -> bool:
        self._servers.remove(self.server.name)
        if not self._servers:
            self._process.kill()
            self._process = None
        elif 'config' not in self.config:
            self.create_config()
            self._process.kill()
            cmd = os.path.basename(self.config['cmd'])
            self._process = subprocess.Popen([cmd, "--bind", self.config['bind'], "--config", 'config\\sneaker.json'],
                                             executable=os.path.expandvars(self.config['cmd']))
        return True

    async def is_running(self) -> bool:
        return self._process is not None

    @property
    def version(self) -> str:
        return "0.0.9"

    def verify(self) -> bool:
        # check if Sneaker is enabled
        if 'enabled' not in self.config or not self.config['enabled']:
            return False
        # check if Sneaker is installed
        if 'cmd' not in self.config or not os.path.exists(os.path.expandvars(self.config['cmd'])):
            self.log.warning("Sneaker executable not found!")
            return False
        return True

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        embed.add_field(name='Sneaker', value='enabled')
