from __future__ import annotations

import inspect
import logging
import os

from abc import ABC
from core import utils, Port
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Callable, Any, TYPE_CHECKING

from core.const import DEFAULT_TAG
from core.data.dataobject import DataObject

if TYPE_CHECKING:
    from core import Server, NodeImpl

# ruamel YAML support
from pykwalify.errors import PyKwalifyException
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()

__all__ = [
    "proxy",
    "Service",
    "ServiceInstallationError"
]

logger = logging.getLogger(__name__)


def proxy(_func: Callable[..., Any] | None = None, *, timeout: float = 60):
    """
    Can be used as a decorator to any service method, that should act as a remote call, if the server provided
    is not on the same node.

    @proxy
    async def my_fancy_method(self, server: Server, *args, **kwargs) -> Any:
        ...

    This will call my_fancy_method on the remote node if the server is remote, and on the local node, if it is not.
    """
    def decorator(original_function: Callable[..., Any]):
        @wraps(original_function)
        async def wrapper(self, *args, **kwargs):
            signature = inspect.signature(original_function)
            bound_args = signature.bind(self, *args, **kwargs)
            bound_args.apply_defaults()
            arg_dict = {k: v for k, v in bound_args.arguments.items() if k != "self"}

            # Dereference DataObject and Enum values in parameters
            params = {
                k: v.name if isinstance(v, DataObject)
                else v.value if isinstance(v, Enum)
                else v
                for k, v in arg_dict.items()
                if v is not None  # Ignore None values
            }

            call = {
                "command": "rpc",
                "service": self.__class__.__name__,
                "method": original_function.__name__,
                "params": params
            }

            # Try to pick the node from the functions arguments
            node = None
            if arg_dict.get("server"):
                node = arg_dict["server"].node
            elif arg_dict.get("instance"):
                node = arg_dict["instance"].node
            elif arg_dict.get("node"):
                node = arg_dict["node"]

            # Log an error if no valid object is found
            if node is None:
                raise ValueError(
                    f"Cannot proxy function {original_function.__name__}: no valid reference object found in arguments. "
                    f"Expected 'server', 'instance', or 'node' parameter with valid node reference.")

            # If the node is remote, send the call synchronously
            if node.is_remote:
                data = await self.bus.send_to_node_sync(call, node=node.name, timeout=timeout)
                return data

            # Otherwise, call the original function directly
            return await original_function(self, *args, **kwargs)
        return wrapper

    # If used as @proxy(timeout=nn)
    if _func is None:
        return decorator

    # If used as @proxy without parentheses
    return decorator(_func)


class Service(ABC):

    def __init__(self, node: NodeImpl, name: str | None = None):
        self.name = name or self.__class__.__name__
        self.running: bool = False
        self.node = node
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.pool = node.pool
        self.apool = node.apool
        self.config = node.config
        self.locals = self.read_locals()
        self._config = dict[str, dict]()

    async def start(self, *args, **kwargs):
        """Start this service. If called from a cascade, we only start
        ourselves; otherwise we first ask the registry to start all
        dependencies, then start ourselves."""

        from .registry import ServiceRegistry

        # Registry-driven call – we are already inside a cascade
        if getattr(self, "_in_registry_cascade", False):
            self.log.info(f'  => Starting Service {self.name} ...')
            self.running = True
            return

        # Direct start – trigger dependency startup first
        self._in_registry_cascade = True
        try:
            await ServiceRegistry.start_service(self.__class__)
        finally:
            self._in_registry_cascade = False

        # After dependencies are up, start ourselves
        # (but only if we haven’t already been started by the cascade)
        if not self.running:
            self.log.info(f'  => Starting Service {self.name} ...')
            self.running = True

    async def stop(self, *args, **kwargs):
        """Stop this service.  If called from a cascade, we only stop
        ourselves; otherwise we first ask the registry to kill all
        dependents, then stop ourselves."""

        from .registry import ServiceRegistry

        # Registry‑driven call – we are already inside a cascade
        if getattr(self, "_in_registry_cascade", False):
            self.running = False
            self.log.info(f'  => Service {self.name} stopped.')
            return

        # Direct stop – trigger the cascade first
        self._in_registry_cascade = True
        try:
            await ServiceRegistry.stop_service(self.__class__)
        finally:
            self._in_registry_cascade = False

        # After dependents are gone, stop ourselves
        # (but only if we haven’t already been stopped by the cascade)
        if self.running:
            self.running = False
            self.log.info(f'  => Service {self.name} stopped.')

    async def switch(self, master: bool):
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
                if schema_files:
                    utils.validate(filename, schema_files, raise_exception=(validation == 'strict'))
                else:
                    self.log.warning(f'No schema file for service "{self.name}" found.')

            return yaml.load(Path(filename).read_text(encoding='utf-8'))
        except (MarkedYAMLError, PyKwalifyException) as ex:
            raise ServiceInstallationError(self.name, ex.__str__())

    def save_config(self):
        with open(os.path.join(self.node.config_dir, 'services', f'{self.name.lower()}.yaml'),
                  mode='w', encoding='utf-8') as outfile:
            yaml.dump(self.locals, outfile)

    def get_config(self, server: Server | None = None, **kwargs) -> dict:
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name]:
            self._config[server.node.name][server.instance.name] = utils.deep_merge(
                    self.locals.get(DEFAULT_TAG, {}),
                    self.locals.get(server.node.name, self.locals).get(server.instance.name, {})
            )
        return self._config.get(server.node.name, {}).get(server.instance.name, {})

    def reload(self):
        self.locals = self.read_locals()

    def get_ports(self) -> dict[str, Port]:
        return {}


class ServiceInstallationError(Exception):
    def __init__(self, service: str, reason: str):
        super().__init__(f'Service "{service.title()}" could not be installed: {reason}')
