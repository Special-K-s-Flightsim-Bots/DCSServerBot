from __future__ import annotations
import os
from core import DataObjectFactory, DataObject, utils
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from ..const import SAVED_GAMES

if TYPE_CHECKING:
    from core import Server, Status


class InstanceBusyError(Exception):
    def __init__(self):
        super().__init__("There is a server assigned to this instance atm.")


@dataclass
@DataObjectFactory.register("Instance")
class Instance(DataObject):
    name: str
    _server: Optional[Server] = field(compare=False, repr=False, default=None, init=False)
    locals: dict = field(repr=False, default_factory=dict)
    home: str = field(repr=False, init=False, default=None)
    missions_dir: str = field(repr=False, init=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        if not self.locals.get('home'):
            self.home = os.path.join(SAVED_GAMES, self.name)
        else:
            self.home = os.path.expandvars(self.locals['home'])
        if not self.locals.get('missions_dir'):
            self.missions_dir = os.path.join(self.home, 'Missions')
        else:
            self.missions_dir = os.path.expandvars(self.locals['missions_dir'])

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
    def server(self) -> Optional[Server]:
        return self._server

    @property
    def configured_server(self) -> str:
        return self.locals['server']

    @property
    def server_user(self) -> str:
        return self.locals.get('server_user', 'Admin')

    @server.setter
    def server(self, server: Server):
        if self._server and self._server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
            raise InstanceBusyError()
        self._server = server
        server.instance = self

    def prepare(self):
        if self.config.getboolean('BOT', 'DESANITIZE'):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.join(self.instance.home,
                                        f'Scripts\\net\\Slmodv7_{version}\\SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break

