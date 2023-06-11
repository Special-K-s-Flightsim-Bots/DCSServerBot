from enum import Enum


class Side(Enum):
    UNKNOWN = -1
    SPECTATOR = 0
    RED = 1
    BLUE = 2
    NEUTRAL = 3


class Status(Enum):
    UNREGISTERED = 'Unregistered'
    SHUTDOWN = 'Shutdown'
    RUNNING = 'Running'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    LOADING = 'Loading'


class Coalition(Enum):
    ALL = 'all'
    RED = 'red'
    BLUE = 'blue'
    NEUTRAL = 'neutral'


class Channel(Enum):
    STATUS = 'STATUS_CHANNEL'
    ADMIN = 'ADMIN_CHANNEL'
    CHAT = 'CHAT_CHANNEL'
    EVENTS = 'EVENTS_CHANNEL'
    COALITION_BLUE_CHAT = 'COALITION_BLUE_CHANNEL'
    COALITION_RED_CHAT = 'COALITION_RED_CHANNEL'
    COALITION_BLUE_EVENTS = 'COALITION_BLUE_EVENTS'
    COALITION_RED_EVENTS = 'COALITION_RED_EVENTS'