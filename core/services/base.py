from __future__ import annotations
import os

from abc import ABC
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..const import DEFAULT_TAG
from ..utils.helper import YAMLError

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
yaml = YAML()

if TYPE_CHECKING:
    from .. import Server
    from ..data.impl.nodeimpl import NodeImpl

__all__ = [
    "Service",
    "ServiceInstallationError"
]


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

    def is_running(self) -> bool:
        return self.running

    def read_locals(self) -> dict:
        filename = f'./config/services/{self.name.lower()}.yaml'
        if not os.path.exists(filename):
            return {}
        self.log.debug(f'  - Reading service configuration from {filename} ...')
        try:
            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except (ParserError, ScannerError) as ex:
            raise YAMLError(filename, ex)

    def get_config(self, server: Optional[Server] = None) -> dict:
        if not server:
            return self.locals.get(DEFAULT_TAG)
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name]:
            self._config[server.node.name][server.instance.name] = (
                    self.locals.get(DEFAULT_TAG, {}) |
                    self.locals.get(server.node.name, self.locals).get(server.instance.name, {})
            )
        return self._config[server.node.name][server.instance.name]


class ServiceInstallationError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f'Service "{service.title()}" could not be installed: {reason}')
