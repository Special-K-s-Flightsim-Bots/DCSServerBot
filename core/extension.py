from __future__ import annotations
from abc import ABC, abstractmethod
from core import report
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import DCSServerBot, Server


class Extension(ABC):

    def __init__(self, server: Server, config: dict):
        self.node = server.node
        self.log = self.node.log
        self.pool = self.node.pool
        self.config: dict = config
        self.server: Server = server
        self.locals: dict = self.load_config()

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self) -> bool:
        return True

    async def startup(self) -> bool:
        schedule = self.__class__.__dict__.get('schedule')
        if schedule and not schedule.is_running():
            schedule.start(self)
        self.log.info(f"  => {self.name} v{self.version} launched for \"{self.server.name}\".")
        return True

    async def shutdown(self) -> bool:
        schedule = self.__class__.__dict__.get('schedule')
        if schedule and schedule.is_running():
            schedule.cancel(self)
        self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        return True

    def is_running(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError()

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        raise NotImplementedError()

    def is_installed(self) -> bool:
        pass
