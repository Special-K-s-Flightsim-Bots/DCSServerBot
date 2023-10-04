from __future__ import annotations
from abc import ABC
from core import report
from typing import Optional, TYPE_CHECKING, Tuple

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

    async def beforeMissionLoad(self, filename: str) -> Tuple[str, bool]:
        return filename, False

    async def startup(self) -> bool:
        schedule = getattr(self, 'schedule', None)
        if schedule and not schedule.is_running():
            schedule.start()
        self.running = True
        self.log.info(f"  => {self.name} launched for \"{self.server.name}\".")
        return True

    async def shutdown(self) -> bool:
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

    def render(self, embed: report.EmbedElement, param: Optional[dict] = None):
        raise NotImplementedError()

    def is_installed(self) -> bool:
        pass
