import os

from core import Instance, InstanceBusyError, Status, utils, ServerImpl, DataObjectFactory
from dataclasses import field, dataclass
from typing import Optional

from core.autoexec import Autoexec
from core.utils.helper import SettingsDict

__all__ = ["InstanceImpl"]


@dataclass
@DataObjectFactory.register("Instance")
class InstanceImpl(Instance):
    _server: Optional[ServerImpl] = field(compare=False, repr=False, default=None, init=False)
    missions_dir: str = field(repr=False, init=False, default=None)

    def __post_init__(self):
        super().__post_init__()
        if not self.locals.get('missions_dir'):
            self.missions_dir = os.path.join(self.home, 'Missions')
        else:
            self.missions_dir = os.path.expandvars(self.locals['missions_dir'])
        autoexec = Autoexec(instance=self)
        self.locals['webgui_port'] = autoexec.webgui_port or 8088
        self.locals['webrtc_port'] = autoexec.webrtc_port or 10309
        settings = {}
        settings_path = os.path.join(self.home, 'Config', 'serverSettings.lua')
        if os.path.exists(settings_path):
            settings = SettingsDict(self, settings_path, root='cfg')
            self.locals['dcs_port'] = settings['port']
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO instances (instance, node, port, server_name)
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT DO NOTHING 
                """, (self.name, self.node.name, self.locals.get('bot_port', 6666),
                      settings['name'] if settings else None))

    @property
    def server(self) -> Optional[ServerImpl]:
        return self._server

    @server.setter
    def server(self, server: Optional[ServerImpl]):
        if self._server and self._server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
            raise InstanceBusyError()
        self._server = server
        self.prepare()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                            UPDATE instances SET server_name = %s, last_seen = NOW() WHERE instance = %s
                        """, (server.name if server else None, self.name))
                if server:
                    server.instance = self

    def prepare(self):
        if self.node.locals['DCS'].get('desanitize', True):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.join(self.home, 'Scripts', 'net', f'Slmodv7_{version}',
                                        'SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break