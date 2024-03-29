from __future__ import annotations
import os

from abc import ABC
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable, Any

from ..const import DEFAULT_TAG
from ..utils.helper import YAMLError
from ..data.dataobject import DataObject

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
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
            }, node=server.node.name)
            return data.get('return')
        return await original_function(self, server, *args, **kwargs)

    return wrapper


class Service(ABC):
    def __init__(self, node: NodeImpl, name: Optional[str] = None):
        self.name = name or self.__class__.__name__
        self.running: bool = False
        self.node: NodeImpl = node
        self.log = node.log
        self.pool = node.pool
        self.apool = node.apool
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
        filename = os.path.join(self.node.config_dir, 'services', f'{self.name.lower()}.yaml')
        if not os.path.exists(filename):
            return {}
        self.log.debug(f'  - Reading service configuration from {filename} ...')
        try:
            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except (ParserError, ScannerError) as ex:
            raise YAMLError(filename, ex)

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
