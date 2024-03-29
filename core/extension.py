from __future__ import annotations
from abc import ABC
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server

__all__ = ["Extension"]


class Extension(ABC):

    def __init__(self, server: Server, config: dict):
        self.node = server.node
        self.log = self.node.log
        self.pool = self.node.pool
        self.config: dict = config
        self.server: Server = server
        self.locals: dict = self.load_config()
        self.running = False

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return filename, False

    async def startup(self) -> bool:
        # avoid race conditions
        if self.is_running():
            return True
        schedule = getattr(self, 'schedule', None)
        if schedule and not schedule.is_running():
            schedule.start()
        self.running = True
        self.log.info(f"  => {self.name} launched for \"{self.server.name}\".")
        return True

    def shutdown(self) -> bool:
        # avoid race conditions
        if not self.is_running():
            return True
        schedule = getattr(self, 'schedule', None)
        if schedule and schedule.is_running():
            schedule.cancel()
        self.running = False
        self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        return True

    def is_running(self) -> bool:
        return self.running

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def version(self) -> Optional[str]:
        return None

    async def render(self, param: Optional[dict] = None) -> dict:
        raise NotImplementedError()

    def is_installed(self) -> bool:
        ...
