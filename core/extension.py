from __future__ import annotations

import asyncio
import logging

from abc import ABC

from aiohttp import ClientResponseError

from core import Status
from core.services.registry import ServiceRegistry
from typing import TYPE_CHECKING
from typing_extensions import override

if TYPE_CHECKING:
    from core import Server, Port

__all__ = [
    "Extension",
    "ExtensionException",
    "InstallException",
    "UninstallException",
    "InstallableExtension"
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
        if not self.enabled:
            return
        self.locals = self.load_config()
        if self.__class__.__name__ not in Extension.started_schedulers:
            schedule = getattr(self, 'schedule', None)
            if schedule:
                schedule.start()
            Extension.started_schedulers.add(self.__class__.__name__)

    def load_config(self) -> dict:
        return dict()

    async def prepare(self) -> bool:
        if not self.is_available():
            raise InstallException(f"{self.name} is not installed.")
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

    def is_available(self) -> bool:
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

    async def enable(self) -> bool:
        self.config['enabled'] = True
        if self.server.status in [Status.RUNNING, Status.PAUSED]:
            if not self.is_running():
                return await self.startup()
            return True
        else:
            return await self.prepare()

    async def disable(self) -> bool:
        if self.is_running():
            if not await asyncio.to_thread(self.shutdown):
                return False
        self.config['enabled'] = False
        return True

    async def render(self, param: dict | None = None) -> dict:
        raise NotImplementedError()

    async def get_config(self, **kwargs) -> dict:
        return self.config

    def get_ports(self) -> dict[str, Port]:
        return {}

    async def change_config(self, config: dict):
        self.config |= config


class InstallableExtension(Extension):
    def __init__(self, server: Server, config: dict, repo: str | None = None, package_name: str | None = None):
        from services.modmanager import ModManagerService

        super().__init__(server, config)
        self.service: ModManagerService | None = ServiceRegistry.get(ModManagerService)
        self.repo = repo
        self.package_name = package_name

    def is_installed(self) -> bool:
        raise NotImplementedError()

    @property
    def autoupdate(self) -> bool:
        return self.config.get('autoupdate', False)

    @override
    async def prepare(self):
        # check if the extension is installed
        if not self.is_installed() and not await self.install():
            self.log.warning(f"  => {self.name}: Mod not installed, skipping.")
            return False

        if self.autoupdate:
            available_version = await self.update_available()
            if available_version:
                await self.update(available_version)

        return await super().prepare()

    async def update_available(self) -> str | None:
        available_version = await self.get_latest_version()
        if available_version != self.version:
            return available_version
        else:
            return None

    async def get_latest_version(self) -> str | None:
        if not self.service:
            return self.version
        if self.repo:
            try:
                latest = await self.service.get_latest_repo_version(self.repo)
            except ClientResponseError:
                self.log.warning(f"Failed to fetch latest version for {self.name} from repository, skipping.")
                return self.version
        else:
            from services.modmanager import Folder

            latest = await self.service.get_latest_version({
                "name": self.package_name,
                "source": Folder.SavedGames.value
            })
        return latest if latest else self.version

    async def install(self, version: str | None = None) -> bool:
        from services.modmanager import Folder

        if not self.service:
            self.log.error(f"  => {self.name}: To install, please enable ModManager service!")
            return False

        if not await self.service.get_installed_package(self.server, Folder.SavedGames, self.package_name):
            if not version:
                version = await self.get_latest_version()
            return await self.service.install_package(
                self.server,
                folder=Folder.SavedGames,
                package_name=self.package_name,
                version=version,
                repo=self.repo
            )
        else:
            self.log.info(f"  => {self.name}: Mod already installed.")
            return False

    async def uninstall(self) -> bool:
        if not self.is_installed():
            return True
        if not self.service:
            self.log.error(f"  => {self.name}: ModManager service not active, cannot uninstall!")
            return False

        from services.modmanager import Folder

        return await self.service.uninstall_package(
            self.server, Folder.SavedGames, self.package_name, self.version, self.repo
        )

    async def update(self, version: str | None = None) -> bool:
        if version == self.version:
            return False
        if await self.uninstall():
            return await self.install(version)
        return False

    async def repair(self) -> bool:
        version = self.version
        if await self.uninstall():
           return await self.install(version)
        return False

    async def enable(self) -> bool:
        if not self.is_installed():
            if not await self.install():
                return False
        return await super().enable()

    async def disable(self) -> bool:
        if not await super().disable():
            return False
        if self.is_installed():
            return await self.uninstall()
        return True
