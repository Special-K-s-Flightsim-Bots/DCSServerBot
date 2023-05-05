from __future__ import annotations
import json
import os
import platform
import socket
import sqlite3
import subprocess
import win32con
from contextlib import suppress
from core import utils, Server, Player
from dataclasses import dataclass
from datetime import datetime
from psutil import Process
from typing import Optional, TYPE_CHECKING, Union
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent, FileSystemMovedEvent

from .dataobject import DataObjectFactory
from .const import Status, Channel
from ..mizfile import MizFile

if TYPE_CHECKING:
    from core import Plugin


class MissionFileSystemEventHandler(FileSystemEventHandler):
    def __init__(self, server: Server):
        self.server = server
        self.log = server.log

    def on_created(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if path.endswith('.miz'):
            self.server.addMission(path)
            self.log.info(f"=> New mission {os.path.basename(path)[:-4]} added to server {self.server.name}.")

    def on_moved(self, event: FileSystemMovedEvent):
        self.on_deleted(event)
        self.on_created(FileSystemEvent(event.dest_path))

    def on_deleted(self, event: FileSystemEvent):
        path: str = os.path.normpath(event.src_path)
        if not path.endswith('.miz'):
            return
        for idx, mission in enumerate(self.server.settings['missionList']):
            if mission != path:
                continue
            if (idx + 1) == self.server.mission_id:
                self.log.fatal(f'The running mission on server {self.server.name} got deleted!')
            else:
                self.server.deleteMission(idx + 1)
                self.log.info(f"=> Mission {os.path.basename(mission)[:-4]} deleted from server {self.server.name}.")
            break


@dataclass
@DataObjectFactory.register("Server")
class ServerImpl(Server):

    def __post_init__(self):
        super().__post_init__()
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO servers (server_name, agent_host, host, port, status_channel, chat_channel) 
                    VALUES(%s, %s, %s, %s, %s, %s) 
                    ON CONFLICT (server_name) DO UPDATE 
                    SET agent_host=excluded.agent_host, 
                        host=excluded.host, 
                        port=excluded.port,
                        status_channel=excluded.status_channel,
                        chat_channel=excluded.chat_channel, 
                        last_seen=NOW()
                """, (self.name, platform.node(), self.host, self.port,
                      self.config[self.installation][Channel.STATUS.value],
                      self.config[self.installation][Channel.CHAT.value]))
        # enable autoscan for missions changes
        if self.config.getboolean(self.installation, 'AUTOSCAN'):
            self.event_handler = MissionFileSystemEventHandler(self)
            self.observer = Observer()
            self.observer.start()
        if self.config.getboolean('BOT', 'DESANITIZE'):
            # check for SLmod and desanitize its MissionScripting.lua
            for version in range(5, 7):
                filename = os.path.expandvars(self.config[self.installation]['DCS_HOME'] +
                                              f'\\Scripts\\net\\Slmodv7_{version}\\SlmodMissionScripting.lua')
                if os.path.exists(filename):
                    utils.desanitize(self, filename)
                    break

    @property
    def is_remote(self) -> bool:
        return False

    async def get_missions_dir(self) -> str:
        if 'MISSIONS_DIR' in self.config[self.installation]:
            return os.path.expandvars(self.config[self.installation]['MISSIONS_DIR'])
        else:
            return os.path.expandvars(self.config[self.installation]['DCS_HOME']) + os.path.sep + 'Missions'

    @property
    def settings(self) -> dict:
        if not self._settings:
            path = os.path.expandvars(self.config[self.installation]['DCS_HOME']) + r'\Config\serverSettings.lua'
            self._settings = utils.SettingsDict(self, path, 'cfg')
        return self._settings

    @property
    def options(self) -> dict:
        if not self._options:
            path = os.path.expandvars(self.config[self.installation]['DCS_HOME']) + r'\Config\options.lua'
            self._options = utils.SettingsDict(self, path, 'options')
        return self._options

    async def get_current_mission_file(self) -> Optional[str]:
        if not self.current_mission or not self.current_mission.filename:
            settings = self.settings
            start_index = int(settings['listStartIndex'])
            if start_index <= len(settings['missionList']):
                filename = settings['missionList'][start_index - 1]
            else:
                filename = None
            if not filename or not os.path.exists(filename):
                for idx, filename in enumerate(settings['missionList']):
                    if os.path.exists(filename):
                        settings['listStartIndex'] = idx + 1
                        break
                else:
                    filename = None
        else:
            filename = self.current_mission.filename
        return filename

    def sendtoDCS(self, message: dict):
        # As Lua does not support large numbers, convert them to strings
        for key, value in message.items():
            if type(value) == int:
                message[key] = str(value)
        msg = json.dumps(message)
        self.log.debug(f"HOST->{self.name}: {msg}")
        dcs_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dcs_socket.sendto(msg.encode('utf-8'), (self.host, int(self.port)))
        dcs_socket.close()

    def rename(self, new_name: str, update_settings: bool = False) -> None:
        # rename the entries in the main database tables
        with self.pool.connection() as conn:
            with conn.transaction():
                # call rename() in all Plugins
                for plugin in self.main.cogs.values():  # type: Plugin
                    plugin.rename(conn, self.name, new_name)
                conn.execute('UPDATE servers SET server_name = %s WHERE server_name = %s',
                             (new_name, self.name))
                conn.execute('UPDATE message_persistence SET server_name = %s WHERE server_name = %s',
                             (new_name, self.name))
        if update_settings:
            self.settings['name'] = new_name
        self.name = new_name

    def do_startup(self):
        basepath = os.path.expandvars(self.config['DCS']['DCS_INSTALLATION'])
        for exe in ['DCS_server.exe', 'DCS.exe']:
            path = basepath + f'\\bin\\{exe}'
            if os.path.exists(path):
                break
        else:
            self.log.error(f"No executable found to start a DCS server in {basepath}!")
            return
        # check if all missions are existing
        missions = [x for x in self.settings['missionList'] if os.path.exists(x)]
        if len(missions) != len(self.settings['missionList']):
            self.settings['missionList'] = missions
            self.log.warning('Removed non-existent missions from serverSettings.lua')
        self.log.debug(r'Launching DCS server with: "{}" --server --norender -w {}'.format(path, self.installation))
        if self.config.getboolean(self.installation, 'START_MINIMIZED'):
            info = subprocess.STARTUPINFO()
            info.dwFlags = subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = win32con.SW_MINIMIZE
        else:
            info = None
        p = subprocess.Popen(
            [exe, '--server', '--norender', '-w', self.installation], executable=path, startupinfo=info
        )
        with suppress(Exception):
            self.process = Process(p.pid)

    async def startup(self) -> None:
        self.do_startup()
        timeout = 300 if self.config.getboolean('BOT', 'SLOW_SYSTEM') else 180
        self.status = Status.LOADING
        await self.wait_for_status_change([Status.STOPPED, Status.PAUSED, Status.RUNNING], timeout)

    async def shutdown(self, force: bool = False) -> None:
        if not force:
            await super().shutdown(False)
        self.terminate()
        self.status = Status.SHUTDOWN

    def terminate(self) -> None:
        if self.process and self.process.is_running():
            self.process.kill()
        self.process = None

    def ban(self, ucid: str, reason: str = 'n/a', period: int = 30*86400):
        player: Player = self.get_player(ucid=ucid, active=True)
        if player and self.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            self.sendtoDCS({
                "command": "ban",
                "id": player.id,
                "period": period,
                "reason": reason
            })
        else:
            conn = sqlite3.connect(os.path.join(os.path.expandvars(self.main.config[self.installation]['DCS_HOME']),
                                                'Config', 'serverdata.sqlite3'))
            try:
                with conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO net_bans (ucid, banned_from, banned_until, reason) 
                            VALUES (?, datetime('now'), datetime('now', '? seconds'), ?)
                            ON CONFLICT (ucid) DO UPDATE 
                            SET banned_from = excluded.banned_from, banned_until = excluded.banned_until, 
                                reason = excluded.reason
                        """, (ucid, period, reason))
            except sqlite3.Error as ex:
                self.log.exception(ex)
            finally:
                conn.close()

    def unban(self, ucid: str):
        if self.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            self.sendtoDCS({"command": "unban", "ucid": ucid})
        else:
            conn = sqlite3.connect(os.path.join(os.path.expandvars(self.main.config[self.installation]['DCS_HOME']),
                                                'Config', 'serverdata.sqlite3'))
            try:
                with conn:
                    with conn.cursor() as cursor:
                        cursor.execute("DELETE FROM net_bans WHERE ucid = ?", (ucid, ))
            except sqlite3.Error as ex:
                self.log.exception(ex)
            finally:
                conn.close()

    async def modifyMission(self, preset: Union[list, dict]) -> None:
        def apply_preset(value: dict):
            if 'start_time' in value:
                miz.start_time = value['start_time']
            if 'date' in value:
                miz.date = datetime.strptime(value['date'], '%Y-%m-%d')
            if 'temperature' in value:
                miz.temperature = int(value['temperature'])
            if 'clouds' in value:
                if isinstance(value['clouds'], str):
                    miz.clouds = {"preset": value['clouds']}
                else:
                    miz.clouds = value['clouds']
            if 'wind' in value:
                miz.wind = value['wind']
            if 'groundTurbulence' in value:
                miz.groundTurbulence = int(value['groundTurbulence'])
            if 'enable_dust' in value:
                miz.enable_dust = value['enable_dust']
            if 'dust_density' in value:
                miz.dust_density = int(value['dust_density'])
            if 'qnh' in value:
                miz.qnh = int(value['qnh'])
            if 'enable_fog' in value:
                miz.enable_fog = value['enable_fog']
            if 'fog' in value:
                miz.fog = value['fog']
            if 'halo' in value:
                miz.halo = value['halo']
            if 'requiredModules' in value:
                miz.requiredModules = value['requiredModules']
            if 'accidental_failures' in value:
                miz.accidental_failures = value['accidental_failures']

        if self.status in [Status.STOPPED, Status.SHUTDOWN]:
            filename = await self.get_current_mission_file()
            if not filename:
                return
            miz = MizFile(self, filename)
            if isinstance(preset, list):
                for p in preset:
                    apply_preset(p)
            else:
                apply_preset(preset)
            miz.save()
