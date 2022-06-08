from abc import ABC, abstractmethod
from core import report
from datetime import datetime
from typing import Any, Optional


class Extension(ABC):

    def __init__(self, bot: Any, server: dict, config: dict):
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.config = config
        self.globals = bot.globals
        self.server = server
        self.locals = self.load_config()

    def load_config(self) -> Optional[dict]:
        return dict()

    async def startup(self) -> bool:
        return False

    async def shutdown(self) -> bool:
        return False

    async def check(self) -> bool:
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
