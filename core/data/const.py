from enum import Enum
from typing import Any

__all__ = [
    "Side",
    "Status",
    "Coalition",
    "Channel",
    "PortType",
    "Port",
]


class Side(Enum):
    UNKNOWN = -1
    NEUTRAL = 0
    RED = 1
    BLUE = 2


class Status(Enum):
    UNREGISTERED = 'Unregistered'
    SHUTDOWN = 'Shutdown'
    LOADING = 'Loading'
    RUNNING = 'Running'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    SHUTTING_DOWN = 'Shutting down'


class Coalition(Enum):
    ALL = 'all'
    RED = 'red'
    BLUE = 'blue'
    NEUTRAL = 'neutral'


class Channel(Enum):
    STATUS = 'status'
    ADMIN = 'admin'
    CHAT = 'chat'
    EVENTS = 'events'
    VOICE = 'voice'
    AUDIT = 'audit'
    COALITION_BLUE_CHAT = 'blue'
    COALITION_BLUE_EVENTS = 'blue_events'
    COALITION_RED_CHAT = 'red'
    COALITION_RED_EVENTS = 'red_events'


class PortType(Enum):
    TCP = 'tcp'
    UDP = 'udp'
    BOTH = 'tcp+udp'


class Port:

    def __init__(self, port: int, typ: PortType, *, public: bool = False):
        self.port: int = port
        self.typ: PortType = typ
        self.public: bool = public

    def __repr__(self):
        return f'{self.port}/{self.typ.value}'

    def __str__(self):
        return str(self.port)

    def __int__(self):
        return self.port

    def __index__(self):
        return self.port

    def __hash__(self):
        return hash(self.port)

    def __eq__(self, other):
        if isinstance(other, Port):
            return self.port == other.port
        elif isinstance(other, int):
            return self.port == other
        return False

    def type(self) -> PortType:
        return self.typ

    def to_dict(self) -> dict:
        return {
            "port": self.port,
            "typ": self.typ.value,
            "public": self.public
        }

    @staticmethod
    def from_dict(data: dict) -> Any: # TODO: Self
        return Port(data['port'], PortType(data['typ']), public=data.get('public', False))
