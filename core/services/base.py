from __future__ import annotations

import asyncio
import logging
import os

from abc import ABC
from core import utils
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable, Any

from ..const import DEFAULT_TAG
from ..data.dataobject import DataObject

# ruamel YAML support
from pykwalify.errors import PyKwalifyException
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

if TYPE_CHECKING:
    from core import Server, NodeImpl

__all__ = [
    "proxy",
    "Service",
    "ServiceInstallationError"
]


def proxy(original_function: Callable[..., Any]):
    """
    Can be used as a decorator to any service method, that should act as a remote call, if the server provided
    is not on the same node.

    @proxy
    async def my_fancy_method(self, server: Server, *args, **kwargs) -> Any:
        ...

    This will call my_fancy_method on the remote node, if the server is remote, and on the local node, if it is not.
    """
    @wraps(original_function)
    async def wrapper(self, server: Server, *args, **kwargs):
        # Get argument names from the original function
        arg_names = list(original_function.__annotations__.keys()) if hasattr(original_function,
                                                                              "__annotations__") else []

        # Prepare params by dereferencing DataObject instances to their names,
        # while matching argument names with values.
        params = {
            k: v.name if isinstance(v, DataObject)
            else v.value if isinstance(v, Enum)
            else v
            for k, v in zip(arg_names[1:], args)
            if v is not None
        }

        if server.is_remote:
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": self.__class__.__name__,
                "method": original_function.__name__,
                "params": {"server": server.name} | params
            }, node=server.node.name, timeout=60)
            return data.get('return')
        return await original_function(self, server, *args, **kwargs)

    return wrapper


class Service(ABC):
    dependencies: list[type[Service]] = None

    def __init__(self, node: NodeImpl, name: Optional[str] = None):
        self.name = name or self.__class__.__name__
        self.running: bool = False
        self.node: NodeImpl = node
        self.log = logging.getLogger(__name__)
        self.pool = node.pool
        self.apool = node.apool
        self.config = node.config
        self.locals = self.read_locals()
        self._config = dict[str, dict]()

    async def start(self, *args, **kwargs):
        from .registry import ServiceRegistry

        self.log.info(f'  => Starting Service {self.name} ...')
        if self.dependencies:
            for dependency in self.dependencies:
                for i in range(30):
                    if ServiceRegistry.get(dependency).is_running():
                        break
                    self.log.debug(f"Waiting for service {dependency} ...")
                    await asyncio.sleep(.1)
                else:
                    raise TimeoutError(f"Timeout during start of Service {self.__class__.__name__}, "
                                       f"dependent service {dependency.__name__} is not running.")
                self.log.debug(f"Dependent service {dependency.__name__} is running.")
        self.running = True

    async def stop(self, *args, **kwargs):
        self.running = False
        self.log.info(f'  => Service {self.name} stopped.')

    async def switch(self):
        ...

    def is_running(self) -> bool:
        return self.running

    def read_locals(self) -> dict:
        filename = os.path.join(self.node.config_dir, 'services', f'{self.name.lower()}.yaml')
        if not os.path.exists(filename):
            return {}
        self.log.debug(f'  - Reading service configuration from {filename} ...')
        try:
            path = os.path.join('services', self.name.lower(), 'schemas')
            validation = self.node.config.get('validation', 'lazy')
            if os.path.exists(path) and validation in ['strict', 'lazy']:
                schema_files = [str(x) for x in Path(path).glob('*.yaml')]
                utils.validate(filename, schema_files, raise_exception=(validation == 'strict'))

            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except (MarkedYAMLError, PyKwalifyException) as ex:
            raise ServiceInstallationError(self.name, ex.__str__())

    def save_config(self):
        with open(os.path.join(self.node.config_dir, 'services', self.name + '.yaml'),
                  mode='w', encoding='utf-8') as outfile:
            yaml.dump(self.locals, outfile)

    def get_config(self, server: Optional[Server] = None) -> dict:
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name]:
            self._config[server.node.name][server.instance.name] = (
                    self.locals.get(DEFAULT_TAG, {}) |
                    self.locals.get(server.node.name, self.locals).get(server.instance.name, {})
            )
        return self._config.get(server.node.name, {}).get(server.instance.name, {})


class ServiceInstallationError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f'Service "{service.title()}" could not be installed: {reason}')
