from __future__ import annotations
import json
import os
import shutil
import yaml

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..const import DEFAULT_TAG

if TYPE_CHECKING:
    from .. import Server
    from ..data.impl.nodeimpl import NodeImpl


class Service(ABC):
    def __init__(self, node, name: str):
        self.name = name
        self.running: bool = False
        self.node: NodeImpl = node
        self.log = node.log
        self.pool = node.pool
        self.config = node.config
        self.locals = self.read_locals()
        self._config = dict[str, dict]()

    async def start(self, *args, **kwargs):
        self.log.info(f'  => Starting Service {self.name} ...')
        self.running = True

    async def stop(self, *args, **kwargs):
        self.running = False
        self.log.info(f'  => Service {self.name} stopped.')

    async def is_running(self) -> bool:
        return self.running

    def read_locals(self) -> dict:
        if os.path.exists(f'./config/{self.name}.json'):
            os.makedirs('./config/backup', exist_ok=True)
            with open(f'./config/{self.name.lower()}.json', 'r') as infile:
                data = json.load(infile)
            with open(f'./config/services/{self.name.lower()}.yaml', 'w') as outfile:
                yaml.dump(data, outfile, default_flow_style=False)
            shutil.move(f'./config/{self.name.lower()}.json', './config/backup')
        filename = f'./config/services/{self.name.lower()}.yaml'
        if not os.path.exists(filename):
            return {}
        self.log.debug(f'  - Reading service configuration from {filename} ...')
        return yaml.safe_load(Path(filename).read_text(encoding='utf-8'))

    def get_config(self, server: Optional[Server] = None) -> dict:
        if not server:
            return self.locals.get(DEFAULT_TAG)
        elif server.instance.name not in self._config:
            self._config[server.instance.name] = (self.locals.get(DEFAULT_TAG, {}) |
                                                  self.locals.get(server.instance.name, {}))
        return self._config[server.instance.name]


class ServiceInstallationError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f'Service "{service.title()}" could not be installed: {reason}')
