from abc import ABC, abstractmethod
from typing import Any


class Extension(ABC):

    def __init__(self, bot: Any, server: dict):
        self.bot = bot
        self.log = bot.log
        self.pool = bot.pool
        self.config = bot.config
        self.globals = bot.globals
        self.server = server
        if 'extensions' not in self.server:
            self.server['extensions'] = dict()
        self.server['extensions'][self.name] = self

    def load_config(self):
        pass

    async def startup(self) -> bool:
        return True

    async def shutdown(self) -> bool:
        return True

    async def check(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    @abstractmethod
    def version(self) -> str:
        pass
