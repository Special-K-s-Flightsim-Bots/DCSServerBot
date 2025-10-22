from __future__ import annotations

from core import DataObject
from core.data.node import FatalException
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    _server: Server | None = field(compare=False, repr=False, default=None, init=False)
    missions_dir: str = field(repr=False, init=False, default=None)

    @property
    def home(self) -> str:
        raise NotImplementedError()

    @property
    def dcs_host(self) -> str:
        if self.server:
            return self.server.settings.get('bind_address') or '127.0.0.1'
        else:
            return "127.0.0.1"

    @property
    def dcs_port(self) -> int:
        if self.server:
            port = int(self.server.settings.get('port', 10308))
        else:
            port = int(self.locals.get('dcs_port', 10308))
        if port < 1024:
            self.log.warning(f"The DCS port of instance {self.name} is < 1024. "
                             f"You need to run this server as Administrator!")
        elif port > 65535:
            raise FatalException(f"The DCS port of instance {self.name} is > 65535!")
        return port

    @property
    def webgui_port(self) -> int:
        webgui_port = int(self.locals.get('webgui_port', 8088))
        if webgui_port < 1024:
            self.log.warning(f"The WebGUI-port of instance {self.name} is < 1024. "
                             f"You need to run this server as Administrator!")
        elif webgui_port > 65535:
            raise FatalException(f"The WebGUI-port of instance {self.name} is > 65535!")
        return webgui_port

    @property
    def bot_port(self) -> int:
        bot_port = int(self.locals.get('bot_port', 6666))
        if bot_port < 1024 or bot_port > 65535:
            raise FatalException(f"The bot-port of instance {self.name} needs to be between 1024 and 65535!")
        return bot_port

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions') or {}

    @property
    def server_user(self) -> str:
        return self.locals.get('server_user', 'Admin')

    @property
    def server(self) -> Server | None:
        return self._server

    @server.setter
    def server(self, server: Server | None):
        self.set_server(server)

    def set_server(self, server: Server | None):
        self._server = server

    def prepare(self):
        raise NotImplementedError()
