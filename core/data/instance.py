from __future__ import annotations
from core import DataObject
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


class InstanceBusyError(Exception):
    def __init__(self):
        super().__init__("There is a server assigned to this instance atm.")


@dataclass
class Instance(DataObject):
    name: str
    locals: dict = field(repr=False, default_factory=dict)

    @property
    def dcs_port(self) -> int:
        return self.locals.get('dcs_port', 10308)

    @property
    def webgui_port(self) -> int:
        return self.locals.get('webgui_port', 8088)

    @property
    def bot_port(self) -> int:
        return self.locals.get('bot_port', 6666)

    @property
    def extensions(self) -> dict:
        return self.locals.get('extensions', {})

    @property
    def configured_server(self) -> str:
        return self.locals['server']

    @property
    def server_user(self) -> str:
        return self.locals.get('server_user', 'Admin')

    @property
    def server(self) -> Optional[Server]:
        raise NotImplemented()

    @server.setter
    def server(self, server: Server):
        raise NotImplemented()

    def prepare(self):
        raise NotImplemented()
