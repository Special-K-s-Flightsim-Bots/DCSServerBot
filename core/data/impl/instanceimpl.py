import os

from core import Instance, Server, InstanceBusyError, Status, utils, ServerImpl, DataObjectFactory
from dataclasses import field, dataclass
from typing import Optional

from core.const import SAVED_GAMES


@dataclass
@DataObjectFactory.register("Instance")
class InstanceImpl(Instance):
    _server: Optional[ServerImpl] = field(compare=False, repr=False, default=None, init=False)
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
    def server(self) -> Optional[Server]:
        return self._server

    @server.setter
    def server(self, server: ServerImpl):
        if self._server and self._server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
            raise InstanceBusyError()
        self._server = server
        server.instance = self

    # TODO: check where to call this best
    def prepare(self):
        if self.node.locals['DCS'].get('desanitize', True):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.join(self.instance.home,
                                        f'Scripts\\net\\Slmodv7_{version}\\SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break
