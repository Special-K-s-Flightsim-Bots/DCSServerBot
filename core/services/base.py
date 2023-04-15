import platform
from abc import ABC


class Service(ABC):
    def __init__(self, main):
        self.running: bool = False
        self.main = main
        self.log = main.log
        self.pool = main.pool
        self.config = main.config
        self.agent = platform.node()

    async def start(self, *args, **kwargs):
        self.running = True

    async def stop(self, *args, **kwargs):
        self.running = False

    async def is_running(self) -> bool:
        return self.running
