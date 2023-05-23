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
    STATUS = 'status'
    ADMIN = 'admin'
    CHAT = 'chat'
    COALITION_BLUE = 'COALITION_BLUE_CHANNEL'
    COALITION_RED = 'COALITION_RED_CHANNEL'
