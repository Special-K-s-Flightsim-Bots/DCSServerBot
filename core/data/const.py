from enum import Enum

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
        self.port = port
        self.typ = typ
        self.public = public

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

    def type(self):
        return self.typ
