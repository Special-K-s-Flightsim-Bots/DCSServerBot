import os

from core import Instance, InstanceBusyError, Status, utils, DataObjectFactory
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from core.autoexec import Autoexec
from core.const import SAVED_GAMES
from core.utils.helper import SettingsDict

if TYPE_CHECKING:
    from core import Server

__all__ = ["InstanceImpl"]


@dataclass
@DataObjectFactory.register()
class InstanceImpl(Instance):

    def __post_init__(self):
        super().__post_init__()
        self.missions_dir = os.path.expandvars(self.locals.get('missions_dir', os.path.join(self.home, 'Missions')))
        os.makedirs(self.missions_dir, exist_ok=True)
        os.makedirs(os.path.join(self.missions_dir, 'Scripts'), exist_ok=True)
        autoexec = Autoexec(instance=self)
        self.locals['webgui_port'] = autoexec.webgui_port or 8088
        settings = {}
        settings_path = os.path.join(self.home, 'Config', 'serverSettings.lua')
        if os.path.exists(settings_path):
            settings = SettingsDict(self, settings_path, root='cfg')
            self.locals['dcs_port'] = settings.get('port', 10308)
        server_name = settings.get('name', 'DCS Server') if settings else None
        if server_name and server_name == 'n/a':
            server_name = None
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO instances (node, instance, port, server_name)
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT (node, instance) DO UPDATE 
                    SET port=excluded.port, server_name=excluded.server_name 
                """, (self.node.name, self.name, self.locals.get('bot_port', 6666), server_name))

    @property
    def home(self) -> str:
        return os.path.expandvars(self.locals.get('home', os.path.join(SAVED_GAMES, self.name)))

    def set_server(self, server: Optional["Server"]):
        if self._server and self._server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
            raise InstanceBusyError()
        self._server = server
        self.prepare()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    UPDATE instances SET server_name = %s, last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE node = %s AND instance = %s
                """, (server.name if server else None, self.node.name, self.name))
                if server and server.name:
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
