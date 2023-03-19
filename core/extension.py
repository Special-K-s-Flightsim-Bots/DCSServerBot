from __future__ import annotations
from abc import ABC, abstractmethod
from core import report
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import DCSServerBot, Server


class Extension(ABC):

    def __init__(self, bot: DCSServerBot, server: Server, config: dict):
        self.bot: DCSServerBot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.config: dict = config
        self.server: Server = server
        self.locals: dict = self.load_config()
        self.lastrun = datetime.now()

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self) -> bool:
        return True

    async def onMissionLoadEnd(self, data: dict) -> bool:
        return True

    async def startup(self) -> bool:
        self.log.info(f"  => {self.name} v{self.version} launched for \"{self.server.name}\".")
        await self.bot.audit(f"Extension {self.name} started", server=self.server)
        return True

    async def shutdown(self, data: dict) -> bool:
        self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        await self.bot.audit(f"Extension {self.name} shut down", server=self.server)
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

    async def schedule(self):
        pass

    def verify(self) -> bool:
        pass
