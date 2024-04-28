from __future__ import annotations

from core import DataObject
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

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
    locals: dict = field(repr=False, default_factory=dict)
    _server: Optional[Server] = field(compare=False, repr=False, default=None, init=False)
    missions_dir: str = field(repr=False, init=False, default=None)

    @property
    def home(self) -> str:
        raise NotImplementedError()

    @property
    def dcs_port(self) -> int:
        if self.server:
            return int(self.server.settings['port'])
        else:
            return int(self.locals.get('dcs_port', 10308))

    @property
    def webgui_port(self) -> int:
        return int(self.locals.get('webgui_port', 8088))

    @property
    def bot_port(self) -> int:
        return int(self.locals.get('bot_port', 6666))

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
        return self._server

    @server.setter
    def server(self, server: Optional[Server]):
        self.set_server(server)

    def set_server(self, server: Optional[Server]):
        self._server = server

    def prepare(self):
        raise NotImplemented()
