from __future__ import annotations
from abc import ABC
from typing import Union, TypeVar, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core import DCSServerBot, Plugin


class EventListener(ABC):

    def __init__(self, plugin):
        self.plugin: Plugin = plugin
        self.plugin_name = type(self).__module__.split('.')[-2]
        self.bot: DCSServerBot = plugin.bot
        self.log = plugin.log
        self.pool = plugin.pool
        self.locals: dict = plugin.locals
        self.loop = plugin.loop
        self.commands: list[str] = [m for m in dir(self) if m not in dir(EventListener) and not m.startswith('_')]

    async def processEvent(self, data: dict[str, Union[str, int]]) -> Any:
        if data['command'] in self.commands:
            return await getattr(self, data['command'])(data)
        else:
            return None


TEventListener = TypeVar("TEventListener", bound=EventListener)
