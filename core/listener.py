from __future__ import annotations
import inspect
from dataclasses import MISSING
from typing import TypeVar, TYPE_CHECKING, Any, Type, Optional

if TYPE_CHECKING:
    from core import DCSServerBot, Plugin, Server, Player

__all__ = [
    "event",
    "chat_command",
    "EventListener",
    "TEventListener"
]


def event(name: str = MISSING, cls: Type[Event] = MISSING, **attrs) -> Any:
    if cls is MISSING:
        cls = Event

    def decorator(func):
        if isinstance(func, Event):
            raise TypeError('Callback is already an Event')
        return cls(func, name=name, **attrs)

    return decorator


class Event:
    def __init__(self, func, **kwargs):
        self.name: str = kwargs.get('name') or func.__name__
        self.callback = func

    async def __call__(self, listener: EventListener, server: Server, data: dict) -> None:
        await self.callback(listener, server, data)


def chat_command(name: str = MISSING, cls: Type[ChatCommand] = MISSING, **attrs) -> Any:
    if cls is MISSING:
        cls = ChatCommand

    def decorator(func):
        if isinstance(func, ChatCommand):
            raise TypeError('Callback is already a ChatCommand')
        return cls(func, name=name, **attrs)

    return decorator


class ChatCommand:
    def __init__(self, func, **kwargs):
        self.name: str = kwargs.get('name') or func.__name__
        self.help: str = inspect.cleandoc(kwargs.get('help', ''))
        self.roles: list[str] = kwargs.get('roles', [])
        self.usage: str = kwargs.get('usage')
        self.aliases: list[str] = kwargs.get('aliases', [])
        self.callback = func

    async def __call__(self, listener: EventListener, server: Server, player: Player, params: list[str]) -> None:
        await self.callback(listener, server, player, params)


class EventListenerMeta(type):
    __events__: dict[str, Event]
    __chat_commands__: dict[str, ChatCommand]

    def __new__(cls, *args: Any, **kwargs: Any):
        name, bases, attrs = args
        events = {}
        chat_commands = {}
        new_cls = super().__new__(cls, name, bases, attrs, **kwargs)
        for base in reversed(new_cls.__mro__):
            for elem, value in base.__dict__.items():
                if elem in events:
                    del events[elem]
                if elem in chat_commands:
                    del chat_commands[elem]
                if isinstance(value, Event):
                    events[value.name] = value
                elif isinstance(value, ChatCommand):
                    chat_commands[value.name] = value
        new_cls.__events__ = events
        new_cls.__chat_commands__ = chat_commands
        return new_cls


class EventListener(metaclass=EventListenerMeta):
    __events__: dict[str, Event]
    __chat_commands__: dict[str, ChatCommand]
    __all_commands__: dict[str, ChatCommand]

    def __new__(cls, plugin: Plugin):
        self = super().__new__(cls)
        self.__events__ = cls.__events__
        self.__chat_commands__ = cls.__chat_commands__
        self.__all_commands__ = {}
        for key, value in self.__chat_commands__.items():
            self.__all_commands__[key] = value
            for alias in value.aliases:
                self.__all_commands__[alias] = value
        return self

    def __init__(self, plugin: Plugin):
        self.plugin: Plugin = plugin
        self.plugin_name = type(self).__module__.split('.')[-2]
        self.bot: DCSServerBot = plugin.bot
        self.log = plugin.log
        self.pool = plugin.pool
        self.locals: dict = plugin.locals
        self.loop = plugin.loop
        self.prefix = self.bot.node.config.get('chat_command_prefix', '-')

    @property
    def events(self) -> Any:
        return self.__events__.values()

    @property
    def chat_commands(self) -> Any:
        return self.__chat_commands__.values()

    def has_event(self, name: str) -> bool:
        return name in self.__events__

    async def processEvent(self, name: str, server: Server, data: dict) -> None:
        try:
            await self.__events__[name](self, server, data)
        except Exception as ex:
            self.log.exception(ex)

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        return self.plugin.get_config(server, plugin_name=plugin_name, use_cache=use_cache)

    @event(name="onChatCommand")
    async def onChatCommand(self, server: Server, data: dict) -> None:
        player: Player = server.get_player(id=data['from_id'], active=True)
        command = self.__all_commands__.get(data['subcommand'])
        if not command or not player:
            return
        if command.roles and not player.has_discord_roles(command.roles):
            return
        await command(self, server, player, data.get('params'))

    async def shutdown(self) -> None:
        pass


TEventListener = TypeVar("TEventListener", bound=EventListener)
