from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from core import DataObject
from ..const import SAVED_GAMES

if TYPE_CHECKING:
    from core import Server

__all__ = [
    "Instance",
    "InstanceBusyError"
]


class InstanceBusyError(Exception):
    def __init__(self):
        super().__init__("There is a server assigned to this instance atm.")


@dataclass
class Instance(DataObject):
    name: str
    locals: dict = field(repr=False, default_factory=dict)

    @property
    def home(self) -> str:
        return os.path.expandvars(self.locals.get('home', os.path.join(SAVED_GAMES, self.name)))

    @property
    def dcs_port(self) -> int:
        if self.server:
            return self.server.settings['port']
        else:
            return self.locals.get('dcs_port', 10308)

    @property
    def webgui_port(self) -> int:
        return self.locals.get('webgui_port', 8088)

    @property
    def webrtc_port(self) -> int:
        return self.locals.get('webrtc_port', 10309)

    @property
    def bot_port(self) -> int:
        return self.locals.get('bot_port', 6666)

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions', {})

    @property
    def configured_server(self) -> Optional[str]:
        return self.locals.get('server')

    @property
    def server_user(self) -> str:
        return self.locals.get('server_user', 'Admin')

    @property
    def server(self) -> Optional[Server]:
        raise NotImplemented()

    @server.setter
    def server(self, server: Optional[Server]):
        raise NotImplemented()

    def prepare(self):
        raise NotImplemented()
