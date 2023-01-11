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

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self) -> bool:
        return False

    async def onMissionLoadEnd(self, data: dict):
        return False

    async def onMissionEnd(self, data: dict):
        return False

    async def startup(self) -> bool:
        return False

    async def shutdown(self) -> bool:
        return False

    async def is_running(self) -> bool:
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

    @staticmethod
    def schedule(config: dict, lastrun: Optional[datetime] = None):
        pass

    def verify(self) -> bool:
        pass
