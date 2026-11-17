from enum import Enum
from typing import Any

__all__ = [
    "Side",
    "Status",
    "Coalition",
    "Channel",
    "PortType",
    "Port",
    "MISSION_LIST_BUSY_STATES",
    "MISSION_LIST_BUSY_REFUSAL",
    "MISSION_DELETE_LOADING_REFUSAL",
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


# ══ THE MISSION LIST'S BUSY WINDOWS ══════════════════════════════════════════════════════════════
# One home for the rule and its wording: the bot's writer refuses with them, the chat plugin gates
# its controls with them, and the console does the same — all three import THIS object, so a wording
# cannot drift between the doors an operator can knock on.

#: The states in which DCS's own process, when it is up, holds the mission list. DCS keeps
#: ``net.missionlist`` in memory from LOADING until it exits and writes ``serverSettings.lua`` itself
#: while it loads and on its way out, so a bot write made then races DCS's own copy of that file. The
#: state alone is not the refusal — the writer asks the process too, because a server marked LOADING
#: whose DCS has not launched yet, or SHUTTING_DOWN whose process is already gone, is writable.
MISSION_LIST_BUSY_STATES: tuple[Status, ...] = (Status.LOADING, Status.SHUTTING_DOWN)

#: The sentence every surface refuses a mission-list write with, so an operator cannot tell which
#: door refused it.
MISSION_LIST_BUSY_REFUSAL = ("The mission list cannot be changed while the server is starting up "
                            "or shutting down.")

#: The sentence a bulk remove refuses a LOADING server with, where the list itself could still be
#: written later but the files behind it are being read right now.
MISSION_DELETE_LOADING_REFUSAL = "Missions cannot be removed while the server is loading."
