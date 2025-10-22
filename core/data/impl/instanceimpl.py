from __future__ import annotations

import os
import psycopg

from core import Instance, InstanceBusyError, Status, utils, DataObjectFactory
from core.autoexec import Autoexec
from core.const import SAVED_GAMES
from core.utils.helper import SettingsDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server

__all__ = ["InstanceImpl"]


@dataclass
@DataObjectFactory.register()
class InstanceImpl(Instance):

    def __post_init__(self):
        super().__post_init__()
        self.is_remote = False
        self.missions_dir = os.path.expandvars(self.locals.get('missions_dir', os.path.join(self.home, 'Missions')))
        os.makedirs(self.missions_dir, exist_ok=True)
        os.makedirs(os.path.join(self.missions_dir, 'Scripts'), exist_ok=True)
        # check / fix autoexec.cfg
        autoexec = Autoexec(instance=self)
        # check WebGUI port
        webgui_port = self.locals.get('webgui_port')
        if webgui_port and webgui_port != autoexec.webgui_port:
            autoexec.webgui_port = webgui_port
        else:
            self.locals['webgui_port'] = autoexec.webgui_port or 8088

        dcs_config = self.node.locals.get('DCS', {})
        # check UPnP
        net = {}
        if autoexec.net:
            net |= autoexec.net
        dirty = False
        use_upnp = dcs_config.get('use_upnp', self.node.locals.get('use_upnp', True))
        if use_upnp != net.get('use_upnp', True):
            net['use_upnp'] = use_upnp
            dirty |= True
        # removed as of DCS 2.9.19
#        # set new security settings (as of DCS 2.9.18)
#        allow_unsafe_api = dcs_config.get('allow_unsafe_api', ["userhooks"])
#        allow_dostring_in = dcs_config.get('allow_dostring_in', ["server", "mission"])
#        if set(allow_unsafe_api) != set(net.get('allow_unsafe_api', set())):
#            net['allow_unsafe_api'] = allow_unsafe_api
#            dirty |= True
#        if set(allow_dostring_in) != net.get('allow_dostring_in', set()):
#            net['allow_dostring_in'] = allow_dostring_in
#            dirty |= True
        if dirty:
            autoexec.net = net

        server_name = None
        settings_path = os.path.join(self.home, 'Config', 'serverSettings.lua')
        if os.path.exists(settings_path):
            settings = SettingsDict(self, settings_path, root='cfg')
            dcs_port = self.locals.get('dcs_port')
            if dcs_port:
                settings['port'] = dcs_port
            else:
                self.locals['dcs_port'] = settings.get('port', 10308)
            server_name = settings.get('name', 'DCS Server') if settings else None
            if server_name == 'n/a':
                server_name = None
        self.update_instance(server_name)

    def update_instance(self, server_name: str | None = None):
        try:
            with self.pool.connection() as conn:
                with conn.transaction():
                    conn.execute("""
                        INSERT INTO instances (node, instance, port, server_name)
                        VALUES (%s, %s, %s, %s) 
                        ON CONFLICT (node, instance) DO UPDATE 
                        SET port=excluded.port, server_name=excluded.server_name 
                    """, (self.node.name, self.name, self.locals.get('bot_port', 6666), server_name))
        except psycopg.errors.UniqueViolation:
            self.log.error(f"bot_port {self.locals.get('bot_port', 6666)} is already in use on node {self.node.name}!")
            raise

    @property
    def home(self) -> str:
        return os.path.expandvars(self.locals.get('home', os.path.join(SAVED_GAMES, self.name)))

    def update_server(self, server: Server | None = None):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    UPDATE instances SET server_name = %s, last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE node = %s AND instance = %s
                """, (server.name if server and server.name != 'n/a' else None, self.node.name, self.name))

    def set_server(self, server: Server | None):
        if self._server and self._server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
            raise InstanceBusyError()
        self._server = server
        # delete the serverSettings.lua to unlink the current server
        if not server:
            settings_path = os.path.join(self.home, 'Config', 'serverSettings.lua')
            if os.path.exists(settings_path):
                os.remove(settings_path)
        self.prepare()
        if server and server.name:
            server.instance = self
        self.update_server(server)

    def prepare(self):
        if 'DCS' not in self.node.locals:
            return
        # desanitisation of Slmod (if there)
        if self.node.locals['DCS'].get('desanitize', True):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.join(self.home, 'Scripts', 'net', f'Slmodv7_{version}',
                                        'SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break
