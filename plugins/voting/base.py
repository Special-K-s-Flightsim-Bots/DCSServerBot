from abc import ABC
from core import Server


class VotableItem(ABC):
    def __init__(self, name: str, server: Server, config: dict, params: list[str] | None = None):
        self.name = name
        self.server = server
        self.config = config
        self.param = params

    def can_vote(self) -> bool:
        return True

    async def print(self) -> str:
        ...

    async def get_choices(self) -> list[str]:
        ...

    async def execute(self, winner: str):
        ...
