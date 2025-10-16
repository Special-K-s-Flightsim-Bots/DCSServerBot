from enum import Enum

__all__ = [
    "Side",
    "Status",
    "Coalition",
    "Channel"
]


class Side(Enum):
    UNKNOWN = -1
    SPECTATOR = 0
    RED = 1
    BLUE = 2
    NEUTRAL = 3


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
