from __future__ import annotations

import asyncio
import logging

from abc import ABC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server, Port

__all__ = [
    "Extension",
    "ExtensionException",
    "InstallException",
    "UninstallException"
]


class ExtensionException(Exception):
    ...


class InstallException(ExtensionException):
    ...


class UninstallException(ExtensionException):
    ...


class Extension(ABC):
    started_schedulers = set()
    CONFIG_DICT = {}

    def __init__(self, server: Server, config: dict):
        self.node = server.node
        self.log = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
        self.pool = self.node.pool
        self.loop = asyncio.get_event_loop()
        self.lock = asyncio.Lock()
        self.config: dict = config
        self.server: Server = server
        self.running = False
        self.locals: dict = {}
        if self.config.get('name'):
            self._name = self.config['name']
        else:
            self._name = self.__class__.__name__
        if not self.enabled or not self.is_installed():
            return
        self.locals = self.load_config()
        if self.__class__.__name__ not in Extension.started_schedulers:
            schedule = getattr(self, 'schedule', None)
            if schedule:
                schedule.start()
            Extension.started_schedulers.add(self.__class__.__name__)

    def load_config(self) -> dict | None:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return filename, False

    async def startup(self, *, quiet: bool = False) -> bool:
        if not self.config.get('enabled', True):
            return False
        self.running = True
        if self.is_running():
            self.loop.create_task(self.server.send_to_dcs({
                "command": "addExtension",
                "extension": self.name
            }))
            if not quiet:
                self.log.info(f"  => {self.name} launched for \"{self.server.name}\".")
            return True
        else:
            if not quiet:
                self.log.warning(f"  => {self.name} NOT launched for \"{self.server.name}\".")
            return False

    def shutdown(self, *, quiet: bool = False) -> bool:
        # unregister extension from DCS
        self.loop.create_task(self.server.send_to_dcs({
            "command": "removeExtension",
            "extension": self.name
        }))
        # avoid race conditions
        if not self.is_running():
            return True
        self.running = False
        if not quiet:
            self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        return True

    def is_running(self) -> bool:
        return self.running

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> str | None:
        return None

    @property
    def enabled(self) -> bool:
        return self.config.get('enabled', True)

    async def enable(self):
        self.config['enabled'] = True
        if not self.is_running():
            asyncio.create_task(self.startup())

    async def disable(self):
        if self.is_running():
            await asyncio.to_thread(self.shutdown)
        self.config['enabled'] = False

    async def render(self, param: dict | None = None) -> dict:
        raise NotImplementedError()

    def is_installed(self) -> bool:
        return self.enabled

    async def install(self):
        pass

    async def uninstall(self):
        pass

    async def get_config(self, **kwargs) -> dict:
        return self.config

    def get_ports(self) -> dict[str, Port]:
        return {}

    async def change_config(self, config: dict):
        self.config |= config
