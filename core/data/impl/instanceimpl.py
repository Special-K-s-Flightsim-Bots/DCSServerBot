import luadata
import os
import psycopg
import shutil

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
        # check UPnP
        net = autoexec.net or {}
        use_upnp = net.get('use_upnp', True)
        if self.node.locals.get('use_upnp', True) != use_upnp:
            net |= {
                "use_upnp": self.node.locals.get('use_upnp', True)
            }
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

    def update_instance(self, server_name: Optional[str] = None):
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

    def update_server(self, server: Optional["Server"] = None):
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    UPDATE instances SET server_name = %s, last_seen = (now() AT TIME ZONE 'utc') 
                    WHERE node = %s AND instance = %s
                """, (server.name if server and server.name != 'n/a' else None, self.node.name, self.name))

    def set_server(self, server: Optional["Server"]):
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
        # Profanity filter
        if self.server and self.server.locals.get('profanity_filter', False):
            language = self.node.config.get('language', 'en')
            wordlist = os.path.join(self.node.config_dir, 'profanity.txt')
            if not os.path.exists(wordlist):
                shutil.copy2(os.path.join('samples', 'wordlists', f"{language}.txt"), wordlist)
            with open(wordlist, mode='r', encoding='utf-8') as wl:
                words = [x.strip() for x in wl.readlines() if not x.startswith('#')]
            targetfile = os.path.join(os.path.expandvars(self.node.locals['DCS']['installation']), 'Data', 'censor.lua')
            bakfile = targetfile.replace('.lua', '.bak')
            if not os.path.exists(bakfile):
                shutil.copy2(targetfile, bakfile)
            with open(targetfile, mode='wb') as outfile:
                outfile.write((f"{language.upper()} = " + luadata.serialize(
                    words, indent='\t', indent_level=0)).encode('utf-8'))
        else:
            targetfile = os.path.join(os.path.expandvars(self.node.locals['DCS']['installation']), 'Data', 'censor.lua')
            bakfile = targetfile.replace('.lua', '.bak')
            if os.path.exists(bakfile):
                shutil.copy2(bakfile, targetfile)
                os.remove(bakfile)
