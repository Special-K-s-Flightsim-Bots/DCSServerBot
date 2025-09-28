from __future__ import annotations

import asyncio
import logging

from abc import ABC
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server

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

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return filename, False

    async def startup(self) -> bool:
        self.running = True
        if self.is_running():
            self.log.info(f"  => {self.name} launched for \"{self.server.name}\".")
            return True
        else:
            self.log.warning(f"  => {self.name} NOT launched for \"{self.server.name}\".")
            return False

    def shutdown(self) -> bool:
        # avoid race conditions
        if not self.is_running():
            return True
        self.running = False
        self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        return True

    def is_running(self) -> bool:
        return self.running

    @property
    def name(self) -> str:
        return self._name

    @property
    def version(self) -> Optional[str]:
        return None

    @property
    def enabled(self) -> bool:
        return self.config.get('enabled', True)

    async def enable(self):
        self.config['enabled'] = True

    async def disable(self):
        if self.is_running():
            await asyncio.to_thread(self.shutdown)
        self.config['enabled'] = False

    async def render(self, param: Optional[dict] = None) -> dict:
        raise NotImplementedError()

    def is_installed(self) -> bool:
        return self.enabled

    async def install(self):
        ...

    async def uninstall(self):
        ...

    async def get_config(self, **kwargs) -> dict:
        return self.config

    async def get_ports(self) -> dict:
        return {}

    async def change_config(self, config: dict):
        self.config |= config
